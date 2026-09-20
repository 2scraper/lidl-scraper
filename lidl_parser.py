#!/usr/bin/env python3
"""lidl_parser.py — this IS the lidl.com site knowledge (the grocery-family
analog of stockx-scraper's product_parser.py and skyscanner-scraper's
flight_parser.py).

**Honesty note, read before trusting anything below** (CLAUDE.md §15: an
unverified site gets that stated plainly, not glossed over).

Updated 2026-09-20 from a REAL, browser-rendered capture (Roman's own
machine, via an actual Chromium browser — not this repo's earlier
non-browser fetch attempts). Everything in this section is CONFIRMED, not
guessed, unless marked otherwise:

  - The real search URL is `https://www.lidl.com/q/search?q=<query>` —
    NOT `/search/products/{query}`, which this file guessed before this
    capture. That guess was independently confirmed WRONG live: the
    network log for the exact same page load shows
    `GET https://www.lidl.com/search/products/whole%20milk → 404`
    alongside the real search request
    `GET https://www.lidl.com/q/search?q=whole+milk → 200`.
  - Category browsing uses a different, also-confirmed-real scheme:
    `/c/{slug}/s{numeric-id}` (e.g. `/c/food-wine/s10068374`), taken
    verbatim from the homepage's own category nav links — not
    `/specials?category={hex-id}` as this file guessed before.
  - The site is built on **Nuxt.js** (`id="__NUXT_DATA__"`, a `devalue`-
    serialized payload), NOT Next.js as this file guessed before. The old
    `__NEXT_DATA__` heuristic below never matched anything (wrong id,
    wrong framework) and is kept only as a documented dead end — see
    `extract_next_data()`.
  - The search-RESULTS page's own `application/ld+json` block is
    `Organization` only — no `Product`/`ItemList` node is present there.
    The "JSON-LD first" strategy this file used to lead with does not
    find search-listing data on the real site; it safely no-ops instead
    (no crash), which is why it's still kept, just no longer relied on
    for listings.
  - An individual PRODUCT-DETAIL page (`/p/{slug}/p{numeric-id}`) DOES
    carry a real, clean schema.org `Product` JSON-LD block — confirmed
    live: `sku`, `name`, `brand.name`, `image`, and an `offers` array with
    `price`/`priceCurrency`/`availability`. `extract_json_ld()` /
    `_json_ld_node_to_product()` are written for (and confirmed against)
    THIS page type, not the search-results listing.
  - The real, confirmed primary source for a search-RESULTS listing is a
    DOM data attribute, not embedded JSON in a `<script>` tag: every
    result tile is a `.odsc-tile` / `.product-grid-box` element inside
    `<li id="grid-item-{position}-{productId}">` inside
    `<ol class="... s-product-grid" data-testselector="s-product-grid__
    list">`, and it carries `data-gridbox-impression="<URL-encoded
    JSON>"` — a GA4-ecommerce-style impression blob with clean `id`,
    `name`, `brand`, `category`, `price`, `position`, `sponsored`, and a
    full category taxonomy path (`wonCategoryPrimary`/
    `wonCategoryPrimaryPath`). This is more reliable than scraping visible
    text and is now this parser's primary path for listings — see
    `extract_gridbox_products()`.
  - Confirmed real visible-text structure inside each tile (used to fill
    in what `data-gridbox-impression` doesn't carry — unit price/size):
    `.product-grid-box__title` (name), `.ods-price__main-wrapper` (current
    price, e.g. `"$3.30*"` — the trailing `*` is a footnote marker, not
    part of the number), `.ods-price__footer` (unit size + per-unit price
    concatenated with no separator, e.g. `"128 fl.oz.$ 0.03 per fl.oz."`),
    and a brand element present only for branded items (private-label
    items like plain "whole milk" render no brand element at all).
  - The product link is `a[href^="/p/"]`; the real `href` value has a
    `#searchTrackingMasterId=...` fragment appended — this must be
    stripped for a clean, stable `product_url` (kept as the fragment
    differs per search/position even for the identical product).
  - "Select your Lidl store for special offers in your area" is a soft,
    dismissible prompt, NOT a blocking modal — a real search (`whole
    milk`) returned real generic-pricing results with no store/zip ever
    selected. This resolves the open question `TESTING.md` flagged about
    a possible store-selection gate blocking the grid: there wasn't one,
    at least not for a plain search.
  - No bot-challenge / block of any kind was encountered on lidl.com in
    this capture — a clean, real success end to end.
  - Pagination is a "View More Products" click-to-load button (48 results
    per page — confirmed via a real 142-result "milk" search), combined
    with PER-TILE lazy rendering: a tile starts as a skeleton
    (`.s-grid-box-skeleton`) and only fills in with real content once
    scrolled near-into-view. A scroll-and-wait loop should therefore work
    for tiles already below the fold, but reaching results beyond the
    first ~48 may need the "View More Products" control clicked, not just
    scrolled to — UNCONFIRMED whether scrolling alone ever triggers it via
    its own intersection observer.
  - Still UNCONFIRMED after this capture: the exact shape of a discounted
    ("weekly deal") tile — none of the tiles captured this session had a
    strikethrough/original price, so `original_price`/`discount_pct`/
    `is_weekly_deal` extraction from a real tile remains a best-effort
    guess (`_TODO_ORIGINAL_PRICE_SELECTORS` below), and how lidl.com keys
    a session to a specific store/zip (cookie? query param? the store-
    picker's own POST?) is still unconfirmed — `--zip`/`--store-id` are
    still accepted and threaded through to `Product` so a run at least
    records which region was asked for, not necessarily which one the
    site actually used.
  - Sibling repo note, confirmed the same session: skyscanner.com served
    an immediate, real PerimeterX "Are you a person or a robot?" bot
    challenge to a plain browser visit — re-confirming the incident
    already on record in skyscanner-scraper's own CHANGELOG, not a new
    finding, but worth noting here as the contrast: lidl.com had nothing
    like it.

Parsing strategy, in priority order (updated to match the confirmed shape
above, family principle unchanged — embedded-data-first, DOM fallback):

  1. `extract_gridbox_products()` — the confirmed-real `data-gridbox-
     impression` DOM attribute + sibling text, for search-RESULTS
     listings. This is the new primary path.
  2. `application/ld+json` schema.org `Product` — confirmed real, but on
     product-DETAIL pages, not listings; kept first-among-JSON paths for
     when this parser is pointed at a `/p/.../p<id>` URL directly, or in
     case a future listing page variant does carry one.
  3. `__NEXT_DATA__` — confirmed WRONG framework guess (site is Nuxt, not
     Next); kept only as a harmless no-op (the id it looks for doesn't
     exist on this site) documented dead end, not a real path.
  4. DOM fallback — updated to the confirmed-real selectors above, used
     when data-gridbox-impression itself is absent (e.g. site markup
     changes) but the DOM still resembles a product grid.

All paths feed the same `Product` shape from `output_writer.py`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlencode, urlparse

from bs4 import BeautifulSoup

from output_writer import Product

log = logging.getLogger("lidl_parser")

BASE_URL = "https://www.lidl.com"
SOURCE = "lidl.com"

SORT_VALUES = ("relevance", "price_asc", "price_desc")  # TODO: verify live — the real search
                                                          # UI's sort control/param name is still
                                                          # unconfirmed (see module docstring;
                                                          # only `q` itself was confirmed).

MIN_CARD_MATCHES = 2  # per family invariant (stockx-scraper's product_parser.py /
                       # skyscanner-scraper's flight_parser.py MIN_CARD_MATCHES):
                       # must be >1, or one unrelated link resolves a "rendered" wait early.

# No live capture of a lidl.com BOT-CHALLENGE page exists (confirmed —
# a real capture on 2026-09-20 hit no block of any kind, see module
# docstring). Left empty on purpose, same starting state skyscanner-
# scraper's flight_parser.py had before its own first live incident —
# `captcha_solver.GENERIC_BOT_CHALLENGE_MARKERS` (Cloudflare Turnstile,
# reCAPTCHA, hCaptcha, PerimeterX, DataDome, "just a moment"...) already
# covers the common cases generically; site-specific corroborating
# markers get added here the moment a real one is captured, per TESTING.md.
BOT_CHALLENGE_MARKERS: tuple = ()


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #
def search_url(
    *,
    query: Optional[str] = None,
    category: Optional[str] = None,
    zip_code: Optional[str] = None,
    sort: str = "relevance",
) -> str:
    """Builds a lidl.com product-search or category-browse URL.

    Confirmed real (2026-09-20 browser capture): a plain search is
    `https://www.lidl.com/q/search?q=<query>` — the `q` param name and
    `/q/search` path are both taken straight from the real request the
    site's own search box issued, not guessed. `category` is expected to
    already be in the site's own confirmed-real shape, `{slug}/s{id}`
    (e.g. `"food-wine/s10068374"`, taken verbatim from a homepage nav
    link) — this function does not further validate or reshape it.
    `sort`/`zip` remain best-effort guesses (# TODO: verify live) at how
    they'd be expressed as query params; this capture only exercised a
    plain query with neither. Exactly one of `query`/`category` is
    expected — `query` takes priority if both are given, matching
    stockx-scraper/skyscanner-scraper's own "most specific input wins"
    convention for overlapping selector inputs.
    """
    if not query and not category:
        raise ValueError("search_url() needs a query or a category id")

    if query:
        path = "/q/search"
        params: Dict[str, str] = {"q": query.strip()}
    else:
        category_path = category.strip().strip("/")  # type: ignore[union-attr]
        path = f"/c/{category_path}"
        params = {}

    if sort and sort != "relevance":
        params["sort"] = sort
    if zip_code:
        # TODO: verify live — completely unconfirmed whether lidl.com reads
        # a store/region from a query param at all, vs. a cookie set only
        # after a UI interaction (the store-picker flyer's own request was
        # not captured this session). Included so a future capture can
        # confirm or refute this in one run rather than needing a second
        # guess added later.
        params["zip"] = zip_code

    return f"{BASE_URL}{path}" + (f"?{urlencode(params)}" if params else "")


def is_search_url(url: str) -> bool:
    path = urlparse(url).path
    return path.startswith("/q/search") or path.startswith("/c/") or path.startswith("/p/")


# --------------------------------------------------------------------------- #
# sku — unlike Skyscanner's itineraries, a Lidl catalog item genuinely has a
# stable identity. Prefer the site's own product id / GTIN when the parser
# can extract one; fall back to a deterministic fingerprint of the product
# URL (NEVER a random/run-scoped id) only when it can't, so diff_runs.py
# still sees the same sku for the same item across two different runs.
# --------------------------------------------------------------------------- #
def make_sku(*, product_id: Optional[str], product_url: Optional[str]) -> str:
    if product_id:
        return f"lidl-{product_id}"
    basis = product_url or ""
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"lidl-url-{digest}"


def _clean_product_url(href: Optional[str]) -> Optional[str]:
    """Strips the real site's `#searchTrackingMasterId=...` fragment (and
    resolves a relative href) — confirmed real: the fragment carries
    per-search/per-position tracking params that differ even for the
    identical product, so leaving it in would make the same item look
    like a different URL across two runs."""
    if not href:
        return None
    href = href.split("#", 1)[0]
    if not href:
        return None
    return href if href.startswith("http") else f"{BASE_URL}{href}"


# --------------------------------------------------------------------------- #
# Path 1 (primary): the confirmed-real `data-gridbox-impression` DOM
# attribute, for search-RESULTS listings — see module docstring.
# --------------------------------------------------------------------------- #
_GRIDBOX_SELECTOR = ".product-grid-box, .odsc-tile[data-gridbox-impression]"

_UNIT_FOOTER_RE = re.compile(
    r"^(?P<size>.*?)\s*\$\s*(?P<unit_price>[\d.,]+)\s*per\s*(?P<unit>.+)$", re.IGNORECASE
)
_MAIN_PRICE_RE = re.compile(r"[\d][\d.,]*")


def _split_unit_footer(text: Optional[str]) -> tuple:
    """Splits the confirmed-real concatenated footer text (e.g.
    `"128 fl.oz.$ 0.03 per fl.oz."`) into `(unit_size, unit_price)`. The
    size and per-unit price are two separate child elements on the real
    page with no separator between their text nodes, hence the regex
    rather than a simple split."""
    if not text:
        return None, None
    m = _UNIT_FOOTER_RE.match(text.strip())
    if not m:
        return None, None
    size = m.group("size").strip() or None
    try:
        unit_price = float(m.group("unit_price").replace(",", ""))
    except ValueError:
        unit_price = None
    return size, unit_price


def _parse_main_price(text: Optional[str]) -> Optional[float]:
    """Confirmed real: the current price renders as e.g. `"$3.30*"` — the
    trailing `*` is a footnote marker (seen on every tile), not part of
    the number, and is simply not matched by the digit regex."""
    if not text:
        return None
    m = _MAIN_PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def extract_gridbox_products(
    html: str, *, zip_code: Optional[str] = None, store_id: Optional[str] = None,
    max_results: Optional[int] = None,
) -> List[Product]:
    """Primary listing-page path — confirmed real 2026-09-20. Reads the
    `data-gridbox-impression` attribute (id/name/brand/category/price)
    plus the sibling visible-text elements this attribute doesn't carry
    (unit price/size), for every tile in a rendered search-results page.

    `original_price`/`discount_pct`/`is_weekly_deal` are left `None`/
    `False` here — no discounted tile was captured this session to
    confirm that shape against (see module docstring); a future capture
    of an actual weekly-deal tile should fill this in rather than guess.
    """
    soup = BeautifulSoup(html, "html.parser")
    tiles = soup.select(_GRIDBOX_SELECTOR)
    products: List[Product] = []

    for tile in tiles:
        raw = tile.get("data-gridbox-impression")
        impression: Dict[str, Any] = {}
        if raw:
            try:
                impression = json.loads(unquote(raw))
            except (ValueError, TypeError):
                impression = {}

        link = tile.select_one('a[href^="/p/"]')
        product_url = _clean_product_url(link.get("href") if link else None)

        title = None
        title_el = tile.select_one(".product-grid-box__title")
        if title_el:
            title = title_el.get_text(strip=True)
        title = title or impression.get("name")

        brand = None
        brand_el = tile.select_one('[class*="brand" i]')
        if brand_el:
            brand = brand_el.get_text(strip=True)
        brand = brand or impression.get("brand")

        price = _parse_main_price(
            (tile.select_one(".ods-price__main-wrapper") or tile).get_text(strip=True)
            if tile.select_one(".ods-price__main-wrapper") else None
        )
        if price is None:
            # data-gridbox-impression carries a clean numeric price even
            # when the visible-text selector above doesn't match (e.g. a
            # markup variant) — confirmed present on every real tile.
            raw_price = impression.get("price")
            if isinstance(raw_price, (int, float)):
                price = float(raw_price)
        if price is None:
            continue  # no usable price anywhere on this tile — skip rather than fabricate a row

        unit_size, unit_price = _split_unit_footer(
            tile.select_one(".ods-price__footer").get_text(strip=True)
            if tile.select_one(".ods-price__footer") else None
        )

        product_id = impression.get("id")
        category = impression.get("categoryPrimary") or impression.get("category")

        products.append(Product(
            sku=make_sku(product_id=str(product_id) if product_id else None, product_url=product_url),
            source=SOURCE,
            category=category,
            title=title,
            brand=brand,
            price=price,
            currency="USD",
            price_source="dom",
            product_url=product_url,
            image_url=None,  # TODO: verify live — image element/attribute for a gridbox tile
                              # was not captured this session (only price/title/link were).
            scraped_at=_now_iso(),
            unit_price=unit_price,
            unit_size=unit_size,
            original_price=None,   # TODO: verify live — see module docstring
            discount_pct=None,     # TODO: verify live — see module docstring
            is_weekly_deal=None,   # TODO: verify live — see module docstring
            deal_valid_from=None,
            deal_valid_until=None,
            store_id=store_id,
            zip_code=zip_code,
        ))
        if max_results and len(products) >= max_results:
            break

    return products


# --------------------------------------------------------------------------- #
# Path 2: schema.org Product structured data (application/ld+json) —
# confirmed real on PRODUCT-DETAIL pages (`/p/{slug}/p{id}`), NOT on
# search-results listings (those carry only an `Organization` block; see
# module docstring). Kept as a real, working path for whenever this
# parser is pointed at a product URL directly.
# --------------------------------------------------------------------------- #
def extract_json_ld(html: str) -> List[dict]:
    """Returns every parseable `application/ld+json` blob on the page, with
    `@graph` arrays flattened into the top-level list — a common pattern
    for pages that bundle several structured-data types (Product,
    BreadcrumbList, Organization, ...) into one script tag."""
    soup = BeautifulSoup(html, "html.parser")
    blobs: List[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("@graph"), list):
                blobs.extend(g for g in item["@graph"] if isinstance(g, dict))
            else:
                blobs.append(item)
    return blobs


def _schema_type(node: dict) -> str:
    t = node.get("@type")
    if isinstance(t, list):
        return "|".join(str(x) for x in t)
    return str(t or "")


def _find_products_in_json_ld(blobs: List[dict]) -> List[dict]:
    """A product-detail page's own structured data lists a `Product`
    directly (confirmed real shape); an `ItemList` wrapping one is also
    handled in case a future listing-page variant uses it (unconfirmed
    for lidl.com's current search-results page, which carries neither)."""
    products: List[dict] = []
    for node in blobs:
        if "Product" in _schema_type(node):
            products.append(node)
        elif "ItemList" in _schema_type(node):
            for element in node.get("itemListElement", []) or []:
                if not isinstance(element, dict):
                    continue
                item = element.get("item") if isinstance(element.get("item"), dict) else element
                if isinstance(item, dict) and "Product" in _schema_type(item):
                    products.append(item)
    return products


def _price_from_offers(node: dict) -> tuple:
    """Returns (price, currency, original_price, discount_pct,
    deal_valid_until) from a schema.org `offers` block — confirmed real
    shape on a product-detail page is a single-item `offers` LIST with
    `price`/`priceCurrency`/`itemCondition`/`availability`; a bare dict is
    also accepted for robustness."""
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), None)
    if not isinstance(offers, dict):
        return None, None, None, None, None

    def _f(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    price = _f(offers.get("price"))
    currency = offers.get("priceCurrency")
    valid_until = offers.get("priceValidUntil")

    # TODO: verify live — the one real product-detail page captured this
    # session had no discount, so a pre-discount "was" price's real
    # property name is still unconfirmed; `highPrice` (on an
    # AggregateOffer) and a nested `priceSpecification` are both
    # plausible schema.org shapes, tried here before giving up.
    original_price = _f(offers.get("highPrice"))
    if original_price is None:
        spec = offers.get("priceSpecification")
        if isinstance(spec, dict):
            original_price = _f(spec.get("price"))
    discount_pct = None
    if price is not None and original_price and original_price > price:
        discount_pct = round((1 - price / original_price) * 100, 1)

    return price, currency, original_price, discount_pct, valid_until


def _unit_price_from_node(node: dict) -> tuple:
    """# TODO: verify live — the real product-detail page captured this
    session put unit size in `description` (`"<ul><li>16 oz.</li></ul>"`)
    and no unit PRICE at all in its JSON-LD; a `UnitPriceSpecification` is
    still tried first as the standard schema.org shape in case another
    product page does emit one, then a crude fallback reads it out of
    `description`. Returns (unit_price, unit_size)."""
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), None)
    if isinstance(offers, dict):
        spec = offers.get("priceSpecification")
        specs = spec if isinstance(spec, list) else [spec] if spec else []
        for s in specs:
            if isinstance(s, dict) and "UnitPriceSpecification" in str(s.get("@type", "")):
                try:
                    unit_price = float(s.get("price"))
                except (TypeError, ValueError):
                    unit_price = None
                unit_size = s.get("unitText") or s.get("unitCode")
                return unit_price, unit_size

    description = node.get("description")
    if isinstance(description, str):
        text = BeautifulSoup(description, "html.parser").get_text(strip=True)
        if text:
            return None, text
    return None, None


