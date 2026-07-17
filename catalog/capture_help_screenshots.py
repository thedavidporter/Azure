#!/usr/bin/env python3
"""
Capture viewport screenshots of each metadata report HTML file and upload
to Azure Blob Storage (screenshots/ prefix in $web container).
These images are displayed in help.html Q&A answers.

Runs via cron on the 1st of each month at 6am.
Uses local file:// URLs so no auth or network access to the reports is needed.
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime
from playwright.sync_api import sync_playwright

REPORTS_DIR     = "/home/thedavidporter"
STORAGE_ACCOUNT = "zus1idohdevv2dbrkdl"
CONTAINER       = "$web"
BLOB_PREFIX     = "screenshots"
VIEWPORT        = {"width": 1440, "height": 860}

# (html filename, screenshot blob name)
SCREENSHOTS = [
    ("synapse_metadata_report_dev.html",   "ss_synapse_dev.png"),
    ("synapse_metadata_delta_dev.html",    "ss_synapse_delta.png"),
    ("adf_metadata_report_dev.html",       "ss_adf_dev.png"),
    ("adls_metadata_report.html",          "ss_adls.png"),
    ("databricks_metadata_report.html",    "ss_databricks.png"),
    ("sql_dw_metadata_report_dev.html",    "ss_sql_dw.png"),
    ("keyvault_metadata_report_dev.html",  "ss_keyvault.png"),
    ("logic_apps_metadata_report_dev.html","ss_logic_apps.png"),
    ("vnet_metadata_report.html",          "ss_vnet.png"),
    ("ado_metadata_report.html",           "ss_ado.png"),
    ("index.html",                         "ss_index.png"),
]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def capture(page, html_path, out_path):
    url = f"file://{html_path}"
    page.goto(url, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(2000)  # let JS finish rendering tabs/trees/tables
    page.screenshot(
        path=out_path,
        clip={"x": 0, "y": 0, "width": VIEWPORT["width"], "height": VIEWPORT["height"]},
    )
    kb = os.path.getsize(out_path) // 1024
    log(f"  captured  {os.path.basename(out_path)}  ({kb} KB)")


def upload(local_path, blob_name):
    r = subprocess.run(
        [
            "az", "storage", "blob", "upload",
            "--account-name", STORAGE_ACCOUNT,
            "--container-name", CONTAINER,
            "--name", blob_name,
            "--file", local_path,
            "--content-type", "image/png",
            "--auth-mode", "login",
            "--overwrite",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log(f"  ERROR uploading {blob_name}: {r.stderr.strip()}")
        return False
    log(f"  uploaded  {blob_name}")
    return True


def main():
    log("=== Metadata Report Screenshot Capture ===")
    log(f"Reports dir : {REPORTS_DIR}")
    log(f"Destination : {STORAGE_ACCOUNT}/{CONTAINER}/{BLOB_PREFIX}/")
    print()

    errors   = []
    captured = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page    = browser.new_page(viewport=VIEWPORT)

            for html_file, png_name in SCREENSHOTS:
                html_path = os.path.join(REPORTS_DIR, html_file)
                out_path  = os.path.join(tmpdir, png_name)

                if not os.path.exists(html_path):
                    log(f"  SKIP {html_file} (file not found)")
                    continue

                log(f"Processing {html_file} …")
                try:
                    capture(page, html_path, out_path)
                except Exception as exc:
                    log(f"  ERROR capturing {html_file}: {exc}")
                    errors.append(html_file)
                    continue

                if upload(out_path, f"{BLOB_PREFIX}/{png_name}"):
                    captured += 1
                else:
                    errors.append(png_name)

            browser.close()

    print()
    if errors:
        log(f"Finished with {len(errors)} error(s): {', '.join(errors)}")
        log(f"{captured} of {len(SCREENSHOTS)} screenshots updated.")
        sys.exit(1)
    else:
        log(f"All {captured} screenshots captured and uploaded successfully.")


if __name__ == "__main__":
    main()
