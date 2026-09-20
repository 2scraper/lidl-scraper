#!/usr/bin/env python3
"""smoke_test.py — one file of plain functions with inline/synthetic-
fixture checks. No pytest, no conftest. `tests/test_smoke.py` wraps this as
a single pytest entry point so `pytest` also works, without a second copy
of the checks.

**Honesty note, read before trusting a green run** (same caveat as every
other family member's smoke_test.py, e.g. skyscanner-scraper's): every
HTML fixture below is SYNTHETIC — hand-written to exercise the parsing
code paths, not a real capture of lidl.com. A green run here proves the
architecture (exit codes, dedupe, precedence, credential redaction, CLI
validation, engines importing cleanly) is sound, and that the parser's OWN
LOGIC does what it says on markup shaped the way this repo GUESSED
lidl.com looks (the JSON-LD path is the one exception with real grounding
— schema.org Product markup is a standard, documented e-commerce SEO
practice, not a lidl.com-specific guess). It does NOT prove
lidl_parser.py's selectors or embedded-JSON shape match the real, current
site — that still needs a live `--dump-html` capture, see TESTING.md.

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
# Engine import/CLI hygiene (CLAUDE.md §6)
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
    exempt_names = {"smoke_test.py", "CLAUDE.md"}
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


@check("all three engines expose the identical --flag set (CLAUDE.md §4)")
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


# --------------------------------------------------------------------------- #
# output_writer — exit codes / precedence / dedupe (CLAUDE.md §9)
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


@check("finish_run precedence: remote_api_error status is never laundered into 'complete' just because products were present")
def _():
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "out.json")
        code = output_writer.finish_run(
            products=[_mk_product("a")], out_path=out, fmt="json", engine="test", url="u",
            pages_requested=1, pages_completed=1, failed_pages=None,
            blocked=True, remote_api_error=True, allow_empty=True, started_at=0.0,
        )
        # Non-empty products means this is NOT the "zero-product outcome"
        # the "write nothing" rule (CLAUDE.md §9) is about — the collected
        # data legitimately gets written, but its STATUS must still say
        # remote_api_error, never "complete".
        assert code == output_writer.EXIT_REMOTE_API_ERROR
        assert Path(out).exists(), "already-collected products must still be written out"
        meta = json.loads(Path(f"{out}.meta.json").read_text())
        assert meta["status"] == "remote_api_error", meta["status"]


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
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {line.split("=", 1)[0] for line in example.splitlines() if "=" in line and not line.startswith("#")}
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
@check("search_url builds a /search/products/<query> URL, query takes priority over category")
def _():
    url = lp.search_url(query="whole milk", category="deadbeef")
    assert url.startswith("https://www.lidl.com/search/products/")
    assert "whole" in url and "milk" in url
    assert lp.is_search_url(url)


@check("search_url builds a /specials?category=<id> URL when no query is given")
def _():
    url = lp.search_url(category="be8072a237eb7908c193ee9175e2f8c87e48fe8b", zip_code="11803")
    assert url.startswith("https://www.lidl.com/specials?")
    assert "category=be8072a237eb7908c193ee9175e2f8c87e48fe8b" in url
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
    assert lp.count_result_cards("<html><body>nothing here</body></html>") == 0


@check("safe_parse_search_results degrades a parse exception to an empty result, never raises")
def _():
    # None isn't valid markup — BeautifulSoup raises TypeError on it, which
    # is exactly the "unexpected exception inside parsing" case this
    # wrapper exists to degrade instead of propagating (see its docstring).
    res = lp.safe_parse_search_results(None)  # type: ignore[arg-type]
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
    _IMPORT_ERROR_ATTR = {
        "playwright_scraper": "_PLAYWRIGHT_IMPORT_ERROR",
        "selenium_scraper": "_SELENIUM_IMPORT_ERROR",
        "puppeteer_scraper": "_PYPPETEER_IMPORT_ERROR",
    }
    exercised = 0
    for mod in (playwright_scraper, selenium_scraper, puppeteer_scraper):
        if getattr(mod, _IMPORT_ERROR_ATTR[mod.__name__], None) is not None:
            continue
        exercised += 1
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
    assert exercised > 0, "no engine's driver is installed — this check ran against zero of the three engines"


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
