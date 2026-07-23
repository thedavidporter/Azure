#!/usr/bin/env python3
"""
IDOH Metadata Marketplace — Screen Recording Demo Script
Navigates the app with on-screen narration overlay for a ~2:02 video.

Usage:
    python3 marketplace_demo.py

On first run you will be prompted to log in via SSO. After that, login
cookies are cached in /tmp/marketplace_demo_profile so re-runs skip login.
"""

import asyncio
import base64
import os
import re
import subprocess
import edge_tts
from playwright.async_api import async_playwright

APP_URL        = "https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com"
PROFILE_DIR    = "/tmp/marketplace_demo_profile"
VOICE          = True                  # set False to disable narration
VOICE_NAME     = "en-US-JennyNeural"  # en-US-GuyNeural / en-US-AriaNeural
PAUSE_AFTER    = 0.2                   # natural pause after each utterance
INTRO_TEXT     = "Metadata Marketplace - Teaser - David Porter - 23 July 2026"
INTRO_DURATION = 5                     # seconds the title card is shown

# Abbreviation expansions — applied to TTS only; overlay always shows original text
ABBREVS = {
    "IDOH":  "Indiana Department of Health",
    "ISDH":  "Indiana State Department of Health",
    "ADF":   "Azure Data Factory",
    "ADLS":  "Azure Data Lake Storage",
    "ADO":   "Azure DevOps",
    "AVD":   "Azure Virtual Desktop",
    "CHIRP": "Children and Hoosiers Immunization Registry Program",
    "NBS":   "Newborn Screening",
    "VNET":  "Virtual Network",
}

_ABBREV_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in ABBREVS) + r")\b"
)

def _expand(text: str) -> str:
    """Expand abbreviations for TTS; display text is unchanged."""
    return _ABBREV_RE.sub(lambda m: ABBREVS[m.group(0)], text)

# All narration lines — each is shown on its matching screen
NARRATION = [
    "Welcome to the IDOH Metadata Marketplace — your hub for Azure infrastructure reports",
    "Reports span Azure Data Factory, Synapse, Databricks, Key Vault, ADLS, and more",
    "The Synapse Metadata Report lists every schema, table, and column in the production data warehouse",
    "The ADF Metadata Report tracks every pipeline across dev and production environments",
    "Pipeline names, trigger schedules, and last-run status — all in one place",
    "The Data Catalog is a registry of all 32 public health datasets at IDOH",
    "14 domains covered — Immunization, Vital Records, Behavioral Health, and more",
    "Use domain chips to filter datasets by public health area",
    "Search finds datasets by name, description, or schema names like CHIRP or NBS",
    "Results update instantly — matching across all metadata fields",
    "Click any card to see full details — layers, row counts, steward, and pipeline notes",
    "Source, Data Mart, and Reporting schemas — with approximate row counts per layer",
    "Submit data access requests directly to IDOH's REDCap system through this form",
    "The Help page includes a glossary, guides, and a changelog of every update deployed",
    "Every deployment is logged — keeping the team informed of new reports and improvements",
    "IDOH Metadata Marketplace — one place for all Azure infrastructure and public health data assets",
]


# ── TTS engine ────────────────────────────────────────────────────────────────
# Uses Windows MCI (winmm.dll) via PowerShell -EncodedCommand.
# mciSendString("play ... wait") blocks until audio finishes, so the PS
# process exits exactly when speech ends — no timing estimates needed.

_tts_cache: dict[str, str] = {}   # text → base64-encoded PS command
_tts_proc = None
_WIN_TTS_DIR = ""   # e.g. C:\Users\...\AppData\Local\Temp\marketplace_tts
_LIN_TTS_DIR = ""   # same dir via /mnt/c/...


def _init_tts_dirs() -> None:
    global _WIN_TTS_DIR, _LIN_TTS_DIR
    win_temp = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-Command", "$env:TEMP"]
    ).decode().strip()
    _WIN_TTS_DIR = win_temp + "\\marketplace_tts"
    _LIN_TTS_DIR = subprocess.check_output(
        ["wslpath", "-u", _WIN_TTS_DIR]
    ).decode().strip()
    os.makedirs(_LIN_TTS_DIR, exist_ok=True)


def _encoded_play_cmd(win_mp3: str) -> str:
    """Base64-encoded UTF-16LE PS script that plays win_mp3 via MCI and exits when done."""
    ps = f"""Add-Type -TypeDefinition @"
using System;using System.Runtime.InteropServices;
public class MCI{{[DllImport("winmm.dll")]public static extern int mciSendString(string c,System.Text.StringBuilder r,int s,IntPtr h);}}
"@ -ErrorAction SilentlyContinue
[MCI]::mciSendString('open "{win_mp3}" type mpegvideo alias tts',$null,0,0)|Out-Null
[MCI]::mciSendString('play tts wait',$null,0,0)|Out-Null
[MCI]::mciSendString('close tts',$null,0,0)|Out-Null"""
    return base64.b64encode(ps.encode("utf-16-le")).decode("ascii")


