#!/usr/bin/env python3
"""output_writer.py — the row model, JSON/CSV writer, dedupe, exit codes,
and run metadata. Carries (almost) no site knowledge: everything here is
part of the family's shared output CONTRACT — diverging from it needs a
written reason, not a per-site tweak. Ported near-verbatim from
skyscanner-scraper's output_writer.py (porting starts by copying the
family-shared modules from the newest sibling and diffing what changed on
purpose) — in turn ported from stockx-scraper's original
(commit 00b5570). The exit codes, STATUS_BY_EXIT map, and finish_run()
outcome-precedence logic are IDENTICAL to both prior family members on
purpose, including the fix from a 2026-09-15 audit that found `blocked` /
`remote_api_error` were being silently ignored whenever products were
present or --allow-empty was passed. Only the `Product` dataclass's
site-specific tail (below the family-common fields) differs per repo.
"""
from __future__ import annotations

import contextlib
import csv
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Exit codes — identical across playwright_scraper / selenium_scraper /
# puppeteer_scraper, and identical to every other 2scraper family repo. A
# caller (CI, a cron job, another program) must be able to tell these apart
# without parsing stdout.
# --------------------------------------------------------------------------- #
EXIT_OK = 0
EXIT_CRASH = 1
EXIT_BAD_USAGE = 2
EXIT_BLOCKED = 3
EXIT_ZERO_PRODUCTS = 4
EXIT_REMOTE_API_ERROR = 5   # the content was never obtained: a remote API
                            # error, a navigation failure, or a served page
                            # whose stated rows the parser lost
EXIT_NOT_OBTAINED = EXIT_REMOTE_API_ERROR
EXIT_PARTIAL = 6

STATUS_BY_EXIT = {
    EXIT_OK: "complete",
    EXIT_CRASH: "crashed",
    EXIT_BAD_USAGE: "bad_usage",
    EXIT_BLOCKED: "blocked",
    EXIT_ZERO_PRODUCTS: "empty",
    EXIT_REMOTE_API_ERROR: "remote_api_error",
    EXIT_PARTIAL: "partial",
}


# --------------------------------------------------------------------------- #
# Row model
# --------------------------------------------------------------------------- #
@dataclass
class Product:
    """Family-common fields first (same order in JSON and CSV across every
    2scraper repo — stockx-scraper, skyscanner-scraper, and this one);
    lidl.com-specific (grocery item) fields at the end.

    Unlike Skyscanner's itineraries, a Lidl catalog item genuinely has a
    stable identity: `sku` is the site's own product id / EAN when
    `lidl_parser.py` can extract one, falling back to a deterministic
    fingerprint of `product_url` (see `make_sku()`) only when it can't —
    never a random or run-scoped id, so `diff_runs.py` still sees the same
    `sku` for the same item across two different runs.

    `brand` carries the actual product brand printed on the item (a Lidl
    private label like "Simply Nature"/"Preferred Selection", or a
    national brand Lidl also stocks) — a real, meaningful field here,
    unlike skyscanner-scraper's repurposing of it for "operating airline".
    `category` is the grocery aisle/category Lidl itself assigns the item
    (e.g. "Dairy & Eggs", "Bakery"), read from the page's own breadcrumb
    or category-page context, not invented by this scraper.

    `price` / `currency` / `price_source` are the columns diff_runs.py and
    every cross-site tool key off, same as every other family member.
    `price_source` is `"embedded_json"` when the price came from the
    page's own hydration state (this app's framework's embedded state
    blob — see `lidl_parser.py`'s module docstring for what that actually
    is, once confirmed live) and `"dom"` when read off the rendered
    product/result card directly.

    US grocery pricing is commonly store/region-scoped (a zip code or
    selected store changes what a page shows) — `store_id`/`zip_code`
    record which one this row's price actually reflects, so two runs
    against different regions are never silently conflated by
    `diff_runs.py` as "the same item, price changed".

    `price` is what a shopper without the Lidl Plus app pays; a member-only
    price shown on the same tile goes to `lidl_plus_price` instead, and
    `original_price`/`discount_pct`/`is_weekly_deal` describe the public
    discount only.
    """

    # --- family-common ---
    sku: Optional[str]
    source: str
    category: Optional[str]
    title: Optional[str]
    brand: Optional[str]
    price: Optional[float]
    currency: Optional[str]
    price_source: Optional[str]
    product_url: Optional[str]
    image_url: Optional[str]
    scraped_at: str

    # --- lidl.com-specific (grocery item) ---
    unit_price: Optional[float] = None
    unit_size: Optional[str] = None
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    is_weekly_deal: Optional[bool] = None
    lidl_plus_price: Optional[float] = None
    deal_valid_from: Optional[str] = None
    deal_valid_until: Optional[str] = None
    store_id: Optional[str] = None
    zip_code: Optional[str] = None


