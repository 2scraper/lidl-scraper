# lidl-scraper

![release](https://img.shields.io/github/v/release/2scraper/lidl-scraper?sort=semver)
![tests](https://github.com/2scraper/lidl-scraper/actions/workflows/tests.yml/badge.svg)
![canary](https://github.com/2scraper/lidl-scraper/actions/workflows/canary.yml/badge.svg)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![licence](https://img.shields.io/badge/licence-MIT-green)
![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)
![local-first](https://img.shields.io/badge/local--first-yes-success)

lidl.com (Lidl US) grocery product scraper: a search term or a specials
category in, a flat list of products out. Three engines (Playwright
primary, Selenium and Puppeteer/pyppeteer for parity), JSON or CSV output,
an open, documented `Product` schema. Part of the
[2scraper](https://github.com/2scraper) family — same output contract,
exit codes, and family modules as `stockx-scraper` / `skyscanner-scraper`.

## Read this before trusting a run

**Updated 2026-09-20 from a real, browser-rendered capture** (a real
Chromium browser, not this repo's own earlier non-browser fetch attempts).
Most of what used to be a guess here is now a confirmed fact:

- **The real search URL is `https://www.lidl.com/q/search?q=<query>`** —
  not `/search/products/{query}`, which this repo guessed before and which
  is confirmed WRONG (a real 404, seen side-by-side with the real request
  succeeding in the same page load). Category browsing is
  `/c/{slug}/s{numeric-id}`, also confirmed real, not the old
  `/specials?category=<hex-id>` guess.
- **The site runs on Nuxt.js**, not Next.js as previously guessed — the
  old `__NEXT_DATA__` heuristic never matched anything real and is now a
  documented dead end, kept only as a harmless no-op.
- **The search-results page's own JSON-LD is `Organization` only** — no
  `Product`/`ItemList` data lives there. An individual product-detail page
  (`/p/{slug}/p{id}`) DOES have a real, clean schema.org `Product` block,
  just not the listing page.
- **The real primary data source for a listing is a DOM attribute**: each
  result tile carries `data-gridbox-impression`, a URL-encoded JSON blob
  with clean id/name/brand/category/price data — confirmed real, and now
  `lidl_parser.py`'s primary extraction path (`extract_gridbox_products`).
  Unit price/size come from the tile's visible price text alongside it.
- **The "select your store" prompt did NOT block a real search** — a
  plain query returned real, generically-priced results with no store/zip
  ever set. The store/zip session-binding MECHANISM itself (cookie? query
  param?) is still unconfirmed — `--zip`/`--store-id` are recorded on
  every output row, but no engine here has been confirmed to actually SET
  a region yet.
- **No bot-challenge of any kind was hit on lidl.com** in this capture —
  clean end to end, in contrast to skyscanner-scraper's sibling repo,
  which served a real, immediate PerimeterX challenge in the same session.
- **Update, 2026-09-20 (later the same day) — the actual engine was run
  live for the first time, not just a manual browser capture**:
  `python3 playwright_scraper.py --query "whole milk" --max-results 10
  --format json --out /tmp/lidl_test.json` against the real, live site —
  exit code `0`, `"status": "complete"`, 8 real products,
  `price_confirmed_pct: 1.0`. Real rows include `{"title": "Lactaid® whole
  milk", "brand": "LACTAID®", "price": 6.38, "unit_price": 0.07,
  "unit_size": "96 fl.oz.", "price_source": "dom"}` — the math checks out
  (6.38 / 96 ≈ 0.07/oz) and the URL used
  (`https://www.lidl.com/q/search?q=whole+milk`) matches the confirmed
  real scheme above. This is the first time `extract_gridbox_products()`
  has been exercised by the actual scraper, not a hand-driven browser
  session — the single biggest remaining gap this repo had (see
  `TESTING.md` step 2) is now closed.
- **Still unconfirmed**: the store/zip session-binding mechanism.
  Discounted tiles and images were confirmed on 2026-10-18 against a real
  weekly-deals listing. Batched virtual-grid pagination and
  the real `View More Products` control are now live-verified.
- **The architecture — exit codes, the output contract, dedupe,
  credential redaction, CLI validation, all three engines importing
  cleanly, and the shared crash-safety wrapper around parsing (a bad
  round degrades to "found nothing new," never a crash that discards
  already-collected sibling rounds — same idiom as
  skyscanner-scraper's `flight_parser.safe_parse_search_results`) — is
  real and tested**, as before. `smoke_test.py` now also includes one
  fixture built from the real capture above
  (`tests/fixtures/lidl_search_real.html`), not just synthetic ones — see
  `smoke_test.py`'s own module docstring for which is which.
- Manual captures were followed by full Playwright engine runs, including a
  60-item query that crossed the first lazy batch. Selenium and pyppeteer
  passed the same live runs on 2026-10-18; `TESTING.md` records what is
  still unverified.

## Local-first

Like the rest of the family, this does **not** require 2Captcha's paid
Scraping Browser API to run. The default is an ordinary local headless
Chromium, no proxy, no key, no account. `--proxy` / `--cdp-endpoint` /
`--fingerprint` are opt-in power options for volume, a specific exit
country, or a consistent device identity — the same reasoning the rest of
the family states, carried over here as an architectural choice even
though (see above) it hasn't been live-measured on *this* site yet.

## Install

Pick one engine (installing more than one into the same environment is not
supported — see "Engines" below):

```bash
pip install -r requirements-playwright.txt && playwright install chromium   # primary
pip install -r requirements-selenium.txt                                    # needs a matching chromedriver
pip install -r requirements-puppeteer.txt                                   # pyppeteer — see its own warning below
```

Copy `.env.example` to `.env` — leave it blank for a normal first run (see
"Local-first" above) and fill in what you use later. `python3 env_config.py`
shows what was picked up without ever printing a secret.

## Usage

```bash
# a product search
python3 playwright_scraper.py --query "whole milk" --format json --out lidl_results.json

# with a region and a result cap
python3 playwright_scraper.py --query "sourdough bread" --zip 11803 --max-results 20

# a category path copied from lidl.com's own navigation
python3 playwright_scraper.py --category food-wine/s10068374

# a full lidl.com URL directly (escape hatch — cannot be combined with
# --query/--category; a typed --query/--category also wins over LIDL_URL)
python3 playwright_scraper.py --url "https://www.lidl.com/q/search?q=milk"

# with 2Captcha's Scraping Browser API (opt-in — see "Local-first" above)
python3 playwright_scraper.py --query "whole milk" --cdp-endpoint "$LIDL_CDP_ENDPOINT"
```

`selenium_scraper.py` and `puppeteer_scraper.py` accept the identical flag
set and produce the identical output contract — see "Engines" for the two
places they genuinely can't behave the same as Playwright.

### Flags

`--url --query --category --zip --store-id --sort --max-results
--max-scrolls --stall-rounds --scroll-delay --format --out --retries
--retry-delay --proxy --proxy-file --proxy-shuffle --proxy-block-retries
--twocaptcha-key --captcha-api --solve-captcha --min-score --cdp-endpoint
--fingerprint --fp-tags --fp-country --allow-empty --dump-html
--headless/--headful`

Identical across all three engines — a `smoke_test.py` check asserts the
three parsers' flag sets never drift apart. `--fingerprint`/`--fp-tags`/
`--fp-country` apply to all three engines: each sets whatever user agent
the 2Captcha Fingerprint API returns via its own driver's real primitive
(Playwright's `new_context(user_agent=...)`, pyppeteer's
`page.setUserAgent()`, Chrome's own `--user-agent=` switch under
Selenium) — see "What this repo deliberately does NOT apply from a
fingerprint" below. `--captcha-api` overrides the 2Captcha REST base URL
(testing only). `--min-score` is 2Captcha's own `minScore` field on a
`RecaptchaV3Task` request (0.3 default, matching the rest of the family).
`--store-id` is recorded on output rows only — there is no confirmed way
yet to select a store by id directly (see "Read this before trusting a
run" above); `--zip` is threaded into the search URL as a best-effort
guess at how region selection might work.

### Family flags that don't apply here — and why

- **`--pages`**: lidl.com's search/specials pages are treated as a single
  scroll-based results page here (see "Pagination" below), the same
  structural reason skyscanner-scraper omits it — `--max-scrolls`/
  `--stall-rounds` are this repo's actual equivalent. Unlike
  skyscanner-scraper, this is a documented ASSUMPTION about lidl.com's
  real UI, not a confirmed fact — if a live capture shows lidl.com
  actually uses `?page=N` pagination instead, `--pages` belongs back in a
  future revision, and the scroll loop degrades safely in the meantime
  (it just stalls after the first page, per `--stall-rounds`, rather than
  crashing or hanging).
- **`--concurrency` / `--proxy-rotate`**: same reasoning as
  skyscanner-scraper — a single scroll-based page has no independently-
  addressable units to parallelize or rotate an exit between.
  `proxy_pool.ProxyPool.worker_view()` is still ported verbatim per the
  family's "copy the core, verbatim" rule and stays tested, for if a
  future feature (e.g. running several search terms as a batch)
  introduces an actual parallelizable unit.

### What this repo deliberately does NOT apply from a fingerprint

`fingerprint_client.py` only ever extracts and applies the user agent from
a 2Captcha Fingerprint API profile — never a locale or timezone, for the
same reason skyscanner-scraper states: this session could not get a
confirmed field name for either from 2Captcha's own public reference, and
a previous family member shipped a *fabricated* locale that went unnoticed
for months. Omitting a signal honestly beats guessing it.

Credentials belong in `.env` / `LIDL_PROXY` / `TWOCAPTCHA_KEY` — never as
literal `--proxy`/`--twocaptcha-key` text on a shared or logged command
line if you can avoid it.

## Output contract

`Product` (`output_writer.py`) — family-common columns first, grocery-item
columns after:

```
sku, source, category, title, brand, price, currency, price_source, product_url,
image_url, scraped_at,
unit_price, unit_size, original_price, discount_pct, is_weekly_deal,
lidl_plus_price, deal_valid_from, deal_valid_until, store_id, zip_code
```

Unlike skyscanner-scraper's flight itineraries, a Lidl catalog item
genuinely has a stable identity: `sku` is lidl.com's own product id/GTIN
when `lidl_parser.py` can extract one, falling back to a deterministic
fingerprint of `product_url` only when it can't. `brand` is the actual
product brand printed on the item. `category` is the grocery
aisle/category lidl.com itself assigns, when available. `price_source` is
`embedded_json` or `dom`, mirroring which extraction path actually
produced the row — never a defaulted guess. `sample_output.json` and
`sample_output.csv` are trimmed from the verified live Playwright run.

**Exit codes**: `0` complete · `1` crash · `2` bad usage · `3` blocked
(no rows) · `4` zero products, and nothing on the page says products exist ·
`5` the content was never obtained (a navigation failure, a remote API/CDP
error, or a served page whose own "N Products" counter states products
while the parser recognised none) · `6` partial (rows were collected, then
something failed or the grid came up short). Every completed/partial run
writes a `<out>.meta.json` sidecar with `status`, `stop_reason` (why the
run ended: `max_results`, `stalled`, `max_scrolls`, `incomplete_grid`, …),
`pages_completed` (scroll rounds, here), `failed_pages`,
`price_confirmed_pct` and `csv_cells_escaped`. An empty, blocked or
never-obtained run writes no output and no sidecar, so it can never
overwrite a previous good run (`--allow-empty` opts out of that). The one
exception is a parser failure: its sidecar IS written (`status: failed`,
`stop_reason: parser_found_nothing`, `output_written: false`) beside the
untouched previous output, because "our selectors lost the rows" is
something a consumer needs to see.

Writes are atomic (a temp file in the same directory, renamed into place),
so an interrupted run never leaves a truncated file. In CSV only, a text
cell starting with `=`, `+`, `-`, `@`, a tab or a line break is prefixed
with `'` so a spreadsheet does not execute it; JSON keeps the site's exact
text, and the sidecar's `csv_cells_escaped` says how many cells differ.

## Pagination

Lidl's grid is both batched and virtualized. Each engine walks the page in
viewport-sized steps so every transient tile is parsed, clicks the confirmed
`View More Products` control only after the current batch has stabilized,
and dedupes by `sku`. The page's own `N Products` counter is an arithmetic
completeness check: if fewer than `min(N, --max-results)` rows were captured,
the run is `partial` (exit 6), never a plausible-looking `complete` result.

Live verification on 2026-09-20: `--query milk --max-results 60` returned 60
unique SKUs in 11 rounds with `status=complete` and price coverage 1.0.

## Engines

Playwright is primary; Selenium and pyppeteer are parity copies — all
three agree on exit codes and the `Product` schema via the shared
`output_writer.finish_run()`. Real, stated limits (identical to the rest
of the family's, since these are properties of the drivers, not the
site):

- **Selenium cannot use an authenticated remote CDP endpoint.**
  chromedriver's `debuggerAddress` takes a bare `host:port`; the Scraping
  Browser API's `ws://login:pass@host:port` shape needs an authenticated
  WebSocket upgrade, which only Playwright's `connect_over_cdp` and
  pyppeteer's `connect` support. `selenium_scraper.py` refuses a
  credentialed `--cdp-endpoint` outright (exit 2).
- **Selenium's `--proxy-server` cannot authenticate at all.** A `--proxy`
  with credentials has them stripped before reaching Chrome, with a loud
  warning — never a silent no-op.
- **pyppeteer is effectively unmaintained** (its own README points at
  Playwright) — shipped for parity, not as a recommendation.
- Install **exactly one** engine per environment — Playwright and
  pyppeteer declare mutually unsatisfiable `pyee` pins, and pyppeteer
  collides with Selenium's `urllib3` pin. Use a venv per engine, same as
  `.github/workflows/tests.yml`'s `engine-smoke` job.

## Known limitations

- **No site-specific block-page marker exists.** `lidl_parser.
  BOT_CHALLENGE_MARKERS` is deliberately empty — detection still runs via
  `captcha_solver.GENERIC_BOT_CHALLENGE_MARKERS` (Cloudflare/reCAPTCHA/
  hCaptcha/PerimeterX/DataDome wording), but nothing specific to how
  lidl.com's own block page (if one exists) reads has been captured yet.
  If you hit one, `TESTING.md` explains how to add it.
- **This repo does not implement captcha token injection on a locally
  launched browser**, so the local engines never BUY a solve: a challenge
  is detected and reported (the run ends blocked, exit 3), and no 2Captcha
  task is created, because a token nothing puts into the page cannot clear
  it. Over `--cdp-endpoint` (the Scraping Browser API) the remote browser's
  own `Captcha.setAutoSolve` solves inside its own infrastructure, which is
  unaffected. If injection is added later, each page still gets at most one
  paid solve (`captcha_solver.SOLVES_PER_PAGE`), shared by every scroll
  round.
- **How lidl.com keys a session to a store/zip is unconfirmed** — see
  "Read this before trusting a run" above. `--zip`/`--store-id` record
  intent and annotate output rows; they are not confirmed to change what
  a search actually returns yet.
- **Deal dates are not filled.** Images, `original_price`, `discount_pct`
  and `is_weekly_deal` come from confirmed tile markup, but no tile states
  a deal's validity dates, so `deal_valid_from`/`deal_valid_until` stay
  null on listings. `price` is what a shopper without the Lidl Plus app
  pays; a member price goes to `lidl_plus_price`, and on those tiles
  `unit_price` is null because Lidl prints only the member per-unit price.

## Development

```bash
python3 smoke_test.py     # or: pytest tests/test_smoke.py
```

Passes with **no** engine library installed at all (each engine guards its
driver import behind a module-level `try/except ImportError`).

**Testing against the live site**: see [`TESTING.md`](TESTING.md). The primary
Playwright path is live-verified; Selenium, pyppeteer, store binding, images,
and discounted tiles remain the highest-value live checks.

## License

MIT — see `LICENSE`.
