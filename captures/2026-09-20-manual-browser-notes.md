# Manual browser capture — 2026-09-20

Not a raw HTML dump (this capture was done interactively through a real
Chromium browser pane, reading the live DOM/network log directly rather
than saving the full page source, which ran into the megabytes). This
file records what was confirmed instead. See `lidl_parser.py`'s module
docstring and the repo's `CHANGELOG.md`/`README.md` for how these facts
changed the parser.

## What was done

1. Opened `https://www.lidl.com` in a real browser — real homepage,
   confirmed "Select your Lidl store for special offers in your area" as
   a soft prompt, not a blocking modal.
2. Used the site's own search box to search "whole milk" (a stray retry
   left some duplicated text in the actual query string, which is why the
   real request URL below looks garbled — harmless, the site still
   returned real fuzzy-matched results).
3. Read the resulting page's network log, DOM, and embedded scripts
   directly (`get_page_text`, `read_network_requests`, a few
   `javascript_tool` calls to query `document.querySelectorAll` and
   `window.location.href`).
4. Repeated a cleaner search ("milk", 142 results) to get a second,
   larger sample and confirm pagination behaviour (48-per-page, "View
   More Products" button, per-tile lazy skeleton rendering on scroll).
5. Opened one product's own detail page
   (`/p/biazzo-whole-milk-mozzarella-cheese/p11244845`) to check for
   JSON-LD there specifically.
6. Checked `https://www.lidl.com/c/offers-leaflets/s10092873` ("Offers &
   Leaflets") looking for a discounted-tile example — this turned out to
   be a leaflet/flyer browsing page, not a product grid, so no discount
   example was captured this session.

## Confirmed facts

- Real search request: `GET https://www.lidl.com/q/search?q=<query> → 200`
  (network log). The originally-guessed
  `GET https://www.lidl.com/search/products/whole%20milk → 404` was seen
  in the SAME page load's network log, side by side — conclusive proof
  the old guess was wrong, not just unconfirmed.
- Real category link (from the homepage's own nav pills):
  `href="/c/food-wine/s10068374"` (and four sibling categories in the
  same `/c/{slug}/s{numeric-id}` shape).
- `document.getElementById('__NUXT_DATA__')` exists and is non-null;
  `window.__NEXT_DATA__`/`document.getElementById('__NEXT_DATA__')` does
  not. The site is Nuxt.js. `__NUXT_DATA__`'s own payload is `devalue`-
  serialized (an array-of-references format, not plain nested JSON) —
  not parsed by this repo; `window.__NUXT__` (Nuxt 2's usual hydration
  global) was checked and is NOT present either (Nuxt 3 apparently
  doesn't expose it the same way here).
- Search-results page `application/ld+json`: exactly one block, `{"@type":
  "Organization", "name": "Lidl", ...}` — no `Product`/`ItemList`.
- Product-detail page (`/p/biazzo-whole-milk-mozzarella-cheese/p11244845`)
  `application/ld+json`: THREE blocks — a real `Product` (sku "11244845",
  name, brand.name, image, one `offers` entry with price/priceCurrency/
  itemCondition/availability), plus two `Organization` blocks (one
  minimal, one fuller with a `hasMemberProgram`/Lidl Plus block).
- Real tile structure (both searches): `<ol class="odsc-tile-grid ...
  s-product-grid" data-testselector="s-product-grid__list">` containing
  `<li id="grid-item-{position}-{productId}">` containing a
  `div.odsc-tile.product-grid-box` (or, before the tile's lazy content
  has rendered in from a skeleton, just `div.odsc-tile` with a
  `.s-grid-box-skeleton` child instead).
- `data-gridbox-impression` attribute (URL-encoded JSON) on that div, real
  sample (trimmed):
  `{"brand":"BIAZZO®","category":"Food","id":"11244845","name":"Biazzo®
  whole milk mozzarella cheese","position":1,"price":3.69,"sponsored":
  false,"categoryPrimary":"Food","wonCategoryPrimary":"Worlds of
  need/Food and near food/Cheese, dairy products &
  eggs/Cheese","wonCategoryPrimaryPath":"0/17/1739/173912", ...}`
  (more fields present — ranking/scoring internals — omitted here as
  noise, not needed by the parser).
- Visible tile text (real, from 4+ different products across both
  searches): title in `.product-grid-box__title`; current price in
  `.ods-price__main-wrapper` as `"$X.XX*"` (trailing `*` footnote marker);
  unit size + per-unit price CONCATENATED with no separator in
  `.ods-price__footer`, e.g. `"16 oz.$ 0.23 per oz."` or
  `"128 fl.oz.$ 0.03 per fl.oz."`; a brand element (class containing
  "brand") present only for branded items — "whole milk" (private label)
  had none.
- Product link: `a[href^="/p/"]`, real href has a
  `#searchTrackingMasterId=...&searchTrackingQuery=...` fragment appended
  that differs per search/position for the IDENTICAL product — must be
  stripped for a stable `product_url`.
- No bot-challenge / block encountered on lidl.com anywhere in this
  session.
- Real products seen (for reference / used to build
  `tests/fixtures/lidl_search_real.html`):
  - Biazzo® whole milk mozzarella cheese — $3.69, 16 oz., $0.23/oz.
  - PET® whole buttermilk — $2.87, 64 fl.oz., $0.04/fl.oz.
  - whole milk (private label) — $3.30, 128 fl.oz., $0.03/fl.oz.
  - chocolate milk, 1% lowfat — $2.13, 64 fl.oz., $0.03/fl.oz.
  - Califia Farms® almond milk, unsweetened — $4.32, 48 fl.oz., $0.09/fl.oz.
  - Dairy Maid® vitamin D milk — $5.99, 128 fl.oz., $0.05/fl.oz.

## Still not captured

- A discounted / "weekly deal" tile's real markup (original price,
  discount badge, deal date range) — none of the products searched this
  session had one.
- The store/zip picker's own request (what it POSTs, what cookie/param it
  sets afterward) — the picker was never opened, only its collapsed
  homepage prompt was observed.
- Whether scrolling alone (vs. clicking "View More Products") loads
  results past the first ~48.
- A full raw HTML page source, for a byte-for-byte selector diff against
  a future site change — this session recorded structure and values via
  direct DOM/network queries instead, which is faster interactively but
  leaves no complete raw file to diff against later. A future contributor
  with `--dump-html` access to a real engine run should still drop one
  here per the instructions below.
