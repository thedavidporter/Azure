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
import datetime
import glob
import os
import re
import subprocess
import time
import edge_tts
from playwright.async_api import async_playwright

APP_URL        = "https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com"
PROFILE_DIR    = "/tmp/marketplace_demo_profile"
VOICE          = True                  # set False to disable narration
VOICE_NAME     = "en-US-JennyNeural"  # en-US-GuyNeural / en-US-AriaNeural
PAUSE_AFTER    = 0.2                   # natural pause after each utterance
INTRO_TEXT     = "Metadata Marketplace - Short Overview - David Porter - " + datetime.date.today().strftime("%d-%b-%Y")
INTRO_DURATION = 3                     # seconds the title card is shown

RECORD_VIDEO  = True                   # set False to skip video recording
VIDEO_DIR     = "/tmp/demo_video"      # Playwright saves .webm here
VIDEO_SIZE    = {"width": 1920, "height": 1080}  # match your monitor resolution
OUTPUT_MP4    = os.path.expanduser("~/marketplace_demo.mp4")

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
    "Welcome to the IDOH Metadata Marketplace — your hub for public health data assets and infrastructure",
    "Reports span Azure Data Factory, Synapse, Databricks, Key Vault, ADLS, and more",
    "The Synapse Metadata Report lists every schema, table, and column in the production data warehouse",
    "Expand any schema to browse tables — then click a table to see its full column list",
    "The ADF Metadata Report tracks every pipeline across dev and production environments",
    "The Pipelines tab lists every pipeline with its trigger schedule and last-run status",
    "Datasets shows all source and sink connections used across pipelines",
    "Linked Services catalogs external connections — databases, storage accounts, and APIs",
    "The Monitor tab tracks recent run history — successes, failures, and durations",
    "The Data Catalog is a registry of 32 public health datasets at IDOH",
    "14 domains are covered — Immunization, Vital Records, Behavioral Health, and more",
    "Use domain chips to filter datasets by public health area",
    "Search finds datasets by name, description, or schema names like CHIRP or NBS",
    "Results update instantly — matching across all metadata fields",
    "Click any card to see full details — layers, row counts, steward, and pipeline notes",
    "Source, Data Mart, and Reporting schemas — with approximate row counts per layer",
    "Submit data access requests directly to IDOH's REDCap system through this form",
    "The Help page includes a glossary, guides, and a changelog of every update deployed",
    "Every deployment is logged — keeping the team informed of new reports and improvements",
    "IDOH Metadata Marketplace — one place for public health data assets and infrastructure",
]


# ── TTS engine ────────────────────────────────────────────────────────────────
# Uses Windows MCI (winmm.dll) via PowerShell -EncodedCommand.
# mciSendString("play ... wait") blocks until audio finishes, so the PS
# process exits exactly when speech ends — no timing estimates needed.

_tts_cache: dict[str, str] = {}   # text → base64-encoded PS command
_wav_cache:  dict[str, str] = {}  # text → linux wav path
_tts_proc  = None
_WIN_TTS_DIR = ""   # e.g. C:\Users\...\AppData\Local\Temp\marketplace_tts
_LIN_TTS_DIR = ""   # same dir via /mnt/c/...

_audio_timeline: list[tuple[float, str]] = []  # (offset_secs_from_demo_start, wav_linux_path)
_demo_start: float = 0.0


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


def _encoded_play_cmd(win_wav: str) -> str:
    """Base64-encoded UTF-16LE PS script — plays WAV via SoundPlayer.PlaySync(),
    which routes through the standard Windows audio mixer (captured by all recorders)."""
    ps = f"(New-Object System.Media.SoundPlayer '{win_wav}').PlaySync()"
    return base64.b64encode(ps.encode("utf-16-le")).decode("ascii")


async def _prefetch() -> None:
    _init_tts_dirs()
    print(f"\nPreparing {len(NARRATION)} narration lines", end="", flush=True)
    for i, text in enumerate(NARRATION):
        mp3_path = f"{_LIN_TTS_DIR}/tts_{i:02d}.mp3"
        wav_path = f"{_LIN_TTS_DIR}/tts_{i:02d}.wav"
        win_wav  = f"{_WIN_TTS_DIR}\\tts_{i:02d}.wav"
        communicate = edge_tts.Communicate(_expand(text), VOICE_NAME)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        with open(mp3_path, "wb") as f:
            for c in chunks:
                f.write(c)
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-ar", "44100", "-ac", "1", wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
        _tts_cache[text] = _encoded_play_cmd(win_wav)
        _wav_cache[text] = wav_path
        print(".", end="", flush=True)
    print(" ready.\n")


