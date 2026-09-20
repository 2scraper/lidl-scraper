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
- **Still unconfirmed**: the exact markup of an actual discounted/
  "weekly deal" tile (none was captured), image extraction, and the
  store/zip session-binding mechanism. Batched virtual-grid pagination and
  the real `View More Products` control are now live-verified.
- **The architecture — exit codes, the output contract, dedupe,
  credential redaction, CLI validation, all three engines importing
  cleanly, and the shared crash-safety wrapper around parsing (a bad
  round degrades to "found nothing new," never a crash that discards
  already-collected sibling rounds — CLAUDE.md §6/§10, same idiom as
  skyscanner-scraper's `flight_parser.safe_parse_search_results`) — is
  real and tested**, as before. `smoke_test.py` now also includes one
  fixture built from the real capture above
  (`tests/fixtures/lidl_search_real.html`), not just synthetic ones — see
  `smoke_test.py`'s own module docstring for which is which.
- Manual captures were followed by full Playwright engine runs, including a
  60-item query that crossed the first lazy batch. `TESTING.md` records the
  remaining Selenium/pyppeteer and field-level live checks.

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

# a full lidl.com URL directly (escape hatch — bypasses --query/--category)
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
  family's "copy the core, verbatim" rule (§7) and stays tested, for if a
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
deal_valid_from, deal_valid_until, store_id, zip_code
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

**Exit codes**: `0` complete · `1` crash · `2` bad usage · `3` blocked ·
`4` zero products (and nothing was written) · `5` remote API error · `6`
partial. Every completed/partial run writes a `<out>.meta.json` sidecar
with `status`, `pages_completed` (scroll rounds, here), `failed_pages` and
`price_confirmed_pct` — **except** a failed/empty/blocked/remote-API-error
run, which writes no sidecar and no output at all, so it can never
overwrite a previous good run (`--allow-empty` opts out of the "don't
write an empty result" half of that guard only — see `output_writer.
finish_run`'s docstring for the exact precedence rule and why products
being present never launders a blocked/remote-API-error run into
"complete").

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
- **Captcha token injection on a locally-launched browser is not
  implemented**, same reason as the rest of the family: injecting a
  solved token is widget/site-specific, and no real challenge from this
  site was ever available to verify an injector against. Over
  `--cdp-endpoint` (the Scraping Browser API), this doesn't matter —
  2Captcha's own `Captcha.setAutoSolve` CDP domain handles it entirely
  inside their infrastructure.
- **How lidl.com keys a session to a store/zip is unconfirmed** — see
  "Read this before trusting a run" above. `--zip`/`--store-id` record
  intent and annotate output rows; they are not confirmed to change what
  a search actually returns yet.
- **Images and weekly-deal fields are not yet live-verified.** The current
  listing path deliberately leaves them null rather than guessing. A future
  capture should add them from Lidl's own product payload or confirmed tile
  markup.

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
