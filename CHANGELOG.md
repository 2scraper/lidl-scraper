# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) as closely as a CLI toolkit can manage. A patch
release means "fixes", not that every flag and default is frozen — a
behaviour-changing default gets called out explicitly in its entry below
rather than being a silent violation of that.

## [Unreleased]

### Changed

- CI only: the whole canary, free and paid jobs alike, now runs weekly (Mondays 06:37 UTC) and on a manual dispatch; the daily schedule is removed.

## [0.2.0] — 2026-10-15

> **Behaviour changes in this batch.**
> - A local engine no longer BUYS a captcha solve. None of them injects a
>   token into the page, so a bought token could never clear a challenge;
>   the page is now reported blocked (exit 3) without creating a 2Captcha
>   task. Before, one blocked page bought one solve per scroll round (5 with
>   default flags, 21 with `--max-scrolls 20 --stall-rounds 20`).
>   `--cdp-endpoint` is unaffected.
> - A served page whose own "N Products" counter states products while the
>   parser recognises none now exits **5** with a sidecar
>   (`status: failed`, `stop_reason: parser_found_nothing`,
>   `output_written: false`), instead of exit 4 "no products".
> - A run that collected rows and was then blocked or hit a remote error
>   now exits **6** (partial) instead of 3/5; `stop_reason` keeps the cause.
> - Negative `--retries`, `--retry-delay`, `--scroll-delay` and
>   `--proxy-block-retries`, and a `--min-score` outside 0..1, are usage
>   errors (exit 2) in every engine. Selenium and pyppeteer used to accept a
>   negative `--retries` and never navigate.
> - `--url` together with `--query`/`--category` is a usage error (exit 2).
>   A `LIDL_URL` from the environment or `.env` no longer overrides a typed
>   `--query`/`--category`, and `.env.example` ships it commented out.

### Fixed — 2026-10-14 audit

- Output files and the sidecar are written atomically (temp file in the
  same directory, fsync, rename), so an interrupted write leaves the
  previous file intact instead of a truncated one. New files follow the
  umask; an existing file keeps its mode.
- CSV output prefixes text cells starting with `=`, `+`, `-`, `@`, a tab or
  a line break with `'`, so a spreadsheet does not execute them. JSON is
  unchanged; the sidecar records `csv_cells_escaped`.
- Paid captcha solves go through one per-page allowance
  (`captcha_solver.SolveBudget`, `SOLVES_PER_PAGE = 1`) checked before any
  purchase and shared by every scroll round.
- The sidecar's `stop_reason` now says why the run ended (`max_results`,
  `stalled`, `max_scrolls`, `incomplete_grid`, `scroll_failed`,
  `parser_found_nothing`, `navigation_failed`, `blocked`, …) instead of
  repeating `status`.
- `diff_runs.py` refuses runs that started from different URLs or carry
  different `zip_code`/`store_id` values, and compares prices as numbers,
  so the same run saved as JSON and as CSV no longer diffs as "changed".
- `.gitignore` covers every renamed `.env` copy (`.env.bak`, `.env.local`,
  …) and raw dump directories and files (`live/`, `out/`, `*.pageN`, a
  root-level `*.html`).
- The credential scan is one script, `.github/ci_checks.py`, run by CI and
  by `smoke_test.py`. It now also catches unquoted and JSON-escaped key
  fields, bare 32-hex keys and URLs with embedded credentials, scans
  untracked files, and skips virtualenvs by `pyvenv.cfg`.
- The numeric argparse validators live in one module (`run_policy.py`) and
  are shared by all three engines.
- The canary's Scraping Browser job fails, naming the missing
  `LIDL_CDP_ENDPOINT` secret, instead of skipping green; the local job also
  checks that core columns are non-null on every row.
- Comments and docs no longer cite private maintainer notes.

## [0.1.0] — 2026-09-20

