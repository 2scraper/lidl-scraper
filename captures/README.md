# captures/

Real, live captures of lidl.com go here — search/category/product HTML,
plus any `application/ld+json` or embedded-state blob pulled out of them —
used to verify `lidl_parser.py`'s selectors and embedded-data heuristics
against the real page instead of guessing.

**`2026-09-20-manual-browser-notes.md`** is the first real capture: a
manual, interactive session through a real Chromium browser (not a raw
HTML dump — see that file for why, and what it does/doesn't cover). It's
what `lidl_parser.py`'s current `extract_gridbox_products()` primary path
and `tests/fixtures/lidl_search_real.html` are both built from. Still
missing: a discounted/"weekly deal" tile, the store/zip picker's own
request, and a full raw HTML page source for a future byte-for-byte diff
— see that file's "Still not captured" section, and drop one here (per
the instructions below) if you get one via an actual engine run:

```bash
python3 playwright_scraper.py --query "whole milk" \
  --max-results 10 --format json --out /tmp/lidl_test.json --dump-html
```

That writes `lidl_test_debug.html` next to the output — drop a copy of it
here (e.g. `captures/search_whole_milk.html`), and if you can, also save
any `application/ld+json` or `__NEXT_DATA__` blob out of it separately —
that's the piece `lidl_parser.py`'s embedded-data extraction actually
needs to stop guessing.

Once a real file is here, `lidl_parser.py` gets updated to match it, the
`# TODO: verify live` markers on whatever that file confirms get removed,
and a trimmed/scrubbed copy plus a new `smoke_test.py` check gets added
under `tests/fixtures/` — see `CONTRIBUTING.md`.
