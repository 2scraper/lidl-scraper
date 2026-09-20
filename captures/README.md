# captures/

Real, live captures of lidl.com go here — search/category/product HTML,
plus any `application/ld+json` or `__NEXT_DATA__` blob pulled out of them
— used to verify `lidl_parser.py`'s selectors and embedded-data heuristics
against the real page instead of the best-effort guesses it ships with
today.

This folder is empty right now because nothing in it has been captured
yet — see the repo's README ("Read this before trusting a run") and
`TESTING.md` step 2 for why, and for the exact command to produce one:

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