### Reviewed — 2026-09-20, restored the family's round-level crash safety
- `lidl_parser.safe_parse_search_results()` had been changed to let a
  parsing exception propagate to the engine's top-level crash handler
  instead of degrading that one round to empty. Restored the family-wide
  invariant (same idiom as skyscanner-scraper's
  `flight_parser.safe_parse_search_results` and stockx-scraper's per-engine
  `safe_parse` closures): a bad round degrades to "found nothing new,"
  logged at ERROR level with the exception's message, rather than crashing
  a multi-round scrape and discarding every product already collected in
  earlier rounds. The logging is what keeps this from silently masking a
  real parser bug as a legitimately empty page — see the function's
  docstring. `smoke_test.py`'s check was updated to match: it now
  reproduces a genuine parsing-time exception (a monkeypatched extraction
  path) and asserts the round degrades instead of asserting the process
  raises.

### Fixed — 2026-09-20, live pagination and contract audit
- Fixed Lidl's virtualized result grid across Playwright, Selenium, and
  pyppeteer. Engines now walk viewport-sized steps, harvest each transient
  set of materialized cards, and click the live `View More Products` control
  only after the current batch stabilizes. The OneTrust overlay is bypassed
  with a DOM click on this confirmed site control.
- Added an arithmetic completeness guard based on Lidl's own `N Products`
  counter. Hitting a scroll limit before collecting
  `min(N, --max-results)` now produces `partial`/exit 6 instead of a false
  `complete` run.
- Live-verified the batch boundary with `--query milk --max-results 60`:
  60 unique SKUs, 11 rounds, exit 0, `status=complete`, price coverage 1.0.
- Fixed Selenium CLI validation running after its optional driver import,
  which made bad input return crash/exit 1 on machines without Selenium.
- Fixed captcha markers making a page `blocked` even when product cards were
  already present; a managed-browser extension marker can no longer poison a
  healthy run.
- Hardened JSON-LD handling for non-dict offer entries, `offers.url`,
  `ImageObject.contentUrl`, and missing currency (kept null, never invented).
- Canary now requires a complete multi-round result with product and price
  floors. Blocked, empty, remote-error, and partial outcomes fail the canary.
- Removed the optional `python-dotenv` core dependency; `env_config.py` keeps
  the documented dependency-free parser and still defers to python-dotenv
  when a host application already provides it.

### Verified live, 2026-09-20 (later the same day) — first real end-to-end engine run
- `python3 playwright_scraper.py --query "whole milk" --max-results 10
  --format json --out /tmp/lidl_test.json` run against the real, live
  site (not a manual browser capture) for the first time: exit code `0`,
  `.meta.json` `"status": "complete"`, `product_count: 8`,
  `price_confirmed_pct: 1.0`. Real example row: `{"title": "Lactaid®
  whole milk", "brand": "LACTAID®", "price": 6.38, "unit_price": 0.07,
  "unit_size": "96 fl.oz.", "price_source": "dom", "product_url":
  "https://www.lidl.com/p/lactaid-whole-milk/p11242918"}` — confirms
  `extract_gridbox_products()` (added below, from the manual capture)
  actually works when driven by the real engine, not just when replayed
  against a hand-saved fixture. Closes the gap `TESTING.md` step 2 and
  the README's honesty section both flagged: nothing had ever run the
  actual scraper against lidl.com before this.

### Fixed — 2026-09-20, corrected against a real browser capture
- `lidl_parser.py`'s search URL was wrong: the real site uses
  `/q/search?q=<query>`, not the originally-guessed
  `/search/products/{query}` (confirmed via a real page load's network
  log showing the guessed path 404 side-by-side with the real path
  succeeding). Category browsing corrected to the confirmed-real
  `/c/{slug}/s{numeric-id}`, replacing the guessed
  `/specials?category=<hex-id>`.
- The framework guess was wrong: lidl.com runs on **Nuxt.js**
  (`__NUXT_DATA__`, `devalue`-serialized), not Next.js. The old
  `__NEXT_DATA__`-walking heuristic never matched anything real; it's now
  documented as a dead end and no longer part of the active parse chain
  (kept only as a harmless no-op for hygiene).