async def _prefetch() -> None:
    _init_tts_dirs()
    print(f"\nPreparing {len(NARRATION)} narration lines", end="", flush=True)
    for i, text in enumerate(NARRATION):
        lin_path = f"{_LIN_TTS_DIR}/tts_{i:02d}.mp3"
        win_path = f"{_WIN_TTS_DIR}\\tts_{i:02d}.mp3"
        communicate = edge_tts.Communicate(_expand(text), VOICE_NAME)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        with open(lin_path, "wb") as f:
            for c in chunks:
                f.write(c)
        _tts_cache[text] = _encoded_play_cmd(win_path)
        print(".", end="", flush=True)
    print(" ready.\n")


def _speak(text: str) -> None:
    """Fire the pre-built MCI command; process exits when audio finishes."""
    global _tts_proc
    encoded = _tts_cache.get(text)
    if not encoded:
        return
    _tts_proc = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


async def wait_speech() -> None:
    """Poll until the PS process exits (= audio finished), then pause."""
    if _tts_proc and _tts_proc.poll() is None:
        while _tts_proc.poll() is None:
            await asyncio.sleep(0.05)
    await asyncio.sleep(PAUSE_AFTER)


# ── Overlay ───────────────────────────────────────────────────────────────────

OVERLAY_CSS = (
    "position:fixed;"
    "bottom:32px;"
    "left:50%;"
    "transform:translateX(-50%);"
    "background:rgba(0,80,180,0.70);"
    "color:#ffffff;"
    "padding:14px 32px;"
    "border-radius:9999px;"
    "font-size:19px;"
    "font-weight:500;"
    "font-family:'Segoe UI',Arial,sans-serif;"
    "z-index:2147483647;"
    "max-width:78%;"
    "text-align:center;"
    "box-shadow:0 4px 28px rgba(0,0,0,0.35);"
    "pointer-events:none;"
    "backdrop-filter:blur(4px);"
)


async def intro(page):
    """Full-screen title card, centered, fades in and out."""
    await page.evaluate(
        """([text, dur]) => {
            const wrap = document.createElement('div');
            wrap.id = '__demo_intro__';
            wrap.style.cssText = [
                'position:fixed;top:0;left:0;width:100%;height:100%;',
                'background:#0f1b35;',
                'display:flex;flex-direction:column;',
                'align-items:center;justify-content:center;',
                'z-index:2147483647;',
                'opacity:0;transition:opacity 0.6s ease;',
            ].join('');
            const title = document.createElement('div');
            title.textContent = text;
            title.style.cssText = [
                'color:#ffffff;',
                'font-size:52px;',
                'font-weight:700;',
                'font-family:"Segoe UI",Arial,sans-serif;',
                'text-align:center;',
                'max-width:82%;',
                'line-height:1.35;',
                'letter-spacing:0.01em;',
            ].join('');
            wrap.appendChild(title);
            document.body.appendChild(wrap);
            requestAnimationFrame(() => { wrap.style.opacity = '1'; });
            setTimeout(() => {
                wrap.style.opacity = '0';
                setTimeout(() => wrap.remove(), 650);
            }, dur * 1000);
        }""",
        [INTRO_TEXT, INTRO_DURATION],
    )
    await asyncio.sleep(INTRO_DURATION + 0.7)   # fade-in + hold + fade-out


async def show(page, text):
    """Show overlay on the current screen and speak — waits for previous speech first."""
    print(f"  ► {text}")
    await wait_speech()
    await page.evaluate(
        """([text, css]) => {
            let el = document.getElementById('__demo_overlay__');
            if (!el) {
                el = document.createElement('div');
                el.id = '__demo_overlay__';
                el.style.cssText = css;
                document.body.appendChild(el);
            }
            el.textContent = text;
        }""",
        [text, OVERLAY_CSS],
    )
    if VOICE:
        _speak(text)


async def hide(page):
    await wait_speech()
    await page.evaluate(
        "() => { const el = document.getElementById('__demo_overlay__'); if (el) el.remove(); }"
    )


async def scroll(page, y):
    await wait_speech()
    await page.evaluate(f"window.scrollTo({{top:{y}, behavior:'smooth'}})")


async def go(page, path=""):
    """Navigate — always finishes current speech before changing page."""
    await wait_speech()
    url = APP_URL + ("/" + path.lstrip("/") if path else "")
    await page.goto(url, wait_until="networkidle", timeout=45000)
    await asyncio.sleep(1.5)


# ── Demo ──────────────────────────────────────────────────────────────────────