def _speak(text: str) -> None:
    """Fire the pre-built PS command; process exits when audio finishes."""
    global _tts_proc
    encoded = _tts_cache.get(text)
    if not encoded:
        return
    if RECORD_VIDEO and _demo_start:
        wav = _wav_cache.get(text)
        if wav:
            _audio_timeline.append((time.monotonic() - _demo_start, wav))
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
    """Inject full-screen dark overlay instantly (opacity:1 — no fade-in), hold, then fade out.
    Called immediately after domcontentloaded so the overlay covers any partial render."""
    await page.evaluate(
        """([text]) => {
            const wrap = document.createElement('div');
            wrap.id = '__demo_intro__';
            wrap.style.cssText = [
                'position:fixed;top:0;left:0;width:100%;height:100%;',
                'background:#0f1b35;',
                'display:flex;flex-direction:column;',
                'align-items:center;justify-content:center;',
                'z-index:2147483647;opacity:1;',
            ].join('');
            const title = document.createElement('div');
            title.textContent = text;
            title.style.cssText = [
                'color:#ffffff;',
                'font-size:34px;',
                'font-weight:700;',
                'font-family:"Segoe UI",Arial,sans-serif;',
                'text-align:center;',
                'white-space:nowrap;',
                'line-height:1.35;',
                'letter-spacing:0.01em;',
            ].join('');
            wrap.appendChild(title);
            document.body.appendChild(wrap);
        }""",
        [INTRO_TEXT],
    )
    await asyncio.sleep(INTRO_DURATION)
    await page.evaluate("""() => {
        const wrap = document.getElementById('__demo_intro__');
        if (wrap) {
            wrap.style.transition = 'opacity 0.6s ease';
            wrap.style.opacity = '0';
            setTimeout(() => wrap.remove(), 650);
        }
    }""")
    await asyncio.sleep(0.7)


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


# ── Video render ─────────────────────────────────────────────────────────────

