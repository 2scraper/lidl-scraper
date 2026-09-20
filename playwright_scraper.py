#!/usr/bin/env python3
"""playwright_scraper.py — Playwright engine for the lidl-scraper family
member. Playwright is the primary engine (see selenium_scraper.py /
puppeteer_scraper.py for parity copies — all three must agree on exit
codes, run status and whether a run crashes or spends money — CLAUDE.md
§4).

**Local-first, same principle as every other family member**: this
launches an ordinary local headless Chromium by default and does NOT
require a 2Captcha Scraping Browser (`--cdp-endpoint`) session to run at
all. Unlike skyscanner-scraper (robots.txt-restricted) this repo's own
build environment COULD fetch lidl.com's robots.txt and homepage — both
came back clean, with no bot-challenge marker anywhere — but no engine
here has actually been run against the live site yet (see TESTING.md).
`--cdp-endpoint` and `--proxy` remain available as opt-in power options
for volume/a specific exit country/a consistent device identity, exactly
as the rest of the family frames them — not because local-first is known
to fail here.

Example:
    python3 playwright_scraper.py --query "whole milk" --zip 11803 --format json --out results.json
    python3 playwright_scraper.py --url "https://www.lidl.com/search/products/milk" --max-results 20

**Pagination model — an assumption, not a confirmed fact** (see
lidl_parser.py's module docstring for the same caveat applied to
selectors/embedded-JSON shape): this engine scrolls and re-parses the
accumulated page, deduping by `sku` (output_writer.sku_key), the same
lazy-load-on-scroll model skyscanner-scraper uses for ITS site — chosen
here because it degrades safely even if wrong (a classically-paginated
lidl.com page would just stall immediately with whatever the first page
had, not crash or hang), not because lidl.com's real UI has been
confirmed to work this way.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
except ImportError as _IMPORT_ERROR:  # pragma: no cover — exercised by smoke_test's no-engine path
    Browser = BrowserContext = Page = None
    async_playwright = None
    _PLAYWRIGHT_IMPORT_ERROR = _IMPORT_ERROR
else:
    _PLAYWRIGHT_IMPORT_ERROR = None

import env_config
import lidl_parser as lp
from captcha_solver import detect_from_html, solve_when_blocked
from fingerprint_client import fetch_fingerprint, refuse_if_cdp, user_agent_from
from output_writer import EXIT_BAD_USAGE, EXIT_CRASH, Product, finish_run, sku_key as _sku_key
from proxy_pool import Proxy, ProxyPool, ProxyParseError, is_proxy_dead_error, load_proxies, redact_credentials
from scraper_api_client import TwoCaptchaClient

ENGINE_NAME = "playwright"

# --- the handful of engine constants that vary per site (CLAUDE.md §5) ---
NAV_TIMEOUT_MS = 30_000
READINESS_WAIT_MS = 3_000  # TODO: verify live — lidl.com's homepage rendered client-side
                            # content (see lidl_parser.py docstring); assumed client-rendered
                            # here too pending a real capture.
MIN_CARD_MATCHES = lp.MIN_CARD_MATCHES

log = logging.getLogger("playwright_scraper")


def _positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
    return ivalue


def _nonnegative_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {value!r})")
    return ivalue


def _nonnegative_float(value: str) -> float:
    fvalue = float(value)
    if fvalue < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {value!r})")
    return fvalue


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="lidl.com grocery product scraper — Playwright engine",
        epilog="Credentials belong in .env / LIDL_PROXY / TWOCAPTCHA_KEY — never on this command line.",
    )
    p.add_argument("--url", default=None, help="Full lidl.com search/specials URL (or set LIDL_URL) — overrides --query/--category")
    p.add_argument("--query", default=None, help="Product search term, e.g. 'whole milk'")
    p.add_argument("--category", default=None, help="A /specials?category=<hex-id> id (see README — these ids are opaque and must be found by browsing, not guessed)")
    p.add_argument("--zip", dest="zip_code", default=None, help="US zip code for store/region pricing (mechanism unconfirmed — see lidl_parser.py docstring)")
    p.add_argument("--store-id", default=None, help="Recorded on output rows only — no confirmed way yet to select a store by id directly (see README)")
    p.add_argument("--sort", choices=lp.SORT_VALUES, default="relevance")
    p.add_argument("--max-results", type=_positive_int, default=30, help="Cap on number of products scraped")
    p.add_argument("--max-scrolls", type=_positive_int, default=20, help="Hard cap on scroll rounds, independent of --stall-rounds")
    p.add_argument("--stall-rounds", type=_positive_int, default=4, help="Stop after this many consecutive scrolls add no new product")
    p.add_argument("--scroll-delay", type=_nonnegative_float, default=1.5, help="Delay between scroll rounds, seconds")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--out", default=None, help="Output path (default: lidl_results.<format>)")
    p.add_argument("--retries", type=_nonnegative_int, default=2, help="Retries on initial navigation failure")
    p.add_argument("--retry-delay", type=_nonnegative_float, default=3.0)
    p.add_argument("--proxy", default=None, help="A single proxy, e.g. http://login:pass@host:port (or set LIDL_PROXY)")
    p.add_argument("--proxy-file", default=None, help="One proxy per line, same formats as --proxy")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int, default=3)
    p.add_argument("--twocaptcha-key", default=None, help="(or set TWOCAPTCHA_KEY)")
    p.add_argument("--captcha-api", default=None, help="Override the 2Captcha API base URL (testing only)")
    p.add_argument("--solve-captcha", choices=["off", "when-blocked", "always"], default="when-blocked")
    p.add_argument("--min-score", type=float, default=0.3, help="Minimum acceptable reCAPTCHA v3 score (2Captcha's minScore task field)")
    p.add_argument("--cdp-endpoint", default=None, help="Connect to a remote CDP session (e.g. the 2Captcha Scraping Browser API) instead of launching locally (or set LIDL_CDP_ENDPOINT) — opt-in, not required for a normal run")
    p.add_argument("--fingerprint", action="store_true", help="Fetch and apply a 2Captcha Fingerprint API profile (ignored with --cdp-endpoint — see fingerprint_client.refuse_if_cdp)")
    p.add_argument("--fp-tags", default=None, help="Fingerprint API filter, e.g. 'Windows,Chrome'")
    p.add_argument("--fp-country", default=None, help="Fingerprint API filter, e.g. 'us'")
    p.add_argument("--allow-empty", action="store_true", help="Write output even if zero products were found")
    p.add_argument("--dump-html", action="store_true", help="Save the final accumulated page HTML next to --out, on success too")
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


async def _new_context(browser: Browser, proxy: Optional[Proxy], user_agent: Optional[str]) -> BrowserContext:
    kwargs = {}
    if proxy is not None:
        kwargs["proxy"] = proxy.playwright_proxy_dict()
    if user_agent:
        kwargs["user_agent"] = user_agent
    return await browser.new_context(**kwargs)


async def _enable_scraping_browser_auto_solve(context: BrowserContext, page: Page) -> None:
    """Only meaningful over --cdp-endpoint — see the identical helper in
    stockx-scraper's / skyscanner-scraper's playwright_scraper.py."""
    try:
        session = await context.new_cdp_session(page)
        session.on("Captcha.detected", lambda *_: log.info("[Scraping Browser API] captcha detected"))
        session.on("Captcha.solveFinished", lambda *_: log.info("[Scraping Browser API] captcha solved"))
        session.on("Captcha.solveFailed", lambda *_: log.warning("[Scraping Browser API] captcha solve failed"))
        await session.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
    except Exception as exc:  # noqa: BLE001 — optional enhancement, never fatal
        log.warning("Captcha.setAutoSolve unavailable on this CDP session (continuing without it): %s", exc)


async def _maybe_solve_captcha(
    *, html: str, url: str, client: Optional[TwoCaptchaClient], policy: str, min_score: float = 0.3,
) -> Optional[dict]:
    if policy == "off" or client is None:
        return None
    result = solve_when_blocked(
        client=client, page_url=url, html=html, count_product_links=lp.count_result_cards,
        extra_markers=lp.BOT_CHALLENGE_MARKERS, min_score=min_score,
    )
    action = result.get("action")
    if action == "no_captcha_detected":
        pass
    elif action == "skipped_products_present":
        log.info("Captcha widget present but results already rendered — not solving.")
    elif action == "warning_no_key":
        log.warning("Captcha solving skipped: %s", result.get("detail"))
    elif action == "warning_solver_error":
        log.warning("Captcha solve failed: %s", result.get("detail"))
    elif action == "solved":
        log.info("Captcha solved via 2Captcha (%s).", result.get("captcha_type"))
    elif action == "detected_unidentified_widget":
        log.warning("A captcha-like marker was detected but no known widget/sitekey could be extracted.")
    return result


async def _connect_over_cdp(pw, cdp_endpoint: str):
    """See stockx-scraper's / skyscanner-scraper's playwright_scraper.py
    for why this wraps the connection error rather than letting it
    propagate: connect_over_cdp repeats a failed endpoint's login:password
    in its own message and "Call log" several times over."""
    try:
        return await pw.chromium.connect_over_cdp(cdp_endpoint)
    except Exception as exc:
        raise RuntimeError(f"CDP connection failed: {redact_credentials(str(exc))}") from None


async def scrape_search(
    *, args: argparse.Namespace, start_url: str, browser: Browser,
    proxy_pool: Optional[ProxyPool], client: Optional[TwoCaptchaClient],
    autosolve: bool, user_agent: Optional[str],
) -> tuple:
    """Returns (products, blocked, remote_api_error, scroll_rounds_done, scroll_error).

    `scroll_error` is True only when the scroll loop itself broke early on
    an exception (not on reaching --max-results/--stall-rounds/--max-scrolls
    normally) — the caller turns that into EXIT_PARTIAL when products were
    already collected, instead of silently reporting "complete" on a run
    that actually stopped short because something failed mid-pagination."""
    blocked = False
    remote_api_error = False
    scroll_error = False

    proxy = proxy_pool.next() if proxy_pool else None
    log.info("Using proxy %s", proxy.masked() if proxy else "(no local proxy pool — direct connection, or a --cdp-endpoint session providing its own exit)")
    context = await _new_context(browser, proxy, user_agent)
    page = await context.new_page()
    if autosolve:
        await _enable_scraping_browser_auto_solve(context, page)

    last_error = None
    status = None
    for attempt in range(args.retries + 1):
        try:
            response = await page.goto(start_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await page.wait_for_timeout(READINESS_WAIT_MS)
            status = response.status if response is not None else None
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001 — every remote call must be bounded and reported
            last_error = str(exc)
            log.warning("Navigation attempt %d/%d failed: %s", attempt + 1, args.retries + 1, last_error)
            if attempt < args.retries:
                await asyncio.sleep(args.retry_delay)

    if last_error is not None:
        await context.close()
        log.error("Search page permanently failed to load: %s", last_error)
        return [], False, True, 0, False

    if status is not None and status >= 400:
        # A 404 here (unlike this repo's own build-time fetches — see
        # lidl_parser.py module docstring) is a REAL browser navigation
        # against a REAL client-side app: if the SPA router 404s under a
        # real browser too, that's worth treating as blocked/failed rather
        # than silently "empty", same family precedent as skyscanner's
        # >=400 handling.
        log.warning("Search page returned HTTP %d — treating as blocked, not empty.", status)
        blocked = True
        if proxy_pool is not None and proxy is not None and status in (403, 429):
            proxy_pool.report_failure(proxy, dead=True)
    elif status is not None and proxy_pool is not None and proxy is not None:
        proxy_pool.report_success(proxy)

    seen_skus: set = set()
    merged: List[Product] = []
    stall = 0
    rounds = 0

    for round_num in range(args.max_scrolls + 1):
        rounds = round_num
        html = await page.content()
        if detect_from_html(html, lp.BOT_CHALLENGE_MARKERS):
            blocked = True
        captcha_result = await _maybe_solve_captcha(html=html, url=start_url, client=client, policy=args.solve_captcha, min_score=args.min_score)
        if captcha_result and captcha_result.get("action") in ("warning_no_key", "warning_solver_error", "detected_unidentified_widget"):
            if lp.count_result_cards(html) == 0:
                blocked = True

        result = lp.safe_parse_search_results(
            html, zip_code=args.zip_code, store_id=args.store_id, max_results=args.max_results,
        )
        if result.source_used == "none" and round_num == 0 and not blocked:
            log.warning(
                "No products recognised on the first render — either this search "
                "genuinely has no results, or lidl_parser.py's selectors need "
                "updating for the current lidl.com markup (see its module "
                "docstring). Re-run with --dump-html to inspect the captured page."
            )

        round_skus = {_sku_key(p) for p in result.products}
        new_skus = round_skus - seen_skus
        if new_skus:
            for p in result.products:
                if _sku_key(p) in new_skus:
                    merged.append(p)
            seen_skus |= new_skus
            stall = 0
        else:
            stall += 1

        if len(merged) >= args.max_results:
            merged = merged[: args.max_results]
            break
        if stall >= args.stall_rounds:
            break
        if round_num >= args.max_scrolls:
            break

        try:
            await page.mouse.wheel(0, 4000)
        except Exception as exc:  # noqa: BLE001 — a scroll failure ends the loop, not the run
            log.warning("Scroll failed, stopping pagination early: %s", exc)
            scroll_error = True
            break
        await asyncio.sleep(args.scroll_delay)

    if args.dump_html:
        Path(_dump_path(args.out)).write_text(await page.content(), encoding="utf-8")

    await context.close()
    return merged, blocked, remote_api_error, rounds, scroll_error


async def run(args: argparse.Namespace) -> int:
    started_at = time.time()
    if async_playwright is None:
        print(f"Error: playwright is not installed ({_PLAYWRIGHT_IMPORT_ERROR}). "
              f"pip install -r requirements-playwright.txt && playwright install chromium", file=sys.stderr)
        return EXIT_CRASH
    start_url = _resolve_start_url(args)
    if not start_url:
        print("Error: provide --url, --query, or --category", file=sys.stderr)
        return EXIT_BAD_USAGE
    if args.format not in ("json", "csv"):
        print(f"Error: unsupported --format {args.format!r}", file=sys.stderr)
        return EXIT_BAD_USAGE
    args.out = args.out or _default_out(args.format)

    try:
        proxies = load_proxies(args.proxy, args.proxy_file)
    except ProxyParseError as exc:
        # A malformed --proxy / --proxy-file line is a USAGE mistake, not a
        # crash — see skyscanner-scraper's playwright_scraper.py for the
        # full incident writeup this guard fixes.
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_BAD_USAGE
    proxy_pool = ProxyPool(proxies, shuffle=args.proxy_shuffle, block_retries=args.proxy_block_retries) if proxies else None

    client = None
    if args.twocaptcha_key and (args.solve_captcha != "off" or args.fingerprint):
        client = TwoCaptchaClient(args.twocaptcha_key, api_base=args.captcha_api)

    user_agent = None
    cdp_refused_fingerprint = refuse_if_cdp(args.cdp_endpoint)
    if args.fingerprint and not cdp_refused_fingerprint:
        if client is None:
            log.warning("--fingerprint requested but no --twocaptcha-key/TWOCAPTCHA_KEY set — continuing without one.")
        else:
            profile = fetch_fingerprint(client, tags=args.fp_tags, country=args.fp_country)
            if profile:
                user_agent = user_agent_from(profile)

    cdp_connect_failed = False
    try:
        async with async_playwright() as pw:
            if args.cdp_endpoint:
                if args.proxy or args.proxy_file:
                    log.warning("Ignoring --proxy: a --cdp-endpoint session already carries its own exit IP.")
                    proxy_pool = None
                try:
                    browser = await _connect_over_cdp(pw, args.cdp_endpoint)
                except RuntimeError as exc:
                    # A broken/misconfigured remote CDP session is a
                    # remote-API failure, not a bug in this scraper.
                    log.error("CDP connection failed — treating as remote_api_error, not a crash: %s", exc)
                    cdp_connect_failed = True
                    browser = None
            else:
                browser = await pw.chromium.launch(headless=args.headless)

            if cdp_connect_failed:
                merged, blocked, remote_api_error, rounds, scroll_error = [], False, True, 0, False
            else:
                autosolve = bool(args.cdp_endpoint) and args.solve_captcha != "off"
                merged, blocked, remote_api_error, rounds, scroll_error = await scrape_search(
                    args=args, start_url=start_url, browser=browser, proxy_pool=proxy_pool,
                    client=client, autosolve=autosolve, user_agent=user_agent,
                )
                await browser.close()
    except Exception:
        log.exception("Unhandled error — this is a crash, not a normal blocked/empty run")
        return EXIT_CRASH

    price_confirmed_pct = (sum(1 for p in merged if p.price is not None) / len(merged)) if merged else None

    # See skyscanner-scraper's playwright_scraper.py for the full incident
    # writeup on why `rounds + 1` (not the raw 0-indexed `rounds`) is what
    # gets reported as pages_completed here.
    completed_rounds = rounds + 1
    failed_pages = [completed_rounds + 1] if scroll_error else None

    return finish_run(
        products=merged,
        out_path=args.out,
        fmt=args.format,
        engine=ENGINE_NAME,
        url=start_url,
        pages_requested=args.max_scrolls,
        pages_completed=completed_rounds,
        failed_pages=failed_pages,
        blocked=blocked,
        remote_api_error=remote_api_error,
        allow_empty=args.allow_empty,
        started_at=started_at,
        price_confirmed_pct=price_confirmed_pct,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = build_arg_parser()
    args = parser.parse_args()
    args = env_config.apply_env(args)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return EXIT_CRASH


if __name__ == "__main__":
    sys.exit(main())
