#!/usr/bin/env python3
"""Fetch the published Google Sheet CSVs, validate them against the frozen
contract (see README.md), and write progress.csv / reach.csv.

Stdlib only—runs on a bare GitHub Actions Python with no installs.

A file is only ever overwritten with data that passed validation; on any
fetch or validation failure the existing file is left untouched and the
exit code reports the failure, so the workflow run shows red while the
last-known-good data stays published.

Exit codes:
  0   all sheets synced (written or already up to date)
  65  a sheet returned data that violates the contract
  69  a sheet was unreachable after retries (retriable)
"""

from __future__ import annotations

import csv
import io
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

FETCH_TIMEOUT_S = 60
FETCH_ATTEMPTS = 3
COLUMNS = ("category", "female", "male", "others")
GENDERS = COLUMNS[1:]

DOC = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQyzECunprHBFcHi_Xsd64BwXchQlMTJT0IAbZXcMHPPtLgIY5Vh6eHzrg_r9gLohc1-9qqvnmoGNOc/pub"
SHEETS = (
    ("progress.csv", f"{DOC}?gid=0&single=true&output=csv"),
    ("reach.csv", f"{DOC}?gid=1472833579&single=true&output=csv"),
)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def fetch(url: str) -> str:
    # Google's published-CSV endpoint rejects Python's default User-Agent,
    # and cold requests are slow enough that timeouts are worth retrying.
    req = urllib.request.Request(url, headers={"User-Agent": "aseanahead-data-sync/1.0"})
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as res:
                return res.read().decode("utf-8")
        except (TimeoutError, urllib.error.URLError, OSError):
            if attempt == FETCH_ATTEMPTS:
                raise
            info(f"fetch attempt {attempt}/{FETCH_ATTEMPTS} failed, retrying…")
    raise RuntimeError("unreachable")


def validate(records: list[list[str]]) -> list[str]:
    """Return contract violations (empty list = valid)."""
    if not records:
        return ["sheet returned no rows"]
    header = [h.strip().lower() for h in records[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        return [f"header is missing the {', '.join(missing)} column(s), got: {', '.join(header)}"]
    problems: list[str] = []
    total = 0
    for line_no, record in enumerate(records[1:], start=2):
        if len(record) != len(header):
            problems.append(f"line {line_no}: expected {len(header)} columns, got {len(record)}")
            continue
        r = {name: field.strip() for name, field in zip(header, record)}
        if not r["category"]:
            problems.append(f"line {line_no}: missing category")
        for g in GENDERS:
            if not re.fullmatch(r"\d+", r[g]):
                problems.append(f'line {line_no}: {g} must be a whole number, got "{r[g]}"')
            else:
                total += int(r[g])
    if not problems and total == 0:
        problems.append("every count is zero—likely a mid-recalculation snapshot, not real data")
    return problems


def canonical(records: list[list[str]]) -> str:
    """Project to exactly the contract columns, in contract order."""
    header = [h.strip().lower() for h in records[0]]
    at = {c: header.index(c) for c in COLUMNS}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for record in records[1:]:
        writer.writerow([record[at[c]].strip() for c in COLUMNS])
    return out.getvalue()


def main() -> int:
    exit_code = 0
    for filename, url in SHEETS:
        try:
            fetched = fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            info(f"error: {filename}: could not fetch the sheet ({e}); file left untouched")
            exit_code = max(exit_code, 69)
            continue
        records = [r for r in csv.reader(io.StringIO(fetched)) if any(f.strip() for f in r)]
        problems = validate(records)
        if problems:
            for p in problems:
                info(f"error: {filename}: {p}")
            info(f"{filename}: sheet data rejected; file left untouched")
            exit_code = max(exit_code, 65)
            continue
        path = Path(__file__).resolve().parent / filename
        text = canonical(records)
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            print(f"{filename}: up to date")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"{filename}: updated")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
