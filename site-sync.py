#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "typer>=0.12",
# ]
# ///
"""Refresh the website's offline fallback data files from this repo's CSVs.

For each site JS data file given (by default progress.js and progress-reach.js
in a sibling checkout of the website), locates its window.<NAME>_CSV backtick
block, replaces it with the validated contents of this repo's matching CSV
(PROGRESS → progress.csv), and sets window.<NAME>_UPDATED to that CSV's
meta.json stamp—the UTC time the data last changed. Nothing else in the
files is touched, so their comments and source switches survive every sync.

No network: the committed CSVs, already validated by sync.py before every
commit, are the source of truth here. Run after a sync lands to keep the
site's fallback numbers and its freshness note in step with the repo.

Assumptions:
  - Each JS file declares exactly one window.<NAME>_CSV = `...` block and a
    window.<NAME>_UPDATED = '...' declaration next to it.
  - <NAME> lowercased names this repo's CSV, which has a meta.json stamp.

Exit codes:
  0   success (files written, or already up to date)
  1   unexpected failure
  2   pre-flight failure (missing file, declaration, CSV, or stamp)
  65  a repo CSV violates the contract (hand-edited without validation?)
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

VERSION = "1.0.0"
GENDERS = ("female", "male", "others")
COLUMNS = ("category", *GENDERS)

DATA_DIR = Path(__file__).resolve().parent
DEFAULT_JS_FILES = [
    DATA_DIR.parent / "aseanahead/static/assets/js" / f
    for f in ("progress.js", "progress-reach.js")
]

app = typer.Typer(add_completion=False)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


@dataclass(frozen=True, slots=True)
class Panel:
    path: Path
    name: str            # e.g. PROGRESS—from the file's window.<NAME>_CSV block
    text: str            # current file content
    csv: str             # current CSV block content
    stamp: str           # current <NAME>_UPDATED value
    data_path: Path      # this repo's CSV for that block
    new_stamp: str       # repo meta.json stamp for that CSV


def load_panel(path: Path, data_dir: Path, meta: dict[str, str]) -> Panel | str:
    """Pair a JS file with its repo CSV and stamp; return an error string on failure."""
    if not path.is_file():
        return f"{path}: file not found"
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"window\.(\w+)_CSV\s*=\s*`([^`]*)`", text)
    if len(blocks) != 1:
        return f"{path.name}: expected exactly one window.<NAME>_CSV backtick block, found {len(blocks)}"
    name, block = blocks[0]
    stamp = re.search(rf"window\.{name}_UPDATED\s*=\s*'([^']*)'", text)
    if not stamp:
        return f"{path.name}: window.{name}_UPDATED declaration not found (add it next to {name}_CSV)"
    data_path = data_dir / f"{name.lower()}.csv"
    if not data_path.is_file():
        return f"{path.name}: no {data_path.name} in {data_dir} for window.{name}_CSV"
    if data_path.name not in meta:
        return f"{data_path.name}: no stamp in meta.json (run sync.py first)"
    return Panel(path, name, text, block, stamp.group(1), data_path, meta[data_path.name])


def validate(csv_text: str, allow_zero: bool) -> list[str]:
    """Mirror index.html's row validation; return a list of problems (empty = valid)."""
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if any(f.strip() for f in r)]
    if not rows:
        return ["file has no rows"]
    header = [h.strip().lower() for h in rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        return [f"header is missing the {', '.join(missing)} column(s), got: {', '.join(header)}"]
    problems: list[str] = []
    total = 0
    for line_no, record in enumerate(rows[1:], start=2):
        if len(record) != len(header):
            problems.append(f"line {line_no}: expected {len(header)} columns, got {len(record)}")
            continue
        r = dict(zip(header, (f.strip() for f in record)))
        if not r["category"]:
            problems.append(f"line {line_no}: missing category")
        for k in GENDERS:
            if not re.fullmatch(r"\d+", r[k]):
                problems.append(f'line {line_no}: {k} must be a whole number, got "{r[k]}"')
            else:
                total += int(r[k])
    if not problems and total == 0 and not allow_zero:
        problems.append("every count is zero (use --allow-zero to write it anyway)")
    return problems


def normalize(csv_text: str) -> str:
    """Canonical block content: only the contract columns, in contract order,
    LF endings, one trailing newline."""
    records = [r for r in csv.reader(io.StringIO(csv_text)) if any(f.strip() for f in r)]
    header = [h.strip().lower() for h in records[0]]
    at = {c: header.index(c) for c in COLUMNS}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for record in records[1:]:
        writer.writerow([record[at[c]].strip() for c in COLUMNS])
    return out.getvalue()


def splice(panel: Panel, new_csv: str) -> str:
    text = panel.text.replace(
        f"window.{panel.name}_CSV = `{panel.csv}`",
        f"window.{panel.name}_CSV = `{new_csv}`",
        1,
    )
    return text.replace(
        f"window.{panel.name}_UPDATED = '{panel.stamp}'",
        f"window.{panel.name}_UPDATED = '{panel.new_stamp}'",
        1,
    )


def diff(panel: Panel, new_csv: str) -> str:
    frm = f"{panel.stamp}\n{panel.csv}".splitlines(keepends=True)
    to = f"{panel.new_stamp}\n{new_csv}".splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        frm, to,
        fromfile=f"{panel.path.name} (current)", tofile=f"{panel.path.name} (repo)",
    ))