- The "JSON-LD first" strategy was half right: a real, clean schema.org
  `Product` block DOES exist, but only on individual product-detail pages
  (`/p/{slug}/p{id}`) — the search-RESULTS page's own JSON-LD is
  `Organization` only. Added `extract_gridbox_products()` as the new
  PRIMARY path for listings: the real site's `data-gridbox-impression`
  DOM attribute (a URL-encoded, GA4-ecommerce-style JSON blob) plus
  sibling visible-text elements for unit price/size — confirmed against a
  real capture of `/q/search?q=whole+milk` and `/q/search?q=milk`.
  `parse_search_results()`'s priority order is now: gridbox DOM attribute
  → JSON-LD → generic DOM fallback (`__NEXT_DATA__` removed from the
  active chain).
- Added `tests/fixtures/lidl_search_real.html`, a trimmed fixture built
  from the real capture (real product names/brands/prices/unit-prices for
  Biazzo® mozzarella, PET® buttermilk, and a private-label "whole milk" —
  not fictional), and a new `smoke_test.py` check against it. Corrected
  the two `search_url()` smoke tests that encoded the old, now-disproven
  URL scheme.
- Confirmed live: the "select your store" prompt does not block a plain
  search from returning results — resolves the open question this repo's
  docs previously flagged about a possible blocking store-selection
  modal. The actual store/zip session-binding mechanism remains
  unconfirmed.
- Re-confirmed (not new, but observed again live in the same session):
  skyscanner-scraper's sibling repo still hits a real, immediate
  PerimeterX bot challenge on skyscanner.com — unrelated to this repo's
  own fixes, noted here only as the contrast (lidl.com showed no blocking
  of any kind).
- Still open: real markup for a discounted/"weekly deal" tile (none was
  captured), image extraction, and the store/zip binding mechanism.

### Added — initial build, third member of the 2scraper family
- First build of `lidl-scraper`, following `stockx-scraper` and
  `skyscanner-scraper`'s established shape: three engine scripts
  (`playwright_scraper.py` primary, `selenium_scraper.py`,
  `puppeteer_scraper.py`), the same family-shared, no-site-knowledge
  modules ported near-verbatim from `skyscanner-scraper` (`output_writer.py`,
  `proxy_pool.py`, `captcha_solver.py`, `fingerprint_client.py`,
  `scraper_api_client.py`, `diff_runs.py`, `env_config.py`), and `lidl_parser.py` as this repo's own site knowledge.
- `Product` schema extended with grocery-specific fields not present in
  either sibling repo: `unit_price`, `unit_size`, `original_price`,
  `discount_pct`, `is_weekly_deal`, `deal_valid_from`, `deal_valid_until`,
  `store_id`, `zip_code` — US grocery pricing is commonly store/region-
  scoped and frequently on a time-limited "weekly deal," neither of which
  either flight itineraries or marketplace listings needed to represent.
- `lidl_parser.py`'s embedded-data extraction tries schema.org
  `application/ld+json` `Product`/`ItemList` markup FIRST, ahead of the
  `__NEXT_DATA__` heuristic both sibling repos use as their primary path —
  structured Product data is a standard, documented e-commerce SEO
  practice, and a more likely place for lidl.com to expose clean data than
  a framework guess this repo has no public confirmation of (unlike
  skyscanner.com, which is publicly known to be Next.js-based).
- `--zip`/`--store-id` flags, threaded through to every output row, for a
  site where the same search can legitimately return different prices by
  region — a dimension neither sibling repo's schema needed to model.

### Family-shared modules, ported unchanged in substance from skyscanner-scraper
- `env_config.ENV_KEYS` renamed to this repo's own `LIDL_PROXY` /
  `LIDL_CDP_ENDPOINT` / `LIDL_URL` (kept in sync with `.env.example`,
  verified by a `smoke_test.py` check).
- `output_writer.py`'s exit codes, `STATUS_BY_EXIT` map, and
  `finish_run()` precedence logic are byte-for-byte identical to both
  prior family members — only `Product`'s site-specific tail differs.
- `diff_runs.py`'s description string updated to name this repo; its
  sku-diff logic is unchanged and unit-tested against two real
  `finish_run()` outputs in `smoke_test.py`.

[Unreleased]: https://github.com/2scraper/lidl-scraper/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/2scraper/lidl-scraper/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/2scraper/lidl-scraper/releases/tag/v0.1.0
