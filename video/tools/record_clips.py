#!/usr/bin/env python3
"""Optional Playwright UI recorder. Install with: pip install playwright; playwright install chromium"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", action="store_true", help="record deterministic sample replays")
    mode.add_argument("--live", action="store_true", help="record the live B1 hero shot only")
    parser.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright is optional. Install it with: pip install playwright && playwright install chromium", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    clips = root / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    beats = ["B1"] if args.live else ["B1", "B2", "B3", "B4", "B5", "B6"]
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        for beat in beats:
            context = await browser.new_context(viewport={"width": 1920, "height": 1080},
                                                record_video_dir=str(clips))
            page = await context.new_page()
            await page.goto(args.url, wait_until="domcontentloaded")
            await page.locator("#incident").select_option("xid79_demo")
            await page.wait_for_timeout(1200)
            if args.live:
                if beat != "B1":
                    continue
                await page.locator("#run").click()
                await page.locator("#status").wait_for(state="visible")
                await page.wait_for_timeout(15000)
            else:
                await page.locator("#replay").wait_for(state="visible")
                if await page.locator("#replay").is_disabled():
                    print("No recorded sample replay is available. Create one first with ./run.sh replays.", file=sys.stderr)
                    await context.close()
                    await browser.close()
                    return 2
                await page.locator("#replay").click()
                await page.wait_for_function(
                    "document.querySelector('#status').textContent === 'Complete'",
                    timeout=120000,
                )
                if beat == "B2":
                    evidence = page.locator("#evidence button").first
                    if await evidence.count():
                        await evidence.click()
                        await page.wait_for_timeout(1000)
                elif beat == "B5":
                    await page.locator("#download").wait_for(state="visible")
                elif beat == "B4":
                    chart = root.parent / "eval" / "chart.png"
                    if chart.exists():
                        await page.evaluate("""src => { const img=document.createElement('img'); img.src=src;
                        img.style='position:fixed;inset:10%;width:80%;height:80%;object-fit:contain;background:#111;z-index:99';
                        document.body.append(img); }""", chart.as_uri())
                    else:
                        await page.evaluate("""() => { const card=document.createElement('div');
                        card.textContent='Evaluation: not yet measured'; card.style='position:fixed;inset:20%;display:grid;place-items:center;background:#101720;color:#e6edf5;font:48px sans-serif;z-index:99;border:2px solid #56d69a';
                        document.body.append(card); }""")
                await page.wait_for_timeout(2000)
            await context.close()
            # Playwright names clips by page id; move the just-recorded clip into the requested stable name.
            produced = sorted(clips.glob("*.webm"), key=lambda path: path.stat().st_mtime)
            if produced:
                destination = clips / f"{beat}.webm"
                if produced[-1] != destination:
                    produced[-1].replace(destination)
        await browser.close()
    print("Recorded clips in video/clips/. Never enter an API key into the browser page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
