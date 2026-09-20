# Lidl Scraper by 2scraper

**Open-source grocery product scraper for lidl.com (Lidl US) — three engines, your own infrastructure by default, 2Captcha's paid products when you actually need them.**

Pull product search and weekly-deal results — title, brand, price, unit price, discount, image, product link — straight from Lidl into JSON or CSV.

[**View source on GitHub →**](https://github.com/2scraper/lidl-scraper)

---

## Before you scrape: official channels

Check Lidl's own site and the Lidl Plus app for anything a formal integration or public feed already covers your use case. This scraper exists for everything outside that: quick price checks, personal grocery-price tracking, and use cases a formal partnership doesn't fit.

## Read this before you rely on it

This project's own build tooling could reach lidl.com's `robots.txt` (wide open — no restriction) and its homepage (a real storefront), but every deeper page it tried came back 404 to a plain, non-browser fetch — most likely because those routes are client-rendered, not because of active blocking. The URL scheme this scraper builds is confirmed real (matched against Google's own index of live lidl.com pages); the exact selectors, embedded-data shape, and pagination model are documented best-effort guesses pending a real, browser-rendered capture. The architecture (exit codes, output schema, dedupe, credential handling, all three engines) is real and tested. Full honesty section in the [repository README](https://github.com/2scraper/lidl-scraper#readme) — read it before you point this at anything that matters.

## What you get

- Free, open-source scraper, one script per engine — **Playwright** (primary, local-first), **Selenium**, and **Puppeteer** (via pyppeteer), all producing the identical output schema and exit codes
- Search by product term, or browse a weekly-deals/specials category
- Reads lidl.com's own schema.org structured product data first, with an embedded-state and DOM fallback — same family principle as this project's sibling scrapers
- Grocery-specific fields other 2scraper repos don't need: unit price, unit size, original (pre-discount) price, discount %, weekly-deal flag, and the store/zip a price reflects
- JSON and CSV export, with a documented `Product` schema and a `.meta.json` sidecar on every completed/partial run
- Optional 2Captcha integration, wired in but never required to get started

## 2Captcha products, when you want them

| Product | What it's for |
|---|---|
| **Captcha solving — [2captcha.com](https://2captcha.com)** | Detects a challenge, decides whether it's actually blocking you (not just present), solves it |
| **Scraping Browser API — 2captcha.com** | A remote browser session over CDP with its own proxy, fingerprint and captcha auto-solve bundled — `--cdp-endpoint` |
| **Browser fingerprints — 2captcha Fingerprint API** | Pin a specific OS/browser/country fingerprint for a locally-launched browser |
| **Proxies — 2captcha.com/proxy** (2prx.com is the same product, different name) | Drop credentials into `.env`, rotated automatically with per-exit failure tracking |

## Who this is for

Grocery-price researchers, deal-tracking tools, and anyone who wants Lidl US search/weekly-deal results in a script rather than a browser tab — and is comfortable that this repo's parsing layer is a documented work in progress (see README).

## Get started

```bash
git clone https://github.com/2scraper/lidl-scraper.git
cd lidl-scraper
pip install -r requirements-playwright.txt && playwright install chromium
cp .env.example .env   # optional — not required for a normal local-first run

python3 playwright_scraper.py --query "whole milk" --format json --out lidl_results.json
```

Full setup, CLI reference, and configuration details in the [repository README](https://github.com/2scraper/lidl-scraper#readme).

---

**Need it running at scale, with proxies, fingerprints, and captcha solving already configured?**
[Talk to us →](https://2captcha.com/contact) · Proxies by [2captcha.com/proxy](https://2captcha.com/proxy) · Scraping Browser API & captcha solving by [2captcha.com](https://2captcha.com)
