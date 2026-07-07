#!/usr/bin/env python3
"""
Diagnostic script — uses camoufox (bot-resistant Firefox) to inspect
what Meijer and ALDI actually render, so we can find correct selectors.
"""
import asyncio
import json
import re
from collections import Counter
from pathlib import Path

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    print("Install camoufox:  pip install 'camoufox[geoip]' && python -m camoufox fetch")
    raise SystemExit(1)

SEARCH_TERM = "milk"
OUT_DIR = Path.home() / "grocery_debug"
OUT_DIR.mkdir(exist_ok=True)

# Meijer's search redirects to a hash-based URL; try the direct path too
MEIJER_URL = f"https://www.meijer.com/search.html#{SEARCH_TERM}"
# ALDI US moved off new.aldi.us — try the main site
ALDI_URL   = f"https://www.aldi.us/en/search/?text={SEARCH_TERM}"


async def dump(page, name: str) -> str:
    html = await page.content()
    (OUT_DIR / f"{name}.html").write_text(html, encoding="utf-8")
    await page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=True)
    print(f"  Saved {name}.html ({len(html):,} chars) and {name}.png")
    return html


async def analyze(html: str, label: str):
    print(f"\n  Dollar signs in {label}:")
    hits = [l.strip() for l in html.splitlines() if "$" in l and len(l.strip()) < 300]
    for h in hits[:20]:
        print(f"    {h[:200]}")
    if not hits:
        print("    (none — may still be blocked or page didn't hydrate)")

    classes = re.findall(r'class="([^"]+)"', html)
    flat    = [c for cls in classes for c in cls.split()]
    common  = Counter(flat).most_common(40)
    print(f"\n  Most common CSS classes in {label} (top 40):")
    for cls, cnt in common:
        print(f"    {cnt:4d}x  {cls}")

    # Also look for data-testid attributes
    testids = re.findall(r'data-testid="([^"]+)"', html)
    if testids:
        print(f"\n  data-testid values found:")
        for t in sorted(set(testids))[:20]:
            print(f"    {t}")


async def intercept_json(page) -> list[dict]:
    captured = []

    async def on_response(response):
        ct = response.headers.get("content-type", "")
        if "json" in ct:
            url = response.url
            if any(k in url for k in ["product", "search", "catalog", "item", "query"]):
                try:
                    body = await response.json()
                    captured.append({"url": url, "body": body})
                except Exception:
                    pass

    page.on("response", on_response)
    return captured


async def main():
    print(f"Output directory: {OUT_DIR}\n")
    print("Using camoufox (bot-resistant Firefox)\n")

    async with AsyncCamoufox(headless=True, geoip=True) as browser:
        # ── Meijer ──────────────────────────────────────────────────────────
        print("── Meijer ──────────────────────────")
        print(f"  URL: {MEIJER_URL}")
        page = await browser.new_page()
        api_calls = await intercept_json(page)

        try:
            await page.goto(MEIJER_URL, wait_until="networkidle", timeout=45_000)
            await page.wait_for_timeout(4_000)
            # Try scrolling to trigger lazy-loaded content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2_000)
        except Exception as e:
            print(f"  Navigation error: {e}")

        html = await dump(page, "meijer")
        await analyze(html, "meijer")

        if api_calls:
            print(f"\n  Intercepted {len(api_calls)} product API call(s):")
            for c in api_calls[:5]:
                print(f"    URL: {c['url']}")
                snippet = json.dumps(c["body"])[:500]
                print(f"    Body: {snippet}\n")
            (OUT_DIR / "meijer_api.json").write_text(
                json.dumps(api_calls, indent=2, default=str), encoding="utf-8"
            )
            print(f"  Full API responses saved to meijer_api.json")

        await page.close()

        # ── ALDI ────────────────────────────────────────────────────────────
        print("\n── ALDI ────────────────────────────")
        print(f"  URL: {ALDI_URL}")
        page = await browser.new_page()
        api_calls_aldi = await intercept_json(page)

        try:
            await page.goto(ALDI_URL, wait_until="networkidle", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2_000)
        except Exception as e:
            print(f"  Navigation error: {e}")

        html_aldi = await dump(page, "aldi")
        await analyze(html_aldi, "aldi")

        if api_calls_aldi:
            print(f"\n  Intercepted {len(api_calls_aldi)} product API call(s):")
            for c in api_calls_aldi[:5]:
                print(f"    URL: {c['url']}")
                snippet = json.dumps(c["body"])[:500]
                print(f"    Body: {snippet}\n")
            (OUT_DIR / "aldi_api.json").write_text(
                json.dumps(api_calls_aldi, indent=2, default=str), encoding="utf-8"
            )
            print(f"  Full API responses saved to aldi_api.json")

        await page.close()

    print(f"\nDone. Files in: {OUT_DIR}")
    print("Paste this output and I'll update the main script with correct selectors.")


asyncio.run(main())
