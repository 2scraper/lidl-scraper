#!/usr/bin/env python3
"""proxy_pool.py — proxy pool, rotation policy, credential masking, argv
safety. No site knowledge lives here.

Accepted proxy formats (one per line in --proxy-file, or a single value on
--proxy):

    http://login:password@host:port
    host:port:login:password
    host:port                          (no auth)

Credentials NEVER reach argv or a command line — see PROHIBITED_FLAG_STYLE
below and the `*_auth()` helpers, which hand credentials to the driver's own
authenticated fields instead. Logs get host:port; credentials are masked.
"""
from __future__ import annotations

import itertools
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# Chromium reports a dead upstream proxy as one of these — NOT as a timeout.
# A caller must retry a *timeout* at the same exit, and a *dead proxy* error
# at a different one; conflating them wastes retries on a proxy that will
# never answer.
PROXY_DEAD_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED",
    "ERR_HTTP_RESPONSE_CODE_FAILURE",
    "net::ERR_NO_SUPPORTED_PROXIES",
)

# A literal command-line flag shaped like this leaks the password to `ps`,
# shell history and any crash report that includes argv. Never build one.
PROHIBITED_FLAG_STYLE = "--proxy-server=http://login:password@host:port"

_LINE_RE = re.compile(
    r"^(?:(?P<scheme>\w+)://)?"
    r"(?:(?P<login>[^:@/]+):(?P<password>[^:@/]*)@)?"
    r"(?P<host>[^:/@]+):(?P<port>\d+)"
    r"(?::(?P<login2>[^:@/]+):(?P<password2>[^:@/]*))?$"
)


class ProxyParseError(ValueError):
    pass


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    login: Optional[str] = None
    password: Optional[str] = None
    scheme: str = "http"

    @property
    def has_auth(self) -> bool:
        return bool(self.login)

    def masked(self) -> str:
        auth = f"{self.login}=***@" if self.login else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def server_only(self) -> str:
        """host:port with NO credentials — safe to pass as a bare
        `--proxy-server` / `proxyServer` value on engines that cannot
        authenticate that way (Selenium's --proxy-server takes no auth)."""
        return f"{self.scheme}://{self.host}:{self.port}"

    def playwright_proxy_dict(self) -> dict:
        """Playwright's native `proxy=` field — credentials never touch argv."""
        d = {"server": self.server_only()}
        if self.login:
            d["username"] = self.login
            d["password"] = self.password or ""
        return d

    def pyppeteer_launch_arg(self) -> str:
        """pyppeteer/puppeteer: server goes on the launch args (no creds);
        call `page.authenticate({...})` separately for credentials."""
        return f"--proxy-server={self.server_only()}"

    def pyppeteer_auth_dict(self) -> Optional[dict]:
        if not self.login:
            return None
        return {"username": self.login, "password": self.password or ""}


def parse_proxy_line(line: str) -> Proxy:
    line = line.strip()
    if not line or line.startswith("#"):
        raise ProxyParseError(f"blank/comment line: {line!r}")
    m = _LINE_RE.match(line)
    if not m:
        raise ProxyParseError(f"unrecognised proxy format: {line!r}")
    g = m.groupdict()
    login = g["login"] or g["login2"]
    password = g["password"] if g["login"] else g["password2"]
    return Proxy(
        host=g["host"],
        port=int(g["port"]),
        login=login,
        password=password,
        scheme=g["scheme"] or "http",
    )


