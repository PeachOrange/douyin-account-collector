---
name: douyin-account-collector
description: Collect videos and metadata from a Douyin account via an already logged-in visible Chrome session, then batch download, clean, and validate the account's own videos. Use when the user wants to scrape a Douyin account, batch download that account's videos, avoid mixed-in recommendation videos, reuse a manual login/CAPTCHA-passed browser session, or build a downstream workflow such as persona analysis, subtitle extraction, or content research from a Douyin creator account.
---

# Douyin Account Collector

Use this skill for **Douyin account-level collection**, not generic web scraping.

This skill assumes Douyin web anti-bot is active. The reliable path is:
1. user manually restores/login in visible Chrome/VNC
2. script reuses that live session via CDP
3. script extracts only the target account's videos
4. script downloads videos and stores metadata

## Workflow

### 1. Prepare the browser session
If Douyin is in challenge state, first restore the visible browser to the target account homepage.

Use the existing logged-in Chrome with remote debugging rather than launching a fresh browser whenever possible.

### 2. Validate homepage state
Before batch work, verify the page is **not** on:
- `验证码中间页`
- `请完成下列验证后继续`

If the homepage is challenged, stop and ask the user to restore the visible session first.

### 3. Collect candidate videos
Use `scripts/douyin-batch-download-cdp.py` to:
- connect over CDP
- scan the account homepage for `/video/<aweme_id>` links
- exclude obvious junk candidates such as spider/external links

Do **not** trust homepage candidates alone.

### 4. Re-validate each video by author
For each candidate, open the video page and confirm it belongs to the intended creator by checking:
- `authorName`
- `secUid`

Only keep videos that match the target account.

This is the most important anti-contamination step.

### 5. Download and persist
Use player config fields in priority order:
1. `playApi`
2. `playApiH265`
3. `playAddr` / `playAddrH265`
4. `bitRateList[*].playAddr`

The batch script already supports:
- atomic `.part` downloads
- retry/backoff
- weak file-size integrity checks
- metadata JSON per item
- SQLite run state
- skip already-downloaded files

### 6. Treat output as a dataset
After collection, use the cleaned dataset for:
- persona analysis
- subtitle extraction
- content pattern analysis
- training/reference material for a downstream skill

## Scripts

### `scripts/douyin-batch-download-cdp.py`
Main production script.

Use it when:
- the browser is already logged in
- a creator homepage is openable in visible Chrome
- batch collection is the goal

Supports:
- config file or CLI overrides for profile URL / author / secUid / output paths / CDP endpoint
- SQLite state
- metadata JSON per item
- run summary JSON
- retry/backoff
- atomic `.part` downloads
- weak integrity checks

Outputs:
- videos: `data/exports/douyin-batch/`
- per-item metadata: `data/exports/douyin-batch-meta/<aweme_id>.json`
- run summary: `data/exports/douyin-batch-meta/last-run.json`
- sqlite state: `data/exports/douyin-batch-meta/douyin_batch.db`

### `scripts/douyin-fetch-cdp.py`
Single-video collector via existing CDP-connected browser.

Use it when debugging one video or validating player extraction logic.

### `scripts/douyin-fetch-local.py`
Fallback script that launches its own Playwright browser.

Use it only for experiments or when a live CDP session is unavailable. In practice, Douyin often challenges this path.

### `scripts/export_results.py`
Export SQLite results into CSV, and emit XLSX when `openpyxl` is available.

Use it when the user wants a spreadsheet-style export for review, sharing, or downstream analysis.

### `scripts/generate_sample_manifest.py`
Generate a cleaned sample manifest from the SQLite dataset.

Use it when the user wants a compact sample index for review, persona analysis, or downstream skill-building.

## Operational rules

- Prefer **CDP reuse of a visible logged-in browser** over fresh headless sessions.
- If the user says the homepage is ready, verify it before batch operations.
- Do not mix recommendation videos into the final dataset.
- Keep invalid/mixed results archived instead of deleting immediately when auditing matters.
- When a batch fails, surface whether the cause was:
  - challenge page
  - author mismatch
  - no download URL
  - timeout
  - integrity failure

## References

- For the cleaned workflow and known failure modes, read `references/workflow.md`.
- For reusable account-level configuration, copy and adapt `references/config.example.json`.