PRODUCT_FIELD_NAMES: List[str] = [f.name for f in fields(Product)]


# --------------------------------------------------------------------------- #
# Dedupe / merge — "merge in page order, not arrival order": the caller
# passes pages already sorted by page number (or, for a concurrent run,
# sorted before calling this), never in whichever-finished-first order.
# --------------------------------------------------------------------------- #
def sku_key(p: Product) -> str:
    """The identity `merge_pages` dedupes on, and the same one an engine's
    scroll/pagination loop should track to decide "did this page add
    anything NEW" — never just "is this page non-empty" (a page repeating
    an already-seen item, e.g. past the real last batch of results, isn't
    empty but isn't new either)."""
    return p.sku or f"__no_sku__:{p.product_url}"


def merge_pages(pages: Sequence[Sequence[Product]]) -> List[Product]:
    seen: dict = {}
    order: List[str] = []
    for page in pages:
        for p in page:
            key = sku_key(p)
            if key not in seen:
                order.append(key)
            seen[key] = p  # last write for a given sku wins, in page order
    return [seen[k] for k in order]


# --------------------------------------------------------------------------- #
# Writers — atomic: a crash, a kill or a full disk halfway through a write
# must leave the PREVIOUS file intact, never a truncated one. The data goes
# to a temp file in the target's OWN directory (os.replace is only atomic
# within one filesystem), is flushed and fsynced, then renamed over the
# target. File modes follow the umask for a new file and are kept for an
# existing one — a temp file is created 0600, and leaving it that way would
# make every output owner-readable only.
# --------------------------------------------------------------------------- #
def _current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


@contextlib.contextmanager
def _atomic(path: str, *, newline: Optional[str] = None) -> Iterator:
    target = Path(path)
    directory = target.parent if str(target.parent) else Path(".")
    try:
        mode = target.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o666 & ~_current_umask()
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            yield f
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(target))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def write_json(products: Sequence[Product], out_path: str) -> None:
    with _atomic(out_path) as f:
        json.dump([asdict(p) for p in products], f, ensure_ascii=False, indent=2)


# A spreadsheet executes a cell that starts with one of these. Every string
# in a row was written by the site (titles, brands, categories), so in CSV
# — and only in CSV — such a cell gets a leading apostrophe. JSON keeps the
# site's exact bytes; the sidecar's `csv_cells_escaped` declares how many
# cells differ between the two outputs.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_cell(value) -> Tuple[object, bool]:
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value, True
    return value, False


def write_csv(products: Sequence[Product], out_path: str) -> int:
    """Write CSV atomically; returns how many cells were formula-escaped."""
    escaped = 0
    with _atomic(out_path, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRODUCT_FIELD_NAMES)
        writer.writeheader()  # written even for zero rows — a consumer reads
        for p in products:    # an empty table, not a zero-byte file.
            row = {}
            for key, value in asdict(p).items():
                row[key], changed = _csv_cell(value)
                escaped += changed
            writer.writerow(row)
    return escaped


def write_output(products: Sequence[Product], out_path: str, fmt: str) -> int:
    """Returns the number of CSV cells formula-escaped (always 0 for JSON)."""
    if fmt == "json":
        write_json(products, out_path)
        return 0
    elif fmt == "csv":
        return write_csv(products, out_path)
    else:
        raise ValueError(f"Unsupported format: {fmt!r} (expected 'json' or 'csv')")


# --------------------------------------------------------------------------- #
# Run metadata sidecar
# --------------------------------------------------------------------------- #
def meta_path_for(out_path: str) -> str:
    return f"{out_path}.meta.json"


