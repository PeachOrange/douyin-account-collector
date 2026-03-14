import argparse
import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime
from urllib.parse import urljoin

import aiohttp
from playwright.async_api import async_playwright

BASE = "https://www.douyin.com"
DEFAULT_PROFILE_URL = "https://www.douyin.com/user/YOUR_SECUID"
DEFAULT_EXPECTED_AUTHOR = "TARGET_AUTHOR_NAME"
DEFAULT_EXPECTED_SECUID = "YOUR_SECUID"
DEFAULT_OUTDIR = "/root/.openclaw/workspace/data/exports/douyin-batch"
DEFAULT_METADIR = "/root/.openclaw/workspace/data/exports/douyin-batch-meta"
DEFAULT_CDP = "http://127.0.0.1:9222"

PROFILE_URL = DEFAULT_PROFILE_URL
EXPECTED_AUTHOR = DEFAULT_EXPECTED_AUTHOR
EXPECTED_SECUID = DEFAULT_EXPECTED_SECUID
OUTDIR = DEFAULT_OUTDIR
METADIR = DEFAULT_METADIR
RESULT_PATH = os.path.join(METADIR, "last-run.json")
DB_PATH = os.path.join(METADIR, "douyin_batch.db")
CDP = DEFAULT_CDP


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_dirs():
    os.makedirs(OUTDIR, exist_ok=True)
    os.makedirs(METADIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS downloads (
            aweme_id TEXT PRIMARY KEY,
            title TEXT,
            author_name TEXT,
            sec_uid TEXT,
            href TEXT,
            output TEXT,
            size INTEGER,
            status TEXT,
            reason TEXT,
            download_url_source TEXT,
            checked_at TEXT
        )'''
    )
    conn.commit()
    return conn


def upsert_db(conn, payload):
    conn.execute(
        '''INSERT INTO downloads (
            aweme_id, title, author_name, sec_uid, href, output, size, status, reason, download_url_source, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(aweme_id) DO UPDATE SET
            title=excluded.title,
            author_name=excluded.author_name,
            sec_uid=excluded.sec_uid,
            href=excluded.href,
            output=excluded.output,
            size=excluded.size,
            status=excluded.status,
            reason=excluded.reason,
            download_url_source=excluded.download_url_source,
            checked_at=excluded.checked_at
        ''',
        (
            payload.get('id'),
            payload.get('title'),
            payload.get('authorName'),
            payload.get('secUid'),
            payload.get('href'),
            payload.get('output'),
            payload.get('size'),
            payload.get('status'),
            payload.get('reason'),
            payload.get('downloadUrlSource'),
            payload.get('checkedAt'),
        )
    )
    conn.commit()


async def get_page(browser):
    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = await browser.new_context()
    if context.pages:
        return context.pages[0]
    return await context.new_page()


async def ensure_profile_ready(page, retries=2):
    last_state = None
    for attempt in range(1, retries + 1):
        await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(5000)
        title = await page.title()
        body = await page.evaluate("() => (document.body?.innerText || '').slice(0, 1200)")
        last_state = {"title": title, "url": page.url}
        if "验证码中间页" not in title and "请完成下列验证后继续" not in body:
            return last_state
        if attempt < retries:
            await asyncio.sleep([2, 5][min(attempt - 1, 1)])
    raise RuntimeError("challenge_page_detected")


async def extract_video_links(page):
    links = await page.evaluate(r'''() => {
      const out = [];
      const seen = new Set();
      for (const a of document.querySelectorAll('a[href]')) {
        const href = a.getAttribute('href') || '';
        if (!href.includes('/video/')) continue;
        if (href.includes('source=Baiduspider')) continue;
        if (href.startsWith('http://') || href.startsWith('https://')) continue;
        const m = href.match(/\/video\/(\d{8,25})/);
        if (!m) continue;
        const id = m[1];
        if (seen.has(id)) continue;
        seen.add(id);
        out.push({
          id,
          href,
          text: (a.innerText || '').trim()
        });
      }
      return out;
    }''')
    return links


async def get_player_data(page, href, retries=2):
    full = urljoin(BASE, href)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            await page.goto(full, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            title = await page.title()
            body = await page.evaluate("() => (document.body?.innerText || '').slice(0, 1200)")
            if "验证码中间页" in title or "请完成下列验证后继续" in body:
                raise RuntimeError("challenge_page_detected")
            data = await page.evaluate("""() => {
              const p = window.player;
              const info = p?.config?.awemeInfo || null;
              const video = info?.video || null;
              return {
                title: info?.desc || null,
                awemeId: info?.awemeId || null,
                authorName: info?.authorInfo?.nickname || null,
                secUid: info?.authorInfo?.secUid || null,
                shareUrl: info?.shareInfo?.shareUrl || null,
                playApi: video?.playApi || null,
                playApiH265: video?.playApiH265 || null,
                playAddr: video?.playAddr || null,
                playAddrH265: video?.playAddrH265 || null,
                bitRateList: video?.bitRateList || null,
                duration: video?.duration || null,
                coverUrlList: video?.coverUrlList || null,
                expectedSize: video?.playAddrSize || video?.dataSize || null,
              };
            }""")
            return data
        except Exception as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep([2, 5][min(attempt - 1, 1)])
    raise last_error


async def download_file(session, url, outpath, expected_size=None, retries=3):
    headers = {
        "Referer": "https://www.douyin.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    }
    last_error = None
    for attempt in range(1, retries + 1):
        tmp = outpath + '.part'
        try:
            async with session.get(url, headers=headers, timeout=180) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
            if not os.path.exists(tmp) or os.path.getsize(tmp) <= 0:
                raise RuntimeError('download_integrity_failed')
            actual_size = os.path.getsize(tmp)
            if expected_size and actual_size < int(expected_size * 0.8):
                raise RuntimeError(f'download_size_too_small:{actual_size}/{expected_size}')
            os.replace(tmp, outpath)
            return
        except Exception as e:
            last_error = e
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            if attempt < retries:
                await asyncio.sleep([1, 2, 5][min(attempt - 1, 2)])
    raise last_error


async def resolve_download_url(d):
    for key in ["playApi", "playApiH265"]:
        if d.get(key):
            return d[key], key
    for key in ["playAddr", "playAddrH265"]:
        arr = d.get(key) or []
        if isinstance(arr, list):
            for item in arr:
                if isinstance(item, dict) and isinstance(item.get("src"), str) and item["src"].startswith("http"):
                    return item["src"], key
    br = d.get("bitRateList") or []
    if isinstance(br, list):
        for item in br:
            arr = item.get("playAddr") or []
            if isinstance(arr, list):
                for sub in arr:
                    if isinstance(sub, dict) and isinstance(sub.get("src"), str) and sub["src"].startswith("http"):
                        return sub["src"], "bitRateList.playAddr"
    return None, None


def save_item_meta(item_id, payload):
    with open(os.path.join(METADIR, f"{item_id}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def main():
    ensure_dirs()
    run = {
        "startedAt": now_iso(),
        "profile": PROFILE_URL,
        "items": [],
    }
    conn = get_db()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP)
        page = await get_page(browser)
        run["profileState"] = await ensure_profile_ready(page)
        links = await extract_video_links(page)
        run["totalFound"] = len(links)

        async with aiohttp.ClientSession() as session:
            for idx, item in enumerate(links, 1):
                item_id = item["id"]
                outpath = os.path.join(OUTDIR, f"{item_id}.mp4")
                meta_path = os.path.join(METADIR, f"{item_id}.json")
                if os.path.exists(outpath) and os.path.getsize(outpath) > 0:
                    result = {
                        "id": item_id,
                        "ok": True,
                        "skipped": True,
                        "reason": "already_downloaded",
                        "status": "ok",
                        "output": outpath,
                        "size": os.path.getsize(outpath),
                        "href": item["href"],
                        "title": item.get("text"),
                        "authorName": EXPECTED_AUTHOR,
                        "secUid": EXPECTED_SECUID,
                        "checkedAt": now_iso(),
                    }
                    run["items"].append(result)
                    upsert_db(conn, result)
                    if not os.path.exists(meta_path):
                        save_item_meta(item_id, result)
                    continue

                try:
                    data = await get_player_data(page, item["href"])
                    author_name = (data.get("authorName") or "").strip()
                    sec_uid = (data.get("secUid") or "").strip()
                    if author_name != EXPECTED_AUTHOR and sec_uid != EXPECTED_SECUID:
                        result = {
                            "id": item_id,
                            "ok": False,
                            "status": "filtered",
                            "reason": "filtered_non_author_video",
                            "href": item["href"],
                            "title": data.get("title") or item.get("text"),
                            "authorName": data.get("authorName"),
                            "secUid": data.get("secUid"),
                            "checkedAt": now_iso(),
                            "playerData": data,
                        }
                        run["items"].append(result)
                        upsert_db(conn, result)
                        save_item_meta(item_id, result)
                        continue
                    download_url, source_key = await resolve_download_url(data)
                    expected_size = data.get('expectedSize')
                    if not download_url:
                        result = {
                            "id": item_id,
                            "ok": False,
                            "status": "failed",
                            "reason": "no_download_url",
                            "href": item["href"],
                            "title": data.get("title") or item.get("text"),
                            "authorName": data.get("authorName"),
                            "secUid": data.get("secUid"),
                            "checkedAt": now_iso(),
                            "playerData": data,
                        }
                        run["items"].append(result)
                        upsert_db(conn, result)
                        save_item_meta(item_id, result)
                        continue

                    await download_file(session, download_url, outpath, expected_size=expected_size, retries=3)
                    result = {
                        "id": item_id,
                        "ok": True,
                        "skipped": False,
                        "status": "ok",
                        "href": item["href"],
                        "title": data.get("title") or item.get("text"),
                        "authorName": data.get("authorName"),
                        "secUid": data.get("secUid"),
                        "output": outpath,
                        "size": os.path.getsize(outpath),
                        "downloadUrl": download_url,
                        "downloadUrlSource": source_key,
                        "checkedAt": now_iso(),
                        "playerData": data,
                    }
                    run["items"].append(result)
                    upsert_db(conn, result)
                    save_item_meta(item_id, result)
                except Exception as e:
                    msg = str(e)
                    if 'challenge_page_detected' in msg:
                        reason = 'challenge_page_detected'
                    elif 'timeout' in msg.lower():
                        reason = 'timeout'
                    else:
                        reason = msg
                    result = {
                        "id": item_id,
                        "ok": False,
                        "status": "failed",
                        "reason": reason,
                        "href": item.get("href"),
                        "title": item.get("text"),
                        "checkedAt": now_iso(),
                    }
                    run["items"].append(result)
                    upsert_db(conn, result)
                    save_item_meta(item_id, result)

    run["finishedAt"] = now_iso()
    run["ok"] = sum(1 for x in run["items"] if x.get("ok"))
    run["failed"] = sum(1 for x in run["items"] if not x.get("ok"))
    run["skipped"] = sum(1 for x in run["items"] if x.get("skipped"))
    reason_stats = {}
    for x in run['items']:
        if x.get('reason'):
            reason_stats[x['reason']] = reason_stats.get(x['reason'], 0) + 1
    run['reasonStats'] = reason_stats
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2)
    conn.close()
    print(json.dumps(run, ensure_ascii=False, indent=2))


def apply_config(args):
    global PROFILE_URL, EXPECTED_AUTHOR, EXPECTED_SECUID, OUTDIR, METADIR, RESULT_PATH, DB_PATH, CDP
    cfg = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    PROFILE_URL = args.profile_url or cfg.get('profile_url') or DEFAULT_PROFILE_URL
    EXPECTED_AUTHOR = args.expected_author or cfg.get('expected_author') or DEFAULT_EXPECTED_AUTHOR
    EXPECTED_SECUID = args.expected_secuid or cfg.get('expected_secuid') or DEFAULT_EXPECTED_SECUID
    OUTDIR = args.output_dir or cfg.get('output_dir') or DEFAULT_OUTDIR
    METADIR = args.meta_dir or cfg.get('meta_dir') or DEFAULT_METADIR
    CDP = args.cdp or cfg.get('cdp') or DEFAULT_CDP
    RESULT_PATH = os.path.join(METADIR, 'last-run.json')
    DB_PATH = os.path.join(METADIR, 'douyin_batch.db')
    if 'YOUR_SECUID' in PROFILE_URL or EXPECTED_AUTHOR == 'TARGET_AUTHOR_NAME' or EXPECTED_SECUID == 'YOUR_SECUID':
        raise SystemExit('Please provide real values for profile_url, expected_author, and expected_secuid via --config or CLI args.')


def parse_args():
    parser = argparse.ArgumentParser(description='Batch download and validate videos from a Douyin account via CDP-connected Chrome')
    parser.add_argument('--config', help='Path to JSON config file')
    parser.add_argument('--profile-url', help='Douyin profile URL')
    parser.add_argument('--expected-author', help='Expected author name for validation')
    parser.add_argument('--expected-secuid', help='Expected secUid for validation')
    parser.add_argument('--output-dir', help='Output directory for mp4 files')
    parser.add_argument('--meta-dir', help='Metadata directory')
    parser.add_argument('--cdp', help='CDP endpoint, e.g. http://127.0.0.1:9222')
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    apply_config(args)
    asyncio.run(main())
