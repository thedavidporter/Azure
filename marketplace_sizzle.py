#!/usr/bin/env python3
"""
IDOH Metadata Marketplace — Sizzle Reel
~75-second high-energy promotional cut.

Usage:
    python3 marketplace_sizzle.py

Set MUSIC_FILE to a local MP3/WAV to add background music (optional).
The final MP4 is saved to ~/marketplace_sizzle.mp4.
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

# ── Config ────────────────────────────────────────────────────────────────────
APP_URL       = "https://idoh-metadata-marketplace-5757046586469840.0.azure.databricksapps.com"
PROFILE_DIR   = "/tmp/marketplace_demo_profile"   # reuse demo's cached SSO cookies
VOICE         = True
VOICE_NAME    = "en-US-JennyNeural"
PAUSE_AFTER   = 0.1                               # tight pauses for sizzle pace

INTRO_TEXT    = "IDOH Metadata Marketplace"
INTRO_SUB     = "One platform for Azure infrastructure & public health data"
INTRO_DURATION = 3                                # seconds the intro card is shown

RECORD_VIDEO  = True
VIDEO_DIR     = "/tmp/sizzle_video"
VIDEO_SIZE    = {"width": 1920, "height": 1080}
OUTPUT_MP4    = os.path.expanduser("~/marketplace_sizzle.mp4")

MUSIC_FILE    = ""    # optional: absolute path to a background MP3/WAV
MUSIC_VOLUME  = 0.12  # 0.0–1.0; low so Jenny stays front-and-center

# Abbreviation expansions for TTS only
ABBREVS = {
    "IDOH":  "Indiana Department of Health",
    "ADF":   "Azure Data Factory",
    "ADLS":  "Azure Data Lake Storage",
    "CHIRP": "Children and Hoosiers Immunization Registry Program",
    "NBS":   "Newborn Screening",
}
_ABBREV_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in ABBREVS) + r")\b")

def _expand(text: str) -> str:
    return _ABBREV_RE.sub(lambda m: ABBREVS[m.group(0)], text)

# ── Narration lines (short, punchy) ──────────────────────────────────────────
NARRATION = [
    "Eighteen reports covering every corner of IDOH's Azure infrastructure",
    "Every pipeline tracked — across dev and production",
    "Pipeline names, trigger schedules, and last-run status",
    "Source and sink connections mapped across the factory",
    "External connections — databases, storage accounts, and APIs",
    "Recent run history — successes, failures, and durations",
    "The Synapse data warehouse — every schema, table, and column",
    "Thirty-two public health datasets across fourteen domains",
    "Filter instantly by public health domain",
    "Search by name, description, or schema — results update instantly",
    "Source, data mart, and reporting schemas — with row counts per layer",
    "IDOH Metadata Marketplace — one platform for infrastructure and public health data",
]

# ── TTS globals ───────────────────────────────────────────────────────────────
_tts_cache: dict[str, str] = {}   # text → base64-encoded PS command
_wav_cache:  dict[str, str] = {}  # text → linux wav path
_tts_proc   = None
_WIN_TTS_DIR = ""
_LIN_TTS_DIR = ""

_audio_timeline: list[tuple[float, str]] = []
_demo_start: float = 0.0


def _init_tts_dirs() -> None:
    global _WIN_TTS_DIR, _LIN_TTS_DIR
    win_temp = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-Command", "$env:TEMP"]
    ).decode().strip()
    _WIN_TTS_DIR = win_temp + "\\sizzle_tts"
    _LIN_TTS_DIR = subprocess.check_output(
        ["wslpath", "-u", _WIN_TTS_DIR]
    ).decode().strip()
    os.makedirs(_LIN_TTS_DIR, exist_ok=True)


def _encoded_play_cmd(win_wav: str) -> str:
    ps = f"(New-Object System.Media.SoundPlayer '{win_wav}').PlaySync()"
    return base64.b64encode(ps.encode("utf-16-le")).decode("ascii")


async def _prefetch() -> None:
    _init_tts_dirs()
    print(f"\nPreparing {len(NARRATION)} narration lines", end="", flush=True)
    for i, text in enumerate(NARRATION):
        mp3_path = f"{_LIN_TTS_DIR}/sz_{i:02d}.mp3"
        wav_path = f"{_LIN_TTS_DIR}/sz_{i:02d}.wav"
        win_wav  = f"{_WIN_TTS_DIR}\\sz_{i:02d}.wav"
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
    if _tts_proc and _tts_proc.poll() is None:
        while _tts_proc.poll() is None:
            await asyncio.sleep(0.05)
    await asyncio.sleep(PAUSE_AFTER)


# ── Visual helpers ────────────────────────────────────────────────────────────

# Large centered callout — the sizzle's signature overlay
_STAT_CSS = ";".join([
    "position:fixed",
    "top:50%",
    "left:50%",
    "transform:translate(-50%,-50%)",
    "background:rgba(10,22,48,0.88)",
    "color:#ffffff",
    "padding:24px 56px",
    "border-radius:18px",
    "font-size:52px",
    "font-weight:800",
    "font-family:'Segoe UI',Arial,sans-serif",
    "z-index:2147483640",   # below modals so they can layer on top
    "text-align:center",
    "letter-spacing:-0.01em",
    "line-height:1.25",
    "pointer-events:none",
    "backdrop-filter:blur(10px)",
    "box-shadow:0 8px 48px rgba(0,0,0,0.55)",
    "white-space:nowrap",
])


async def stat(page, callout: str, narration: str = "", hold: float = 0) -> None:
    """Show a large centered callout. Fires speech and optionally holds."""
    print(f"  ✦ {callout}")
    await wait_speech()
    await page.evaluate(
        """([text, css]) => {
            let el = document.getElementById('__sz_stat__');
            if (!el) {
                el = document.createElement('div');
                el.id = '__sz_stat__';
                document.body.appendChild(el);
            }
            el.style.cssText = css;
            el.textContent = text;
        }""",
        [callout, _STAT_CSS],
    )
    if narration and VOICE:
        _speak(narration)
    if hold > 0:
        await asyncio.sleep(hold)


async def clear_stat(page) -> None:
    await page.evaluate(
        "() => { const el = document.getElementById('__sz_stat__'); if (el) el.remove(); }"
    )


async def fade(page, duration: float = 0.4) -> None:
    """Flash to black — clears any stat callout and bridges sections."""
    await wait_speech()
    await clear_stat(page)
    await page.evaluate(
        """([dur]) => {
            const el = document.createElement('div');
            el.id = '__sz_fade__';
            el.style.cssText = [
                'position:fixed;top:0;left:0;width:100%;height:100%;',
                'background:#000;z-index:2147483645;',
                'opacity:0;transition:opacity 0.22s ease;pointer-events:none;',
            ].join('');
            document.body.appendChild(el);
            requestAnimationFrame(() => { el.style.opacity = '1'; });
            setTimeout(() => {
                el.style.opacity = '0';
                setTimeout(() => el.remove(), 240);
            }, dur * 1000);
        }""",
        [duration],
    )
    await asyncio.sleep(duration + 0.28)


async def intro(page) -> None:
    """Full-screen opening card with title and subtitle."""
    await page.evaluate(
        """([title, sub]) => {
            const wrap = document.createElement('div');
            wrap.id = '__sz_intro__';
            wrap.style.cssText = [
                'position:fixed;top:0;left:0;width:100%;height:100%;',
                'background:#0a1630;',
                'display:flex;flex-direction:column;gap:18px;',
                'align-items:center;justify-content:center;',
                'z-index:2147483647;opacity:1;',
            ].join('');
            const t = document.createElement('div');
            t.textContent = title;
            t.style.cssText = [
                'color:#fff;font-size:58px;font-weight:800;',
                'font-family:"Segoe UI",Arial,sans-serif;',
                'text-align:center;letter-spacing:-0.01em;',
            ].join('');
            const s = document.createElement('div');
            s.textContent = sub;
            s.style.cssText = [
                'color:rgba(255,255,255,0.6);font-size:22px;',
                'font-family:"Segoe UI",Arial,sans-serif;text-align:center;',
            ].join('');
            wrap.appendChild(t);
            wrap.appendChild(s);
            document.body.appendChild(wrap);
        }""",
        [INTRO_TEXT, INTRO_SUB],
    )
    await asyncio.sleep(INTRO_DURATION)
    await page.evaluate("""() => {
        const w = document.getElementById('__sz_intro__');
        if (w) {
            w.style.transition = 'opacity 0.4s ease';
            w.style.opacity = '0';
            setTimeout(() => w.remove(), 420);
        }
    }""")
    await asyncio.sleep(0.5)


async def end_card(page) -> None:
    """Full-screen closing card with title, tagline, and URL."""
    await wait_speech()
    await clear_stat(page)
    today = datetime.date.today().strftime("%d-%b-%Y")
    await page.evaluate(
        """([title, sub, url, date]) => {
            const wrap = document.createElement('div');
            wrap.id = '__sz_end__';
            wrap.style.cssText = [
                'position:fixed;top:0;left:0;width:100%;height:100%;',
                'background:#0a1630;',
                'display:flex;flex-direction:column;gap:16px;',
                'align-items:center;justify-content:center;',
                'z-index:2147483647;opacity:0;transition:opacity 0.5s ease;',
            ].join('');
            const mkDiv = (text, css) => {
                const d = document.createElement('div');
                d.textContent = text;
                d.style.cssText = css;
                return d;
            };
            wrap.appendChild(mkDiv(title, [
                'color:#fff;font-size:54px;font-weight:800;',
                'font-family:"Segoe UI",Arial,sans-serif;text-align:center;',
            ].join('')));
            wrap.appendChild(mkDiv(sub, [
                'color:rgba(255,255,255,0.55);font-size:20px;',
                'font-family:"Segoe UI",Arial,sans-serif;text-align:center;',
            ].join('')));
            wrap.appendChild(mkDiv(url, [
                'color:rgba(100,160,255,0.85);font-size:14px;letter-spacing:0.04em;',
                'font-family:"Segoe UI",Arial,sans-serif;text-align:center;margin-top:8px;',
            ].join('')));
            wrap.appendChild(mkDiv(date, [
                'color:rgba(255,255,255,0.3);font-size:13px;',
                'font-family:"Segoe UI",Arial,sans-serif;text-align:center;',
            ].join('')));
            document.body.appendChild(wrap);
            requestAnimationFrame(() => { wrap.style.opacity = '1'; });
        }""",
        [INTRO_TEXT, INTRO_SUB, APP_URL, today],
    )
    await asyncio.sleep(5)


async def go(page, path: str = "") -> None:
    await wait_speech()
    url = APP_URL + ("/" + path.lstrip("/") if path else "")
    await page.goto(url, wait_until="networkidle", timeout=45000)
    await asyncio.sleep(1.0)


# ── Video render ──────────────────────────────────────────────────────────────

def _render_sizzle(webm_path: str) -> None:
    print("\nRendering sizzle reel MP4...")
    probe = subprocess.check_output([
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        webm_path,
    ])
    video_dur = float(probe.decode().strip())
    print(f"  Video: {video_dur:.1f}s  |  Speech events: {len(_audio_timeline)}")

    # Build narration track
    audio_mix = None
    if _audio_timeline:
        audio_mix = "/tmp/sizzle_audio_mix.wav"
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
            f"{mix_ins}amix=inputs={n}:normalize=0:dropout_transition=0[narr]"
        )
        cmd += ["-filter_complex", ";".join(filter_parts),
                "-map", "[narr]", "-t", f"{video_dur:.3f}", audio_mix]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    has_music = bool(MUSIC_FILE and os.path.exists(MUSIC_FILE))

    ffcmd = ["ffmpeg", "-y", "-i", webm_path]
    if audio_mix:
        ffcmd += ["-i", audio_mix]
    if has_music:
        ffcmd += ["-i", MUSIC_FILE]

    if audio_mix and has_music:
        ffcmd += [
            "-filter_complex",
            f"[2]volume={MUSIC_VOLUME},aloop=loop=-1:size=2e+09[music];"
            "[1][music]amix=inputs=2:normalize=0:dropout_transition=0[final]",
            "-map", "0:v", "-map", "[final]",
        ]
    elif audio_mix:
        ffcmd += ["-map", "0:v", "-map", "1:a"]
    elif has_music:
        ffcmd += [
            "-filter_complex",
            f"[1]volume={MUSIC_VOLUME},aloop=loop=-1:size=2e+09[music]",
            "-map", "0:v", "-map", "[music]",
        ]
    else:
        ffcmd += ["-map", "0:v", "-an"]

    ffcmd += [
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        OUTPUT_MP4,
    ]
    subprocess.run(ffcmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  Saved: {OUTPUT_MP4}")


# ── Sizzle reel ───────────────────────────────────────────────────────────────

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
        _demo_start = time.monotonic()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Dark background while navigating — no white flash
        await page.set_content('<html><body style="margin:0;background:#0a1630"></body></html>')

        print("Opening Metadata Marketplace...")
        await page.goto(APP_URL, wait_until="domcontentloaded", timeout=45000)

        if "login.microsoftonline" in page.url or "login" in page.url.lower():
            print("\n⚠  SSO login required.")
            input("   Press Enter once you can see the Marketplace homepage...")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            _demo_start = time.monotonic()
            await intro(page)
        else:
            await intro(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

        await asyncio.sleep(0.5)

        # ── [1] HOMEPAGE — establish the platform ─────────────────────────────
        print("\n[1/6] Homepage")
        await stat(page, "18+ Azure Reports",
                   "Eighteen reports covering every corner of IDOH's Azure infrastructure",
                   hold=2)
        await page.evaluate("window.scrollTo({top:500,behavior:'smooth'})")
        await asyncio.sleep(1.5)
        await page.evaluate("window.scrollTo({top:0,behavior:'smooth'})")
        await asyncio.sleep(1)

        # ── [2] ADF — tab walk ────────────────────────────────────────────────
        print("\n[2/6] ADF")
        await fade(page)
        await go(page, "adf_metadata_report_prd.html")
        await stat(page, "Azure Data Factory",
                   "Every pipeline tracked — across dev and production",
                   hold=2)
        for tab_id, callout, narration in [
            ("tab-pipelines", "Pipelines",
             "Pipeline names, trigger schedules, and last-run status"),
            ("tab-datasets",  "Datasets",
             "Source and sink connections mapped across the factory"),
            ("tab-linkedsvc", "Linked Services",
             "External connections — databases, storage accounts, and APIs"),
            ("tab-monitor",   "Run History",
             "Recent run history — successes, failures, and durations"),
        ]:
            await wait_speech()
            await page.locator(f"#{tab_id}").click()
            await asyncio.sleep(0.5)
            await stat(page, callout, narration, hold=2)

        # ── [3] SYNAPSE — expand a schema ─────────────────────────────────────
        print("\n[3/6] Synapse")
        await fade(page)
        await go(page, "synapse_metadata_report_prd.html")
        await stat(page, "Schema · Table · Column",
                   "The Synapse data warehouse — every schema, table, and column",
                   hold=2)
        schema_items = page.locator(".sch-item")
        n_items = await schema_items.count()
        if n_items > 0:
            await wait_speech()
            target_item = schema_items.nth(min(2, n_items - 1))
            await target_item.locator(".sch-hdr").click()
            await asyncio.sleep(1)
            table_row = target_item.locator(".obj-row:has(.bdg-T)").first
            try:
                await table_row.scroll_into_view_if_needed(timeout=3000)
                await table_row.click(timeout=5000)
                await asyncio.sleep(2)
            except Exception:
                pass

        # ── [4] DATA CATALOG — domain chips + search ──────────────────────────
        print("\n[4/6] Data Catalog")
        await fade(page)
        await go(page, "data_catalog.html")
        await stat(page, "32 Datasets · 14 Domains",
                   "Thirty-two public health datasets across fourteen domains",
                   hold=2)
        await wait_speech()
        for domain in ["Communicable Disease", "Maternal & Child Health", "Immunization & Registries"]:
            chip = page.locator(f'button.sb-item[data-domain="{domain}"]')
            if await chip.count() > 0:
                await chip.click()
                await asyncio.sleep(1)
        reset_chip = page.locator('button.sb-item[data-domain="all"]').first
        if await reset_chip.count() > 0:
            await reset_chip.click()
            await asyncio.sleep(0.5)

        search = page.locator("#search-input, input[placeholder*='search' i]").first
        if await search.count() > 0:
            await stat(page, "Instant Search",
                       "Search by name, description, or schema — results update instantly",
                       hold=1)
            await wait_speech()
            await search.click()
            await page.keyboard.type("immunization", delay=70)
            await asyncio.sleep(2)
            await search.click(click_count=3)
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.5)

        # ── [5] DATASET MODAL — data lineage ──────────────────────────────────
        print("\n[5/6] Dataset modal")
        await fade(page)
        await stat(page, "Full Data Lineage",
                   "Source, data mart, and reporting schemas — with row counts per layer",
                   hold=2)
        await wait_speech()
        await clear_stat(page)   # remove before modal opens so it doesn't overlap
        cards = page.locator(".card")
        if await cards.count() > 0:
            await cards.first.click()
            await asyncio.sleep(3)
            await page.keyboard.press("Escape")
            try:
                await page.wait_for_selector("#modal-overlay:not(.open)", timeout=3000)
            except Exception:
                await asyncio.sleep(1)

        # ── [6] END CARD ──────────────────────────────────────────────────────
        print("\n[6/6] End card")
        await fade(page)
        await stat(page, "IDOH Metadata Marketplace",
                   "IDOH Metadata Marketplace — one platform for infrastructure and public health data",
                   hold=1)
        await wait_speech()
        await end_card(page)

        print("\n✅  Sizzle reel complete.")
        await asyncio.sleep(2)

        video_path = None
        if RECORD_VIDEO and page.video:
            try:
                video_path = await page.video.path()
            except Exception:
                pass

        await ctx.close()

    if RECORD_VIDEO and video_path:
        _render_sizzle(str(video_path))
    elif RECORD_VIDEO:
        webm_files = glob.glob(f"{VIDEO_DIR}/*.webm")
        if webm_files:
            _render_sizzle(webm_files[0])
        else:
            print("\n⚠  No .webm recording found — video recording may not have worked.")


if __name__ == "__main__":
    asyncio.run(main())
