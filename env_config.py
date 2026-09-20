#!/usr/bin/env python3
"""env_config.py — hand-rolled .env loader, no site knowledge.

Precedence, highest first: explicit CLI flag > exported environment variable
> `.env` file > argparse default. That order matters in both directions: a
`.env` file must never override something the caller typed or already
exported, and a value from `.env` must never win over a CI/`direnv` secret
that is already in the environment when this process starts.

`ENV_KEYS` is an EXPLICIT map of env-var-name -> argparse destination, so a
typo in `.env` (e.g. `TWOCAPTCH_KEY`) is reported instead of silently
ignored. Two rules that matter here:

  - Never map a variable onto a flag that already has a non-empty default
    (`--out` is the classic example) — apply_env() only fills UNSET values,
    so such a mapping would look configurable and quietly do nothing.
  - The literal placeholder text in `.env.example` (an empty value after
    `=`) is treated as unset, never sent anywhere as if it were a real key.

Run `python3 env_config.py` to see what would be picked up, without ever
printing a secret value.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("env_config")

try:
    from dotenv import dotenv_values as _dotenv_values  # type: ignore
except ImportError:  # pragma: no cover - exercised by the "no dotenv" path
    _dotenv_values = None

# variable -> argparse destination. Keep in exact sync with .env.example;
# a test asserts the two sets are equal in both directions.
ENV_KEYS: Dict[str, str] = {
    "TWOCAPTCHA_KEY": "twocaptcha_key",
    "LIDL_PROXY": "proxy",
    "LIDL_CDP_ENDPOINT": "cdp_endpoint",
    "LIDL_URL": "url",
}

_PLACEHOLDER_RE = re.compile(r"^\s*$")
# CLAUDE.md §17: a copied .env.example can document a credentialled URL the
# way the vendor documents it — `ws://{login}-zone-...-pid-{profileId}:
# {password}@cb.2captcha.com:9222` — and a literal-placeholder check (an
# exact string like "your_key_here") never matches that shape, so pasting
# the DOCUMENTED example past the `=` reads as configured: a run then
# authenticates to the real host as the literal string "{login}-zone-…" and
# fails with a confusing 401 far from its actual cause. This repo's own
# .env.example currently ships blank `KEY=` lines (not a literal braced
# value), so the historical incident hasn't happened here — this is
# defensive hardening for a user who pastes the doc comment by hand, not a
# fix for a bug already shipped. Treat ANY `{...}` left in a value as
# unset, generically, rather than matching one specific vendor's example.
_BRACED_PLACEHOLDER_RE = re.compile(r"[{}]")


def _is_placeholder(value: Optional[str]) -> bool:
    """One implementation, used by both apply_env() and _report() — CLAUDE.md
    §17 names the alternative (two independent checks that quietly drift
    apart) as its own defect class. True for an empty value, or one that
    still contains a `{...}` example fragment."""
    if value is None:
        return True
    return bool(_PLACEHOLDER_RE.match(value)) or bool(_BRACED_PLACEHOLDER_RE.search(value))


def _read_dotenv(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    if _dotenv_values is not None:
        raw = _dotenv_values(str(path))
        return {k: v for k, v in raw.items() if v is not None}
    # Minimal hand-rolled parser used when python-dotenv isn't installed.
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_dotenv_values(dotenv_path: str = ".env") -> Dict[str, str]:
    return _read_dotenv(Path(dotenv_path))


def apply_env(args: argparse.Namespace, dotenv_path: str = ".env") -> argparse.Namespace:
    """Fill UNSET argparse destinations from the environment, then `.env`.

    A destination counts as "unset" only when it is still at its argparse
    default (None for the flags this is used with) — a flag the caller typed
    is never touched, per the precedence rule above.
    """
    dotenv_values = load_dotenv_values(dotenv_path)
    for env_key, dest in ENV_KEYS.items():
        if not hasattr(args, dest) or getattr(args, dest) not in (None, ""):
            continue
        # exported environment variable outranks .env
        value = os.environ.get(env_key)
        source = "environment"
        if value is None:
            value = dotenv_values.get(env_key)
            source = ".env"
        if value is None:
            continue  # genuinely unset — nothing to warn about
        if _is_placeholder(value):
            if _BRACED_PLACEHOLDER_RE.search(value):
                # Name the PLACEHOLDER, never quote the value — the value
                # can be a credentialled URL with a real key/password
                # sitting right next to the unfilled `{...}` part.
                log.warning(
                    "%s (%s) still contains a %s placeholder fragment — "
                    "treating it as unset rather than sending it as a real "
                    "credential. Replace the whole {...} part with your "
                    "actual value.",
                    env_key, source, "{...}",
                )
            continue  # empty, or a braced placeholder — never send as real
        setattr(args, dest, value)
        setattr(args, f"_{dest}_source", source)
    return args


def _mask(value: Optional[str]) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-2:]} ({len(value)} chars)"


def _report() -> None:
    dotenv_values = load_dotenv_values(".env")
    print("env_config: what this process would pick up (values never printed)\n")
    for env_key, dest in ENV_KEYS.items():
        in_env = env_key in os.environ and not _is_placeholder(os.environ.get(env_key))
        in_dotenv = env_key in dotenv_values and not _is_placeholder(dotenv_values[env_key])
        if in_env:
            source, raw = "exported environment variable", os.environ[env_key]
        elif in_dotenv:
            source, raw = ".env", dotenv_values[env_key]
        elif env_key in os.environ and _BRACED_PLACEHOLDER_RE.search(os.environ[env_key]):
            source, raw = "(still has a {...} placeholder fragment — treated as unset, see warning above)", None
        elif env_key in dotenv_values and _BRACED_PLACEHOLDER_RE.search(dotenv_values[env_key]):
            source, raw = "(still has a {...} placeholder fragment — treated as unset, see warning above)", None
        else:
            source, raw = "(unset — falls back to CLI default)", None
        print(f"  {env_key:22s} -> --{dest.replace('_', '-'):16s} [{source}] {_mask(raw)}")


if __name__ == "__main__":
    _report()
