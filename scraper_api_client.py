#!/usr/bin/env python3
"""scraper_api_client.py — the browserless HTTP client for 2Captcha's REST
surface. No site knowledge. `captcha_solver.py` and `fingerprint_client.py`
both sit on top of this rather than calling `requests` directly, so there is
exactly one place that knows the base URL, auth shape and error envelope.

Covers three of 2Captcha's four separately-billed products behind one key
(the fourth, proxies, is `proxy_pool.py` + LIDL_PROXY — a 2captcha.com/
proxy credential, 2prx.com is a synonym for the same product, not a
separate host):

  - classic captcha solving (createTask / getTaskResult)
  - the Scraping Browser API (POST /browser/connection -> a CDP URL)
  - the Fingerprint API (GET /fingerprint/random)

Never construct a competitor's API call from this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests

from proxy_pool import redact_credentials

API_BASE = "https://api.2captcha.com"


class TwoCaptchaError(RuntimeError):
    pass


class TwoCaptchaAuthError(TwoCaptchaError):
    """Missing/invalid key. Callers treat this as a WARNING, not a crash —
    a run continues without solving and reports EXIT_BLOCKED only if the
    page turns out to actually need it."""


@dataclass
class CaptchaTask:
    task_id: int


class TwoCaptchaClient:
    def __init__(self, api_key: Optional[str], timeout: int = 30, api_base: Optional[str] = None):
        self.api_key = api_key
        self.timeout = timeout
        # `--captcha-api` (testing only): override the base URL so a smoke
        # test or a local run can point this at a mock server instead of
        # the real https://api.2captcha.com. CLAUDE.md §17 names the exact
        # failure mode this guards against — a CLI flag defined on the
        # parser but never read by anything is "a documented feature not
        # working at all", found live in this family more than once.
        # `self.api_base` (an instance attribute, never the module-level
        # API_BASE) is what every call below actually uses.
        self.api_base = api_base or API_BASE

    def _require_key(self) -> str:
        if not self.api_key:
            raise TwoCaptchaAuthError(
                "TWOCAPTCHA_KEY is not set — get one at https://2captcha.com/setting"
            )
        return self.api_key

    def _post(self, path: str, payload: dict) -> dict:
        # createTask/getTaskResult/getBalance all come through here, and
        # until this fix a network failure (2Captcha down, DNS failure, a
        # `--captcha-api` override pointed at nothing) raised a bare
        # `requests.exceptions.RequestException` instead of
        # `TwoCaptchaError` — invisible to captcha_solver.solve_when_
        # blocked's `except TwoCaptchaAuthError / except TwoCaptchaError`
        # pair, so it propagated all the way up as an uncaught crash.
        # That directly contradicts this codebase's own stated policy (see
        # captcha_solver.py's module docstring): "a solver-side error is a
        # WARNING, never a crash — the run continues". Confirmed the
        # identical, still-unfixed gap in stockx-scraper's
        # scraper_api_client.py (left there per the standing instruction
        # not to touch that repo).
        try:
            resp = requests.post(f"{self.api_base}{path}", json=payload, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise TwoCaptchaError(f"{path} request failed: {redact_credentials(str(exc))}") from None
        try:
            data = resp.json()
        except ValueError as exc:
            raise TwoCaptchaError(f"non-JSON response from {path}: {resp.text[:200]}") from exc
        return data

    # ----------------------------------------------------------------- #
    # Classic captcha solving — createTask / getTaskResult
    # ----------------------------------------------------------------- #
    def create_task(self, task: dict) -> CaptchaTask:
        key = self._require_key()
        data = self._post("/createTask", {"clientKey": key, "task": task})
        if data.get("errorId"):
            raise TwoCaptchaError(f"createTask failed: {data.get('errorCode')} {data.get('errorDescription')}")
        return CaptchaTask(task_id=data["taskId"])

    def get_task_result(self, task: CaptchaTask) -> dict:
        key = self._require_key()
        return self._post("/getTaskResult", {"clientKey": key, "taskId": task.task_id})

    def get_balance(self) -> float:
        """Account balance in USD, shared across all four 2Captcha products
        behind this one key. Cheap way to confirm a key is live without
        spending anything (unlike createTask/random_fingerprint, both of
        which are billed)."""
        key = self._require_key()
        data = self._post("/getBalance", {"clientKey": key})
        if data.get("errorId"):
            raise TwoCaptchaError(f"getBalance failed: {data.get('errorCode')} {data.get('errorDescription')}")
        return float(data["balance"])

    def solve_and_wait(self, task: dict, poll_interval: float = 5.0, max_wait: float = 180.0) -> str:
        """Blocks (in small polls) until the task resolves, and returns the
        solution token. Raises TwoCaptchaError on failure/timeout."""
        created = self.create_task(task)
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            result = self.get_task_result(created)
            if result.get("errorId"):
                raise TwoCaptchaError(
                    f"getTaskResult failed: {result.get('errorCode')} {result.get('errorDescription')}"
                )
            if result.get("status") == "ready":
                solution = result.get("solution", {})
                token = solution.get("token") or solution.get("gRecaptchaResponse")
                if not token:
                    raise TwoCaptchaError(f"solved task carried no usable token: {solution!r}")
                return token
            time.sleep(poll_interval)
        raise TwoCaptchaError(f"solve timed out after {max_wait:.0f}s")

    # ----------------------------------------------------------------- #
    # Scraping Browser API — one live CDP connection per profile
    # ----------------------------------------------------------------- #
    def scraping_browser_connection_url(
        self, *, country: Optional[str] = None, profile_id: Optional[str] = None
    ) -> str:
        """Returns a `ws://...@cb.2captcha.com:9222` endpoint. Reuse
        `profile_id` across runs rather than minting a fresh one every
        time — profiles are capped per account and each allows exactly one
        live connection."""
        key = self._require_key()
        login = key  # 2Captcha's Scraping Browser auths the login on the key
        cc = f"-country-{country}" if country else ""
        pid = f"-pid-{profile_id}" if profile_id else ""
        return f"ws://{login}-zone-scraping_browser{cc}{pid}:{key}@cb.2captcha.com:9222"

    # ----------------------------------------------------------------- #
    # Fingerprint API
    # ----------------------------------------------------------------- #
    def random_fingerprint(self, *, tags: Optional[str] = None, country: Optional[str] = None) -> dict:
        # Unlike createTask/getTaskResult/getBalance above, this endpoint
        # takes its key as a URL query parameter (`?key=...`), not a JSON
        # body — and `requests` puts the FULL URL, query string included,
        # into the text of every HTTPError and connection error (CLAUDE.md
        # §8 names this exact endpoint). Redact before raising, and drop
        # the original exception from the chain (`from None`) so its own
        # unredacted text can't surface via "the above exception was the
        # direct cause of…".
        key = self._require_key()
        params = {"key": key, "format": "chromium"}
        if tags:
            params["tags"] = tags
        if country:
            params["country"] = country
        try:
            resp = requests.get(f"{self.api_base}/fingerprint/random", params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise TwoCaptchaError(f"fingerprint/random request failed: {redact_credentials(str(exc))}") from None
        return resp.json()
