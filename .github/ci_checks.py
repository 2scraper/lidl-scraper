#!/usr/bin/env python3
"""ci_checks.py — the credential scan, in ONE place. Invoked by tests.yml
and by smoke_test.py, so a local run and CI cannot disagree about what
counts as a leak.

    python3 .github/ci_checks.py --secret-check [--root DIR]

What it looks for, in every file under the repository root — tracked or
not, because an untracked `.env.bak` is exactly the file one `git add -A`
away from a commit:

  - a bare 32-character lowercase hex string (the shape of a 2Captcha key);
  - a key-named field with a long value, quoted, unquoted or JSON-escaped
    (`TWOCAPTCHA_KEY=…`, `api_key = "…"`, `\\"apiKey\\": \\"…\\"`);
  - a URL carrying `user:password@` that is not an obvious placeholder.

What it skips: `.git`, `__pycache__`, `node_modules`, any directory holding a
`pyvenv.cfg` (a virtualenv, whatever it is called) and the working `.env`
itself, which is where credentials are SUPPOSED to live and is ignored by
git. Exclusions are judged on the path RELATIVE to the root, so a clone
under /tmp, ~/build or ~/out is scanned like any other.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache"}
SKIP_FILES = {".env"}
MAX_BYTES = 8 * 1024 * 1024

HEX_KEY = re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{32}(?![0-9A-Fa-f])")
KEY_FIELD = re.compile(
    r"(?i)\b(?:twocaptcha[_-]?key|api[_-]?key|apikey|client[_-]?key|secret[_-]?key)"
    r"\\?[\"']?\s*[:=]\s*\\?[\"']?([A-Za-z0-9_\-]{20,})"
)
CRED_URL = re.compile(
    r"\b(?:https?|wss?|socks5h?)://([^\s:/@'\"<>\\]+):([^\s@/'\"<>\\]+)@([^\s/'\"<>:\\]+)"
)
# Placeholder spellings used in this repo's docs and in its masking tests.
FAKE_PARTS = {"user", "login", "pass", "password", "secret", "mysecret", "secretpass", "myuser"}
FAKE_HOST_SUFFIXES = (".example", ".test", ".invalid", "example.com")


def _is_fake_cred(user: str, password: str, host: str) -> bool:
    if any(c in user + password for c in "{}<>$*"):
        return True
    if password.lower() in FAKE_PARTS or user.lower() in FAKE_PARTS:
        return True
    host = host.lower()
    return host in ("host", "localhost") or host.endswith(FAKE_HOST_SUFFIXES)


def scanned_files(root: Path) -> List[Path]:
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_parts = here.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            dirnames[:] = []
            continue
        if here != root and (here / "pyvenv.cfg").exists():
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not rel_parts and name in SKIP_FILES:
                continue
            path = here / name
            try:
                if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            out.append(path)
    return out


def scan(root: Path) -> Tuple[int, List[str]]:
    root = root.resolve()
    findings: List[str] = []
    files = scanned_files(root)
    for path in files:
        try:
            text = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), 1):
            if HEX_KEY.search(line):
                findings.append(f"{rel}:{lineno}: a 32-char hex string (the shape of a 2Captcha key)")
            m = KEY_FIELD.search(line)
            if m:
                findings.append(f"{rel}:{lineno}: a key-named field with a {len(m.group(1))}-char value")
            for um in CRED_URL.finditer(line):
                if not _is_fake_cred(*um.groups()):
                    findings.append(f"{rel}:{lineno}: a URL with embedded credentials (host {um.group(3)})")
    return len(files), findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--secret-check", action="store_true", required=True)
    p.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = p.parse_args()
    count, findings = scan(Path(args.root))
    if count == 0:
        print("::error::credential scan read 0 files — it scanned nothing, which is not 'clean'")
        return 1
    if findings:
        for f in findings:  # never print the matched value itself
            print(f)
        print(f"::error::possible credential in {len(findings)} place(s) — see above")
        return 1
    print(f"credential scan: {count} files, nothing credential-shaped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
