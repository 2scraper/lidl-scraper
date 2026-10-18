#!/usr/bin/env python3
"""smoke_test.py — one file of plain functions with inline/synthetic-
fixture checks. No pytest, no conftest. `tests/test_smoke.py` wraps this as
a single pytest entry point so `pytest` also works, without a second copy
of the checks.

**Honesty note, read before trusting a green run** (same caveat as every
other family member's smoke_test.py, e.g. skyscanner-scraper's): most HTML
fixtures below are SYNTHETIC — hand-written to exercise the parsing code
paths, not a real capture of lidl.com. `tests/fixtures/lidl_search_real.html`
is the one exception: it's a trimmed, hand-built reproduction of the REAL
markup shape and REAL values captured live on 2026-09-20 via an actual
rendered browser (see `lidl_parser.py`'s module docstring and TESTING.md)
— real product names/brands/prices/unit-prices, only page chrome trimmed
and tracking params simplified. A green run on the synthetic fixtures
proves the architecture (exit codes, dedupe, precedence, credential
redaction, CLI validation, engines importing cleanly) is sound; a green
run on the real-capture fixture proves `extract_gridbox_products()`'s
selectors match what the live site actually rendered, at least as of that
capture date. It does NOT prove today's live site still looks the same —
markup drifts; re-verify against TESTING.md before trusting an old green
run indefinitely.

Run directly: `python3 smoke_test.py`
"""
from __future__ import annotations

import asyncio
import inspect as _inspect
import json
import tempfile
from pathlib import Path

import captcha_solver
import diff_runs
import env_config
import lidl_parser as lp
import output_writer
import proxy_pool
import puppeteer_scraper
import scraper_api_client
import selenium_scraper

try:
    import playwright_scraper
except Exception as exc:  # pragma: no cover — this import itself must never fail
    raise AssertionError(f"playwright_scraper must import cleanly even without playwright installed: {exc}") from exc

ROOT = Path(__file__).parent

RESULTS = []  # (name, ok, detail)


