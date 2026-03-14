# douyin-account-collector

A reusable OpenClaw skill for collecting videos and metadata from a Douyin creator account through an already logged-in visible Chrome session.

## What it does

This repository packages a practical Douyin account collection workflow that has already been validated in a real OpenClaw workspace.

Core capabilities:

- Reuse a manually logged-in Chrome session via CDP
- Detect challenge / CAPTCHA pages before batch work
- Collect candidate videos from a creator homepage
- Re-validate each video by author name and secUid
- Batch download only the target account's own videos
- Persist per-item metadata as JSON
- Persist run state in SQLite
- Skip already-downloaded items
- Export results to CSV / XLSX
- Generate a cleaned sample manifest for downstream analysis

## Repository structure

```text
.
├── SKILL.md
├── README.md
├── references/
│   ├── config.example.json
│   └── workflow.md
└── scripts/
    ├── douyin-batch-download-cdp.py
    ├── douyin-fetch-cdp.py
    ├── douyin-fetch-local.py
    ├── export_results.py
    └── generate_sample_manifest.py
```

## Main workflow

The most reliable path is:

1. Open the target Douyin creator homepage in a visible Chrome session
2. Manually complete login / challenge if Douyin asks for it
3. Reuse the live browser via CDP (`http://127.0.0.1:9222`)
4. Collect candidate videos from the homepage
5. Open each video page and verify the author matches the target account
6. Extract `playApi` / `playAddr` from the player config
7. Download the mp4 and write metadata

## Main script

### `scripts/douyin-batch-download-cdp.py`

This is the production entry point.

It supports:

- config file input
- CLI overrides
- retry/backoff
- `.part` atomic writes
- weak integrity checks
- SQLite recording
- metadata JSON output
- run summary JSON output

Example:

```bash
python scripts/douyin-batch-download-cdp.py \
  --config references/config.example.json
```

Or override directly:

```bash
python scripts/douyin-batch-download-cdp.py \
  --profile-url "https://www.douyin.com/user/SECUID" \
  --expected-author "阿昆说牙材" \
  --expected-secuid "MS4wLjABAAAAB..." \
  --cdp "http://127.0.0.1:9222"
```

## Outputs

Default output layout:

- videos: `data/exports/douyin-batch/`
- metadata: `data/exports/douyin-batch-meta/<aweme_id>.json`
- run summary: `data/exports/douyin-batch-meta/last-run.json`
- sqlite db: `data/exports/douyin-batch-meta/douyin_batch.db`
- sample manifest:
  - `data/exports/douyin-batch-meta/sample-manifest.json`
  - `data/exports/douyin-batch-meta/sample-manifest.md`

## Exporting results

### Export CSV / XLSX

```bash
python scripts/export_results.py \
  --db data/exports/douyin-batch-meta/douyin_batch.db \
  --csv data/exports/douyin-batch-meta/results.csv \
  --xlsx data/exports/douyin-batch-meta/results.xlsx
```

### Generate sample manifest

```bash
python scripts/generate_sample_manifest.py \
  --db data/exports/douyin-batch-meta/douyin_batch.db \
  --profile-url "https://www.douyin.com/user/SECUID" \
  --json-out data/exports/douyin-batch-meta/sample-manifest.json \
  --md-out data/exports/douyin-batch-meta/sample-manifest.md
```

## Notes

- This repository is for **account-level collection**, not generic web scraping.
- Douyin anti-bot is active. Manual session recovery is part of the workflow.
- The safest pattern is: visible browser first, automation second.
- Author re-validation is mandatory to avoid contaminating the dataset with recommended videos.

## License

Add the license you want for this repository.
