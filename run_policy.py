#!/usr/bin/env python3
"""run_policy.py — small pieces of run policy the three engines must agree
on, kept in ONE place so they cannot drift apart:

  - the argparse validators for every numeric flag (a negative --retries
    used to be accepted by two engines of three and silently skip
    navigation; a negative --scroll-delay crashed one of them mid-run);
  - which start URL a run uses when a LIDL_URL from the environment / .env
    meets a --query/--category the caller actually typed;
  - why a run stopped, decided from what the page itself states;
  - which store a run prices against (`--store-id`/`--zip`).

No driver code and no JavaScript lives here.
"""
from __future__ import annotations

import argparse
import logging
from typing import Optional, Tuple

import lidl_parser as lp
import lidl_stores

log = logging.getLogger("run_policy")


# --------------------------------------------------------------------------- #
# argparse validators — argparse turns ArgumentTypeError into "error:
# argument --flag: ..." and exit 2, which names the flag.
# --------------------------------------------------------------------------- #
def positive_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer (got {value!r})")
    return ivalue


def nonnegative_int(value: str) -> int:
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0 (got {value!r})")
    return ivalue


def nonnegative_float(value: str) -> float:
    fvalue = float(value)
    if fvalue != fvalue or fvalue < 0:  # NaN, or negative
        raise argparse.ArgumentTypeError(f"must be a number >= 0 (got {value!r})")
    return fvalue


def unit_float(value: str) -> float:
    fvalue = float(value)
    if not 0.0 <= fvalue <= 1.0:
        raise argparse.ArgumentTypeError(f"must be between 0 and 1 (got {value!r})")
    return fvalue


# --------------------------------------------------------------------------- #
# Start URL
# --------------------------------------------------------------------------- #
class UsageError(Exception):
    """A combination of flags that cannot both be honoured."""


def resolve_start_url(args: argparse.Namespace) -> Optional[str]:
    """Pick the start URL.

    `env_config.apply_env` records `_url_source` when IT filled `--url`
    from LIDL_URL; a `--url` the caller typed carries no such attribute.
    So:

      - a typed --query/--category beats an environment/.env LIDL_URL (the
        variable is a convenience default, not an instruction to ignore
        what was typed) — logged, never silent;
      - a typed --url together with a typed --query/--category is refused:
        one of the two would be ignored, and guessing which is what made a
        run scrape a different search than the one asked for.
    """
    builders = bool(args.query or args.category)
    url_from_env = getattr(args, "_url_source", None)
    if args.url and builders:
        if url_from_env:
            log.info(
                "Ignoring LIDL_URL from %s: --query/--category was given on the command line.",
                url_from_env,
            )
            args.url = None
        else:
            raise UsageError("--url cannot be combined with --query/--category; pass one or the other")
    if args.url:
        return args.url
    if builders:
        return lp.search_url(query=args.query, category=args.category, sort=args.sort)
    return None


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def resolve_store(args: argparse.Namespace) -> Optional[lidl_stores.Store]:
    """Look up the store `--store-id`/`--zip` name, and from then on record
    the store actually used on every row: `args.store_id` becomes its full
    number and `args.zip_code` its 5-digit zip.

    Raises UsageError for a store that does not exist, and
    lidl_stores.StoreApiError when the site or its store API cannot be read
    — the engines report that as a remote failure (exit 5), never as a run
    priced against no store at all."""
    try:
        store = lidl_stores.resolve_store(store_id=args.store_id, zip_code=args.zip_code)
    except lidl_stores.StoreLookupError as exc:
        raise UsageError(str(exc)) from None
    if store is not None:
        log.info("Pricing against Lidl store %s", store.describe())
        args.store_id = store.object_number
        args.zip_code = store.zip5
    return store


# --------------------------------------------------------------------------- #
# Scroll budget
# --------------------------------------------------------------------------- #
DEFAULT_MAX_SCROLLS = 20


