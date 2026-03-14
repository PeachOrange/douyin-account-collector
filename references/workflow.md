# Douyin Account Collector Workflow Notes

## What is already proven in this workspace

The following path has already been validated:

1. restore the target Douyin homepage in visible Chrome/VNC
2. connect via CDP (`http://127.0.0.1:9222`)
3. collect homepage `/video/<aweme_id>` candidates
4. open each video page
5. extract player config from `window.player.config.awemeInfo.video`
6. use `playApi` / `playAddr` / bitrate-derived URLs to download mp4
7. keep only videos whose `authorName` or `secUid` matches the target account

## Known failure modes

### 1. Challenge page at homepage
Symptoms:
- title becomes `验证码中间页`
- body contains `请完成下列验证后继续`

Action:
- stop automation
- ask the user to restore the visible session
- retry only after confirmation

### 2. Mixed-in recommendation videos
Cause:
- homepage contains non-author `/video/` links

Action:
- never trust homepage candidates alone
- always re-check author on the video page

### 3. Blob-only video tag
Sometimes `<video>` only exposes a `blob:` URL.

Action:
- inspect `window.player.config.awemeInfo.video`
- prefer `playApi`, then `playAddr`, then bitrate entries

## Output conventions

### Videos
- `data/exports/douyin-batch/<aweme_id>.mp4`

### Metadata
- `data/exports/douyin-batch-meta/<aweme_id>.json`
- `data/exports/douyin-batch-meta/last-run.json`
- `data/exports/douyin-batch-meta/douyin_batch.db`
- `data/exports/douyin-batch-meta/sample-manifest.json`
- `data/exports/douyin-batch-meta/sample-manifest.md`

### Invalid or contaminated results
When cleaning datasets, move bad results to archive-style folders rather than hard deleting immediately.

## Session recovery prompts

When homepage validation fails, the operator prompt should be explicit:

- Restore visible Chrome/VNC to the target account homepage
- Ensure the page title is the account title, not the challenge page
- Do not click into unrelated videos before resuming automation
- Confirm with a short ack such as `主页恢复好了`

## What to improve next

- subtitle extraction pipeline after download
- direct export into analysis tables or persona datasets
- optional bitable backfill after collection
