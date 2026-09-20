# Playwright engine only (the primary one, local-first — see README) —
# Selenium/pyppeteer need their own images per requirements-*.txt (the
# three engines' pins are mutually unsatisfiable in one environment; see
# requirements-puppeteer.txt).
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt requirements-playwright.txt ./
RUN pip install --no-cache-dir -r requirements-playwright.txt \
    && playwright install --with-deps chromium

# smoke_test.py imports puppeteer_scraper.py and selenium_scraper.py at
# module level UNCONDITIONALLY (only each engine's own driver import is
# guarded, not the file's presence) — leaving either module out here makes
# `RUN python3 smoke_test.py` crash the build with a plain
# ModuleNotFoundError, before ever reaching the guarded-import checks it
# exists to run (same lesson the rest of the family's Dockerfiles
# document). Copying the .py file costs nothing: selenium/pyppeteer aren't
# installed in this image, so their own try/except ImportError guard is
# exactly what fires, the same as smoke_test.py's no-engine-installed path
# in CI.
COPY env_config.py proxy_pool.py output_writer.py captcha_solver.py \
     fingerprint_client.py scraper_api_client.py diff_runs.py \
     lidl_parser.py playwright_scraper.py puppeteer_scraper.py \
     selenium_scraper.py smoke_test.py ./
# smoke_test.py also reads these straight off disk (the ENV_KEYS<->
# .env.example sync check and the sample-output-schema check) — missing
# any one crashes the build the same way the two .py files above would.
COPY .env.example sample_output.json sample_output.csv ./
COPY tests ./tests

RUN python3 smoke_test.py

# The test suite and its fixtures are needed to VERIFY the build above, not
# to run the scraper — the image should carry no test suite and no stray
# .env (a .env was never COPYed here in the first place; this is the "no
# test suite / no fixtures" half of that same rule). Strip them from the
# final layer rather than leaving them in a published image.
RUN rm -rf smoke_test.py tests

ENTRYPOINT ["python3", "playwright_scraper.py"]
CMD ["--help"]
