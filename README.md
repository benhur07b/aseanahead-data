# ASEAN AHEAD — programme data

Live data for the [ASEAN AHEAD Guide](https://aseanahead.bnhr.xyz) homepage
progress panels. A scheduled workflow pulls the published Google Sheets,
validates them, and commits `progress.csv` and `reach.csv`; the website reads
those files from this repo's raw URLs. Invalid sheet data is never committed,
so the site only ever sees data that passed the contract below.

## Files

| File | Contents |
|---|---|
| `progress.csv` | Verified course **completions** per beneficiary category and gender |
| `reach.csv` | **Course takers** (started, completed or not) per beneficiary category and gender |
| `meta.json` | Per-file freshness: maps each CSV's filename to the UTC time (ISO-8601, `Z` suffix) its contents last changed |

## The contract (frozen)

Both files use exactly this header, in this order:

```
category,female,male,others
```

- `category` — the beneficiary segment as reported to the ASEAN Foundation; never empty
- `female`, `male`, `others` — whole-number counts (no blanks, signs, or decimals);
  the three options mirror the pre-/post-course assessment forms, where
  `others` covers non-binary and "rather not say" responses
- Row order is preserved from the sheet and is meaningful (the site assigns
  bar colours in order)
- A response whose counts are all zero is rejected as a mid-recalculation
  snapshot from Google, not real data
- There is deliberately **no target column**: the programme target is a
  page-level constant on the website, not part of the data
- `meta.json` timestamps move only when the CSV next to them changes
  (granularity: one sync interval). A failed sync touches neither, so a
  timestamp always describes the published file—and a date that stops
  advancing means the numbers are old, not that the pipeline is healthy

**This format must not change.** The website validates against the same rules
on read (`index.html`) and its `tools/progress-sync.py` refreshes the site's
offline fallback from these files. If a change ever becomes unavoidable, the
consuming site must be updated first, then this repo—never the reverse.

## How it syncs

`.github/workflows/sync.yml` runs `sync.py` (Python stdlib only) every
15 minutes and on manual dispatch, committing only when validated data
changed. On fetch failure or contract violations the run fails visibly
and the last-known-good CSVs stay published.

Run locally: `python3 sync.py`

Exit codes: `0` synced, `65` sheet data violates the contract, `69` sheet
unreachable (retriable).

## Maintenance notes

- GitHub disables scheduled workflows in public repos after ~60 days without
  repository activity—watch for the re-enable email, or re-enable under
  Actions → Sync sheet data.
- raw.githubusercontent.com caches for ~5 minutes; total staleness is the
  cron interval plus that (~20 minutes worst case).
- The source sheet URLs live at the top of `sync.py`.