def version_callback(value: bool) -> None:
    if value:
        print(f"site-sync {VERSION}")
        raise typer.Exit()


@app.command(
    help=__doc__,
    epilog=(
        "Examples:\n\n"
        "  uv run site-sync.py                        refresh the sibling-checkout site files\n\n"
        "  uv run site-sync.py path/to/progress.js    refresh only the given file(s)\n\n"
        "  uv run site-sync.py --dry-run              show what would change, write nothing\n"
    ),
)
def main(
    js_files: Annotated[list[Path] | None, typer.Argument(help="Site JS data files to update (default: progress.js and progress-reach.js in ../aseanahead).", show_default=False)] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show what would change without writing.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Print the diff for each changed file.")] = False,
    allow_zero: Annotated[bool, typer.Option("--allow-zero", help="Write repo data even if every count is zero.")] = False,
    data_dir: Annotated[Path, typer.Option("--data-dir", help="Directory holding the CSVs and meta.json.")] = DATA_DIR,
    _version: Annotated[bool, typer.Option("--version", callback=version_callback, is_eager=True, help="Print version and exit.")] = False,
) -> None:
    try:
        meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        assert isinstance(meta, dict)
    except (OSError, ValueError, AssertionError):
        info(f"error: no readable meta.json in {data_dir} (run sync.py first)")
        raise typer.Exit(2)

    # Pre-flight: every file must pair up cleanly before any write.
    panels: list[Panel] = []
    preflight: list[str] = []
    for path in js_files or DEFAULT_JS_FILES:
        match load_panel(path, data_dir, meta):
            case Panel() as p:
                panels.append(p)
            case str() as err:
                preflight.append(err)
    if preflight:
        for err in preflight:
            info(f"error: {err}")
        info("Pre-flight failed—nothing was written.")
        raise typer.Exit(2)

    exit_code = 0
    for panel in panels:
        data_text = panel.data_path.read_text(encoding="utf-8")
        problems = validate(data_text, allow_zero)
        if problems:
            for p in problems:
                info(f"error: {panel.data_path.name}: {p}")
            info(f"{panel.path.name}: repo data rejected; file left untouched")
            exit_code = max(exit_code, 65)
            continue
        new_csv = normalize(data_text)
        if new_csv == panel.csv and panel.new_stamp == panel.stamp:
            print(f"{panel.path.name}: up to date")
            continue
        if verbose or dry_run:
            info(diff(panel, new_csv))
        if dry_run:
            print(f"{panel.path.name}: DRY RUN—would update")
            continue
        panel.path.write_text(splice(panel, new_csv), encoding="utf-8")
        print(f"{panel.path.name}: updated (data as of {panel.new_stamp})")

    raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
