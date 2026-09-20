#!/usr/bin/env python3
"""fingerprint_client.py — 2Captcha Fingerprint API fetch + application.
No site knowledge.

Two rules, both from the family's invariants (CLAUDE.md §8):

  - Never set a fingerprint over `--cdp-endpoint`. The Scraping Browser API
    brings its own device identity; stacking a second on top creates a
    contradiction (mismatched UA/TLS/JS fingerprint) rather than better
    cover. `apply_fingerprint()` refuses and warns instead of applying one.
  - The user agent comes from the BROWSER, never a literal string. A
    hardcoded UA drifts from whatever Chromium build is actually installed,
    and claiming an older Chrome than the JS engine/TLS handshake report is
    itself a mismatch a bot-detector can key on. This module only ever
    forwards what the Fingerprint API returned — it never invents one.
"""
from __future__ import annotations

import logging
from typing import Optional

from scraper_api_client import TwoCaptchaClient, TwoCaptchaError

log = logging.getLogger("fingerprint_client")


def fetch_fingerprint(
    client: TwoCaptchaClient, *, tags: Optional[str] = None, country: Optional[str] = None
) -> Optional[dict]:
    """Returns the raw Fingerprint API profile, or None on any failure — a
    fingerprint is a cover-quality improvement, never something a run
    should crash over. Caller logs/warns as it sees fit."""
    try:
        return client.random_fingerprint(tags=tags, country=country)
    except TwoCaptchaError as exc:
        log.warning("Fingerprint API request failed, continuing without one: %s", exc)
        return None


def user_agent_from(fingerprint: dict) -> Optional[str]:
    ua = fingerprint.get("userAgent")
    if isinstance(ua, dict):
        return ua.get("value")
    if isinstance(ua, str):
        return ua
    return None


# This module deliberately extracts and applies ONLY the user agent from a
# Fingerprint API profile — never a locale or timezone, even though the
# real API almost certainly carries signal for both somewhere in the
# response. CLAUDE.md §16/§17 names the exact failure mode this avoids:
# an older sibling repo shipped `f"en-{country}"` as a fabricated locale
# and never applied the API's own timezone field at all, and both went
# undetected for months because nothing exercises a credentialed path in
# an offline suite. This session could not get a confirmed field name for
# either from 2Captcha's own reference (the public docs page for this
# endpoint 404s, and a fetched marketing page's JSON sample is not a
# citable API contract) — inventing one now would repeat that exact
# mistake with a different guessed key. Add locale/timezone application
# here ONLY once a real API response (or 2Captcha support) confirms the
# field name; until then, omitting it is more honest than guessing it
# (CLAUDE.md §8: "never present a guess as a fact").


def refuse_if_cdp(cdp_endpoint: Optional[str]) -> bool:
    """Call before requesting/applying a fingerprint. Returns True (and the
    caller should skip fingerprinting) when a remote CDP endpoint is in
    play — the remote browser already has its own device identity."""
    if cdp_endpoint:
        log.warning(
            "Ignoring --fingerprint: a --cdp-endpoint session already carries "
            "its own device identity, and stacking a second is a contradiction, "
            "not better cover."
        )
        return True
    return False