def _product_id_from_node(node: dict) -> Optional[str]:
    for key in ("sku", "gtin13", "gtin", "gtin12", "mpn", "productID"):
        val = node.get(key)
        if val:
            return str(val)
    return None


def _brand_from_node(node: dict) -> Optional[str]:
    brand = node.get("brand")
    if isinstance(brand, dict):
        return brand.get("name")
    if isinstance(brand, str):
        return brand
    return None


def _json_ld_node_to_product(node: dict, *, zip_code: Optional[str], store_id: Optional[str]) -> Optional[Product]:
    title = node.get("name")
    price, currency, original_price, discount_pct, deal_valid_until = _price_from_offers(node)
    if price is None:
        return None  # no usable price on this node — skip rather than fabricate a row

    product_id = _product_id_from_node(node)
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), None)
    offers_url = offers.get("url") if isinstance(offers, dict) else None
    product_url = _clean_product_url(node.get("url") or offers_url)
    image = node.get("image")
    if isinstance(image, list):
        image = next((item for item in image if isinstance(item, (str, dict))), None)
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")

    unit_price, unit_size = _unit_price_from_node(node)

    return Product(
        sku=make_sku(product_id=product_id, product_url=product_url),
        source=SOURCE,
        category=node.get("category"),
        title=title,
        brand=_brand_from_node(node),
        price=price,
        currency=currency,
        price_source="embedded_json",
        product_url=product_url,
        image_url=image,
        scraped_at=_now_iso(),
        unit_price=unit_price,
        unit_size=unit_size,
        original_price=original_price,
        discount_pct=discount_pct,
        is_weekly_deal=bool(original_price and discount_pct),
        deal_valid_from=None,
        deal_valid_until=deal_valid_until,
        store_id=store_id,
        zip_code=zip_code,
    )


