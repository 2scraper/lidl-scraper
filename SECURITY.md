# Security

## Supported versions

Only the latest commit on `main` and the most recently tagged release get
security fixes. This project is pre-1.0 (see `CHANGELOG.md`) — older tags
are not backported.

## Reporting a vulnerability

Please report security issues privately — open a GitHub security advisory
on this repo, or email support@2captcha.com — rather than a public issue.
Include the version/commit and a minimal reproduction.

We aim to acknowledge a new report within 5 business days, confirm whether
it's in scope, and share a fix timeline once it's confirmed. If a report
turns out to be a real, exploitable issue, we'll credit the reporter in
`CHANGELOG.md` unless they'd rather stay anonymous.

## Scope

**In scope**: this repo's own code and dependencies — credential handling,
injection risks, anything that would make this tool leak a secret, execute
untrusted code, or misreport what it actually did (a `complete` status on a
run that silently dropped data would count, for example).

**Out of scope**: vulnerabilities in lidl.com itself — report those to Lidl
directly, not here. The fact that this tool can run without tripping a bot
challenge is a documented feature (see README's "Read this before trusting
a run"), not a vulnerability to report against this repo.

## What this tool does with credentials

- `TWOCAPTCHA_KEY`, `LIDL_PROXY`, `LIDL_CDP_ENDPOINT` are read only from `.env` or the environment (see `env_config.py`) — never from the command line, and never logged in full (`proxy_pool.py` masks credentials in every log line, keeping only host:port, which is the useful part of a log and not the secret).
- This project's own code doesn't phone home. The only network calls it makes are to `lidl.com` and, when configured, `api.2captcha.com` / `cb.2captcha.com`. **One caveat on the Selenium engine**: Selenium's own bundled Selenium Manager sends anonymous usage stats to `plausible.io` by default whenever it launches a browser, which `selenium_scraper.py` disables on your behalf (`SE_AVOID_STATS=true`, set as a default rather than forced, so it never overrides a value you set yourself) so this project's "nothing phones home" claim actually holds. Selenium Manager can still make a *separate* network call to resolve a matching `chromedriver` version if one isn't already reachable on `PATH` / `SELENIUM_CHROME_BIN` — set that variable to a Chrome/Chromium binary you already have to avoid it entirely (see `selenium_scraper.py`'s module docstring).
- This is a scraper, not an account tool: it never logs in, never touches an authenticated lidl.com session (Lidl Plus, saved payment methods, order history), and never writes anything back to lidl.com — no cart actions, no checkout, ever. `--zip`/`--store-id` only annotate which region a scraped price reflects; nothing in this repo places an order or saves a delivery address to an account.
