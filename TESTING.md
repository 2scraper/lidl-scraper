# Testing with real credentials and the live site

**Update, 2026-09-20**: step 2 below — "check what the real page looks
like" — has now been done once, manually, through a real Chromium browser
(not an engine run). It found the original URL scheme and framework
guesses wrong (see README's "Read this before trusting a run" and
`lidl_parser.py`'s module docstring for the full, corrected picture) and
`lidl_parser.py` has been updated to match what was actually seen, with a
new real-capture fixture in `tests/fixtures/lidl_search_real.html`. What
that manual pass did NOT do: run an actual engine (`playwright_scraper.py`
etc.) end-to-end, confirm the discount/"weekly deal" markup shape, or
confirm the store/zip session-binding mechanism. Everything below is
still the checklist for closing those remaining gaps for real.

Everything in this repo's own CHANGELOG/README not covered by the manual
capture above was verified offline (`smoke_test.py`, mostly SYNTHETIC
fixture replay — see that file's module docstring). **No engine has ever
been run against the live www.lidl.com, from any environment.** Unlike
skyscanner-scraper — whose build environment couldn't even READ the site
(robots.txt-restricted) — this repo's own non-browser fetch tooling could
reach lidl.com's robots.txt (wide open) and homepage cleanly, but every
deeper URL it tried by plain fetch returned a 404; the manual browser
capture above confirmed that was "a static fetch can't drive this app's
client-side router," not active blocking — a real rendered browser had no
trouble at all. This file is the checklist for closing the remaining gap
(an actual engine run) for real.

Run everything below from a normal terminal on your own machine — wherever
this repo lives for you.

## 1. Basic setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-playwright.txt
playwright install chromium
cp .env.example .env
```

Leave `.env` blank for the first run — the whole point of "local-first" is
that nothing in it is required. Fill in `TWOCAPTCHA_KEY` /
`LIDL_PROXY` / `LIDL_CDP_ENDPOINT` later, only if you want to test those
specifically.

## 2. The most important run you can do: check what the real page looks like

**Update, 2026-09-20 (later the same day) — this step has now been done
for real**: `python3 playwright_scraper.py --query "whole milk"
--max-results 10 --format json --out /tmp/lidl_test.json` (no `--dump-
html` needed that run, since it succeeded) landed on the FIRST outcome
below — exit `0`, `"status": "complete"`, 8 real products,
`price_confirmed_pct: 1.0`, plausible rows (see README "Read this before
trusting a run" for a real example row). This is the first live run of
the actual engine, not just a manual browser capture — the rest of this
section stays as the general decision tree for whoever runs it next
(a different query, a different day, after a site change), but "will this
even work at all" is now answered: yes, confirmed live.

This is the step nothing else in this repo could do for you:

```bash
python3 playwright_scraper.py --query "whole milk" \
  --max-results 10 --format json --out /tmp/lidl_test.json --dump-html
echo "exit code: $?"
cat /tmp/lidl_test.json.meta.json 2>/dev/null || echo "(no sidecar — see below)"
```

Four outcomes, and what each one means:

- **`exit code: 0`, a `.meta.json` with `"status": "complete"` and a
  believable `product_count`**: either the JSON-LD path, the
  `__NEXT_DATA__` heuristic, or the DOM fallback selectors happened to
  match the real page. Open `/tmp/lidl_test.json` and actually look at a
  few rows — a plausible-looking `price`/`brand`/`title` is what "happened
  to match" looks like; a null-filled row is what "matched the wrong
  shape" looks like even when the exit code says 0.
- **`exit code: 4` (zero products), no `.meta.json` written** (by design —
  see `output_writer.finish_run`): open `lidl_test_debug.html` and check,
  in this order: (1) does the raw HTML contain an `application/ld+json`
  block with `"@type":"Product"` or `"ItemList"` anywhere — if so,
  `lidl_parser._find_products_in_json_ld`'s property-name assumptions
  need adjusting, not the whole strategy; (2) does it contain
  `__NEXT_DATA__` at all — confirms or refutes the Next.js guess; (3)
  compare the actual product-tile markup against `lidl_parser.py`'s
  `_CARD_SELECTORS`. This is the expected first-run outcome if the
  selectors need updating — not evidence the free path is blocked.
- **`exit code: 3` (blocked)**: a `captcha_solver.GENERIC_BOT_CHALLENGE_
  MARKERS` hit, or an HTTP >=400 status. No incident like this has ever
  been captured for lidl.com — if you get one, this is genuinely new
  information: save a scrubbed capture, and add site-specific markers to
  `lidl_parser.BOT_CHALLENGE_MARKERS` the same way skyscanner-scraper's
  PerimeterX incident did for that repo (see its CHANGELOG entry for the
  shape of that kind of entry).
- **A real page renders but with a "select your store" / zip-code modal
  blocking the product grid**: this is the other likely first-run outcome
  given "Read this before trusting a run" in the README — if so, that
  modal's DOM (what it asks for, what closing it looks like) is exactly
  what `search_url()`'s `zip` param and a real region-selection step in
  the engines need to be built against. Capture it.

Whatever you find, **updating `lidl_parser.py`'s selectors/heuristics to
match what you actually saw — with a saved, scrubbed fixture under
`tests/fixtures/` and a new `smoke_test.py` check against it — is the
single most valuable contribution this repo can receive** (see
`CONTRIBUTING.md`).

## 3. Selenium, for real

```bash
python3 -m venv .venv-selenium   # separate venv — see README "Engines"
source .venv-selenium/bin/activate
pip install -r requirements-selenium.txt
python3 selenium_scraper.py --query "whole milk" --out /tmp/lidl_selenium.json
```

## 4. Puppeteer (pyppeteer), for real

```bash
python3 -m venv .venv-puppeteer
source .venv-puppeteer/bin/activate
pip install -r requirements-puppeteer.txt
python3 puppeteer_scraper.py --query "whole milk" --out /tmp/lidl_puppeteer.json
```

## 5. The 2Captcha REST API, with your real key

Confirms the key and hits a real, billed-nothing endpoint first:

```bash
python3 -c "
import env_config
from scraper_api_client import TwoCaptchaClient
args = type('A', (), {'twocaptcha_key': None, 'proxy': None, 'cdp_endpoint': None, 'url': None})()
env_config.apply_env(args)
c = TwoCaptchaClient(args.twocaptcha_key)
print('balance: \$%.2f' % c.get_balance())
"
```

## 6. The Scraping Browser API (`--cdp-endpoint`), for real

`LIDL_CDP_ENDPOINT` in `.env` is picked up automatically:

```bash
python3 playwright_scraper.py --query "whole milk" --out /tmp/lidl_cdp.json
```

**Gotcha**, same as the rest of the family: if `.env` has BOTH
`LIDL_CDP_ENDPOINT` and `LIDL_PROXY` set, the code ignores `LIDL_PROXY`
and warns — a CDP session already carries its own exit IP, stacking a
second one on top is a contradiction, not better cover (same for a
fingerprint over `--cdp-endpoint`). Comment out whichever you're not
testing if you want to test them in isolation.

## 7. The residential proxy (`--proxy` / `LIDL_PROXY`), for real

```bash
python3 playwright_scraper.py --query "whole milk" --out /tmp/lidl_proxy.json
```

## 8. Push to GitHub and let CI do the rest

```bash
git remote add origin git@github.com:2scraper/lidl-scraper.git
git push -u origin main
git push --tags
```

Then, in the GitHub repo's Settings:

- **Secrets and variables → Actions**: add `TWOCAPTCHA_KEY`,
  `LIDL_CDP_ENDPOINT` / `LIDL_PROXY` (only if you want the `canary-cdp`
  job using them — `canary-local` needs no secrets at all), and
  `CLAUDE_CODE_OAUTH_TOKEN` (for `claude.yml` / `claude-code-review.yml` —
  both silently no-op without it, by design, rather than failing every
  PR check).
- **Actions → canary → Run workflow**: dispatch it manually at least once
  rather than waiting a day for the cron and trusting the badge blind —
  this is this repo's actual FIRST live test, so look at the run's log and
  uploaded artifact, not just the badge color.

## 9. What "done" looks like

- `tests.yml` green on both Python versions and all three `engine-smoke`
  matrix legs.
- At least one manually-dispatched `canary.yml` run, looked at — not just
  the badge — including whichever of the outcomes in step 2 above it
  landed on.
- If step 2 landed on anything other than a clean `complete` with
  plausible-looking rows: `lidl_parser.py` updated to match what you
  actually saw, with a fixture under `tests/fixtures/` and a new
  `smoke_test.py` check, per `CONTRIBUTING.md`.
- The store/zip-selection question (see "Read this before trusting a run"
  in the README) answered one way or the other, with the mechanism
  documented here and in `lidl_parser.py`'s module docstring.