def _render_mp4(webm_path: str) -> None:
    """Mix tracked speech WAVs onto the Playwright .webm and output an MP4."""
    print("\nRendering final MP4...")

    probe = subprocess.check_output([
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        webm_path,
    ])
    video_dur = float(probe.decode().strip())
    print(f"  Video: {video_dur:.1f}s  |  Speech events: {len(_audio_timeline)}")

    if not _audio_timeline:
        subprocess.run([
            "ffmpeg", "-y", "-i", webm_path,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            OUTPUT_MP4,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        audio_mix = "/tmp/demo_audio_mix.wav"

        # One silent base track + each WAV delayed to its recorded offset
        cmd = ["ffmpeg", "-y",
               "-f", "lavfi", "-i",
               f"anullsrc=channel_layout=mono:sample_rate=44100:d={video_dur:.3f}"]
        for _, wav in _audio_timeline:
            cmd += ["-i", wav]

        filter_parts = []
        for i, (offset, _) in enumerate(_audio_timeline):
            ms = int(offset * 1000)
            filter_parts.append(f"[{i+1}]adelay={ms}|{ms}[s{i}]")

        mix_ins = "[0]" + "".join(f"[s{i}]" for i in range(len(_audio_timeline)))
        n = len(_audio_timeline) + 1
        filter_parts.append(
            f"{mix_ins}amix=inputs={n}:normalize=0:dropout_transition=0[aout]"
        )

        cmd += [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[aout]",
            "-t", f"{video_dur:.3f}",
            audio_mix,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run([
            "ffmpeg", "-y",
            "-i", webm_path, "-i", audio_mix,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            OUTPUT_MP4,
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"  Saved: {OUTPUT_MP4}")


# ── Demo ──────────────────────────────────────────────────────────────────────

async def main():
    global _demo_start
    if VOICE:
        await _prefetch()

    if RECORD_VIDEO:
        os.makedirs(VIDEO_DIR, exist_ok=True)
        for f in glob.glob(f"{VIDEO_DIR}/*.webm"):
            os.remove(f)

    async with async_playwright() as p:
        ctx_kwargs: dict = {
            "user_data_dir": PROFILE_DIR,
            "headless":      False,
            "args":          ["--kiosk"],
            "no_viewport":   True,
        }
        if RECORD_VIDEO:
            ctx_kwargs["record_video_dir"]  = VIDEO_DIR
            ctx_kwargs["record_video_size"] = VIDEO_SIZE

        ctx = await p.chromium.launch_persistent_context(**ctx_kwargs)
        _demo_start = time.monotonic()   # video recording starts here
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Cover the blank tab immediately so there's no white flash at startup
        await page.set_content('<html><body style="margin:0;background:#0f1b35"></body></html>')

        print("Opening Metadata Marketplace...")
        # domcontentloaded fires before the app renders — lets us inject the intro
        # overlay immediately, covering any partial page state
        await page.goto(APP_URL, wait_until="domcontentloaded", timeout=45000)

        if "login.microsoftonline" in page.url or "login" in page.url.lower():
            print("\n⚠  SSO login required.")
            print("   Log in to your account in the browser window that just opened.")
            input("   Press Enter here once you can see the Marketplace homepage...")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            _demo_start = time.monotonic()   # reset so SSO time isn't in the audio timeline
            # Already on app page — inject intro and let page settle
            print("\nShowing intro card...")
            await intro(page)
        else:
            # Inject intro overlay instantly; page finishes loading behind it
            print("\nShowing intro card...")
            await intro(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        await asyncio.sleep(0.5)

        # ── SECTION 1: Homepage ──────────────────────────────────────────────
        print("\n[1/8] Homepage")
        await show(page, "Welcome to the IDOH Metadata Marketplace — your hub for public health data assets and infrastructure")
        await scroll(page, 350)
        await show(page, "Reports span Azure Data Factory, Synapse, Databricks, Key Vault, ADLS, and more")
        await scroll(page, 700)
        await asyncio.sleep(1)
        await scroll(page, 0)
        await asyncio.sleep(1)

        # ── SECTION 2: Synapse ───────────────────────────────────────────────
        print("\n[2/8] Synapse report")
        await go(page, "synapse_metadata_report_prd.html")
        await show(page, "The Synapse Metadata Report lists every schema, table, and column in the production data warehouse")

        # Expand the 3rd schema item (scoped so we only click tables inside it)
        schema_items = page.locator('.sch-item')
        n_items = await schema_items.count()
        if n_items > 0:
            target_item = schema_items.nth(min(2, n_items - 1))
            target_hdr  = target_item.locator('.sch-hdr')
            await show(page, "Expand any schema to browse tables — then click a table to see its full column list")
            await wait_speech()
            await target_hdr.click()
            await asyncio.sleep(1.5)
            # Click the first table row WITHIN this schema item
            table_row = target_item.locator('.obj-row:has(.bdg-T)').first
            try:
                await table_row.scroll_into_view_if_needed(timeout=3000)
                await table_row.click(timeout=5000)
                await asyncio.sleep(2.5)
            except Exception:
                pass   # expansion is the main visual; clicking table is bonus
        else:
            await scroll(page, 600)
            await asyncio.sleep(1)

        await scroll(page, 0)
        await asyncio.sleep(1)

        # ── SECTION 3: ADF ───────────────────────────────────────────────────
        print("\n[3/8] ADF report")
        await go(page, "adf_metadata_report_prd.html")
        await show(page, "The ADF Metadata Report tracks every pipeline across dev and production environments")
        for tab_id, narration in [
            ("tab-pipelines", "The Pipelines tab lists every pipeline with its trigger schedule and last-run status"),
            ("tab-datasets",  "Datasets shows all source and sink connections used across pipelines"),
            ("tab-linkedsvc", "Linked Services catalogs external connections — databases, storage accounts, and APIs"),
            ("tab-monitor",   "The Monitor tab tracks recent run history — successes, failures, and durations"),
        ]:
            await wait_speech()
            await page.locator(f"#{tab_id}").click()
            await asyncio.sleep(1)
            await show(page, narration)
        await wait_speech()

        # ── SECTION 4: Data Catalog — browse ────────────────────────────────
        print("\n[4/8] Data Catalog — browse & filter")
        await go(page, "data_catalog.html")
        await show(page, "The Data Catalog is a registry of 32 public health datasets at IDOH")
        await show(page, "14 domains are covered — Immunization, Vital Records, Behavioral Health, and more")

        await show(page, "Use domain chips to filter datasets by public health area")
        await wait_speech()
        for domain in ["Communicable Disease", "Maternal & Child Health", "Immunization & Registries"]:
            chip = page.locator(f'button.sb-item[data-domain="{domain}"]')
            if await chip.count() > 0:
                await chip.click()
                await asyncio.sleep(1.5)
        reset_chip = page.locator('button.sb-item[data-domain="all"]').first
        if await reset_chip.count() > 0:
            await reset_chip.click()
            await asyncio.sleep(1)

        # ── SECTION 5: Data Catalog — search ────────────────────────────────
        print("\n[5/8] Data Catalog — search")
        search = page.locator("#search-input, input[placeholder*='search' i]").first

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
        await wait_speech()
        cards = page.locator(".card")
        n_cards = await cards.count()
        async def close_modal():
            """Press Escape and wait for modal-overlay to lose its 'open' class."""
            await page.keyboard.press("Escape")
            try:
                await page.wait_for_selector(
                    "#modal-overlay:not(.open)", timeout=4000
                )
            except Exception:
                await asyncio.sleep(1)
            await asyncio.sleep(0.3)

        if n_cards > 0:
            await cards.first.click()
            await asyncio.sleep(1.5)
            await show(page, "Source, Data Mart, and Reporting schemas — with approximate row counts per layer")
            await wait_speech()
            await close_modal()
            if n_cards > 3:
                try:
                    await cards.nth(3).click(timeout=5000)
                    await asyncio.sleep(2)
                    await close_modal()
                except Exception:
                    pass

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
        await show(page, "IDOH Metadata Marketplace — one place for public health data assets and infrastructure")

        await hide(page)
        print("\n✅  Demo complete.")
        await asyncio.sleep(3)

        video_path = None
        if RECORD_VIDEO and page.video:
            try:
                video_path = await page.video.path()
            except Exception:
                pass

        await ctx.close()

    if RECORD_VIDEO and video_path:
        _render_mp4(str(video_path))
    elif RECORD_VIDEO:
        webm_files = glob.glob(f"{VIDEO_DIR}/*.webm")
        if webm_files:
            _render_mp4(webm_files[0])
        else:
            print("\n⚠  No .webm recording found — video recording may not have worked.")


if __name__ == "__main__":
    asyncio.run(main())