# --------------------------------------------------------------------------- #
# Path 3: __NEXT_DATA__ — CONFIRMED WRONG framework guess (the site is
# Nuxt.js, id `__NUXT_DATA__`, a `devalue`-serialized payload — not React/
# Next's plain-JSON `__NEXT_DATA__`). Kept only as a harmless, documented
# dead end: the id this looks for does not exist on lidl.com, so it always
# returns None here rather than ever being wired into a real parse path.
# Real Nuxt-payload support (parsing the actual `devalue` format) is not
# implemented — it would need reimplementing Nuxt's own deserializer, and
# nothing found in this capture required it (the DOM data-attribute path
# in `extract_gridbox_products()` covers what was needed instead).
# --------------------------------------------------------------------------- #
def extract_next_data(html: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except (ValueError, TypeError):
        log.debug("__NEXT_DATA__ present but not parseable as JSON")
        return None


_PRICE_KEYS = ("price", "currentPrice", "unitPrice", "minPrice")
_PRODUCT_SHAPE_KEYS = ("sku", "gtin", "ean", "productId", "brand", "title", "name")


def _looks_like_product(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    has_price = any(k in node for k in _PRICE_KEYS)
    has_product_shape = any(k in node for k in _PRODUCT_SHAPE_KEYS)
    return has_price and has_product_shape


def _find_product_lists(node: Any, depth: int = 0, found: Optional[List[List[dict]]] = None) -> List[List[dict]]:
    if found is None:
        found = []
    if depth > 14:
        return found
    if isinstance(node, list):
        if node:
            matches = sum(1 for item in node if _looks_like_product(item))
            if matches >= max(1, len(node) // 2):
                found.append([item for item in node if isinstance(item, dict)])
        for item in node:
            _find_product_lists(item, depth + 1, found)
    elif isinstance(node, dict):
        for value in node.values():
            _find_product_lists(value, depth + 1, found)
    return found


def _num(node: Any, *path: str) -> Optional[float]:
    cur = node
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, (int, float)):
        return float(cur)
    if isinstance(cur, str):
        try:
            return float(cur.replace(",", ""))
        except ValueError:
            return None
    return None


def _next_data_node_to_product(node: dict, *, zip_code: Optional[str], store_id: Optional[str]) -> Optional[Product]:
    """Dead-end path (see block docstring above) — never reached via a
    real lidl.com page, kept only so `_find_product_lists`'s generic
    shape-walk has somewhere to feed if it's ever repurposed."""
    price = _num(node, "price") or _num(node, "currentPrice") or _num(node, "price", "value")
    if price is None:
        return None

    product_id = node.get("sku") or node.get("gtin") or node.get("ean") or node.get("productId")
    title = node.get("title") or node.get("name")
    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    product_url = _clean_product_url(node.get("url") or node.get("link"))
    image = node.get("image") or node.get("imageUrl")
    if isinstance(image, list):
        image = image[0] if image else None

    original_price = _num(node, "originalPrice") or _num(node, "regularPrice")
    discount_pct = None
    if original_price and original_price > price:
        discount_pct = round((1 - price / original_price) * 100, 1)

    return Product(
        sku=make_sku(product_id=str(product_id) if product_id else None, product_url=product_url),
        source=SOURCE,
        category=node.get("category"),
        title=title,
        brand=brand,
        price=price,
        currency=node.get("currency") or "USD",
        price_source="embedded_json",
        product_url=product_url,
        image_url=image,
        scraped_at=_now_iso(),
        unit_price=_num(node, "unitPrice"),
        unit_size=node.get("unitSize") or node.get("packSize"),
        original_price=original_price,
        discount_pct=discount_pct,
        is_weekly_deal=bool(node.get("isPromo") or node.get("isWeeklyDeal") or discount_pct),
        deal_valid_from=node.get("validFrom"),
        deal_valid_until=node.get("validUntil") or node.get("validTo"),
        store_id=store_id,
        zip_code=zip_code,
    )


# --------------------------------------------------------------------------- #
# Path 4: generic DOM fallback — used only when data-gridbox-impression
# itself is absent (a markup change on the real site). Selectors updated
# to the confirmed-real ones from `extract_gridbox_products()`'s own
# discovery, plus the family's older generic guesses kept as a second
# tier in case a future page variant looks more like those instead.
# --------------------------------------------------------------------------- #
_CARD_SELECTORS = (
    '[id^="grid-item-"]',
    ".product-grid-box",
    ".odsc-tile",
    '[data-testid="product-tile"]',
    '[data-testid="product-card"]',
    '[class*="ProductTile"]',
    ".product-grid-item",
    ".product-tile",
)
_TITLE_SELECTORS = (".product-grid-box__title", '[data-testid="product-title"]', ".product-title", "h3", "h2")
_PRICE_SELECTORS = (".ods-price__main-wrapper", '[data-testid="price"]', ".price", '[class*="Price"]')
_BRAND_SELECTORS = ('[class*="brand" i]', '[data-testid="product-brand"]', ".product-brand", ".brand")
_IMAGE_SELECTORS = ("img",)
_LINK_SELECTORS = ('a[href^="/p/"]', 'a[href*="/p/"]', "a[href]")


def _first_text(card, selectors) -> Optional[str]:
    for sel in selectors:
        found = card.select_one(sel)
        if found:
            text = found.get_text(strip=True)
            if text:
                return text
    return None


def count_result_cards(html: str) -> int:
    """Used by captcha_solver.solve_when_blocked — cheap presence check,
    no readiness wait."""
    soup = BeautifulSoup(html, "html.parser")
    for sel in _CARD_SELECTORS:
        matches = soup.select(sel)
        if matches:
            return len(matches)
    return 0


_TOTAL_RESULTS_RE = re.compile(r"\b([\d,]+)\s+Products?\b", re.IGNORECASE)


def total_result_count(html: str) -> Optional[int]:
    """Read Lidl's own catalogue total (for example ``142 Products``).

    This is used as an arithmetic completeness check. Lidl virtualizes its
    grid, so the final DOM can truthfully say 142 products while only eight
    cards are materialized at that exact scroll position.
    """
    soup = BeautifulSoup(html, "html.parser")
    label = soup.select_one(".s-products-count__label")
    text = label.get_text(" ", strip=True) if label else soup.get_text(" ", strip=True)
    match = _TOTAL_RESULTS_RE.search(text)
    return int(match.group(1).replace(",", "")) if match else None


def _parse_result_cards_from_dom(
    html: str, *, zip_code: Optional[str], store_id: Optional[str], max_results: Optional[int],
) -> List[Product]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for sel in _CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            break
    if not cards:
        return []

    products: List[Product] = []
    for card in cards:
        price = _parse_main_price(_first_text(card, _PRICE_SELECTORS))
        if price is None:
            continue  # no usable price on this card — skip rather than fabricate a row
        title = _first_text(card, _TITLE_SELECTORS)
        brand = _first_text(card, _BRAND_SELECTORS)

        image = None
        img_tag = card.select_one(_IMAGE_SELECTORS[0])
        if img_tag:
            image = img_tag.get("src") or img_tag.get("data-src")

        product_url = None
        for sel in _LINK_SELECTORS:
            a = card.select_one(sel)
            if a and a.get("href"):
                product_url = _clean_product_url(a["href"])
                break

        products.append(Product(
            sku=make_sku(product_id=None, product_url=product_url),
            source=SOURCE,
            category=None,
            title=title,
            brand=brand,
            price=price,
            currency="USD",
            price_source="dom",
            product_url=product_url,
            image_url=image,
            scraped_at=_now_iso(),
            store_id=store_id,
            zip_code=zip_code,
        ))
        if max_results and len(products) >= max_results:
            break
    return products


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
@dataclass
class SearchResult:
    products: List[Product]
    source_used: str  # "dom_gridbox" | "embedded_json" | "dom" | "none"


def parse_search_results(
    html: str, *, zip_code: Optional[str] = None, store_id: Optional[str] = None,
    max_results: Optional[int] = None,
) -> SearchResult:
    gridbox_products = extract_gridbox_products(
        html, zip_code=zip_code, store_id=store_id, max_results=max_results,
    )
    if gridbox_products:
        return SearchResult(products=gridbox_products, source_used="dom_gridbox")

    json_ld_blobs = extract_json_ld(html)
    product_nodes = _find_products_in_json_ld(json_ld_blobs)
    if product_nodes:
        products = []
        for node in product_nodes:
            p = _json_ld_node_to_product(node, zip_code=zip_code, store_id=store_id)
            if p is not None:
                products.append(p)
            if max_results and len(products) >= max_results:
                break
        if products:
            return SearchResult(products=products, source_used="embedded_json")

    # __NEXT_DATA__ intentionally not attempted here — confirmed dead end,
    # see the Path 3 block docstring above.

    dom_products = _parse_result_cards_from_dom(
        html, zip_code=zip_code, store_id=store_id, max_results=max_results,
    )
    if dom_products:
        return SearchResult(products=dom_products, source_used="dom")

    return SearchResult(products=[], source_used="none")


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_parse_search_results(html: str, **kwargs) -> SearchResult:
    """Public engine entry point. Wraps `parse_search_results` so an
    unexpected exception INSIDE parsing (a round whose markup doesn't match
    either extraction path in some new way `parse_search_results` itself
    doesn't already guard against) degrades that ONE round to "found
    nothing new here" instead of propagating out of an engine's round loop
    and crashing the whole run — which would discard every product already
    collected in earlier rounds. This is the same family-wide invariant
    (CLAUDE.md §6/§10) as skyscanner-scraper's `flight_parser.
    safe_parse_search_results` and stockx-scraper's per-engine `safe_parse`
    closures: one bad round is a reason to log loudly and move on, not to
    lose everything gathered so far. The exception is logged at ERROR level
    with its message so a real parser defect is never silently confused
    with a legitimately empty page — a suspiciously high rate of
    `source_used == "none"` in the logs across many rounds/runs is the
    signal to investigate, not proof the site changed. All three engines
    call this instead of `parse_search_results` directly.
    """
    try:
        return parse_search_results(html, **kwargs)
    except Exception as exc:  # noqa: BLE001 — see docstring above
        log.error("A round's HTML failed to parse — treating it as empty, not crashing: %s", exc)
        return SearchResult(products=[], source_used="none")
