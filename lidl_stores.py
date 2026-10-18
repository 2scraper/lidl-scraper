#!/usr/bin/env python3
"""lidl_stores.py — choosing the Lidl store a run prices against.

Confirmed live 2026-10-18: lidl.com prices a search by the store the
visitor picked, and the picker records that store in four first-party
cookies on www.lidl.com, set by the site's own store-search bundle
(`/s/storesearch-frontend/<version>/entry/index.js`):

  - `st`  the store number without its country prefix ("US01449" -> "1449")
  - `wh`  the store's offer region (`marketingData.offerRegion`)
  - `zn`  the store's zone (`marketingData.zone`)
  - `ar`  all of it in one URI-encoded string:
          `ar=<offerRegion>;EntityID=<st>;zip=<zip>;city=<city>;street=<street>`

Setting these before the first navigation changes the prices a search
returns: the same "whole milk" gallon read $3.48 with no store, $4.25 in
Weehawken NJ, $5.09 in Philadelphia and $2.57 in Charlotte.

The offer region and zone come from Lidl's store API, which answers 401
without the `x-apikey` header its own frontend sends. That key is public —
every visitor's browser sends it — and is read from the store-search
bundle at run time rather than copied into this repo, so a rotated key is
picked up without a release.

Some stores (US01575, New York 10001, on 2026-10-18) make every search
answer 404 with the site's "no match" page; nothing here can tell that
ahead of time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import quote

import requests

log = logging.getLogger("lidl_stores")

BASE_URL = "https://www.lidl.com"
STORES_API = "https://live.api.schwarz/odj/stores-api/v2/myapi/stores-frontend/stores"
COUNTRY = "US"
TIMEOUT_S = 20

_BUNDLE_VERSION_RE = re.compile(r"/s/storesearch-frontend/([A-Za-z0-9_.-]+)/")
_API_KEY_RE = re.compile(r"x-apikey`\s*,\s*`([A-Za-z0-9]+)`")
_STORE_ID_RE = re.compile(r"^(?:US)?0*(\d{1,5})$", re.IGNORECASE)


class StoreLookupError(Exception):
    """The store asked for does not exist — a usage mistake."""


class StoreApiError(Exception):
    """The site or its store API could not be reached or read."""


@dataclass(frozen=True)
class Store:
    object_number: str  # "US01449"
    offer_region: str
    zone: str
    zip: str
    city: str
    street: str

    @property
    def entity_id(self) -> str:
        return self.object_number[2:].lstrip("0")

    @property
    def zip5(self) -> str:
        return self.zip[:5]

    def cookies(self) -> List[Tuple[str, str]]:
        """(name, value) pairs exactly as the site's own picker writes them."""
        ar = (f"ar={self.offer_region};EntityID={self.entity_id};zip={self.zip};"
              f"city={self.city};street={self.street}")
        return [(name, _encode_uri_component(value)) for name, value in (
            ("ar", ar), ("wh", self.offer_region), ("st", self.entity_id), ("zn", self.zone),
        )]

    def describe(self) -> str:
        return f"{self.object_number} ({self.street}, {self.city} {self.zip5})"


def _encode_uri_component(value: str) -> str:
    return quote(value, safe="-_.!~*'()")


def normalise_store_id(store_id: str) -> str:
    """"1449", "01449", "US01449" and "us1449" all mean "US01449"."""
    m = _STORE_ID_RE.match(store_id.strip())
    if not m:
        raise StoreLookupError(f"--store-id {store_id!r} is not a Lidl US store number (e.g. US01449 or 1449)")
    return f"{COUNTRY}{int(m.group(1)):05d}"


def _store_from_item(item: dict) -> Store:
    md = item.get("marketingData") or {}
    address = item.get("address") or {}
    return Store(
        object_number=item["objectNumber"],
        offer_region=str(md.get("offerRegion") or ""),
        zone=str(md.get("zone") or ""),
        zip=str(address.get("zip") or ""),
        city=str(address.get("city") or ""),
        street=str(address.get("streetName") or ""),
    )


def _get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    try:
        resp = session.get(url, timeout=TIMEOUT_S, **kwargs)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise StoreApiError(f"GET {url.split('?')[0]} failed: {exc}") from None
    return resp


def fetch_api_key(session: requests.Session) -> str:
    """The `x-apikey` lidl.com's own store search sends, read from its bundle."""
    home = _get(session, f"{BASE_URL}/").text
    m = _BUNDLE_VERSION_RE.search(home)
    if not m:
        raise StoreApiError("lidl.com's homepage no longer links its store-search bundle")
    bundle = _get(session, f"{BASE_URL}/s/storesearch-frontend/{m.group(1)}/entry/index.js").text
    k = _API_KEY_RE.search(bundle)
    if not k:
        raise StoreApiError("the store-search bundle no longer carries an x-apikey")
    return k.group(1)


def _query_stores(session: requests.Session, api_key: str, params: dict) -> List[Store]:
    resp = _get(session, STORES_API, params=params, headers={"x-apikey": api_key})
    try:
        items = resp.json().get("items") or []
    except ValueError:
        raise StoreApiError("the store API answered with something other than JSON") from None
    return [_store_from_item(item) for item in items if item.get("objectNumber")]


def pick_store_for_zip(zip_code: str, stores: List[Store]) -> Tuple[Optional[Store], List[Store]]:
    """(store, candidates). An exact 5-digit match wins; otherwise the
    store with the numerically closest zip sharing the first three digits
    (the same USPS sectional centre) is taken. With no such store, `store`
    is None and `candidates` lists the five closest zips to suggest."""
    zip5 = zip_code.strip()[:5]
    exact = [s for s in stores if s.zip5 == zip5]
    if exact:
        return exact[0], exact
    if not zip5.isdigit():
        return None, []
    by_distance = sorted((s for s in stores if s.zip5.isdigit()), key=lambda s: abs(int(s.zip5) - int(zip5)))
    same_area = [s for s in by_distance if s.zip5[:3] == zip5[:3]]
    if same_area:
        return same_area[0], same_area
    return None, by_distance[:5]


def resolve_store(
    *, store_id: Optional[str] = None, zip_code: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Optional[Store]:
    """The store to price against, or None when neither flag was given.
    `store_id` wins over `zip_code`."""
    if not store_id and not zip_code:
        return None
    session = session or requests.Session()
    object_number = normalise_store_id(store_id) if store_id else None
    if zip_code and not object_number and not re.fullmatch(r"\d{5}(-\d{4})?", zip_code.strip()):
        raise StoreLookupError(f"--zip {zip_code!r} is not a US zip code")

    api_key = fetch_api_key(session)
    if object_number:
        found = _query_stores(session, api_key, {"object_number": object_number})
        if not found:
            raise StoreLookupError(f"no Lidl store {object_number}")
        return found[0]

    stores = _query_stores(session, api_key, {"limit": 250, "offset": 0, "country_code": COUNTRY})
    store, candidates = pick_store_for_zip(zip_code, stores)  # type: ignore[arg-type]
    if store is None:
        nearest = ", ".join(s.describe() for s in candidates) or "none"
        raise StoreLookupError(
            f"no Lidl store in or near zip {zip_code}; pass --store-id instead. Closest zips: {nearest}"
        )
    if store.zip5 != zip_code.strip()[:5]:  # type: ignore[union-attr]
        log.warning("No Lidl store in zip %s — using the closest one in the same area: %s",
                    zip_code, store.describe())
    elif len(candidates) > 1:
        log.info("%d Lidl stores in zip %s — using %s; pass --store-id to choose another.",
                 len(candidates), zip_code, store.describe())
    return store
