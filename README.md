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

This repo's own build environment could reach lidl.com in a way neither
prior family member's could — **robots.txt here is wide open**
(`User-agent: *` / `Disallow:` empty), and `https://www.lidl.com`'s
homepage fetched cleanly and confirms a real storefront (product
categories, a "Weekly Deals" section, a Lidl Plus loyalty program). But
that success didn't extend past the homepage:

- Every deeper URL this session tried — a guessed
  `/search/products/{query}`, a guessed `/specials?category=<hex-id>`
  copied from a real Google-indexed lidl.com page, even `/sitemap.xml` —
  returned a plain HTTP 404 to a non-browser fetch. No bot-challenge
  marker was present in any of those 404s, unlike skyscanner-scraper's
  PerimeterX incident — this looks like "a static fetch can't drive this
  app's client-side router," not active blocking. Still unconfirmed
  against a real, browser-rendered page.
- Net effect: **the URL scheme lidl_parser.py builds is real** (confirmed
  via Google's own index of live lidl.com pages, not invented), but the
  exact query params, the embedded-JSON shape, every DOM selector, and
  even the scroll-based pagination MODEL are best-effort guesses — each
  marked `# TODO: verify live` in `lidl_parser.py`. The one partial
  exception: the JSON-LD `Product`/`ItemList` extraction path is grounded
  in schema.org's standard, documented e-commerce SEO markup, not a
  lidl.com-specific guess — still unconfirmed whether lidl.com actually
  emits it, but a reasonable first thing to check.
- **Everything else — the architecture — is real and tested**: exit
  codes, the output contract, dedupe, credential redaction, CLI
  validation, all three engines importing cleanly, the crash-safety
  wrapper around parsing. `smoke_test.py` proves all of that against
  SYNTHETIC fixtures (see its own module docstring).
- No engine has been run against the live site from any environment yet.
  `TESTING.md` step 2 and the `canary-local` CI job (no secrets, on a
  schedule) are what close that gap. If a real run comes back with zero
  products, that's the **expected first-run outcome** for a parser nobody
  has pointed at the real markup yet — not evidence the scraper is broken.
  Open the `--dump-html` capture, compare it to `lidl_parser.py`, fix
  what doesn't match, and you've turned a guess into this repo's first
  actually-verified fact.
- **Unconfirmed: how lidl.com keys a session to a store/zip.** US grocery
  pricing is commonly region-scoped, but whether that's a cookie, a query
  param, or a modal that must be dismissed first is unknown — `--zip`/
  `--store-id` are accepted and recorded on every output row, but no
  engine here has been confirmed to actually SET a region yet.

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

# a specials/weekly-deals category (the id is opaque — find one by browsing
# lidl.com/specials and copying its ?category=<hex-id>, not by guessing)
python3 playwright_scraper.py --category be8072a237eb7908c193ee9175e2f8c87e48fe8b

# a full lidl.com URL directly (escape hatch — bypasses --query/--category)
python3 playwright_scraper.py --url "https://www.lidl.com/search/products/milk"

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
produced the row — never a defaulted guess. See `sample_output.json` /
`sample_output.csv` — **these are a clearly fictional illustration of the
schema** (every brand name is suffixed `(fictional sample)`), not a real
capture — see "Read this before trusting a run" above for why no real one
exists yet.

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

**Assumption, not a confirmed fact** (see "Read this before trusting a
run" above): every engine here scrolls and re-parses the accumulated
page, deduping by `sku`, until `--max-results` is reached or
`--stall-rounds` consecutive scrolls add nothing new (capped by
`--max-scrolls` either way) — the same lazy-load-on-scroll model
skyscanner-scraper uses for ITS site, chosen here because it degrades
safely even if lidl.com's real UI turns out to paginate differently.

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

- **Nothing in `lidl_parser.py` has been checked against a live,
  browser-rendered response** — see "Read this before trusting a run"
  above. This is the single biggest open item, and the reason this
  section leads with it.
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
- **The embedded-JSON extraction path (`__NEXT_DATA__` fallback) is a
  generic heuristic on an unconfirmed framework guess** — unlike
  skyscanner.com (publicly known to be Next.js-based), whether lidl.com
  uses Next.js at all is unverified. The JSON-LD path is tried first
  precisely because it doesn't depend on that guess being right.

## Development

```bash
python3 smoke_test.py     # or: pytest tests/test_smoke.py
```

Passes with **no** engine library installed at all (each engine guards its
driver import behind a module-level `try/except ImportError`).

**Testing against the live site**: see [`TESTING.md`](TESTING.md) — a
step-by-step checklist, starting with the one test that actually matters
for this repo: pointing the local-first default at a real search and
checking whether `lidl_parser.py`'s guesses hold up.

## License

MIT — see `LICENSE`.