async def main():
    if VOICE:
        await _prefetch()

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=["--kiosk"],
            no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print("Opening Metadata Marketplace...")
        await go(page)

        if "login.microsoftonline" in page.url or "login" in page.url.lower():
            print("\n⚠  SSO login required.")
            print("   Log in to your account in the browser window that just opened.")
            input("   Press Enter here once you can see the Marketplace homepage...")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

        # ── Intro title card ────────────────────────────────────────────────
        print("\nShowing intro card...")
        await intro(page)

        # ── SECTION 1: Homepage ──────────────────────────────────────────────
        print("\n[1/8] Homepage")
        await show(page, "Welcome to the IDOH Metadata Marketplace — your hub for Azure infrastructure reports")
        await scroll(page, 350)
        await show(page, "Reports span Azure Data Factory, Synapse, Databricks, Key Vault, ADLS, and more")
        await scroll(page, 700)
        await asyncio.sleep(1)
        await scroll(page, 0)
        await asyncio.sleep(1)

        # ── SECTION 2: Synapse (navigate first, then narrate on that screen) ─
        print("\n[2/8] Synapse report")
        await go(page, "synapse_metadata_report_prd.html")
        await show(page, "The Synapse Metadata Report lists every schema, table, and column in the production data warehouse")
        await scroll(page, 500)
        await asyncio.sleep(1)
        await scroll(page, 1000)
        await asyncio.sleep(1)
        await scroll(page, 0)
        await asyncio.sleep(1)

        # ── SECTION 3: ADF ───────────────────────────────────────────────────
        print("\n[3/8] ADF report")
        await go(page, "adf_metadata_report_prd.html")
        await show(page, "The ADF Metadata Report tracks every pipeline across dev and production environments")
        await asyncio.sleep(1)
        await show(page, "Pipeline names, trigger schedules, and last-run status — all in one place")
        await scroll(page, 400)
        await asyncio.sleep(1)
        await scroll(page, 0)
        await asyncio.sleep(1)

        # ── SECTION 4: Data Catalog — browse ────────────────────────────────
        print("\n[4/8] Data Catalog — browse & filter")
        await go(page, "data_catalog.html")
        await show(page, "The Data Catalog is a registry of all 32 public health datasets at IDOH")
        await show(page, "14 domains covered — Immunization, Vital Records, Behavioral Health, and more")

        chip = page.locator("button, .chip, [class*='chip'], [class*='pill']").filter(has_text="Vital")
        if await chip.count() > 0:
            await show(page, "Use domain chips to filter datasets by public health area")
            await chip.first.click()
            await asyncio.sleep(2)
            all_chip = page.locator("button, .chip, [class*='chip']").filter(has_text="All")
            if await all_chip.count() > 0:
                await all_chip.first.click()
                await asyncio.sleep(1)

        # ── SECTION 5: Data Catalog — search ────────────────────────────────
        print("\n[5/8] Data Catalog — search")
        search = page.locator(
            "input[type='search'], input[placeholder*='search' i], input[placeholder*='Search' i]"
        ).first

        if await search.count() > 0:
            await show(page, "Search finds datasets by name, description, or schema names like CHIRP or NBS")
            await search.click()
            await page.keyboard.type("immunization", delay=90)
            await asyncio.sleep(1)
            await show(page, "Results update instantly — matching across all metadata fields")
            await search.click(click_count=3)
            await page.keyboard.press("Backspace")
            await asyncio.sleep(1)

        # ── SECTION 6: Dataset modal ─────────────────────────────────────────
        print("\n[6/8] Dataset modal")
        await show(page, "Click any card to see full details — layers, row counts, steward, and pipeline notes")
        first_card = page.locator(".card, [class*='card'], [class*='dataset'], [data-index]").first
        if await first_card.count() > 0:
            await first_card.click()
            await asyncio.sleep(1)
            await show(page, "Source, Data Mart, and Reporting schemas — with approximate row counts per layer")
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)

        # ── SECTION 7: Data Request Form ────────────────────────────────────
        print("\n[7/8] Data Request Form")
        await go(page, "data_request_form.html")
        await show(page, "Submit data access requests directly to IDOH's REDCap system through this form")
        await scroll(page, 300)
        await asyncio.sleep(1)
        await scroll(page, 0)
        await asyncio.sleep(1)

        # ── SECTION 8: Help & Changelog ──────────────────────────────────────
        print("\n[8/8] Help & Changelog")
        await go(page, "help.html")
        await show(page, "The Help page includes a glossary, guides, and a changelog of every update deployed")
        await show(page, "Every deployment is logged — keeping the team informed of new reports and improvements")
        await asyncio.sleep(1)

        # ── Closing shot ─────────────────────────────────────────────────────
        await go(page)
        await show(page, "IDOH Metadata Marketplace — one place for all Azure infrastructure and public health data assets")

        await hide(page)
        print("\n✅  Demo complete — stop your screen recording now.")
        await asyncio.sleep(3)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
