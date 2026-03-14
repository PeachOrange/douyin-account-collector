import asyncio
import os
import re
import json
import time
import argparse
from urllib.parse import unquote

from playwright.async_api import async_playwright
import aiohttp

CHALLENGE_CHECK_INTERVAL_MS = 2000
CHALLENGE_MAX_WAIT_SECONDS = 45
DETAIL_WAIT_MS = 8000


def looks_like_waf_challenge(html: str) -> bool:
    if not html:
        return True
    text = html.lower()
    markers = [
        "please wait",
        "waf-jschallenge",
        "_wafchallengeid",
        "argus-csp-token",
        "验证码中间页",
    ]
    return any(m in text for m in markers)


async def wait_until_page_ready(page, max_wait_seconds=CHALLENGE_MAX_WAIT_SECONDS):
    deadline = time.monotonic() + max_wait_seconds
    while time.monotonic() < deadline:
        try:
            html = await page.content()
        except Exception as e:
            msg = str(e).lower()
            if "navigating" in msg or "execution context was destroyed" in msg:
                await page.wait_for_timeout(CHALLENGE_CHECK_INTERVAL_MS)
                continue
            return False
        if not looks_like_waf_challenge(html):
            return True
        await page.wait_for_timeout(CHALLENGE_CHECK_INTERVAL_MS)
    return False


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

    m = re.search(r"RENDER_DATA=([^&]+)&", html)
    if m:
        try:
            decoded = unquote(m.group(1))
            data = json.loads(decoded)
            found = deep_find_aweme_detail(data)
            src = extract_src_from_aweme_detail(found) if found else None
            if src:
                return src
        except Exception:
            pass

    return None


async def download_video(video_url, output_path, headed=False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN"
        )
        page = await context.new_page()
        aweme_detail_payload = None
        media_candidates = []
        response_tasks = []

        async def route_handler(route):
            if route.request.resource_type in ["image", "font", "stylesheet"]:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", route_handler)

        async def handle_response(response):
            nonlocal aweme_detail_payload
            try:
                url = response.url
                if response.status in [200, 206] and "douyinvod.com" in url and url.startswith("http"):
                    media_candidates.append(url)
                if response.status == 200 and "/aweme/v1/web/aweme/detail/" in url and aweme_detail_payload is None:
                    aweme_detail_payload = await response.json()
            except Exception:
                return

        def on_response(response):
            task = asyncio.create_task(handle_response(response))
            response_tasks.append(task)

        page.on("response", on_response)

        try:
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            await browser.close()
            return False, "page_load_failed"

        ready = await wait_until_page_ready(page)
        if not ready:
            await browser.close()
            return False, "challenge_not_resolved"

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

        if not src or not src.startswith("http"):
            await browser.close()
            return False, "no_video_src"

        headers = {
            "User-Agent": await page.evaluate("navigator.userAgent"),
            "Referer": "https://www.douyin.com/"
        }

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        async with aiohttp.ClientSession() as session:
            async with session.get(src, headers=headers, timeout=120) as resp:
                if resp.status not in [200, 206]:
                    await browser.close()
                    return False, f"download_status_{resp.status}"
                with open(output_path, 'wb') as f:
                    while True:
                        chunk = await resp.content.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)

        await browser.close()
        return True, output_path


def normalize_input_to_url(item: str) -> str:
    item = (item or "").strip()
    if not item:
        return ""
    if item.startswith("http://") or item.startswith("https://"):
        return item
    if item.isdigit() and 8 <= len(item) <= 25:
        return f"https://www.douyin.com/video/{item}"
    return item


async def main_async(args):
    url = normalize_input_to_url(args.input)
    vid_match = re.search(r"(?:video/|modal_id=)(\d{8,25})", url)
    vid = vid_match.group(1) if vid_match else str(int(time.time() * 1000))
    output_path = os.path.join(args.output_dir, f"{vid}.mp4")
    ok, detail = await download_video(url, output_path, headed=args.headed)
    print(json.dumps({
        "ok": ok,
        "input": args.input,
        "url": url,
        "detail": detail,
        "output": output_path if ok else ""
    }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Local Douyin fetch script")
    parser.add_argument("input", help="Douyin URL or video_id")
    parser.add_argument("--output-dir", default="downloads/douyin-local")
    parser.add_argument("--headed", action="store_true", help="Launch visible browser")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
