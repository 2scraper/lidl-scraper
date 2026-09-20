#!/usr/bin/env python3
"""puppeteer_scraper.py — pyppeteer engine, parity copy of
playwright_scraper.py (same flags, same exit codes and status semantics —
see output_writer.finish_run). Not the primary engine (Playwright is); kept
for parity, and because it — like Playwright, unlike Selenium — CAN open an
authenticated `ws://login:pass@host:port` CDP session, so it is the second
engine able to use the Scraping Browser API's own `Captcha.setAutoSolve`.

Named and shaped like stockx-scraper's / skyscanner-scraper's own
`puppeteer_scraper.py` (Python + pyppeteer, not a separate Node.js file) —
this repo follows that family convention for the same reason: one shared
output contract and one set of family modules across all three engines.

pyppeteer itself is effectively unmaintained (its own README points at
Playwright) — this file exists for parity/completeness, not as a
recommendation to prefer it.

Chromium binary: sourced from `PYPPETEER_EXECUTABLE_PATH` /
`PUPPETEER_EXECUTABLE_PATH` if set (handy for reusing an existing
Playwright/system Chromium instead of pyppeteer's own bundled download),
otherwise pyppeteer's own default.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from pyppeteer import connect as pyppeteer_connect
    from pyppeteer import launch as pyppeteer_launch
    from pyppeteer.errors import NetworkError, PageError, TimeoutError as PyppeteerTimeoutError
except ImportError as _IMPORT_ERROR:  # pragma: no cover — exercised by smoke_test's no-engine path
    pyppeteer_launch = None
    pyppeteer_connect = None
    NetworkError = PageError = PyppeteerTimeoutError = Exception
    _PYPPETEER_IMPORT_ERROR = _IMPORT_ERROR
else:
    _PYPPETEER_IMPORT_ERROR = None

import env_config
import lidl_parser as lp
from captcha_solver import detect_from_html, solve_when_blocked
from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, Product, finish_run, sku_key as _sku_key
from fingerprint_client import fetch_fingerprint, refuse_if_cdp, user_agent_from
from proxy_pool import Proxy, ProxyPool, ProxyParseError, is_proxy_dead_error, load_proxies, redact_credentials
from scraper_api_client import TwoCaptchaClient

ENGINE_NAME = "puppeteer"
NAV_TIMEOUT_MS = 30_000
READINESS_WAIT_S = 3.0

log = logging.getLogger("puppeteer_scraper")

_CHROMIUM_EXECUTABLE = os.environ.get("PYPPETEER_EXECUTABLE_PATH") or os.environ.get("PUPPETEER_EXECUTABLE_PATH")


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
    return ivalue


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="lidl.com grocery product scraper — pyppeteer (Puppeteer) engine",
        epilog="Credentials belong in .env / LIDL_PROXY / TWOCAPTCHA_KEY — never on this command line.",
    )
    p.add_argument("--url", default=None)
    p.add_argument("--query", default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--zip", dest="zip_code", default=None)
    p.add_argument("--store-id", default=None)
    p.add_argument("--sort", choices=lp.SORT_VALUES, default="relevance")
    p.add_argument("--max-results", type=_positive_int, default=30)
    p.add_argument("--max-scrolls", type=_positive_int, default=20)
    p.add_argument("--stall-rounds", type=_positive_int, default=4)
    p.add_argument("--scroll-delay", type=float, default=1.5)
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--out", default=None)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--retry-delay", type=float, default=3.0)
    p.add_argument("--proxy", default=None)
    p.add_argument("--proxy-file", default=None)
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int, default=3)
    p.add_argument("--twocaptcha-key", default=None)
    p.add_argument("--captcha-api", default=None, help="Override the 2Captcha API base URL (testing only)")
    p.add_argument("--solve-captcha", choices=["off", "when-blocked", "always"], default="when-blocked")
    p.add_argument("--min-score", type=float, default=0.3, help="Minimum acceptable reCAPTCHA v3 score (2Captcha's minScore task field)")
    p.add_argument("--fingerprint", action="store_true", help="Fetch and apply a 2Captcha Fingerprint API profile's user agent (ignored with --cdp-endpoint — see fingerprint_client.refuse_if_cdp)")
    p.add_argument("--fp-tags", default=None, help="Fingerprint API filter, e.g. 'Windows'")
    p.add_argument("--fp-country", default=None, help="Fingerprint API filter, e.g. 'us'")
    p.add_argument("--cdp-endpoint", default=None)
    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--dump-html", action="store_true")
    p.add_argument("--headless", dest="headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    return p


def _default_out(fmt: str) -> str:
    return f"lidl_results.{fmt}"


def _resolve_start_url(args: argparse.Namespace) -> Optional[str]:
    if args.url:
        return args.url
    if args.query or args.category:
        return lp.search_url(query=args.query, category=args.category, zip_code=args.zip_code, sort=args.sort)
    return None


def _dump_path(out_path: str) -> str:
    stem = Path(out_path).with_suffix("")
    return f"{stem}_debug.html"


async def _launch(*, headless: bool, proxy: Optional[Proxy], cdp_endpoint: Optional[str]):
    if cdp_endpoint:
        try:
            return await pyppeteer_connect(browserWSEndpoint=cdp_endpoint, defaultViewport=None)
        except Exception as exc:
            raise RuntimeError(f"CDP connection failed: {redact_credentials(str(exc))}") from None
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if proxy is not None:
        args.append(proxy.pyppeteer_launch_arg())
    kwargs = dict(headless=headless, args=args)
    if _CHROMIUM_EXECUTABLE:
        kwargs["executablePath"] = _CHROMIUM_EXECUTABLE
    return await pyppeteer_launch(**kwargs)


async def _authenticate_if_needed(page, proxy: Optional[Proxy]) -> None:
    if proxy is not None:
        auth = proxy.pyppeteer_auth_dict()
        if auth:
            await page.authenticate(auth)


async def _enable_scraping_browser_auto_solve(page) -> None:
    try:
        client = await page.target.createCDPSession()
        client.on("Captcha.detected", lambda *_: log.info("[Scraping Browser API] captcha detected"))
        client.on("Captcha.solveFinished", lambda *_: log.info("[Scraping Browser API] captcha solved"))
        client.on("Captcha.solveFailed", lambda *_: log.warning("[Scraping Browser API] captcha solve failed"))
        await client.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
    except Exception as exc:  # noqa: BLE001 — optional enhancement, never fatal
        log.warning("Captcha.setAutoSolve unavailable on this CDP session (continuing without it): %s", exc)


async def _maybe_solve_captcha(*, html: str, url: str, client: Optional[TwoCaptchaClient], policy: str, min_score: float = 0.3) -> Optional[dict]:
    if policy == "off" or client is None:
        return None
    result = solve_when_blocked(
        client=client, page_url=url, html=html, count_product_links=lp.count_result_cards,
        extra_markers=lp.BOT_CHALLENGE_MARKERS, min_score=min_score,
    )
    action = result.get("action")
    if action == "warning_no_key":
        log.warning("Captcha solving skipped: %s", result.get("detail"))
    elif action == "warning_solver_error":
        log.warning("Captcha solve failed: %s", result.get("detail"))
    elif action == "solved":
        log.info("Captcha solved via 2Captcha (%s).", result.get("captcha_type"))
    return result


async def scrape_search(
    *, args: argparse.Namespace, start_url: str,
    proxy_pool: Optional[ProxyPool], client: Optional[TwoCaptchaClient], autosolve: bool = False,
    user_agent: Optional[str] = None,
) -> Tuple[List[Product], bool, bool, int, bool]:
    """Returns (products, blocked, remote_api_error, scroll_rounds_done, scroll_error).

    `scroll_error` is True only when the scroll loop itself broke early on
    an exception — the caller turns that into EXIT_PARTIAL when products
    were already collected, instead of silently reporting "complete" on a
    run that actually stopped short because something failed mid-pagination."""
    blocked = False
    remote_api_error = False
    scroll_error = False

    proxy = proxy_pool.next() if proxy_pool else None
    log.info("Using proxy %s", proxy.masked() if proxy else "(no local proxy pool — direct connection, or a --cdp-endpoint session providing its own exit)")
    try:
        browser = await _launch(headless=args.headless, proxy=proxy, cdp_endpoint=args.cdp_endpoint)
    except RuntimeError as exc:
        log.error("CDP connection failed — treating as remote_api_error, not a crash: %s", exc)
        return [], False, True, 0, False
    page = await browser.newPage()
    if user_agent:
        await page.setUserAgent(user_agent)
    await _authenticate_if_needed(page, proxy)
    if autosolve:
        await _enable_scraping_browser_auto_solve(page)

    last_error = None
    status = None
    for attempt in range(args.retries + 1):
        try:
            response = await page.goto(start_url, {"waitUntil": "domcontentloaded", "timeout": NAV_TIMEOUT_MS})
            await asyncio.sleep(READINESS_WAIT_S)
            status = response.status if response is not None else None
            if proxy_pool is not None and proxy is not None:
                proxy_pool.report_success(proxy)
            last_error = None
            break
        except (NetworkError, PageError, PyppeteerTimeoutError, Exception) as exc:  # noqa: BLE001
            message = str(exc)
            last_error = message
            dead = is_proxy_dead_error(message)
            if proxy_pool is not None and proxy is not None and dead:
                proxy_pool.report_failure(proxy, dead=True)
                log.warning("Proxy reported dead: %s", message)
            else:
                log.warning("Navigation attempt %d/%d failed: %s", attempt + 1, args.retries + 1, message)
            if attempt < args.retries:
                await asyncio.sleep(args.retry_delay)

    if last_error is not None:
        await browser.close()
        log.error("Search page permanently failed to load: %s", last_error)
        return [], False, True, 0, False

    if status is not None and status >= 400:
        log.warning("Search page returned HTTP %d — treating as blocked, not empty.", status)
        blocked = True

    seen_skus: set = set()
    merged: List[Product] = []
    stall = 0
    previous_height: Optional[int] = None
    clicked_last_round = False
    rounds = 0

    for round_num in range(args.max_scrolls + 1):
        rounds = round_num
        html = await page.content()
        captcha_detected = detect_from_html(html, lp.BOT_CHALLENGE_MARKERS)
        cards_present = lp.count_result_cards(html) > 0
        if captcha_detected and not cards_present:
            blocked = True
        captcha_result = await _maybe_solve_captcha(html=html, url=start_url, client=client, policy=args.solve_captcha, min_score=args.min_score)
        if captcha_result and captcha_result.get("action") in ("warning_no_key", "warning_solver_error", "detected_unidentified_widget"):
            if lp.count_result_cards(html) == 0:
                blocked = True

        result = lp.safe_parse_search_results(
            html, zip_code=args.zip_code, store_id=args.store_id, max_results=args.max_results,
        )
        round_skus = {_sku_key(p) for p in result.products}
        new_skus = round_skus - seen_skus
        page_height = await page.evaluate("() => document.body.scrollHeight")
        if new_skus:
            for p in result.products:
                if _sku_key(p) in new_skus:
                    merged.append(p)
            seen_skus |= new_skus
            stall = 0
        else:
            if previous_height == page_height:
                stall += 1
            else:
                stall = 0
        previous_height = page_height

        if result.products:
            blocked = False

        if len(merged) >= args.max_results:
            merged = merged[: args.max_results]
            break
        if round_num >= args.max_scrolls:
            break

        try:
            load_more = await page.querySelector("button.s-load-more__button")
            if not new_skus and stall > 0 and not clicked_last_round and load_more is not None:
                await page.evaluate("(button) => button.click()", load_more)
                clicked_last_round = True
                stall = 0
            else:
                if stall >= args.stall_rounds:
                    break
                await page.evaluate(
                    "() => window.scrollBy(0, Math.max(Math.floor(window.innerHeight * 0.8), 600))"
                )
                clicked_last_round = False
        except Exception as exc:  # noqa: BLE001 — a scroll failure ends the loop, not the run
            log.warning("Scroll failed, stopping pagination early: %s", exc)
            scroll_error = True
            break
        await asyncio.sleep(args.scroll_delay)

    final_html = await page.content()
    total_available = lp.total_result_count(final_html)
    expected = min(total_available, args.max_results) if total_available is not None else None
    if expected is not None and len(merged) < expected:
        log.warning(
            "Incomplete virtualized grid: Lidl reports %d products, requested %d, collected %d.",
            total_available, args.max_results, len(merged),
        )
        scroll_error = True

    if args.dump_html:
        Path(_dump_path(args.out)).write_text(final_html, encoding="utf-8")

    await browser.close()
    return merged, blocked, remote_api_error, rounds, scroll_error


async def run(args: argparse.Namespace) -> int:
    started_at = time.time()
    start_url = _resolve_start_url(args)
    if not start_url:
        print("Error: provide --url, --query, or --category", file=sys.stderr)
        return EXIT_BAD_USAGE
    if args.format not in ("json", "csv"):
        print(f"Error: unsupported --format {args.format!r}", file=sys.stderr)
        return EXIT_BAD_USAGE
    if pyppeteer_launch is None:
        print(f"Error: pyppeteer is not installed ({_PYPPETEER_IMPORT_ERROR}). "
              f"pip install -r requirements-puppeteer.txt", file=sys.stderr)
        return EXIT_CRASH
    args.out = args.out or _default_out(args.format)

    try:
        proxies = load_proxies(args.proxy, args.proxy_file)
    except ProxyParseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_BAD_USAGE
    proxy_pool = ProxyPool(proxies, shuffle=args.proxy_shuffle, block_retries=args.proxy_block_retries) if proxies else None
    if args.cdp_endpoint and proxy_pool is not None:
        log.warning("Ignoring --proxy: a --cdp-endpoint session already carries its own exit IP.")
        proxy_pool = None

    client = None
    if args.twocaptcha_key and (args.solve_captcha != "off" or args.fingerprint):
        client = TwoCaptchaClient(args.twocaptcha_key, api_base=args.captcha_api)
    autosolve = bool(args.cdp_endpoint) and args.solve_captcha != "off"

    user_agent = None
    cdp_refused_fingerprint = refuse_if_cdp(args.cdp_endpoint)
    if args.fingerprint and not cdp_refused_fingerprint:
        if client is None:
            log.warning("--fingerprint requested but no --twocaptcha-key/TWOCAPTCHA_KEY set — continuing without one.")
        else:
            profile = fetch_fingerprint(client, tags=args.fp_tags, country=args.fp_country)
            if profile:
                user_agent = user_agent_from(profile)

    try:
        merged, blocked, remote_api_error, rounds, scroll_error = await scrape_search(
            args=args, start_url=start_url, proxy_pool=proxy_pool, client=client, autosolve=autosolve,
            user_agent=user_agent,
        )
        price_confirmed_pct = (sum(1 for p in merged if p.price is not None) / len(merged)) if merged else None
    except Exception:
        log.exception("Unhandled error — this is a crash, not a normal blocked/empty run")
        return EXIT_CRASH

    completed_rounds = rounds + 1
    failed_pages = [completed_rounds + 1] if scroll_error else None

    return finish_run(
        products=merged, out_path=args.out, fmt=args.format, engine=ENGINE_NAME, url=start_url,
        pages_requested=args.max_scrolls, pages_completed=completed_rounds, failed_pages=failed_pages,
        blocked=blocked, remote_api_error=remote_api_error, allow_empty=args.allow_empty,
        started_at=started_at, price_confirmed_pct=price_confirmed_pct,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_arg_parser().parse_args()
    args = env_config.apply_env(args)
    try:
        return asyncio.get_event_loop().run_until_complete(run(args))
    except KeyboardInterrupt:
        return EXIT_CRASH


if __name__ == "__main__":
    sys.exit(main())