def write_meta(
    out_path: str,
    *,
    status: str,
    stop_reason: str,
    engine: str,
    url: str,
    pages_requested: int,
    pages_completed: int,
    failed_pages: Optional[List[int]] = None,
    product_count: int,
    price_confirmed_pct: Optional[float] = None,
    started_at: float,
    finished_at: Optional[float] = None,
    extra: Optional[dict] = None,
) -> None:
    meta = {
        "status": status,
        "stop_reason": stop_reason,
        "engine": engine,
        "url": url,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "failed_pages": failed_pages or [],
        "product_count": product_count,
        "price_confirmed_pct": price_confirmed_pct,
        "started_at": started_at,
        "finished_at": finished_at or time.time(),
    }
    if extra:
        meta.update(extra)
    with _atomic(meta_path_for(out_path)) as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
# finish_run — the single place every engine calls to decide exit code,
# whether to write output at all, and whether to write a sidecar. Keeping
# this in one shared function is what stops the three engines' exit-code
# mapping from drifting apart. Structurally identical to stockx-scraper's
# post-audit-fix finish_run() (commit 00b5570) — see that file's comment
# for the full incident writeup this precedence order fixes.
# --------------------------------------------------------------------------- #
def finish_run(
    *,
    products: List[Product],
    out_path: str,
    fmt: str,
    engine: str,
    url: str,
    pages_requested: int,
    pages_completed: int,
    failed_pages: Optional[List[int]],
    blocked: bool,
    remote_api_error: bool,
    allow_empty: bool,
    started_at: float,
    price_confirmed_pct: Optional[float] = None,
    extra_meta: Optional[dict] = None,
    parser_failed: bool = False,
    stop_reason: Optional[str] = None,
) -> int:
    """Decide status/exit code, write output + sidecar (or neither), return
    the process exit code.

      - rows, nothing wrong                        -> 0 complete
      - rows, then a failure (remote error, block,
        failed rounds)                             -> 6 partial
      - no rows, blocked                           -> 3 blocked
      - no rows, content never obtained            -> 5 remote_api_error
      - no rows, the page STATED rows but the
        parser recognised none (`parser_failed`)   -> 5 failed
      - no rows, nothing says rows exist           -> 4 empty

    NEVER overwrites a previous good output with an empty one unless the
    caller passed --allow-empty, and writes no sidecar for a failed run —
    except a parser failure, whose sidecar is written (with
    `output_written: false`, the data file untouched) so a consumer can see
    that the page was served and our selectors lost its rows."""
    failed_pages = failed_pages or []
    zero_products = len(products) == 0

    # Outcome precedence — decided ONCE, independent of --allow-empty.
    # `--allow-empty` controls only whether a zero-product result gets
    # WRITTEN as a file (below); it never launders a failure into
    # "complete".
    if zero_products:
        if remote_api_error:
            status, exit_code = "remote_api_error", EXIT_REMOTE_API_ERROR
        elif blocked:
            status, exit_code = "blocked", EXIT_BLOCKED
        elif parser_failed:
            status, exit_code = "failed", EXIT_NOT_OBTAINED
        else:
            status, exit_code = "empty", EXIT_ZERO_PRODUCTS
    elif remote_api_error or blocked or failed_pages:
        status, exit_code = "partial", EXIT_PARTIAL
    else:
        status, exit_code = "complete", EXIT_OK

    if stop_reason is None:
        if zero_products or status == "complete":
            stop_reason = status
        elif remote_api_error:
            stop_reason = "remote_api_error"
        elif blocked:
            stop_reason = "blocked"
        else:
            stop_reason = "partial"

    meta_kwargs = dict(
        status=status, stop_reason=stop_reason, engine=engine, url=url,
        pages_requested=pages_requested, pages_completed=pages_completed,
        failed_pages=failed_pages, product_count=len(products),
        price_confirmed_pct=price_confirmed_pct, started_at=started_at,
    )

    if zero_products and not allow_empty:
        if parser_failed and not remote_api_error and not blocked:
            write_meta(out_path, **meta_kwargs, extra={"output_written": False, **(extra_meta or {})})
        return exit_code

    escaped = write_output(products, out_path, fmt)
    # Housekeeping first, the caller's extra over it: a collision must never
    # silently drop site data in favour of our own bookkeeping.
    write_meta(out_path, **meta_kwargs, extra={"csv_cells_escaped": escaped, **(extra_meta or {})})
    return exit_code
