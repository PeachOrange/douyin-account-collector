import asyncio
import json
import os
import re
import time
import argparse
from playwright.async_api import async_playwright
import aiohttp

DETAIL_WAIT_MS = 12000
CHALLENGE_CHECK_INTERVAL_MS = 2000
CHALLENGE_MAX_WAIT_SECONDS = 90


def first_http_url(urls):
    if not isinstance(urls, list):
        return None
    for url in urls:
        if isinstance(url, str) and url.startswith("http"):
            return url
    return None


def extract_src_from_aweme_detail(detail_payload):
    if not isinstance(detail_payload, dict):
        return None
    aweme = detail_payload.get("aweme_detail")
    if not isinstance(aweme, dict):
        return None
    video = aweme.get("video")
    if not isinstance(video, dict):
        return None

    bit_rates = video.get("bit_rate")
    if isinstance(bit_rates, list):
        sortable = []
        for item in bit_rates:
            if not isinstance(item, dict):
                continue
            score = item.get("bit_rate", 0)
            play_addr = item.get("play_addr")
            urls = play_addr.get("url_list") if isinstance(play_addr, dict) else []
            src = first_http_url(urls)
            if src:
                sortable.append((score, src))
        if sortable:
            sortable.sort(key=lambda x: x[0], reverse=True)
            return sortable[0][1]

    for key in ["play_addr_h264", "play_addr", "download_addr", "play_addr_265"]:
        addr = video.get(key)
        if isinstance(addr, dict):
            src = first_http_url(addr.get("url_list"))
            if src:
                return src
    return None


def deep_find_aweme_detail(obj):
    if isinstance(obj, dict):
        if "aweme_detail" in obj and isinstance(obj.get("aweme_detail"), dict):
            return obj
        for v in obj.values():
            found = deep_find_aweme_detail(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = deep_find_aweme_detail(it)
            if found:
                return found
    return None


def extract_from_html_fallback(html: str):
    if not html:
        return None
    m = re.search(r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            state = json.loads(m.group(1))
            item_module = state.get("ItemModule") or state.get("itemModule") or {}
            if isinstance(item_module, dict):
                for _, v in item_module.items():
                    if not isinstance(v, dict):
                        continue
                    video = v.get("video") or {}
                    for key in ["playAddr", "play_addr", "downloadAddr", "download_addr"]:
                        addr = video.get(key)
                        if isinstance(addr, dict):
                            src = first_http_url(addr.get("urlList") or addr.get("url_list") or [])
                            if src:
                                return src
        except Exception:
            pass
    return None


def looks_like_challenge(html: str, title: str = "") -> bool:
    blob = f"{title}\n{html}".lower() if html else (title or "").lower()
    markers = [
        "验证码中间页",
        "请完成下列验证后继续",
        "argus-csp-token",
        "waf-jschallenge",
        "please wait",
    ]
    return any(m.lower() in blob for m in markers)


async def wait_until_ready(page, max_wait_seconds=CHALLENGE_MAX_WAIT_SECONDS):
    deadline = time.monotonic() + max_wait_seconds
    last_title = ""
    while time.monotonic() < deadline:
        try:
            title = await page.title()
            html = await page.content()
            last_title = title
        except Exception:
            await page.wait_for_timeout(CHALLENGE_CHECK_INTERVAL_MS)
            continue
        if not looks_like_challenge(html, title):
            return True, title
        await page.wait_for_timeout(CHALLENGE_CHECK_INTERVAL_MS)
    return False, last_title


async def fetch_via_cdp(url: str, output_dir: str, cdp_endpoint: str):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_endpoint)

        # Reuse an existing context/page if present, otherwise create one in the connected browser.
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = await browser.new_context()

        if context.pages:
            page = context.pages[0]
        else:
            page = await context.new_page()

        aweme_detail_payload = None
        media_candidates = []
        response_tasks = []

        async def handle_response(response):
            nonlocal aweme_detail_payload
            try:
                rurl = response.url
                if response.status in [200, 206] and "douyinvod.com" in rurl and rurl.startswith("http"):
                    media_candidates.append(rurl)
                if response.status == 200 and "/aweme/v1/web/aweme/detail/" in rurl and aweme_detail_payload is None:
                    aweme_detail_payload = await response.json()
            except Exception:
                return

        def on_response(response):
            task = asyncio.create_task(handle_response(response))
            response_tasks.append(task)

        page.on("response", on_response)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        ready, title = await wait_until_ready(page)
        if not ready:
            return {"ok": False, "detail": f"challenge_not_resolved:{title}"}

        await page.wait_for_timeout(DETAIL_WAIT_MS)
        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)

        src = None
        if aweme_detail_payload:
            src = extract_src_from_aweme_detail(aweme_detail_payload)
        if not src and media_candidates:
            src = media_candidates[0]
        if not src:
            try:
                html = await page.content()
                src = extract_from_html_fallback(html)
            except Exception:
                src = None
        if not src:
            try:
                src = await page.evaluate("""() => {
                    const v = document.querySelector('video');
                    if (!v) return null;
                    if (v.src && v.src.startsWith('http')) return v.src;
                    const sources = Array.from(v.querySelectorAll('source'));
                    const mp4 = sources.find(s => s.type === 'video/mp4');
                    return mp4 ? mp4.src : (sources[0] ? sources[0].src : null);
                }""")
            except Exception:
                src = None

        if not src or not str(src).startswith("http"):
            return {"ok": False, "detail": "no_video_src"}

        os.makedirs(output_dir, exist_ok=True)
        vid_match = re.search(r"(?:video/|modal_id=)(\d{8,25})", url)
        vid = vid_match.group(1) if vid_match else str(int(time.time() * 1000))
        output_path = os.path.join(output_dir, f"{vid}.mp4")

        headers = {
            "User-Agent": await page.evaluate("navigator.userAgent"),
            "Referer": "https://www.douyin.com/"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(src, headers=headers, timeout=120) as resp:
                if resp.status not in [200, 206]:
                    return {"ok": False, "detail": f"download_status_{resp.status}", "src": src}
                with open(output_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)

        return {"ok": True, "output": output_path, "src": src}


def main():
    parser = argparse.ArgumentParser(description="Fetch Douyin video via CDP-connected Chrome")
    parser.add_argument("url")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222")
    parser.add_argument("--output-dir", default="downloads/douyin-cdp")
    args = parser.parse_args()
    result = asyncio.run(fetch_via_cdp(args.url, args.output_dir, args.cdp))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
