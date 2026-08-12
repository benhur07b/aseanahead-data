#!/usr/bin/env python3
"""Fetch the published Google Sheet CSVs, validate each against its frozen
contract (see README.md), and write progress.csv / reach.csv / events.csv.

Stdlib only—runs on a bare GitHub Actions Python with no installs.

A file is only ever overwritten with data that passed validation; on any
fetch or validation failure the existing file is left untouched and the
exit code reports the failure, so the workflow run shows red while the
last-known-good data stays published.

meta.json maps each data file to the UTC time its contents last changed
(granularity: one sync interval). A failed sync never touches a file's
entry—the timestamp always describes the published CSV next to it, and a
date that stops advancing is the visible sign the numbers are old.

Exit codes:
  0   all sheets synced (written or already up to date)
  65  a sheet returned data that violates the contract
  69  a sheet was unreachable after retries (retriable)
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

FETCH_TIMEOUT_S = 60
FETCH_ATTEMPTS = 3

COUNT_COLUMNS = ("category", "female", "male", "others")
GENDERS = COUNT_COLUMNS[1:]
EVENT_COLUMNS = ("date", "time", "title", "modality", "venue", "city", "host", "register_url", "notes")
MODALITIES = ("Webinar", "In-person", "Hybrid", "Self-paced")

COUNTS_DOC = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQyzECunprHBFcHi_Xsd64BwXchQlMTJT0IAbZXcMHPPtLgIY5Vh6eHzrg_r9gLohc1-9qqvnmoGNOc/pub"
EVENTS_DOC = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTb9QKPZR9UqYA1cAI-MVZsvQAE4ybnLR4KvfVHtLtRDE3-ZFJa0Rl0mTJk_nb32cRP2hlxEdq4gStj/pub"
# (filename, contract columns, row validator, source URL)
SHEETS = (
    ("progress.csv", COUNT_COLUMNS, "counts", f"{COUNTS_DOC}?gid=0&single=true&output=csv"),
    ("reach.csv", COUNT_COLUMNS, "counts", f"{COUNTS_DOC}?gid=1472833579&single=true&output=csv"),
    ("events.csv", EVENT_COLUMNS, "events", f"{EVENTS_DOC}?gid=0&single=true&output=csv"),
)


META = "meta.json"


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_meta(path: Path) -> dict[str, str]:
    # A missing or malformed meta.json is rebuilt from this run's stamps
    # rather than aborting the sync—the CSVs are the data, this is bookkeeping.
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(meta, dict) and all(isinstance(v, str) for v in meta.values()):
        return meta
    return {}


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


def header_problems(records: list[list[str]], columns: tuple[str, ...]) -> list[str]:
    if not records:
        return ["sheet returned no rows"]
    header = [h.strip().lower() for h in records[0]]
    missing = [c for c in columns if c not in header]
    if missing:
        return [f"header is missing the {', '.join(missing)} column(s), got: {', '.join(header)}"]
    return []


def rows(records: list[list[str]]):
    """Yield (line_no, row-dict) per record; None for width-mismatched rows."""
    header = [h.strip().lower() for h in records[0]]
    for line_no, record in enumerate(records[1:], start=2):
        if len(record) != len(header):
            yield line_no, None
        else:
            yield line_no, {name: field.strip() for name, field in zip(header, record)}


def validate_counts(records: list[list[str]]) -> list[str]:
    """Beneficiary-count contract violations (empty list = valid)."""
    if problems := header_problems(records, COUNT_COLUMNS):
        return problems
    total = 0
    for line_no, r in rows(records):
        if r is None:
            problems.append(f"line {line_no}: expected {len(records[0])} columns, got a different count")
            continue
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


def validate_events(records: list[list[str]]) -> list[str]:
    """Event-schedule contract violations, mirroring join.html's row rules."""
    if problems := header_problems(records, EVENT_COLUMNS):
        return problems
    for line_no, r in rows(records):
        if r is None:
            problems.append(f"line {line_no}: expected {len(records[0])} columns, got a different count")
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]) or not valid_date(r["date"]):
            problems.append(f'line {line_no}: bad date "{r["date"]}" (need YYYY-MM-DD)')
        if r["modality"] not in MODALITIES:
            problems.append(f'line {line_no}: bad modality "{r["modality"]}" (need {" | ".join(MODALITIES)})')
        if not r["title"]:
            problems.append(f"line {line_no}: missing title")
    return problems


def valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


VALIDATORS = {"counts": validate_counts, "events": validate_events}


def canonical(records: list[list[str]], columns: tuple[str, ...]) -> str:
    """Project to exactly the contract columns, in contract order."""
    header = [h.strip().lower() for h in records[0]]
    at = {c: header.index(c) for c in columns}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for record in records[1:]:
        writer.writerow([record[at[c]].strip() for c in columns])
    return out.getvalue()


def main() -> int:
    root = Path(__file__).resolve().parent
    meta = load_meta(root / META)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    exit_code = 0
    for filename, columns, contract, url in SHEETS:
        try:
            fetched = fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            info(f"error: {filename}: could not fetch the sheet ({e}); file left untouched")
            exit_code = max(exit_code, 69)
            continue
        records = [r for r in csv.reader(io.StringIO(fetched)) if any(f.strip() for f in r)]
        problems = VALIDATORS[contract](records)
        if problems:
            for p in problems:
                info(f"error: {filename}: {p}")
            info(f"{filename}: sheet data rejected; file left untouched")
            exit_code = max(exit_code, 65)
            continue
        path = root / filename
        text = canonical(records, columns)
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            print(f"{filename}: up to date")
            meta.setdefault(filename, now)  # backfill a lost stamp only
        else:
            path.write_text(text, encoding="utf-8")
            meta[filename] = now
            print(f"{filename}: updated")

    meta_text = json.dumps(dict(sorted(meta.items())), indent=2) + "\n"
    meta_path = root / META
    if not meta_path.is_file() or meta_path.read_text(encoding="utf-8") != meta_text:
        meta_path.write_text(meta_text, encoding="utf-8")
        print(f"{META}: updated")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
