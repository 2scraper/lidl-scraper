#!/usr/bin/env python3
"""lidl_parser.py — this IS the lidl.com site knowledge (the grocery-family
analog of stockx-scraper's product_parser.py and skyscanner-scraper's
flight_parser.py).

**Honesty note, read before trusting anything below** (CLAUDE.md §15: an
unverified site gets that stated plainly, not glossed over):

  - `robots.txt` on lidl.com was fetched successfully and is WIDE OPEN —
    `User-agent: *` / `Disallow:` (empty) / `Sitemap: https://www.lidl.com
    /sitemap.xml` — unlike skyscanner-scraper, there is no robots.txt
    restriction standing between this repo and the live site.
  - The homepage (`https://www.lidl.com`) was fetched successfully in this
    environment and confirms the site is a real e-commerce storefront:
    product categories ("Food & Wine", "Kitchen & Household", "DIY &
    Garden", ...), a "Weekly Deals" section, and a "Lidl Plus" loyalty
    program are genuinely present.
  - Every deeper URL this repo's own build environment tried — a guessed
    `/search/products/{query}` (a URL of this EXACT shape is independently
    confirmed real: Google's own index has `lidl.com/search/products/weekly
    ad` and `lidl.com/specials?category=<hex-id>` pages titled "Search
    Results" / "Fresh Deals" / "Clothing" — so the path scheme below is a
    confirmed-real URL family, not invented), a guessed `/specials?category
    =<hex-id>` (copied from one of those indexed URLs verbatim), and a
    guessed `/sitemap.xml` / `/stores/{id}` — all returned a plain HTTP 404
    to this environment's non-browser fetch. No bot-challenge marker
    (`captcha_solver.GENERIC_BOT_CHALLENGE_MARKERS`) was present in any of
    those 404 bodies — this looks like "a static fetch can't drive this
    app's client-side router" (the homepage may be the only
    server-rendered / statically-generated route, with everything else a
    client-side SPA route Google can index via its own JS-rendering
    crawler but a plain `GET` cannot), NOT evidence of active bot-blocking
    the way skyscanner.com's PerimeterX challenge was. That distinction
    matters for how this repo's engines should behave — a 404 here is not
    treated as `EXIT_BLOCKED` (see `MIN_CARD_MATCHES`/`count_result_cards`
    below) — but it is still unconfirmed against a real rendered page.
  - Net effect: the URL *scheme* below is real (confirmed via Google's own
    index of live lidl.com pages); the exact query-string params, the
    embedded-JSON shape, and every DOM selector are best-effort guesses,
    every one marked `# TODO: verify live`. Nothing here has been checked
    against lidl.com markup actually rendered by a browser. See
    `TESTING.md` for the checklist that closes this gap for real.
  - US grocery pricing is commonly store/region-scoped. How lidl.com
    actually keys a session to a store/zip (a cookie? a query param? a
    modal that must be dismissed first?) is UNCONFIRMED — `--zip`/
    `--store-id` below are accepted and threaded through to `Product`
    so a captured run at least RECORDS which region a price came from,
    but no engine here has been confirmed to successfully set one yet.

Parsing strategy, in priority order (family principle: embedded-data-first,
DOM fallback — see stockx-scraper's `product_parser.py` and skyscanner-
scraper's `flight_parser.py` for the same shape applied to their own sites):

  1. `application/ld+json` schema.org `Product` (and `ItemList`/
     `BreadcrumbList` wrapping one) blocks — NOT a guess in the same sense
     as the rest of this file: structured Product markup is a standard,
     widely-documented SEO practice for e-commerce sites (Google's own
     "Product structured data" guidelines), so it is the single most
     likely place lidl.com exposes clean price/brand/sku data even before
     any lidl.com-specific shape is confirmed. Still marked `# TODO: verify
     live` for the exact property names lidl.com actually populates.
  2. `__NEXT_DATA__` (a common but UNCONFIRMED framework guess for
     lidl.com, unlike skyscanner.com where it's public knowledge the site
     is Next.js-based) — walked generically for any list of dicts that
     LOOKS like a product-result list, the same generic-shape-walk
     technique as `flight_parser._find_itinerary_lists`.
  3. DOM fallback: a handful of guessed `data-testid`/class selectors.

All three paths feed the same `Product` shape from `output_writer.py`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urlparse

from bs4 import BeautifulSoup

from output_writer import Product

log = logging.getLogger("lidl_parser")

BASE_URL = "https://www.lidl.com"
SOURCE = "lidl.com"

SORT_VALUES = ("relevance", "price_asc", "price_desc")  # TODO: verify live — guessed values;
                                                          # lidl.com's real sort UI/param names
                                                          # are unconfirmed (see module docstring).

MIN_CARD_MATCHES = 2  # per family invariant (stockx-scraper's product_parser.py /
                       # skyscanner-scraper's flight_parser.py MIN_CARD_MATCHES):
                       # must be >1, or one unrelated link resolves a "rendered" wait early.

# No live capture of a lidl.com BOT-CHALLENGE page exists yet (see module
# docstring — the 404s hit so far carried no captcha/challenge marker, so
# they are not treated as one). Left empty on purpose, same starting state
# skyscanner-scraper's flight_parser.py had before its own first live
# incident — `captcha_solver.GENERIC_BOT_CHALLENGE_MARKERS` (Cloudflare
# Turnstile, reCAPTCHA, hCaptcha, PerimeterX, DataDome, "just a moment"...)
# already covers the common cases generically; site-specific corroborating
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
    """Builds a lidl.com product-search or specials-category URL.

    # TODO: verify live — the PATH shape (`/search/products/{query}` and
    `/specials?category={hex-id}`) is confirmed real (see module
    docstring); the QUERY PARAMS added here (`sort`, `zip`) are this
    repo's own best-effort guess at how lidl.com's real UI would express
    them, not a confirmed live response. Exactly one of `query`/`category`
    is expected — `query` takes priority if both are given, matching
    stockx-scraper/skyscanner-scraper's own "most specific input wins"
    convention for overlapping selector inputs.
    """
    if not query and not category:
        raise ValueError("search_url() needs a query or a category id")

    if query:
        path = f"/search/products/{quote(query.strip())}"
        params: Dict[str, str] = {}
    else:
        path = "/specials"
        params = {"category": category}  # type: ignore[dict-item]

    if sort and sort != "relevance":
        params["sort"] = sort
    if zip_code:
        # TODO: verify live — completely unconfirmed whether lidl.com reads
        # a store/region from a query param at all, vs. a cookie set only
        # after a UI interaction. Included so a capture can confirm or
        # refute this in one run rather than needing a second guess added
        # later.
        params["zip"] = zip_code

    return f"{BASE_URL}{path}" + (f"?{urlencode(params)}" if params else "")


def is_search_url(url: str) -> bool:
    path = urlparse(url).path
    return path.startswith("/search/products/") or path.startswith("/specials")


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


# --------------------------------------------------------------------------- #
# Path 1: schema.org Product structured data (application/ld+json)
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
    """A search/category page's own structured data may list products
    directly as top-level `Product` nodes, or wrap them in an `ItemList`
    whose `itemListElement[].item` is the actual `Product` — both shapes
    are legitimate schema.org, so both are checked. # TODO: verify live —
    which shape (if either) lidl.com actually uses is unconfirmed."""
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
    deal_valid_until) from a schema.org `offers` block — a single `Offer`
    dict or a list of them (e.g. one per store/condition)."""
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
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

    # TODO: verify live — a pre-discount "was" price has no single
    # standard schema.org property; `highPrice` (on an AggregateOffer) and
    # a nested `priceSpecification` are both plausible on a real grocery
    # site's markup, so both are tried before giving up.
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
    """# TODO: verify live — `UnitPriceSpecification` is the standard
    schema.org shape for a per-unit price ($/lb, $/oz), but whether
    lidl.com actually emits one is unconfirmed. Returns (unit_price,
    unit_size)."""
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, None
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
    product_url = node.get("url")
    if product_url and not str(product_url).startswith("http"):
        product_url = f"{BASE_URL}{product_url}"
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")

    unit_price, unit_size = _unit_price_from_node(node)

    return Product(
        sku=make_sku(product_id=product_id, product_url=product_url),
        source=SOURCE,
        category=node.get("category"),
        title=title,
        brand=_brand_from_node(node),
        price=price,
        currency=currency or "USD",
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
# Path 2: __NEXT_DATA__ (or any similarly-shaped embedded state) — generic
# product-shaped-list walk, same technique as flight_parser._find_itinerary_lists
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
    """# TODO: verify live — field names below are a best-effort guess at a
    plausible product-tile shape, not a confirmed one. Returns None rather
    than a half-populated Product if not even a price can be found."""
    price = _num(node, "price") or _num(node, "currentPrice") or _num(node, "price", "value")
    if price is None:
        return None

    product_id = node.get("sku") or node.get("gtin") or node.get("ean") or node.get("productId")
    title = node.get("title") or node.get("name")
    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    product_url = node.get("url") or node.get("link")
    if product_url and not str(product_url).startswith("http"):
        product_url = f"{BASE_URL}{product_url}"
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
# Path 3: DOM fallback — candidate selectors are best-effort guesses,
# unconfirmed against a live page (see module docstring).
# --------------------------------------------------------------------------- #
_CARD_SELECTORS = (
    '[data-testid="product-tile"]',
    '[data-testid="product-card"]',
    '[class*="ProductTile"]',
    ".product-grid-item",
    ".product-tile",
)
_TITLE_SELECTORS = ('[data-testid="product-title"]', ".product-title", "h3", "h2")
_PRICE_SELECTORS = ('[data-testid="price"]', ".price", '[class*="Price"]')
_BRAND_SELECTORS = ('[data-testid="product-brand"]', ".product-brand", ".brand")
_IMAGE_SELECTORS = ("img",)
_LINK_SELECTORS = ('a[href*="/p/"]', "a[href]")

_PRICE_TEXT_RE = re.compile(r"[\d][\d.,]*")


def _first_text(card, selectors) -> Optional[str]:
    for sel in selectors:
        found = card.select_one(sel)
        if found:
            text = found.get_text(strip=True)
            if text:
                return text
    return None


def _parse_price_text(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = _PRICE_TEXT_RE.search(text.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
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
        price = _parse_price_text(_first_text(card, _PRICE_SELECTORS))
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
                href = a["href"]
                product_url = href if href.startswith("http") else f"{BASE_URL}{href}"
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
    source_used: str  # "embedded_json" | "dom" | "none"


def parse_search_results(
    html: str, *, zip_code: Optional[str] = None, store_id: Optional[str] = None,
    max_results: Optional[int] = None,
) -> SearchResult:
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

    next_data = extract_next_data(html)
    if next_data is not None:
        candidate_lists = _find_product_lists(next_data)
        if candidate_lists:
            best = max(candidate_lists, key=len)
            products = []
            for node in best:
                p = _next_data_node_to_product(node, zip_code=zip_code, store_id=store_id)
                if p is not None:
                    products.append(p)
                if max_results and len(products) >= max_results:
                    break
            if products:
                return SearchResult(products=products, source_used="embedded_json")

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
    """Wraps `parse_search_results` so an unexpected exception INSIDE
    parsing degrades that ONE scroll/page round to "found nothing new
    here" instead of propagating out of an engine's loop and crashing the
    whole run — which would discard every product already collected in
    earlier rounds (CLAUDE.md §6/§10). All three of this repo's engines
    call this instead of `parse_search_results` directly."""
    try:
        return parse_search_results(html, **kwargs)
    except Exception as exc:  # noqa: BLE001 — see docstring above
        log.error("A page/round's HTML failed to parse — treating it as empty, not crashing: %s", exc)
        return SearchResult(products=[], source_used="none")