def check(name):
    """Runs the decorated function IMMEDIATELY (at module-load time) and
    records the outcome — same pattern as every other family member's
    smoke_test.py; every check function is named `_` because only RESULTS
    is ever read, nothing looks a check up by name."""
    def decorator(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except AssertionError as exc:
            RESULTS.append((name, False, str(exc)))
        except Exception as exc:  # a check that crashes is still a failure, not an uncaught traceback
            RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return decorator


def asyncio_run_maybe(mod, args):
    """playwright_scraper.run()/puppeteer_scraper.run() are coroutines;
    selenium_scraper.run() is plain sync."""
    result = mod.run(args)
    if _inspect.iscoroutine(result):
        return asyncio.run(result)
    return result


# --------------------------------------------------------------------------- #
# Engine import/CLI hygiene
# --------------------------------------------------------------------------- #
@check("engines import cleanly regardless of installed drivers")
def _():
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        assert hasattr(mod, "build_arg_parser")
        assert hasattr(mod, "run")


@check("each engine imports its driver at MODULE level, guarded by try/except ImportError")
def _():
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "except ImportError as _IMPORT_ERROR" in src, f"{path}: missing guarded driver import"


@check("no forbidden overclaiming wording in any shipped .py/.md/.yml file")
def _():
    banned = (
        "cloud browser", "antidetect browser", "2scraper antidetect browser",
        "gate.2prx.com", "--antidetect", "antidetect_local_api",
    )
    exempt_names = {"smoke_test.py"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".py", ".md", ".html", ".toml", ".cfg", ".yml", ".yaml"):
            continue
        if path.name in exempt_names or path.name.startswith("2scraper"):
            continue
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for phrase in banned:
            assert phrase not in text, f"{path.relative_to(ROOT)}: contains banned phrase {phrase!r}"


@check("all three engines expose the identical --flag set")
def _():
    def flag_set(mod):
        return {opt for a in mod.build_arg_parser()._actions for opt in a.option_strings if opt.startswith("--")}

    pw, se, pu = flag_set(playwright_scraper), flag_set(selenium_scraper), flag_set(puppeteer_scraper)
    all_engines = pw | se | pu
    for name, flags in (("playwright_scraper", pw), ("selenium_scraper", se), ("puppeteer_scraper", pu)):
        missing = all_engines - flags
        assert not missing, f"{name} is missing {sorted(missing)} that (an)other engine(s) define — flag sets have drifted apart"


@check("all three engines share the same default output filename stem")
def _():
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        assert mod._default_out("json") == "lidl_results.json"


@check("all engines load to document bottom, click Lidl's View More control, and require stable height")
def _():
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "button.s-load-more__button" in src, f"{path}: confirmed live load-more button is ignored"
        assert "document.body.scrollHeight" in src, f"{path}: still uses a fixed-distance scroll"
        assert "previous_height" in src, f"{path}: height is absent from the stability decision"


@check("captcha markers only classify a page as blocked when product cards are absent")
def _():
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "captcha_detected and not cards_present" in src, (
            f"{path}: a marker can still turn a healthy product page into EXIT_BLOCKED"
        )


# --------------------------------------------------------------------------- #
# output_writer — exit codes / precedence / dedupe
# --------------------------------------------------------------------------- #
@check("exit codes and STATUS_BY_EXIT match the family contract exactly")
def _():
    expected = {0: "complete", 1: "crashed", 2: "bad_usage", 3: "blocked", 4: "empty", 5: "remote_api_error", 6: "partial"}
    assert output_writer.STATUS_BY_EXIT == expected


def _mk_product(sku, price=3.49, **kw):
    defaults = dict(
        sku=sku, source="lidl.com", category="Dairy & Eggs", title="Whole Milk, 1 Gal",
        brand="Preferred Selection", price=price, currency="USD", price_source="embedded_json",
        product_url=f"https://www.lidl.com/p/{sku}", image_url=None, scraped_at="2026-09-20T00:00:00Z",
    )
    defaults.update(kw)
    return output_writer.Product(**defaults)


@check("finish_run precedence: rows followed by a remote error are PARTIAL (6), never 'complete'")
def _():
    # This check used to pin exit 5 for "rows, then a remote API error".
    # The exit-code contract says 5 means the content was never obtained and
    # 6 means rows were gathered and then something failed — a run holding
    # rows is the second case, so it now pins 6 (stop_reason keeps the cause).
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.json")
        code = output_writer.finish_run(
            products=[_mk_product("a")], out_path=out, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=True, remote_api_error=True, allow_empty=True, started_at=0.0,
        )
        assert code == output_writer.EXIT_PARTIAL, code
        assert Path(out).exists(), "already-collected products must still be written out"
        meta = json.loads(Path(f"{out}.meta.json").read_text())
        assert meta["status"] == "partial", meta["status"]
        assert meta["stop_reason"] == "remote_api_error", meta["stop_reason"]


@check("finish_run precedence: blocked+zero-products respects --allow-empty for WHETHER to write, never for the STATUS")
def _():
    with tempfile.TemporaryDirectory() as td:
        out_a = str(Path(td) / "a.json")
        code = output_writer.finish_run(
            products=[], out_path=out_a, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=[2],
            blocked=True, remote_api_error=False, allow_empty=True, started_at=0.0,
        )
        assert code == output_writer.EXIT_BLOCKED
        assert Path(out_a).exists(), "--allow-empty means a zero-product outcome DOES get written"
        meta = json.loads(Path(f"{out_a}.meta.json").read_text())
        assert meta["status"] == "blocked", "--allow-empty must never launder this into 'complete'"

        out_b = str(Path(td) / "b.json")
        code = output_writer.finish_run(
            products=[], out_path=out_b, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=[2],
            blocked=True, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == output_writer.EXIT_BLOCKED
        assert not Path(out_b).exists(), "without --allow-empty, a zero-product outcome writes nothing"


@check("finish_run: zero products without --allow-empty writes neither file nor sidecar")
def _():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.json")
        code = output_writer.finish_run(
            products=[], out_path=out, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == output_writer.EXIT_ZERO_PRODUCTS
        assert not Path(out).exists()
        assert not Path(f"{out}.meta.json").exists()


@check("finish_run: partial (failed pages, some products) writes output and reports EXIT_PARTIAL")
def _():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.json")
        code = output_writer.finish_run(
            products=[_mk_product("a")], out_path=out, fmt="json", engine="test", url="u",
            pages_requested=2, pages_completed=1, failed_pages=[2],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == output_writer.EXIT_PARTIAL
        assert Path(out).exists()
        meta = json.loads(Path(f"{out}.meta.json").read_text())
        assert meta["status"] == "partial"


@check("finish_run: a clean run with products writes output and reports EXIT_OK/complete")
def _():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.json")
        code = output_writer.finish_run(
            products=[_mk_product("a"), _mk_product("b")], out_path=out, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == output_writer.EXIT_OK
        data = json.loads(Path(out).read_text())
        assert len(data) == 2


@check("Product field order: family-common fields first, lidl-specific fields after")
def _():
    expected_head = [
        "sku", "source", "category", "title", "brand", "price", "currency",
        "price_source", "product_url", "image_url", "scraped_at",
    ]
    assert output_writer.PRODUCT_FIELD_NAMES[: len(expected_head)] == expected_head
    tail = output_writer.PRODUCT_FIELD_NAMES[len(expected_head):]
    assert "unit_price" in tail and "zip_code" in tail and "is_weekly_deal" in tail
    assert "lidl_plus_price" in tail


@check("merge_pages dedupes by sku, last-write-wins, in PAGE order not arrival order")
def _():
    page1 = [_mk_product("a", price=1.00), _mk_product("b", price=2.00)]
    page2 = [_mk_product("a", price=1.50), _mk_product("c", price=3.00)]  # "a" price changed
    merged = output_writer.merge_pages([page1, page2])
    skus = [p.sku for p in merged]
    assert skus == ["a", "b", "c"], f"expected page-order with new items appended, got {skus}"
    a = next(p for p in merged if p.sku == "a")
    assert a.price == 1.50, "later page's value must win for a repeated sku"


@check("write_csv writes a header even for zero rows")
def _():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.csv")
        output_writer.write_csv([], out)
        text = Path(out).read_text()
        assert text.strip() != ""
        assert "sku" in text.splitlines()[0]


# --------------------------------------------------------------------------- #
# proxy_pool — parsing, redaction, dead-marking
# --------------------------------------------------------------------------- #
@check("proxy_pool rejects a malformed proxy string with ProxyParseError")
def _():
    try:
        proxy_pool.load_proxies("not a proxy!!", None)
        raise AssertionError("expected ProxyParseError")
    except proxy_pool.ProxyParseError:
        pass


@check("proxy_pool parses a credentialed proxy and masks it in logs")
def _():
    proxies = proxy_pool.load_proxies("http://user:secretpass@host.example:8080", None)
    assert len(proxies) == 1
    p = proxies[0]
    assert p.has_auth
    masked = p.masked()
    assert "secretpass" not in masked
    assert "host.example" in masked


@check("proxy_pool.redact_credentials strips login:password out of an arbitrary string")
def _():
    raw = "connect failed: ws://myuser:mysecret@cb.2captcha.com:9222 (5 attempts)"
    redacted = proxy_pool.redact_credentials(raw)
    assert "mysecret" not in redacted
    assert "myuser" not in redacted


# --------------------------------------------------------------------------- #
# captcha_solver — generic + widget-specific detection
# --------------------------------------------------------------------------- #
@check("captcha_solver.detect_from_html finds generic bot-challenge markers")
def _():
    assert captcha_solver.detect_from_html("<html>please complete the g-recaptcha below</html>")
    assert not captcha_solver.detect_from_html("<html><body>ordinary page, no widgets</body></html>")


@check("captcha_solver.identify_widget extracts a Turnstile sitekey")
def _():
    html = '<div class="cf-turnstile" data-sitekey="0x4AAA_example"></div>'
    signal = captcha_solver.identify_widget(html)
    assert signal is not None
    assert signal.captcha_type == captcha_solver.CaptchaType.CLOUDFLARE_TURNSTILE
    assert signal.sitekey == "0x4AAA_example"


# --------------------------------------------------------------------------- #
# env_config — LIDL_* keys, placeholder detection, precedence
# --------------------------------------------------------------------------- #
@check("env_config.ENV_KEYS matches .env.example exactly, in both directions")
def _():
    import re as _re
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    # A variable may be shipped commented out (`# LIDL_URL=...`) so a copied
    # .env.example does not set it — it is still documented.
    documented = set()
    for line in example.splitlines():
        m = _re.match(r"^(?:#\s*)?([A-Z][A-Z0-9_]+)=", line)
        if m:
            documented.add(m.group(1))
    assert documented == set(env_config.ENV_KEYS), (documented, set(env_config.ENV_KEYS))


@check("env_config uses LIDL_ prefixed keys, not a leftover SKYSCANNER_/other-site name")
def _():
    for key in env_config.ENV_KEYS:
        assert key == "TWOCAPTCHA_KEY" or key.startswith("LIDL_"), f"unexpected env key {key!r}"


@check("env_config._is_placeholder treats a braced {...} fragment as unset")
def _():
    assert env_config._is_placeholder("")
    assert env_config._is_placeholder(None)
    assert env_config._is_placeholder("{login}-zone-scraping_browser:{password}@cb.2captcha.com")
    assert not env_config._is_placeholder("a-real-looking-value-123")


@check("env_config.apply_env never overrides an explicitly-set CLI flag")
def _():
    import argparse
    import os as _os
    ns = argparse.Namespace(proxy="http://explicit:pass@host:1")
    _os.environ["LIDL_PROXY"] = "http://from-env:pass@host:2"
    try:
        env_config.apply_env(ns, dotenv_path="/nonexistent/.env")
        assert ns.proxy == "http://explicit:pass@host:1"
    finally:
        del _os.environ["LIDL_PROXY"]


# --------------------------------------------------------------------------- #
# lidl_parser — URL building, sku, JSON-LD, __NEXT_DATA__, DOM fallback
# --------------------------------------------------------------------------- #
@check("search_url builds the confirmed-real /q/search?q=<query> URL, query takes priority over category")
def _():
    url = lp.search_url(query="whole milk", category="food-wine/s10068374")
    assert url.startswith("https://www.lidl.com/q/search?")
    assert "q=whole" in url and "milk" in url
    assert lp.is_search_url(url)


@check("search_url builds a confirmed-real /c/<slug>/s<id> category URL when no query is given")
def _():
    url = lp.search_url(category="food-wine/s10068374", zip_code="11803")
    assert url.startswith("https://www.lidl.com/c/food-wine/s10068374")
    assert "zip=11803" in url
    assert lp.is_search_url(url)


@check("search_url raises without a query or category")
def _():
    try:
        lp.search_url()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


@check("make_sku prefers a real product id over a URL fingerprint, and is deterministic")
def _():
    a = lp.make_sku(product_id="000111222", product_url="https://www.lidl.com/p/x")
    b = lp.make_sku(product_id="000111222", product_url="https://www.lidl.com/p/y")
    assert a == b == "lidl-000111222"
    c1 = lp.make_sku(product_id=None, product_url="https://www.lidl.com/p/whole-milk")
    c2 = lp.make_sku(product_id=None, product_url="https://www.lidl.com/p/whole-milk")
    c3 = lp.make_sku(product_id=None, product_url="https://www.lidl.com/p/oat-milk")
    assert c1 == c2, "same URL must fingerprint to the same sku across runs"
    assert c1 != c3


_JSON_LD_PRODUCT_HTML = """
<html><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Whole Milk, 1 Gal",
 "sku":"000111222","brand":{"name":"Preferred Selection"},
 "url":"/p/whole-milk/p000111222","image":"https://img.lidl.com/000111222.jpg",
 "category":"Dairy & Eggs",
 "offers":{"@type":"Offer","price":"3.49","priceCurrency":"USD","highPrice":"3.99"}}
</script>
</body></html>
"""


@check("parse_search_results: a single JSON-LD Product node parses with discount computed")
def _():
    res = lp.parse_search_results(_JSON_LD_PRODUCT_HTML)
    assert res.source_used == "embedded_json"
    assert len(res.products) == 1
    p = res.products[0]
    assert p.sku == "lidl-000111222"
    assert p.price == 3.49
    assert p.original_price == 3.99
    assert p.discount_pct is not None and p.discount_pct > 0
    assert p.is_weekly_deal is True
    assert p.brand == "Preferred Selection"
    assert p.product_url == "https://www.lidl.com/p/whole-milk/p000111222"


_JSON_LD_ITEMLIST_HTML = """
<html><body>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
  {"@type":"ListItem","position":1,"item":{"@type":"Product","name":"Oat Milk",
    "sku":"000333444","offers":{"@type":"Offer","price":"4.29","priceCurrency":"USD"}}},
  {"@type":"ListItem","position":2,"item":{"@type":"Product","name":"Almond Milk",
    "sku":"000555666","offers":{"@type":"Offer","price":"3.99","priceCurrency":"USD"}}}
]}
</script>
</body></html>
"""


@check("parse_search_results: an ItemList-wrapped set of Products all parse")
def _():
    res = lp.parse_search_results(_JSON_LD_ITEMLIST_HTML)
    assert res.source_used == "embedded_json"
    assert len(res.products) == 2
    skus = {p.sku for p in res.products}
    assert skus == {"lidl-000333444", "lidl-000555666"}


@check("parse_search_results respects max_results across JSON-LD nodes")
def _():
    res = lp.parse_search_results(_JSON_LD_ITEMLIST_HTML, max_results=1)
    assert len(res.products) == 1


@check("a Product node with no usable price is skipped, not fabricated")
def _():
    html = """<script type="application/ld+json">
    {"@type":"Product","name":"Mystery Item","offers":{"@type":"Offer"}}
    </script>"""
    res = lp.parse_search_results(html)
    assert res.products == []
    assert res.source_used == "none"


@check("JSON-LD handles legal list/image/url shapes without inventing currency")
def _():
    html = """<script type="application/ld+json">{
      "@context":"https://schema.org", "@type":"Product", "name":"Milk",
      "image":[null, {"contentUrl":"https://img.example/milk.jpg"}],
      "offers":[null, "junk", {"price":"2.50", "url":"/p/milk/p123"}]
    }</script>"""
    result = lp.parse_search_results(html)
    assert len(result.products) == 1
    product = result.products[0]
    assert product.product_url == "https://www.lidl.com/p/milk/p123"
    assert product.image_url == "https://img.example/milk.jpg"
    assert product.currency is None, "missing structured currency must remain null"


_DOM_FALLBACK_HTML = """
<html><body>
<div data-testid="product-tile">
  <h3 data-testid="product-title">Sourdough Bread</h3>
  <span data-testid="product-brand">Bakehouse</span>
  <span data-testid="price">$2.99</span>
  <img src="https://img.lidl.com/bread.jpg">
  <a href="/p/sourdough-bread/p999">View</a>
</div>
<div data-testid="product-tile">
  <h3 data-testid="product-title">Rye Bread</h3>
  <span data-testid="price">$3.49</span>
  <a href="/p/rye-bread/p998">View</a>
</div>
</body></html>
"""


@check("DOM fallback parses product tiles when no embedded JSON is present")
def _():
    res = lp.parse_search_results(_DOM_FALLBACK_HTML)
    assert res.source_used == "dom"
    assert len(res.products) == 2
    titles = {p.title for p in res.products}
    assert titles == {"Sourdough Bread", "Rye Bread"}
    bread = next(p for p in res.products if p.title == "Sourdough Bread")
    assert bread.price == 2.99
    assert bread.product_url == "https://www.lidl.com/p/sourdough-bread/p999"


@check("count_result_cards counts DOM cards without a readiness wait")
def _():
    assert lp.count_result_cards(_DOM_FALLBACK_HTML) == 2


@check("total_result_count reads Lidl's own catalogue total for completeness checks")
def _():
    html = '<span class="s-products-count__label">1,142 Products</span>'
    assert lp.total_result_count(html) == 1142
    assert lp.total_result_count("<html><body>No catalogue counter</body></html>") is None
    assert lp.count_result_cards("<html><body>nothing here</body></html>") == 0


_REAL_CAPTURE_FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "lidl_search_real.html"


@check("extract_gridbox_products parses the real 2026-09-20 capture fixture (data-gridbox-impression)")
def _():
    with open(_REAL_CAPTURE_FIXTURE, encoding="utf-8") as f:
        html = f.read()
    res = lp.parse_search_results(html)
    assert res.source_used == "dom_gridbox", res.source_used
    assert len(res.products) == 3, len(res.products)

    by_sku = {p.sku: p for p in res.products}
    cheese = by_sku["lidl-11244845"]
    assert cheese.title == "Biazzo® whole milk mozzarella cheese"
    assert cheese.brand == "BIAZZO®"
    assert cheese.price == 3.69
    assert cheese.unit_size == "16 oz."
    assert cheese.unit_price == 0.23
    assert cheese.product_url == "https://www.lidl.com/p/biazzo-whole-milk-mozzarella-cheese/p11244845"
    assert "#" not in cheese.product_url, "the real #searchTrackingMasterId=... fragment must be stripped"

    buttermilk = by_sku["lidl-11250439"]
    assert buttermilk.brand == "PET®"
    assert buttermilk.price == 2.87
    assert buttermilk.unit_size == "64 fl.oz."
    assert buttermilk.unit_price == 0.04

    generic_milk = by_sku["lidl-11675058"]
    assert generic_milk.title == "whole milk"
    assert generic_milk.brand is None, "a private-label tile renders no brand element on the real site"
    assert generic_milk.price == 3.30
    assert generic_milk.unit_size == "128 fl.oz."
    assert generic_milk.unit_price == 0.03

    for p in res.products:
        assert p.price_source == "dom"
        assert p.currency == "USD"


_REAL_DEALS_FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "lidl_deals_real.html"


@check("extract_gridbox_products reads discounted tiles from the real 2026-10-18 weekly-deals capture")
def _():
    with open(_REAL_DEALS_FIXTURE, encoding="utf-8") as f:
        html = f.read()
    res = lp.parse_search_results(html)
    assert res.source_used == "dom_gridbox", res.source_used
    by_sku = {p.sku: p for p in res.products}
    assert len(by_sku) == 4, len(by_sku)

    onions = by_sku["lidl-11242052"]
    # The "-20%" badge renders ahead of "$1.59*" inside .ods-price__main-wrapper;
    # number-matching that wrapper used to report price=20.0.
    assert onions.price == 1.59, onions.price
    assert onions.original_price == 1.99
    assert onions.discount_pct == 20.0
    assert onions.is_weekly_deal is True
    assert onions.unit_size == "2 lb." and onions.unit_price == 0.80
    assert onions.image_url == "https://www.lidl.com/assets/gcp56066dae.png"

    assert onions.lidl_plus_price is None

    avocados = by_sku["lidl-11241859"]
    # Public promo price, not the Lidl Plus member price (1.99) the impression blob carries.
    assert avocados.price == 2.19, avocados.price
    assert avocados.lidl_plus_price == 1.99
    assert avocados.original_price == 3.19, "the stroke price renders as '3.19 $' on this tile"
    assert avocados.discount_pct == 31.0 and avocados.is_weekly_deal is True
    # The footer's "$ 0.33 each" is 1.99 / 6, the member price per piece.
    assert avocados.unit_size == "6 Piece" and avocados.unit_price is None

    steak = by_sku["lidl-11320818"]
    # Member-only discount: a shopper without the app pays the struck-through price.
    assert steak.price == 11.89, steak.price
    assert steak.lidl_plus_price == 9.99
    assert steak.original_price is None and steak.discount_pct is None
    assert steak.is_weekly_deal is False
    assert steak.unit_price is None

    field_picks = by_sku["lidl-11238009"]
    assert field_picks.price == 5.99
    assert field_picks.original_price is None and field_picks.discount_pct is None
    assert field_picks.is_weekly_deal is False
    assert field_picks.unit_size == "1 Piece" and field_picks.unit_price == 5.99
    assert field_picks.lidl_plus_price is None

    for p in res.products:
        assert p.image_url and p.image_url.startswith("https://www.lidl.com/assets/"), p.image_url


@check("extract_gridbox_products is what parse_search_results tries first (before JSON-LD)")
def _():
    # A page with BOTH a gridbox tile and an unrelated JSON-LD Product block
    # must prefer the gridbox extraction — matches the real site, where the
    # search-results page's own JSON-LD is Organization-only anyway (see
    # lidl_parser.py's module docstring).
    with open(_REAL_CAPTURE_FIXTURE, encoding="utf-8") as f:
        gridbox_html = f.read()
    combined = gridbox_html.replace("</body>", _JSON_LD_PRODUCT_HTML.split("<body>")[1])
    res = lp.parse_search_results(combined)
    assert res.source_used == "dom_gridbox"
    assert len(res.products) == 3


@check("safe_parse_search_results degrades a bad round instead of crashing the whole run")
def _():
    # Family invariant: a parse-time exception on one
    # round must degrade to an empty round, never propagate and crash the
    # multi-round scrape, discarding every product already collected in
    # earlier rounds. Reproduce a genuine parsing-time defect (not a caller
    # passing the wrong type) by monkeypatching the primary extraction path.
    original = lp.extract_gridbox_products
    lp.extract_gridbox_products = lambda *a, **kw: (_ for _ in ()).throw(ValueError("simulated parser defect"))
    try:
        res = lp.safe_parse_search_results("<html></html>")
    finally:
        lp.extract_gridbox_products = original
    assert res.products == []
    assert res.source_used == "none"


@check("zip_code/store_id flow through to every Product row when supplied")
def _():
    res = lp.parse_search_results(_JSON_LD_PRODUCT_HTML, zip_code="11803", store_id="US08021")
    assert res.products[0].zip_code == "11803"
    assert res.products[0].store_id == "US08021"


# --------------------------------------------------------------------------- #
# CLI validation — bad usage never crashes, never writes output
# --------------------------------------------------------------------------- #
@check("each engine: no --url/--query/--category is EXIT_BAD_USAGE, not a crash")
def _():
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "out.json")
            args = mod.build_arg_parser().parse_args(["--out", out])
            code = asyncio_run_maybe(mod, args)
            assert code == output_writer.EXIT_BAD_USAGE, f"{mod.__name__}: expected EXIT_BAD_USAGE, got {code}"
            assert not Path(out).exists()


@check("each engine: a malformed --proxy is EXIT_BAD_USAGE, not a crash, and writes nothing")
def _():
    # Best-effort, not a requirement that a driver be installed: the family
    # requires smoke_test.py to run cleanly with ZERO engine drivers
    # present (that's exactly what the "offline checks" CI job installs —
    # requirements.txt only, per the per-engine dependency split), so this must not
    # demand `exercised > 0`. "This particular engine wasn't skipped" is
    # already asserted independently, per engine, by tests.yml's own
    # engine-smoke jobs (see the "assert THIS engine did not skip" step) —
    # that's the right layer for it, not this driver-agnostic offline check.
    _IMPORT_ERROR_ATTR = {
        "playwright_scraper": "_PLAYWRIGHT_IMPORT_ERROR",
        "selenium_scraper": "_SELENIUM_IMPORT_ERROR",
        "puppeteer_scraper": "_PYPPETEER_IMPORT_ERROR",
    }
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        if getattr(mod, _IMPORT_ERROR_ATTR[mod.__name__], None) is not None:
            continue
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "out.json")
            args = mod.build_arg_parser().parse_args([
                "--query", "milk", "--proxy", "not a proxy!!", "--out", out,
            ])
            code = asyncio_run_maybe(mod, args)
            assert code == output_writer.EXIT_BAD_USAGE, (
                f"{mod.__name__}: a malformed --proxy must exit {output_writer.EXIT_BAD_USAGE} "
                f"(bad usage), got {code}"
            )
            assert not Path(out).exists(), f"{mod.__name__}: a bad-usage run must never write output"


@check("selenium_scraper refuses a credentialed --cdp-endpoint with EXIT_BAD_USAGE")
def _():
    args = selenium_scraper.build_arg_parser().parse_args([
        "--query", "milk", "--cdp-endpoint", "ws://user:pass@cb.2captcha.com:9222",
    ])
    code = selenium_scraper.run(args)
    assert code == output_writer.EXIT_BAD_USAGE


@check("each engine rejects --max-results 0 at the argparse level")
def _():
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        try:
            mod.build_arg_parser().parse_args(["--query", "milk", "--max-results", "0"])
            raise AssertionError(f"{mod.__name__}: expected argparse to reject --max-results 0")
        except SystemExit:
            pass


# --------------------------------------------------------------------------- #
# diff_runs / scraper_api_client — sanity only (family-shared, no site knowledge)
# --------------------------------------------------------------------------- #
@check("diff_runs reports added/removed/changed/source_changed between two real finish_run() outputs")
def _():
    with tempfile.TemporaryDirectory() as td:
        old_out, new_out = str(Path(td) / "old.json"), str(Path(td) / "new.json")
        output_writer.finish_run(
            products=[_mk_product("a", price=1.00), _mk_product("b", price=2.00, price_source="dom")],
            out_path=old_out, fmt="json", engine="test", url="u", pages_requested=1, pages_completed=1,
            failed_pages=None, blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        output_writer.finish_run(
            products=[_mk_product("a", price=1.50), _mk_product("c", price=3.00)],
            out_path=new_out, fmt="json", engine="test", url="u", pages_requested=1, pages_completed=1,
            failed_pages=None, blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        result = diff_runs.diff(old_out, new_out)
        assert result["added"] == ["c"]
        assert result["removed"] == ["b"]
        assert any(row["sku"] == "a" for row in result["changed"])


@check("diff_runs refuses to compare a non-'complete' run")
def _():
    with tempfile.TemporaryDirectory() as td:
        old_out, new_out = str(Path(td) / "old.json"), str(Path(td) / "new.json")
        output_writer.finish_run(
            products=[_mk_product("a")], out_path=old_out, fmt="json", engine="test", url="u",
            pages_requested=2, pages_completed=1, failed_pages=[2],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        output_writer.finish_run(
            products=[_mk_product("a")], out_path=new_out, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        try:
            diff_runs.diff(old_out, new_out)
            raise AssertionError("expected a refusal — old run is 'partial', not 'complete'")
        except SystemExit:
            pass


@check("scraper_api_client.TwoCaptchaClient._require_key rejects a missing/empty key")
def _():
    client = scraper_api_client.TwoCaptchaClient("")
    try:
        client._require_key()
        raise AssertionError("expected TwoCaptchaAuthError")
    except scraper_api_client.TwoCaptchaAuthError:
        pass


@check("scraper_api_client honors --captcha-api override, not the module-level API_BASE")
def _():
    client = scraper_api_client.TwoCaptchaClient("fakekey", api_base="https://mock.example.test")
    assert client.api_base == "https://mock.example.test"
    assert client.api_base != scraper_api_client.API_BASE


# --------------------------------------------------------------------------- #
# Output contract: atomic writes, file modes, CSV formula guard, exit matrix
# --------------------------------------------------------------------------- #
@check("atomic writes: a failure halfway through a write leaves the previous JSON, CSV and sidecar intact")
def _():
    import os as _os
    with tempfile.TemporaryDirectory() as td:
        for fmt in ("json", "csv"):
            out = str(Path(td) / f"out.{fmt}")
            output_writer.finish_run(
                products=[_mk_product("a"), _mk_product("b")], out_path=out, fmt=fmt, engine="t", url="u",
                pages_requested=1, pages_completed=1, failed_pages=None,
                blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
            )
            before = Path(out).read_bytes()
            meta_before = Path(f"{out}.meta.json").read_bytes()

            class Boom(Exception):
                pass

            orig_asdict = output_writer.asdict
            calls = {"n": 0}

            def failing_asdict(obj):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise Boom("disk full")
                return orig_asdict(obj)

            output_writer.asdict = failing_asdict
            try:
                output_writer.write_output([_mk_product("c"), _mk_product("d")], out, fmt)
                raise AssertionError("the planted failure did not fire")
            except Boom:
                pass
            finally:
                output_writer.asdict = orig_asdict
            assert Path(out).read_bytes() == before, f"{fmt}: a failed write truncated the previous good output"

            orig_dumps = output_writer.json.dumps
            output_writer.json.dumps = lambda *a, **k: (_ for _ in ()).throw(Boom("killed"))
            try:
                output_writer.write_meta(out, status="complete", stop_reason="x", engine="t", url="u",
                                         pages_requested=1, pages_completed=1, product_count=0, started_at=0.0)
                raise AssertionError("the planted sidecar failure did not fire")
            except Boom:
                pass
            finally:
                output_writer.json.dumps = orig_dumps
            assert Path(f"{out}.meta.json").read_bytes() == meta_before, "a failed sidecar write truncated the sidecar"
        leftovers = [n for n in _os.listdir(td) if n.endswith(".tmp")]
        assert not leftovers, f"temp files left behind: {leftovers}"


@check("atomic writes: a new file follows the umask (incl. 002), an existing file keeps its mode")
def _():
    import os as _os
    import stat as _stat
    with tempfile.TemporaryDirectory() as td:
        for umask, expected in ((0o022, 0o644), (0o002, 0o664), (0o077, 0o600)):
            old = _os.umask(umask)
            try:
                out = str(Path(td) / f"new{umask:o}.json")
                output_writer.write_json([_mk_product("a")], out)
            finally:
                _os.umask(old)
            got = _stat.S_IMODE(_os.stat(out).st_mode)
            assert got == expected, f"umask {umask:o}: new file is {got:o}, expected {expected:o}"
        out = str(Path(td) / "existing.csv")
        Path(out).write_text("x")
        _os.chmod(out, 0o640)
        output_writer.write_csv([_mk_product("a")], out)
        got = _stat.S_IMODE(_os.stat(out).st_mode)
        assert got == 0o640, f"an existing file's mode changed to {got:o}"


@check("CSV formula guard: text cells starting = + - @ TAB CR LF get a leading ', numbers and JSON do not")
def _():
    import csv as _csv
    risky = _mk_product("a", title="=HYPERLINK(\"x\")", brand="+cmd", category="@SUM(A1)",
                        unit_size="-2 oz", image_url="\tx", price=-5.0)
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.csv")
        code = output_writer.finish_run(
            products=[risky], out_path=out, fmt="csv", engine="t", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
            extra_meta={"site_note": "kept"},
        )
        assert code == output_writer.EXIT_OK
        with open(out, newline="", encoding="utf-8") as f:
            row = next(_csv.DictReader(f))
        assert row["title"] == "'=HYPERLINK(\"x\")", row["title"]
        assert row["brand"] == "'+cmd" and row["category"] == "'@SUM(A1)", (row["brand"], row["category"])
        assert row["unit_size"] == "'-2 oz" and row["image_url"] == "'\tx"
        assert row["price"] == "-5.0", f"a negative NUMBER must stay a number: {row['price']!r}"
        meta = json.loads(Path(f"{out}.meta.json").read_text())
        assert meta["csv_cells_escaped"] == 5, meta.get("csv_cells_escaped")
        assert meta["site_note"] == "kept"

        out_j = str(Path(td) / "out.json")
        output_writer.finish_run(
            products=[risky], out_path=out_j, fmt="json", engine="t", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
            extra_meta={"csv_cells_escaped": "caller wins"},
        )
        data = json.loads(Path(out_j).read_text())
        assert data[0]["title"] == "=HYPERLINK(\"x\")", "JSON must keep the site's exact text"
        meta_j = json.loads(Path(f"{out_j}.meta.json").read_text())
        assert meta_j["csv_cells_escaped"] == "caller wins", "housekeeping must merge UNDER the caller's extra"


@check("exit matrix: a served page that states rows but parses to none is 5 with a sidecar; rows then a block is 6")
def _():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.json")
        Path(out).write_text("[\"previous good run\"]")
        code = output_writer.finish_run(
            products=[], out_path=out, fmt="json", engine="t", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
            parser_failed=True, stop_reason="parser_found_nothing",
        )
        assert code == output_writer.EXIT_NOT_OBTAINED == 5, code
        assert Path(out).read_text() == "[\"previous good run\"]", "a parser failure must not touch the previous output"
        meta = json.loads(Path(f"{out}.meta.json").read_text())
        assert meta["stop_reason"] == "parser_found_nothing" and meta["status"] == "failed", meta
        assert meta["output_written"] is False and meta["product_count"] == 0

        out2 = str(Path(td) / "rows_then_block.json")
        code = output_writer.finish_run(
            products=[_mk_product("a")], out_path=out2, fmt="json", engine="t", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=True, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == output_writer.EXIT_PARTIAL, code
        assert json.loads(Path(f"{out2}.meta.json").read_text())["stop_reason"] == "blocked"

        out3 = str(Path(td) / "empty.json")
        code = output_writer.finish_run(
            products=[], out_path=out3, fmt="json", engine="t", url="u",
            pages_requested=1, pages_completed=1, failed_pages=[2],
            blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0,
        )
        assert code == output_writer.EXIT_ZERO_PRODUCTS, "a page that states nothing and yields nothing is empty"


@check("end_of_run: Lidl's own counter with zero parsed rows is parser_found_nothing; a short grid is incomplete")
def _():
    import run_policy
    counter = '<span class="s-products-count__label">142 Products</span>'
    reason, shortfall, parser_failed = run_policy.end_of_run(
        final_html=counter, collected=0, max_results=30, blocked=False,
        loop_reason=run_policy.STOP_STALLED, scroll_failed=True)
    assert (reason, parser_failed) == (run_policy.STOP_PARSER_FOUND_NOTHING, True), reason
    reason, shortfall, parser_failed = run_policy.end_of_run(
        final_html=counter, collected=12, max_results=30, blocked=False,
        loop_reason=run_policy.STOP_STALLED, scroll_failed=False)
    assert (reason, shortfall, parser_failed) == (run_policy.STOP_INCOMPLETE_GRID, True, False)
    reason, shortfall, parser_failed = run_policy.end_of_run(
        final_html="<html><body>no counter</body></html>", collected=0, max_results=30, blocked=False,
        loop_reason=run_policy.STOP_STALLED, scroll_failed=False)
    assert (reason, parser_failed) == (run_policy.STOP_STALLED, False)


# --------------------------------------------------------------------------- #
# Paid captcha solves: one allowance per page, and nothing bought that is
# never used
# --------------------------------------------------------------------------- #
_CAPTCHA_PAGE = '<html><body><div class="g-recaptcha" data-sitekey="6Lc_stub_sitekey"></div></body></html>'


class _StubSolver:
    """Stands in for TwoCaptchaClient. Never touches the network."""
    instances = []

    def __init__(self, *a, **k):
        self.purchases = 0
        _StubSolver.instances.append(self)

    def solve_and_wait(self, task):
        self.purchases += 1
        return "stub-token"


@check("solve_when_blocked buys nothing it cannot inject, and at most one solve per page budget")
def _():
    client = _StubSolver()
    budget = captcha_solver.SolveBudget()
    for _round in range(6):
        res = captcha_solver.solve_when_blocked(
            client=client, page_url="u", html=_CAPTCHA_PAGE, count_product_links=lambda h: 0,
            budget=budget, can_inject=False)
        assert res["action"] == "detected_not_injectable", res
    assert client.purchases == 0, f"{client.purchases} tokens bought that nothing injects"
    budget = captcha_solver.SolveBudget()
    actions = [captcha_solver.solve_when_blocked(
        client=client, page_url="u", html=_CAPTCHA_PAGE, count_product_links=lambda h: 0,
        budget=budget, can_inject=True)["action"] for _ in range(6)]
    assert client.purchases == 1, f"one page bought {client.purchases} solves"
    assert actions[0] == "solved" and set(actions[1:]) == {"budget_exhausted"}, actions
    import run_policy
    assert run_policy.LOCAL_ENGINES_INJECT_TOKEN is False, (
        "no engine injects a token: enabling purchases needs an injector first")


# Fake drivers: enough of each driver's surface for one scrape_search() run,
# serving fixed HTML. Lets the whole engine path (navigation, scroll loop,
# captcha step, end-of-run decision, finish_run) run with no browser.
class _FakeResponse:
    def __init__(self, status):
        self.status = status


class _PWLocator:
    @property
    def first(self):
        return self

    async def count(self):
        return 0


class _PWPage:
    def __init__(self, html):
        self.html = html

    async def goto(self, *a, **k):
        return _FakeResponse(200)

    async def wait_for_timeout(self, ms):
        return None

    async def content(self):
        return self.html

    async def evaluate(self, js, *a):
        return 1000 if "scrollHeight" in js else None

    def locator(self, sel):
        return _PWLocator()


class _PWBrowser:
    def __init__(self, html):
        self.html = html

    async def new_context(self, **kw):
        browser = self

        class Ctx:
            async def new_page(self):
                return _PWPage(browser.html)

            async def close(self):
                return None
        return Ctx()

    async def close(self):
        return None


def _fake_async_playwright(html):
    class Chromium:
        async def launch(self, **kw):
            return _PWBrowser(html)

    class PW:
        chromium = Chromium()

    class CM:
        async def __aenter__(self):
            return PW()

        async def __aexit__(self, *a):
            return False
    return lambda: CM()


class _PPPage:
    def __init__(self, html):
        self.html = html

    async def goto(self, *a, **k):
        return _FakeResponse(200)

    async def content(self):
        return self.html

    async def evaluate(self, js, *a):
        return 1000 if "scrollHeight" in js else None

    async def querySelector(self, sel):
        return None

    async def setUserAgent(self, ua):
        return None


class _PPBrowser:
    def __init__(self, html):
        self.html = html

    async def newPage(self):
        return _PPPage(self.html)

    async def close(self):
        return None


class _SEDriver:
    def __init__(self, html):
        self.page_source = html

    def set_page_load_timeout(self, s):
        pass

    def get(self, url):
        pass

    def execute_script(self, js, *a):
        if "responseStatus" in js:
            return 200
        return 1000 if "scrollHeight" in js else None

    def find_elements(self, *a):
        return []

    def quit(self):
        pass


def _drive_engine(name, html, extra_args=()):
    """Run one engine's real run() against fake driver serving `html`.
    Returns (exit_code, purchases, meta_or_None, out_path_exists)."""
    mod = {"playwright": playwright_scraper, "selenium": selenium_scraper, "puppeteer": puppeteer_scraper}[name]
    saved = {}

    def patch(attr, value):
        saved[attr] = getattr(mod, attr)
        setattr(mod, attr, value)

    _StubSolver.instances.clear()
    patch("TwoCaptchaClient", _StubSolver)
    if name == "playwright":
        patch("async_playwright", _fake_async_playwright(html))
    elif name == "selenium":
        patch("webdriver", object())
        patch("_build_driver", lambda **kw: _SEDriver(html))
        patch("READINESS_WAIT_S", 0)
    else:
        async def _launch(**kw):
            return _PPBrowser(html)
        patch("pyppeteer_launch", object())
        patch("_launch", _launch)
        patch("READINESS_WAIT_S", 0)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = str(Path(td) / "out.json")
            args = mod.build_arg_parser().parse_args([
                "--query", "milk", "--out", out, "--scroll-delay", "0", "--retry-delay", "0",
                "--twocaptcha-key", "stub-not-a-key", *extra_args,
            ])
            code = asyncio_run_maybe(mod, args)
            meta_path = Path(f"{out}.meta.json")
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
            purchases = sum(c.purchases for c in _StubSolver.instances)
            return code, purchases, meta, Path(out).exists()
    finally:
        for attr, value in saved.items():
            setattr(mod, attr, value)


@check("each engine: a captcha page with a key set buys NO solve across every scroll round, and exits 3")
def _():
    for name in ("playwright", "selenium", "puppeteer"):
        code, purchases, meta, wrote = _drive_engine(name, _CAPTCHA_PAGE, ["--max-scrolls", "20", "--stall-rounds", "20"])
        assert purchases == 0, f"{name}: {purchases} paid solves for one page, none of them injected"
        assert code == output_writer.EXIT_BLOCKED, f"{name}: exit {code}"
        assert not wrote and meta is None


@check("each engine: Lidl's counter stating products with none parsed exits 5 with a parser_found_nothing sidecar")
def _():
    lost = ('<html><body><span class="s-products-count__label">142 Products</span>'
            '<div class="renamed-tile"><a href="/p/x/p1">x</a></div></body></html>')
    for name in ("playwright", "selenium", "puppeteer"):
        code, purchases, meta, wrote = _drive_engine(name, lost)
        assert code == 5, f"{name}: a selector break reported as exit {code}"
        assert meta and meta["stop_reason"] == "parser_found_nothing", f"{name}: sidecar {meta}"
        assert not wrote


@check("each engine: the real capture fixture still parses through the whole run to exit 0")
def _():
    html = _REAL_CAPTURE_FIXTURE.read_text(encoding="utf-8")
    for name in ("playwright", "selenium", "puppeteer"):
        code, purchases, meta, wrote = _drive_engine(name, html, ["--max-results", "3"])
        assert code == output_writer.EXIT_OK, f"{name}: exit {code}"
        assert meta["product_count"] == 3 and meta["stop_reason"] == "max_results", meta


@check("each engine creates the solve budget ONCE per page, outside the scroll and retry loops")
def _():
    import ast as _ast
    for path in ("playwright_scraper.py", "selenium_scraper.py", "puppeteer_scraper.py"):
        tree = _ast.parse((ROOT / path).read_text(encoding="utf-8"))
        fn = next(n for n in _ast.walk(tree)
                  if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == "scrape_search")
        creations = [n for n in _ast.walk(fn) if isinstance(n, _ast.Call)
                     and getattr(n.func, "id", None) == "SolveBudget"]
        assert len(creations) == 1, f"{path}: {len(creations)} SolveBudget() calls in scrape_search"
        for loop in (n for n in _ast.walk(fn) if isinstance(n, (_ast.For, _ast.While))):
            inside = {id(c) for c in _ast.walk(loop)}
            assert id(creations[0]) not in inside, f"{path}: the budget is re-created inside a loop"
        solve_calls = [n for n in _ast.walk(fn) if isinstance(n, _ast.Call)
                       and getattr(n.func, "id", None) == "_maybe_solve_captcha"]
        assert solve_calls and all(any(k.arg == "budget" for k in c.keywords) for c in solve_calls), (
            f"{path}: a captcha call site without the page budget")


# --------------------------------------------------------------------------- #
# CLI: numeric validators shared by all engines; LIDL_URL never hijacks a
# typed --query
# --------------------------------------------------------------------------- #
@check("each engine rejects a negative --retries/--retry-delay/--scroll-delay/--proxy-block-retries with exit 2 naming the flag")
def _():
    import contextlib as _cl
    import io as _io
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        for flag in ("--retries", "--retry-delay", "--scroll-delay", "--proxy-block-retries"):
            err = _io.StringIO()
            try:
                with _cl.redirect_stderr(err):
                    mod.build_arg_parser().parse_args(["--query", "milk", flag, "-1"])
                raise AssertionError(f"{mod.__name__}: {flag} -1 accepted")
            except SystemExit as exc:
                assert exc.code == 2, (mod.__name__, flag, exc.code)
            assert f"argument {flag}" in err.getvalue(), f"{mod.__name__}: error does not name {flag}"


@check("every numeric flag uses the same validator in all three engines")
def _():
    def types(mod):
        return {a.option_strings[0]: getattr(a.type, "__name__", None)
                for a in mod.build_arg_parser()._actions if a.option_strings and a.type is not None}
    pw, se, pu = types(playwright_scraper), types(selenium_scraper), types(puppeteer_scraper)
    assert pw == se == pu, {"playwright": pw, "selenium": se, "puppeteer": pu}
    assert "int" not in pw.values() and "float" not in pw.values(), f"an unvalidated numeric flag: {pw}"


@check("a LIDL_URL from the environment does not override a typed --query; a typed --url with --query is refused")
def _():
    import os as _os
    import run_policy
    _os.environ["LIDL_URL"] = "https://www.lidl.com/q/search?q=FROM_ENV"
    try:
        for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
            args = mod.build_arg_parser().parse_args(["--query", "typed-query"])
            env_config.apply_env(args, dotenv_path="/nonexistent/.env")
            url = run_policy.resolve_start_url(args)
            assert "typed-query" in url and "FROM_ENV" not in url, f"{mod.__name__}: {url}"
            args = mod.build_arg_parser().parse_args([])
            env_config.apply_env(args, dotenv_path="/nonexistent/.env")
            assert "FROM_ENV" in run_policy.resolve_start_url(args), "LIDL_URL alone must still work"
    finally:
        del _os.environ["LIDL_URL"]
    args = playwright_scraper.build_arg_parser().parse_args(["--url", "https://www.lidl.com/x", "--query", "milk"])
    try:
        run_policy.resolve_start_url(args)
        raise AssertionError("a typed --url with --query was accepted")
    except run_policy.UsageError:
        pass
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert not any(line.startswith("LIDL_URL=") for line in example.splitlines()), (
        ".env.example must ship LIDL_URL commented out")


# --------------------------------------------------------------------------- #
# diff_runs comparability
# --------------------------------------------------------------------------- #
@check("diff_runs refuses runs from different regions or URLs, and JSON vs CSV of one run diffs as unchanged")
def _():
    with tempfile.TemporaryDirectory() as td:
        def save(name, url, fmt="json", **kw):
            out = str(Path(td) / f"{name}.{fmt}")
            output_writer.finish_run(
                products=[_mk_product("a", price=3.3, **kw)], out_path=out, fmt=fmt, engine="t", url=url,
                pages_requested=1, pages_completed=1, failed_pages=None,
                blocked=False, remote_api_error=False, allow_empty=False, started_at=0.0)
            return out
        a = save("a", "u", zip_code="10001")
        b = save("b", "u", zip_code="90210")
        c = save("c", "other-url", zip_code="10001")
        for other in (b, c):
            try:
                diff_runs.diff(a, other)
                raise AssertionError(f"diff of {other} was not refused")
            except SystemExit:
                pass
        j, k = save("j", "u", "json", zip_code="10001"), save("k", "u", "csv", zip_code="10001")
        result = diff_runs.diff(j, k)
        assert result["changed"] == [] and result["source_changed"] == [], result


# --------------------------------------------------------------------------- #
# Credential scan and public-text hygiene
# --------------------------------------------------------------------------- #
def _load_ci_checks():
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("ci_checks", ROOT / ".github" / "ci_checks.py")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@check("credential scan: the repo is clean, and planted keys in .env backups, unquoted, bare or as URLs are caught")
def _():
    if not (ROOT / ".github").is_dir():
        return  # the Docker image ships no CI material at all; CI runs this check
    ci = _load_ci_checks()
    count, findings = ci.scan(ROOT)
    assert count > 10, f"the scan read only {count} files"
    assert not findings, findings
    hexkey = "ab" * 16
    plants = {
        ".env.bak": f"TWOCAPTCHA_KEY={hexkey}\n",
        ".env.local": "LIDL_PROXY=http://" + "realuser:realpw9" + "@gw.proxyhost.net:2334\n",
        "leak_bare.py": f"KEY = {hexkey}\n",
        "leak_unquoted.env": "api_key=" + "Zx9" * 8 + "\n",
        "fixture.json": "\"<script>{\\\"apiKey\\\": \\\"" + "Q7w" * 8 + "\\\"}</script>\"\n",
    }
    with tempfile.TemporaryDirectory() as td:
        # a parent directory named like a build/output dir must not hide the repo
        root = Path(td) / "tmp" / "build" / "out" / "repo"
        root.mkdir(parents=True)
        (root / "clean.py").write_text("print('hello')\n")
        venv = root / "myenv"
        (venv / "lib").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr\n")
        (venv / "lib" / "vendored.py").write_text(f"X = '{hexkey}'\n")
        (root / ".env").write_text(f"TWOCAPTCHA_KEY={hexkey}\n")
        count, findings = ci.scan(root)
        assert count == 1 and not findings, (count, findings)  # clean.py only: .env and the venv are skipped
        for name, text in plants.items():
            (root / name).write_text(text)
        count, findings = ci.scan(root)
        hit = {f.split(":", 1)[0] for f in findings}
        assert hit == set(plants), f"caught {sorted(hit)}, planted {sorted(plants)}"
        assert all(hexkey not in f for f in findings), "a finding printed the secret itself"


@check("tests.yml runs the shared credential scan instead of its own grep")
def _():
    wf = ROOT / ".github" / "workflows" / "tests.yml"
    if not (ROOT / ".github").is_dir():
        return
    text = wf.read_text(encoding="utf-8")
    assert "ci_checks.py --secret-check" in text, "tests.yml does not call .github/ci_checks.py"
    assert "grep -rnE" not in text, "tests.yml carries its own credential grep again"


@check("no shipped file cites private maintainer notes or their section numbers")
def _():
    import re as _re
    _SEC = chr(0xA7)  # the section sign, built so this file does not match itself
    banned = [
        "CLAUDE" + ".md", "claude_" + "2scraper", "2scraper_" + "repo_rules",
        "play" + "book " + _SEC, "temp" + "late " + _SEC,
    ]
    section = _re.compile(_SEC + r"\s*\d")
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if not path.is_file() or any(p in (".git", "__pycache__") for p in rel.parts):
            continue
        if any((ROOT / Path(*rel.parts[:i]) / "pyvenv.cfg").exists() for i in range(1, len(rel.parts))):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in banned:
            assert token not in text, f"{rel}: cites {token!r}"
        assert not section.search(text), f"{rel}: a bare section-number citation"


def run() -> int:
    """All @check-decorated functions above already ran at import time
    (that's the point — see the `check()` docstring) and self-registered
    into RESULTS. This just reports them."""
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(n, d) for n, ok, d in RESULTS if not ok]
    print(f"smoke_test: {passed}/{len(RESULTS)} checks passed")
    for name, detail in failed:
        print(f"  FAIL: {name}\n        {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    import sys
    sys.exit(run())