def default_max_scrolls(max_results: int) -> int:
    """Live, 2026-10-18: 60 products took 11-14 rounds and 200 took 38, so
    a fixed 20 stopped a 200-product aisle at 96. One round per four
    products plus slack, never below the old 20."""
    return max(DEFAULT_MAX_SCROLLS, max_results // 4 + 10)


def apply_scroll_default(args: argparse.Namespace) -> None:
    if args.max_scrolls is None:
        args.max_scrolls = default_max_scrolls(args.max_results)


# --------------------------------------------------------------------------- #
# HTTP status of the first navigation
# --------------------------------------------------------------------------- #
def status_means_blocked(status: Optional[int], html: str) -> bool:
    """A status >= 400 counts as blocked, except Lidl's own no-match page,
    which it serves as a 404 for some stores (see lidl_stores.py)."""
    return status is not None and status >= 400 and not lp.is_no_match_page(html)


# --------------------------------------------------------------------------- #
# End of run
# --------------------------------------------------------------------------- #
# Values `stop_reason` can take in the sidecar.
STOP_MAX_RESULTS = "max_results"
STOP_MAX_SCROLLS = "max_scrolls"
STOP_STALLED = "stalled"
STOP_SCROLL_FAILED = "scroll_failed"
STOP_INCOMPLETE_GRID = "incomplete_grid"
STOP_PARSER_FOUND_NOTHING = "parser_found_nothing"
STOP_NAVIGATION_FAILED = "navigation_failed"
STOP_CDP_CONNECT_FAILED = "cdp_connect_failed"
STOP_BLOCKED = "blocked"
STOP_NO_MATCH = "no_match"


def end_of_run(
    *, final_html: str, collected: int, max_results: int, blocked: bool,
    loop_reason: str, scroll_failed: bool,
) -> Tuple[str, bool, bool]:
    """Decide (stop_reason, shortfall, parser_failed) once the scroll loop
    has ended.

    Lidl prints its own catalogue counter ("142 Products"). When that
    counter states products and the parser collected none, the page was
    SERVED and the rows were lost by our selectors — a parser failure, never
    an empty catalogue. When it states more than were collected (up to
    --max-results) the run is short and must not read as complete.
    """
    total = lp.total_result_count(final_html)
    if blocked:
        # finish_run reports blocked with no rows as 3, with rows as partial.
        return STOP_BLOCKED, False, False
    if lp.is_no_match_page(final_html):
        # The parser drops the unrelated fallback grid this page carries, so
        # its own "N Products" counter must not read as lost rows.
        log.warning("Lidl found no match for this search — the products it shows instead are not results.")
        return STOP_NO_MATCH, False, False
    if total is not None and total > 0 and collected == 0:
        log.error(
            "Lidl's own counter states %d products but the parser recognised none — "
            "treating as a parser failure, not an empty search. Re-run with --dump-html "
            "and compare the markup against lidl_parser.py.", total,
        )
        return STOP_PARSER_FOUND_NOTHING, False, True
    if scroll_failed:
        return STOP_SCROLL_FAILED, True, False
    expected = min(total, max_results) if total is not None else None
    if expected is not None and collected < expected:
        log.warning(
            "Incomplete virtualized grid: Lidl reports %d products, requested %d, collected %d.",
            total, max_results, collected,
        )
        return STOP_INCOMPLETE_GRID, True, False
    return loop_reason, False, False


def warn_if_nothing_found(*, collected: int, blocked: bool, stop_reason: str, logger) -> None:
    """Said once at the end of an empty run, not on a first render that has
    not filled its lazy grid yet (an aisle page always looked empty then)."""
    if collected or blocked or stop_reason in (STOP_NO_MATCH, STOP_PARSER_FOUND_NOTHING):
        return
    logger.warning(
        "No products recognised on this page — either it lists none (a /c/…/s… "
        "landing page often holds no product grid; aisle listings are /h/…/h… "
        "paths), or lidl_parser.py's selectors need updating for the current "
        "lidl.com markup. Re-run with --dump-html to inspect the captured page."
    )


# --------------------------------------------------------------------------- #
# Captcha step — one implementation for all three engines.
# --------------------------------------------------------------------------- #
# None of the local engines injects a solved token into the page, so none of
# them may buy one. The Scraping Browser API (--cdp-endpoint) solves inside
# the remote browser with its own Captcha.setAutoSolve, which is unaffected.
LOCAL_ENGINES_INJECT_TOKEN = False

# Outcomes after which a page with no product cards counts as blocked.
UNSOLVED_ACTIONS = (
    "warning_no_key", "warning_solver_error", "detected_unidentified_widget",
    "detected_not_injectable", "budget_exhausted",
)


def captcha_step(*, html: str, url: str, client, policy: str, min_score: float, budget, logger) -> Optional[dict]:
    """Run the when-blocked solve policy once for one scroll round. `budget`
    is the page's SolveBudget, created once per page by the engine."""
    from captcha_solver import solve_when_blocked  # local: keeps this module light to import

    if policy == "off" or client is None:
        return None
    result = solve_when_blocked(
        client=client, page_url=url, html=html, count_product_links=lp.count_result_cards,
        budget=budget, can_inject=LOCAL_ENGINES_INJECT_TOKEN,
        extra_markers=lp.BOT_CHALLENGE_MARKERS, min_score=min_score,
    )
    action = result.get("action")
    if not budget.first_time(action):
        return result
    if action == "skipped_products_present":
        logger.info("Captcha widget present but results already rendered — not solving.")
    elif action == "warning_no_key":
        logger.warning("Captcha solving skipped: %s", result.get("detail"))
    elif action == "warning_solver_error":
        logger.warning("Captcha solve failed: %s", result.get("detail"))
    elif action == "solved":
        logger.info("Captcha solved via 2Captcha (%s).", result.get("captcha_type"))
    elif action == "detected_unidentified_widget":
        logger.warning("A captcha-like marker was detected but no known widget/sitekey could be extracted.")
    elif action == "detected_not_injectable":
        logger.warning(
            "A %s challenge is gating this page. This engine does not inject a solved token "
            "into a locally launched browser, so no solve was bought (it could not clear the "
            "page). Use --cdp-endpoint (the Scraping Browser API solves inside its own browser) "
            "or a different exit.", result.get("captcha_type"),
        )
    elif action == "budget_exhausted":
        logger.warning("Captcha solve allowance for this page is spent — not buying another.")
    return result