def load_proxies(proxy: Optional[str], proxy_file: Optional[str]) -> List[Proxy]:
    out: List[Proxy] = []
    if proxy:
        out.append(parse_proxy_line(proxy))
    if proxy_file:
        for line in Path(proxy_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(parse_proxy_line(line))
    return out


class ProxyPool:
    """Round-robin pool with per-proxy failure tracking.

    Concurrency contract (see engines' --concurrency): call `.worker_view(i,
    n)` once per worker to get an independent `ProxyPool` rotated to a
    different starting offset over the SAME underlying proxy list. Each
    worker then only ever calls `.next()` on its own view, so no lock is
    needed anywhere — the safety comes from construction, not discipline.
    """

    def __init__(self, proxies: Sequence[Proxy], shuffle: bool = False, block_retries: int = 3):
        proxies = list(proxies)
        if shuffle:
            proxies = proxies[:]
            random.shuffle(proxies)
        self._proxies: List[Proxy] = proxies
        self._blocked_count = {id(p): 0 for p in proxies}
        self._dead = {id(p): False for p in proxies}
        self.block_retries = max(1, block_retries)
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._proxies)

    def worker_view(self, worker_index: int, total_workers: int) -> "ProxyPool":
        if not self._proxies:
            return ProxyPool([], block_retries=self.block_retries)
        offset = worker_index % len(self._proxies)
        rotated = self._proxies[offset:] + self._proxies[:offset]
        view = ProxyPool(rotated, block_retries=self.block_retries)
        return view

    def _live_proxies(self) -> List[Proxy]:
        return [p for p in self._proxies if not self._dead[id(p)]]

    def next(self) -> Optional[Proxy]:
        live = self._live_proxies()
        if not live:
            return None
        p = live[self._cursor % len(live)]
        self._cursor += 1
        return p

    def report_failure(self, proxy: Proxy, dead: bool) -> None:
        """`dead=True` for a PROXY_DEAD_MARKERS error (kills the exit after
        `block_retries` in a row); `dead=False` (a timeout / non-proxy
        error) does not count against the exit at all."""
        key = id(proxy)
        if key not in self._blocked_count:
            return
        if not dead:
            return
        self._blocked_count[key] += 1
        if self._blocked_count[key] >= self.block_retries:
            self._dead[key] = True

    def report_success(self, proxy: Proxy) -> None:
        self._blocked_count[id(proxy)] = 0


def is_proxy_dead_error(message: str) -> bool:
    return any(marker in message for marker in PROXY_DEAD_MARKERS)


# --------------------------------------------------------------------------- #
# Redacting a credential OUT OF an arbitrary string — not the Proxy dataclass
# above, but a driver/HTTP library's own exception message. CLAUDE.md §8:
# "an EXCEPTION MESSAGE is a log" — Playwright's connect_over_cdp repeats a
# failed ws:// endpoint (login:password embedded) up to five times in one
# error (message + a four-line call log), and `requests` puts the full URL,
# query string included, into every HTTPError/connection-error message. Any
# endpoint whose credential rides in the URL — a --cdp-endpoint connection
# string, or 2Captcha's fingerprint/random which takes `?key=...` — leaks it
# the moment the call fails, independent of the `Proxy.masked()` path above,
# which only ever sees a *successful* parse, never a driver's own error text.
# Live-reproduced (2026-09-14): a bad connect_over_cdp call put the fake
# login AND password into the exception's "Call log" four times over.
_URL_CREDENTIAL_RE = re.compile(r"(?P<scheme>\w+://)[^\s/@]+@")
_KEY_PARAM_RE = re.compile(r"(?i)\b((?:client)?key|token|api[_-]?key)=[^&\s'\"]+")


def redact_credentials(text: str) -> str:
    """Scrub every credential shape this family's own endpoints can leak
    into a driver/HTTP exception's message, GLOBALLY (re.sub, not just the
    first occurrence — a masker that stops after one hit prints the
    password the other four times and looks like it is working). Call this
    on `str(exc)` BEFORE re-raising or logging — never pass the original
    exception through afterwards (chain with `from None`), or its own
    unredacted text still reaches the traceback."""
    if not text:
        return text
    text = _URL_CREDENTIAL_RE.sub(lambda m: f"{m.group('scheme')}***:***@", text)
    text = _KEY_PARAM_RE.sub(lambda m: f"{m.group(1)}=***", text)
    return text
