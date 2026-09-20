# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) as closely as a CLI toolkit can manage. A patch
release means "fixes", not that every flag and default is frozen — a
behaviour-changing default gets called out explicitly in its entry below
rather than being a silent violation of that.

## [Unreleased]

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
  captured), and confirmation of whether scroll-only pagination reaches
  results past the first ~48 or whether the real "View More Products"
  button must be clicked.

### Added — initial build, third member of the 2scraper family
- First build of `lidl-scraper`, following `stockx-scraper` and
  `skyscanner-scraper`'s established shape: three engine scripts
  (`playwright_scraper.py` primary, `selenium_scraper.py`,
  `puppeteer_scraper.py`), the same family-shared, no-site-knowledge
  modules ported near-verbatim from `skyscanner-scraper` (`output_writer.py`,
  `proxy_pool.py`, `captcha_solver.py`, `fingerprint_client.py`,
  `scraper_api_client.py`, `diff_runs.py`, `env_config.py` — per CLAUDE.md
  §7), and `lidl_parser.py` as this repo's own site knowledge.
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

### Honesty note — read before trusting anything above
- **robots.txt on lidl.com is wide open** (`Disallow:` empty) — unlike
  skyscanner-scraper, nothing here was blocked from being fetched by
  policy. But every URL deeper than the homepage that this session tried
  returned a plain HTTP 404 to a non-browser fetch (no bot-challenge
  marker present), most likely because lidl.com's deeper routes are
  client-side-rendered and a static fetch can't drive them. Net effect:
  **the URL scheme `lidl_parser.py` builds is confirmed real** (matched
  against Google's own index of live lidl.com pages, not invented) — the
  exact query params, the embedded-JSON shape, every DOM selector, and
  even the scroll-based pagination model are still best-effort guesses,
  each marked `# TODO: verify live`.
- No engine in this repo has been run against the live site yet, from any
  environment. See `TESTING.md` for the checklist that closes this gap,
  starting with the one run that matters most: a plain `--query` search
  against the real site, inspected with `--dump-html`.
- `sample_output.json`/`.csv` are clearly fictional (every brand name is
  suffixed `(fictional sample)`), per CLAUDE.md §15 — no real capture
  exists yet to sample from honestly.

### Family-shared modules, ported unchanged in substance from skyscanner-scraper
- `env_config.ENV_KEYS` renamed to this repo's own `LIDL_PROXY` /
  `LIDL_CDP_ENDPOINT` / `LIDL_URL` (kept in sync with `.env.example`,
  verified by a `smoke_test.py` check per CLAUDE.md §17).
- `output_writer.py`'s exit codes, `STATUS_BY_EXIT` map, and
  `finish_run()` precedence logic are byte-for-byte identical to both
  prior family members — only `Product`'s site-specific tail differs.
- `diff_runs.py`'s description string updated to name this repo; its
  sku-diff logic is unchanged and unit-tested against two real
  `finish_run()` outputs in `smoke_test.py`.

[Unreleased]: https://github.com/2scraper/lidl-scraper/compare/main...HEAD
