#!/usr/bin/env python3
"""
Parse the existing adf_metadata_report_{env}.html files and report which
pipelines have no trigger and are not called by any other pipeline.

For each such pipeline, show:
  - folder
  - activity count and types
  - last run date, status, and how it was invoked (from the 7-day monitor data)
  - a likely classification (Manual/Ad-hoc, Deprecated/Orphan, Missing Trigger, Test)
"""

import json
import re
from collections import Counter
from pathlib import Path

REPORTS = {
    "dev": Path("/home/thedavidporter/adf_metadata_report_dev.html"),
    "prd": Path("/home/thedavidporter/adf_metadata_report_prd.html"),
}

# Folder/name keywords that hint at the pipeline's purpose
MANUAL_HINTS  = {"manual", "ad_hoc", "adhoc", "ad-hoc", "one_time", "onetime"}
ARCHIVE_HINTS = {"archive", "retired", "old", "deprecated", "legacy", "backup"}
TEST_HINTS    = {"test", "testing", "temp", "tmp", "debug", "poc", "sample", "demo", "reserve"}
TRAIN_HINTS   = {"training", "train"}


def extract_json_var(html: str, var_name: str):
    pattern = rf"const {re.escape(var_name)}\s*=\s*(\{{.*?\}}|\[.*?\]);"
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        raise ValueError(f"Could not find {var_name} in HTML")
    return json.loads(m.group(1))


def classify(name: str, folder: str, act_types: list[str]) -> str:
    n = name.lower()
    f = folder.lower()
    combined = n + " " + f

    if any(h in combined for h in TEST_HINTS):
        return "Test / Reserve"
    if any(h in combined for h in TRAIN_HINTS):
        return "Training"
    if any(h in combined for h in MANUAL_HINTS):
        return "Manual / Ad-hoc"
    if any(h in combined for h in ARCHIVE_HINTS):
        return "Archived / Deprecated"

    # Pipelines whose names suggest they are top-level orchestrators
    # (end in _MASTER, _MAIN, _PIPELINE) but have no trigger are likely
    # missing a trigger rather than intentionally untriggered.
    if any(n.endswith(s) for s in ("_master", "_main", "_pipeline", "_run")):
        return "Likely missing trigger"

    return "Unknown / Needs review"


def analyse(env: str, path: Path):
    print(f"\n{'='*70}")
    print(f"  ENVIRONMENT: {env.upper()}")
    print(f"{'='*70}")

    html = path.read_text(encoding="utf-8")

    pipeline_map = extract_json_var(html, "PIPELINE_DATA")
    trigger_list = extract_json_var(html, "TRIGGER_DATA")

    # Try to extract run data (may not exist in all report versions)
    try:
        run_list = extract_json_var(html, "RUN_DATA")
    except ValueError:
        run_list = []

    # If no RUN_DATA global, scrape the mon-tbl rows for last-run info
    # (The HTML encodes runs inline — we'll fall back to parsing the JSON blobs
    # from the JS that actually stores run data differently.)
    # Instead, look for the runs embedded in the monitor table via regex.
    # Actually the runs are NOT stored as a JS variable — they're baked into HTML.
    # We'll parse them from the <tbody> of #mon-tbl.
    run_info: dict[str, dict] = {}  # pipeline_name → {last_start, status, triggered_by}
    if not run_list:
        # Parse mon-tbl rows: each <tr data-status="..."> contains pipeline link + status chip
        row_pattern = re.compile(
            r'<tr data-status="([^"]+)">'
            r'.*?<a class="mon-pipeline-link"[^>]*>([^<]+)</a>'
            r'.*?<span class="status-chip[^"]*">([^<]+)</span>'
            r'.*?<td[^>]*>([\d\-: ]*)</td>'   # start date
            r'.*?</tr>',
            re.DOTALL
        )
        for m in row_pattern.finditer(html):
            status_lc, pname, status_display, start = (
                m.group(1).strip(), m.group(2).strip(),
                m.group(3).strip(), m.group(4).strip()
            )
            # Keep most recent (rows are sorted newest-first in the report)
            if pname not in run_info:
                run_info[pname] = {"status": status_display, "last_start": start}

    # pipelines triggered directly
    triggered = set()
    for t in trigger_list:
        for p in t.get("pipelines", []):
            triggered.add(p)

    # callers map
    callers: dict[str, list[str]] = {}
    for pipe_name, pipe in pipeline_map.items():
        for act in pipe.get("activities", []):
            child = act.get("sub_pipeline", "")
            if child:
                callers.setdefault(child, []).append(pipe_name)

    truly_standalone = sorted(
        n for n in pipeline_map if n not in triggered and n not in callers
    )

    print(f"\nTotals: {len(pipeline_map)} pipelines | {len(triggered)} triggered | "
          f"{len(callers)} are child pipelines | {len(truly_standalone)} truly standalone\n")

    # Group by classification
    by_class: dict[str, list[tuple]] = {}
    for name in truly_standalone:
        pipe   = pipeline_map[name]
        folder = pipe.get("folder", "") or "(No Folder)"
        acts   = pipe.get("activities", [])
        act_types = [a.get("type", "?") for a in acts]
        type_counts = Counter(act_types)
        label  = classify(name, folder, act_types)
        run    = run_info.get(name)
        by_class.setdefault(label, []).append((name, folder, type_counts, run, pipe))

    for label, entries in sorted(by_class.items()):
        print(f"\n{'─'*70}")
        print(f"  {label.upper()}  ({len(entries)} pipelines)")
        print(f"{'─'*70}")
        for name, folder, type_counts, run, pipe in entries:
            desc = pipe.get("description", "") or ""
            act_summary = ", ".join(
                f"{cnt}×{t}" for t, cnt in type_counts.most_common()
            ) or "no activities"

            print(f"\n  Pipeline : {name}")
            print(f"  Folder   : {folder}")
            if desc:
                print(f"  Desc     : {desc[:120]}")
            print(f"  Acts     : {act_summary}")
            if run:
                print(f"  Last Run : {run['last_start']}  [{run['status']}]")
            else:
                print(f"  Last Run : no run in past 7 days")


def main():
    for env, path in REPORTS.items():
        if not path.exists():
            print(f"[SKIP] {path} not found.")
            continue
        analyse(env, path)


if __name__ == "__main__":
    main()
