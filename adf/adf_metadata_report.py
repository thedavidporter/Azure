#!/usr/bin/env python3
"""
Azure Data Factory Metadata Report
Usage:
  python3 adf_metadata_report.py --env dev
  python3 adf_metadata_report.py --env prd
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from azure.identity import DefaultAzureCredential
from azure.mgmt.datafactory import DataFactoryManagementClient
from azure.mgmt.datafactory.models import RunFilterParameters

ENVIRONMENTS = {
    "dev": {
        "subscription_id": "57493fde-eff8-432f-8574-4f1281bd2ce3",
        "resource_group": "zus1-idoh-dev-v2-rg",
        "factory_name":   "zus1-idoh-dev-v2-df",
    },
    "prd": {
        "subscription_id": "57493fde-eff8-432f-8574-4f1281bd2ce3",
        "resource_group": "zus1-idoh-prd-v1-rg",
        "factory_name":   "zus1-idoh-prd-v1-df",
    },
}

ADF_PORTAL = "https://adf.azure.com"

def fmt_duration(ms):
    if ms is None:
        return "—"
    s = int(ms) // 1000
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def monitor_run_url(run_id, resource_group, factory_name, subscription_id):
    factory_path = (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.DataFactory/factories/{factory_name}"
    )
    return f"{ADF_PORTAL}/en/monitoring/pipelineruns/{run_id}?factory={factory_path}"

# ── helpers ────────────────────────────────────────────────────────────────────

def safe(v):
    return "" if v is None else str(v)

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def js_esc(s):
    if s is None: return ""
    return str(s).replace("\\","\\\\").replace("'","\\'").replace('"','\\"').replace("\n","\\n").replace("\r","")

def ref_name(obj):
    if obj is None: return ""
    return safe(getattr(obj, 'reference_name', '') or '')

def _safe_val(val):
    """Convert SDK value types (SecureString, KV refs, primitives) to display string."""
    if val is None:
        return ''
    cls = type(val).__name__
    # SecureString — show value if present (plaintext), else mark secure
    if cls in ('SecureString',):
        inner = getattr(val, 'value', None)
        return str(inner) if inner else '[Secure]'
    # AzureKeyVaultSecretReference
    if hasattr(val, 'store') and hasattr(val, 'secret_name'):
        store  = ref_name(getattr(val, 'store', None))
        secret = safe(getattr(val, 'secret_name', ''))
        ver    = safe(getattr(val, 'secret_version', ''))
        return f'[KeyVault] {store} → {secret}' + (f' (v{ver})' if ver else '')
    if isinstance(val, (str, int, float, bool)):
        return str(val)
    if isinstance(val, list):
        return ', '.join(_safe_val(v) for v in val)
    try:
        s = str(val)
        return s[:300] + '…' if len(s) > 300 else s
    except Exception:
        return cls

def parse_connection_string(cs):
    """Return non-sensitive key→value pairs from a semicolon-delimited connection string."""
    if not cs or not isinstance(cs, str):
        return {}
    SKIP = {'password', 'pwd', 'accountkey', 'sharedaccesssignature', 'accesstoken', 'secret'}
    out = {}
    for part in cs.split(';'):
        if '=' not in part:
            continue
        k, _, v = part.partition('=')
        k = k.strip().lower().replace(' ', '')
        v = v.strip()
        if v and k not in SKIP:
            out[k] = v
    return out

# ── extraction ─────────────────────────────────────────────────────────────────

def extract_activity(act, pipe_name=""):
    cls = type(act).__name__
    act_type = cls.replace("Activity", "") if cls.endswith("Activity") else cls
    name = safe(getattr(act, 'name', '') or '')
    desc = safe(getattr(act, 'description', '') or '')

    deps = []
    for d in (getattr(act, 'depends_on', None) or []):
        deps.append({
            "activity":   safe(getattr(d, 'activity', '') or ''),
            "conditions": [safe(c) for c in (getattr(d, 'dependency_conditions', None) or [])],
        })

    inputs  = [ref_name(r) for r in (getattr(act, 'inputs',  None) or []) if ref_name(r)]
    outputs = [ref_name(r) for r in (getattr(act, 'outputs', None) or []) if ref_name(r)]

    ds_attr = getattr(act, 'dataset', None)
    if ds_attr:
        rn = ref_name(ds_attr)
        if rn and rn not in inputs:
            inputs.append(rn)

    ls_attr = getattr(act, 'linked_service_name', None)
    linked_service = ref_name(ls_attr) if ls_attr else ""

    pipe_attr = getattr(act, 'pipeline', None)
    sub_pipeline = ref_name(pipe_attr) if pipe_attr else ""

    df_attr = getattr(act, 'data_flow', None)
    data_flow = ref_name(df_attr) if df_attr else ""

    # Collect nested activity names (ForEach, IfCondition, Switch)
    nested = []
    for attr in ['activities', 'if_true_activities', 'if_false_activities']:
        for n in (getattr(act, attr, None) or []):
            n_name = safe(getattr(n, 'name', '') or '')
            if n_name:
                nested.append(n_name)

    return {
        "name": name, "type": act_type, "description": desc,
        "depends_on": deps, "inputs": inputs, "outputs": outputs,
        "linked_service": linked_service, "sub_pipeline": sub_pipeline,
        "data_flow": data_flow, "nested": nested, "pipeline": pipe_name,
    }


def extract_pipeline(p):
    name   = safe(p.name or '')
    folder = safe(getattr(getattr(p, 'folder', None), 'name', '') or '')
    desc   = safe(getattr(p, 'description', '') or '')

    activities = []
    for act in (getattr(p, 'activities', None) or []):
        try:
            activities.append(extract_activity(act, name))
        except Exception as e:
            activities.append({
                "name": safe(getattr(act, 'name', 'Unknown')),
                "type": type(act).__name__, "description": f"[parse error: {e}]",
                "depends_on": [], "inputs": [], "outputs": [],
                "linked_service": "", "sub_pipeline": "", "data_flow": "",
                "nested": [], "pipeline": name,
            })

    params = []
    for pname, pspec in (getattr(p, 'parameters', None) or {}).items():
        params.append({
            "name":    pname,
            "type":    safe(getattr(pspec, 'type', '') or ''),
            "default": safe(getattr(pspec, 'default_value', '') or ''),
        })

    return {"name": name, "folder": folder, "description": desc,
            "activities": activities, "parameters": params}


def extract_dataset(ds):
    props = ds.properties
    t  = type(props).__name__.replace("Dataset", "")
    ls = ref_name(getattr(props, 'linked_service_name', None))
    return {"name": safe(ds.name or ''), "type": t, "linked_service": ls,
            "description": safe(getattr(props, 'description', '') or '')}


def extract_linked_service(ls):
    props = ls.properties
    t  = type(props).__name__.replace("LinkedService", "")
    ir = ref_name(getattr(props, 'connect_via', None))

    # Ordered list of (sdk_attr, display_label)
    CONN_ATTRS = [
        ('url',                     'URL'),
        ('base_url',                'Base URL'),
        ('host',                    'Host'),
        ('host_name',               'Host'),
        ('port',                    'Port'),
        ('server',                  'Server'),
        ('instance_name',           'Instance'),
        ('database',                'Database'),
        ('schema',                  'Schema'),
        ('account_name',            'Account Name'),
        ('service_endpoint',        'Service Endpoint'),
        ('container_uri',           'Container URI'),
        ('sas_uri',                 'SAS URI'),
        ('data_lake_store_uri',     'ADLS URI'),
        ('domain',                  'Workspace URL'),
        ('workspace_endpoint',      'Workspace Endpoint'),
        ('workspace_resource_id',   'Workspace Resource ID'),
        ('site_url',                'Site URL'),
        ('endpoint',                'Endpoint'),
        ('location',                'Location'),
        ('tenant',                  'Tenant'),
        ('tenant_id',               'Tenant ID'),
        ('service_principal_id',    'Service Principal ID'),
        ('authentication_type',     'Auth Type'),
        ('user_name',               'Username'),
        ('username',                'Username'),
        ('encrypt_connection',      'Encrypted'),
        ('trust_server_certificate','Trust Server Cert'),
        ('existing_cluster_id',     'Cluster ID'),
        ('new_cluster_node_type',   'Cluster Node Type'),
        ('new_cluster_num_workers', 'Cluster Workers'),
        ('new_cluster_spark_conf',  'Spark Config'),
        ('file_share',              'File Share'),
        ('file_path',               'File Path'),
        ('packet_size',             'Packet Size'),
    ]

    conn = {}
    for attr, label in CONN_ATTRS:
        val = getattr(props, attr, None)
        if val is not None:
            s = _safe_val(val)
            if s:
                conn[label] = s

    # Connection string — parse non-sensitive parts
    cs_raw = getattr(props, 'connection_string', None)
    if cs_raw is not None:
        cs_str = _safe_val(cs_raw)
        if cs_str and cs_str not in ('[Secure]',):
            conn['Connection String'] = cs_str
            for k, v in parse_connection_string(cs_str).items():
                nice = k.replace('datasource','Server').replace('initialcatalog','Database') \
                        .replace('data source','Server').replace('initial catalog','Database') \
                        .replace('server','Server').replace('database','Database') \
                        .replace('userid','Username').replace('user id','Username') \
                        .replace('integrated security','Integrated Security') \
                        .replace('encrypt','Encrypt').replace('trustservercertificate','Trust Cert') \
                        .replace('multipleactiveresultsets','MARS') \
                        .replace('connection timeout','Connect Timeout') \
                        .replace('applicationintent','App Intent')
                conn.setdefault(f'  {nice}', v)
        else:
            conn['Connection String'] = '[Encrypted / Secure]'

    # Derive summary fields for the table
    server = (conn.get('Host') or conn.get('Server') or conn.get('URL') or
              conn.get('Base URL') or conn.get('Workspace URL') or
              conn.get('Service Endpoint') or conn.get('ADLS URI') or
              conn.get('Workspace Endpoint') or conn.get('Site URL') or
              conn.get('  Server') or conn.get('  data source') or '')
    database = (conn.get('Database') or conn.get('Schema') or
                conn.get('  Database') or conn.get('  initialcatalog') or '')
    auth = conn.get('Auth Type', '')

    return {
        "name":                safe(ls.name or ''),
        "type":                t,
        "integration_runtime": ir,
        "description":         safe(getattr(props, 'description', '') or ''),
        "conn":                conn,
        "server":              server,
        "database":            database,
        "auth":                auth,
    }


def extract_trigger(trig):
    props = trig.properties
    t     = type(props).__name__.replace("Trigger", "")
    state = safe(getattr(props, 'runtime_state', '') or '')

    pipelines = []
    for pr in (getattr(props, 'pipelines', None) or []):
        p_ref = getattr(pr, 'pipeline_reference', None)
        pname = ref_name(p_ref) if p_ref else ref_name(pr)
        if pname: pipelines.append(pname)
    single = getattr(props, 'pipeline', None)
    if single:
        p_ref = getattr(single, 'pipeline_reference', None)
        pname = ref_name(p_ref) if p_ref else ref_name(single)
        if pname: pipelines.append(pname)

    schedule = ""
    rec = getattr(props, 'recurrence', None)
    if rec:
        freq     = safe(getattr(rec, 'frequency', '') or '')
        interval = safe(getattr(rec, 'interval',  '') or '')
        schedule = f"{freq} × {interval}" if freq else ""
    elif getattr(props, 'frequency', None):
        freq     = safe(props.frequency)
        interval = safe(getattr(props, 'interval', '') or '')
        schedule = f"{freq} × {interval}"

    return {"name": safe(trig.name or ''), "type": t, "pipelines": pipelines,
            "schedule": schedule, "state": state,
            "description": safe(getattr(props, 'description', '') or '')}


def extract_data_flow(df):
    props = df.properties
    t = type(props).__name__.replace("DataFlow", "")
    sources, sinks = [], []
    for s in (getattr(props, 'sources', None) or []):
        ds   = getattr(s, 'dataset', None)
        name = ref_name(ds) if ds else safe(getattr(s, 'name', '') or '')
        if name: sources.append(name)
    for s in (getattr(props, 'sinks', None) or []):
        ds   = getattr(s, 'dataset', None)
        name = ref_name(ds) if ds else safe(getattr(s, 'name', '') or '')
        if name: sinks.append(name)
    return {"name": safe(df.name or ''), "type": t,
            "description": safe(getattr(props, 'description', '') or ''),
            "sources": sources, "sinks": sinks}


def extract_ir(ir):
    props = ir.properties
    t = type(props).__name__.replace("IntegrationRuntime", "")
    return {"name": safe(ir.name or ''), "type": t,
            "description": safe(getattr(props, 'description', '') or '')}


# ── hierarchy helpers ──────────────────────────────────────────────────────────

_TEST_KW    = {"test", "reserve", "tmp", "temp", "poc", "demo", "sample"}
_TRAIN_KW   = {"training", "train"}
_MANUAL_KW  = {"manual", "ad_hoc", "adhoc", "ad-hoc", "one_time"}
_ARCH_KW    = {"archive", "deprecated", "retired", "legacy", "old"}
_MASTER_SFX = ("_master", "_main", "_pipeline", "_run")


def _classify_standalone(name: str, folder: str) -> str:
    c = (name + " " + folder).lower()
    if any(k in c for k in _TEST_KW):   return "test"
    if any(k in c for k in _TRAIN_KW):  return "training"
    if any(k in c for k in _MANUAL_KW): return "manual"
    if any(k in c for k in _ARCH_KW):   return "archived"
    if any(name.lower().endswith(s) for s in _MASTER_SFX): return "missing_trigger"
    return "unknown"


def _run_badge(run: dict | None) -> str:
    if not run:
        return '<span class="htree-run-none">no recent run</span>'
    s = run["status"]
    d = run["start"][:10] if run.get("start") else ""
    cls = ("htree-run-ok" if s == "Succeeded"
           else "htree-run-fail" if s == "Failed"
           else "htree-run-none")
    sym = "●" if s == "Succeeded" else "✗" if s == "Failed" else "○"
    return f'<span class="{cls}">{sym} {esc(s)} {d}</span>'


def _pipe_link(name: str) -> str:
    return (f'<span class="htree-pipe-name" '
            f'onclick="openPipeline(\'{js_esc(name)}\',null)">{esc(name)}</span>')


def _render_children(pipe_name: str, pipeline_map: dict, children_map: dict,
                     last_run_map: dict, visited: set, depth: int = 0) -> str:
    children = children_map.get(pipe_name, [])
    if not children or depth > 14:
        return ""
    parts = []
    for child in children:
        pipe   = pipeline_map.get(child, {})
        folder = pipe.get("folder", "") or ""
        run    = last_run_map.get(child)
        folder_tag = (f'<span class="htree-folder-tag">📁 {esc(folder)}</span>'
                      if folder else "")
        if child in visited:
            parts.append(
                f'<div class="htree-child-row">'
                f'<span class="htree-child-connector">└─</span>'
                f'<span style="color:var(--mut);font-size:11px">↑ {esc(child)} (already shown above)</span>'
                f'</div>'
            )
            continue
        row = (f'<div class="htree-child-row">'
               f'<span class="htree-child-connector">└─</span>'
               f'{_pipe_link(child)} {folder_tag} {_run_badge(run)}'
               f'</div>')
        grandkids = _render_children(child, pipeline_map, children_map,
                                     last_run_map, visited | {child}, depth + 1)
        if grandkids:
            row += f'<div class="htree-children">{grandkids}</div>'
        parts.append(row)
    return "".join(parts)


def _build_hierarchy_html(pipelines: list, triggers: list, pipeline_map: dict,
                          pipe_to_trigs: dict, children_map: dict,
                          all_children_set: set, last_run_map: dict) -> str:
    parts: list[str] = []
    all_triggered = {n for t in triggers for n in t["pipelines"]}

    # ── Section 1: Triggered chains ──────────────────────────────────────────
    parts.append('<div class="htree-section" id="htree-sec-triggered">')
    parts.append(
        f'<div class="htree-section-title">'
        f'🔔 Triggered Entry Points — {len(all_triggered)} pipeline(s) across {len(triggers)} trigger(s)'
        f'</div>'
    )
    for trig in sorted(triggers, key=lambda t: t["name"]):
        t_name  = trig["name"]
        t_sched = trig["schedule"]
        t_state = trig["state"]
        t_pipes = trig["pipelines"]
        state_cls   = ("state-started" if t_state.lower() == "started"
                        else "state-stopped")
        sched_html  = (f'<span style="color:var(--mut);font-size:10px"> · {esc(t_sched)}</span>'
                       if t_sched else "")
        state_html  = f'<span class="{state_cls}" style="font-size:10px">{esc(t_state)}</span>'
        pipe_trees  = []
        for pname in sorted(t_pipes):
            pipe   = pipeline_map.get(pname, {})
            folder = pipe.get("folder", "") or ""
            run    = last_run_map.get(pname)
            folder_tag = (f'<span class="htree-folder-tag">📁 {esc(folder)}</span>'
                          if folder else "")
            kids = _render_children(pname, pipeline_map, children_map,
                                    last_run_map, {pname})
            body = (f'<div style="padding:5px 0">'
                    f'<div class="htree-child-row">'
                    f'<span class="htree-child-connector" style="color:var(--cyn)">▶</span>'
                    f'{_pipe_link(pname)} {folder_tag} {_run_badge(run)}'
                    f'</div>'
                    + (f'<div class="htree-children">{kids}</div>' if kids else '') +
                    f'</div>')
            pipe_trees.append(body)
        parts.append(
            f'<div class="htree-root-card" data-htree-name="{esc(t_name.lower())}">'
            f'<div class="htree-root-hdr" onclick="toggleHTree(this)">'
            f'<span class="arr">&#x25B6;</span>'
            f'<span class="chip chip-trig">{esc(t_name)}</span>'
            f'{sched_html} {state_html}'
            f'<span style="margin-left:auto;font-size:10px;color:var(--mut)">'
            f'{len(t_pipes)} pipeline(s)</span>'
            f'</div>'
            f'<div class="htree-root-body">{"".join(pipe_trees)}</div>'
            f'</div>'
        )
    parts.append('</div>')

    # ── Section 2: Untriggered orchestrators (no trigger, no parent, has children) ──
    orch = sorted(
        n for n in pipeline_map
        if n not in all_triggered and n not in all_children_set and n in children_map
    )
    if orch:
        parts.append('<div class="htree-section" id="htree-sec-orch">')
        parts.append(
            f'<div class="htree-section-title">'
            f'⚠ Untriggered Orchestrators — no trigger, no parent, but calls children '
            f'({len(orch)} pipelines) — likely missing a trigger'
            f'</div>'
        )
        for pname in orch:
            pipe   = pipeline_map.get(pname, {})
            folder = pipe.get("folder", "") or ""
            run    = last_run_map.get(pname)
            folder_tag = (f'<span class="htree-folder-tag">📁 {esc(folder)}</span>'
                          if folder else "")
            kids = _render_children(pname, pipeline_map, children_map,
                                    last_run_map, {pname})
            parts.append(
                f'<div class="htree-root-card htree-card-warn" '
                f'data-htree-name="{esc(pname.lower())}">'
                f'<div class="htree-root-hdr" onclick="toggleHTree(this)">'
                f'<span class="arr">&#x25B6;</span>'
                f'<span class="htree-class-badge htree-cls-missing">⚠ No Trigger</span>'
                f'{_pipe_link(pname)} {folder_tag} {_run_badge(run)}'
                f'</div>'
                f'<div class="htree-root-body">'
                + (f'<div class="htree-children">{kids}</div>'
                   if kids else '<p style="color:var(--mut);font-size:12px">No child pipelines.</p>') +
                f'</div>'
                f'</div>'
            )
        parts.append('</div>')

    # ── Section 3: Truly standalone ──────────────────────────────────────────
    standalone = sorted(
        n for n in pipeline_map
        if n not in all_triggered and n not in all_children_set and n not in children_map
    )
    if standalone:
        by_class: dict[str, list[str]] = {}
        for n in standalone:
            folder = pipeline_map.get(n, {}).get("folder", "") or ""
            by_class.setdefault(_classify_standalone(n, folder), []).append(n)

        CLASS_META = {
            "missing_trigger": ("Missing Trigger",       "htree-cls-missing",  "⚠"),
            "manual":          ("Manual / Ad-hoc",        "htree-cls-manual",   "🖐"),
            "test":            ("Test / Reserve",         "htree-cls-test",     "🧪"),
            "training":        ("Training",               "htree-cls-training", "📚"),
            "archived":        ("Archived / Deprecated",  "htree-cls-archived", "🗄"),
            "unknown":         ("Unknown / Needs Review", "htree-cls-unknown",  "?"),
        }
        parts.append('<div class="htree-section" id="htree-sec-standalone">')
        parts.append(
            f'<div class="htree-section-title">'
            f'🔍 Standalone Pipelines — no trigger, no parent, no children '
            f'({len(standalone)} pipelines)'
            f'</div>'
        )
        for cls_key in ("missing_trigger", "manual", "test", "training", "archived", "unknown"):
            entries = by_class.get(cls_key, [])
            if not entries:
                continue
            label, badge_cls, icon = CLASS_META[cls_key]
            parts.append(
                f'<div style="margin-bottom:16px">'
                f'<div style="font-size:11px;font-weight:700;color:var(--mut);'
                f'margin-bottom:6px">{icon} {label} ({len(entries)})</div>'
            )
            for n in entries:
                pipe   = pipeline_map.get(n, {})
                folder = pipe.get("folder", "") or ""
                acts   = pipe.get("activities", [])
                desc   = pipe.get("description", "") or ""
                run    = last_run_map.get(n)
                type_counts = Counter(a.get("type", "?") for a in acts)
                act_summary = (", ".join(f'{c}×{t}' for t, c in type_counts.most_common())
                               or "no activities")
                folder_tag = (f'<span class="htree-folder-tag">📁 {esc(folder)}</span>'
                              if folder else "")
                desc_html = (f'<div style="font-size:10px;color:var(--mut);margin-top:2px">'
                             f'{esc(desc[:130])}</div>' if desc else "")
                parts.append(
                    f'<div class="htree-standalone-card" '
                    f'data-htree-name="{esc(n.lower())}">'
                    f'<span class="htree-class-badge {badge_cls}">{esc(label)}</span>'
                    f'<div style="flex:1;min-width:0">'
                    f'<span class="htree-standalone-name" '
                    f'onclick="openPipeline(\'{js_esc(n)}\',null)">{esc(n)}</span>'
                    f' {folder_tag}'
                    f'{desc_html}'
                    f'<div class="htree-standalone-acts">{esc(act_summary)}</div>'
                    f'</div>'
                    f'<div style="text-align:right;white-space:nowrap">{_run_badge(run)}</div>'
                    f'</div>'
                )
            parts.append('</div>')
        parts.append('</div>')

    return '\n'.join(parts) or '<p style="color:var(--mut)">No pipelines found.</p>'


def fetch_all(client, resource_group, factory_name):
    def page(label, pager, fn):
        print(f"  {label}...", end="", flush=True)
        try:
            items = [fn(x) for x in pager]
            print(f" {len(items)}")
            return items
        except Exception as e:
            print(f" ERROR: {e}")
            return []
    rg, fn = resource_group, factory_name
    return {
        "pipelines":            page("Pipelines",            client.pipelines.list_by_factory(rg, fn),            extract_pipeline),
        "datasets":             page("Datasets",             client.datasets.list_by_factory(rg, fn),             extract_dataset),
        "linked_services":      page("Linked Services",      client.linked_services.list_by_factory(rg, fn),      extract_linked_service),
        "triggers":             page("Triggers",             client.triggers.list_by_factory(rg, fn),             extract_trigger),
        "data_flows":           page("Data Flows",           client.data_flows.list_by_factory(rg, fn),           extract_data_flow),
        "integration_runtimes": page("Integration Runtimes", client.integration_runtimes.list_by_factory(rg, fn), extract_ir),
    }


def fetch_pipeline_runs(client, resource_group, factory_name, subscription_id, days=7):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    print(f"  Pipeline Runs (last {days}d)...", end="", flush=True)
    params = RunFilterParameters(last_updated_after=start, last_updated_before=now)
    all_runs = []
    try:
        result = client.pipeline_runs.query_by_factory(
            resource_group_name=resource_group,
            factory_name=factory_name,
            filter_parameters=params,
        )
        all_runs.extend(result.value or [])
        while result.continuation_token:
            params.continuation_token = result.continuation_token
            result = client.pipeline_runs.query_by_factory(
                resource_group_name=resource_group,
                factory_name=factory_name,
                filter_parameters=params,
            )
            all_runs.extend(result.value or [])
    except Exception as e:
        print(f" ERROR: {e}")
        return []

    runs = []
    for r in all_runs:
        invoked = getattr(r, 'invoked_by', None)
        triggered_name = safe(getattr(invoked, 'name', '') or '') if invoked else ''
        triggered_type = safe(getattr(invoked, 'invoked_by_type', '') or '') if invoked else ''
        start_dt  = getattr(r, 'run_start', None)
        end_dt    = getattr(r, 'run_end', None)
        dur_ms    = getattr(r, 'duration_in_ms', None)
        runs.append({
            "run_id":         safe(r.run_id or ''),
            "pipeline_name":  safe(r.pipeline_name or ''),
            "status":         safe(r.status or ''),
            "start":          start_dt.strftime("%Y-%m-%d %H:%M:%S") if start_dt else '',
            "end":            end_dt.strftime("%Y-%m-%d %H:%M:%S") if end_dt else '',
            "duration":       fmt_duration(dur_ms),
            "triggered_name": triggered_name,
            "triggered_type": triggered_type,
            "message":        safe(getattr(r, 'message', '') or ''),
            "url":            monitor_run_url(r.run_id, resource_group, factory_name, subscription_id),
        })

    runs.sort(key=lambda x: x["start"], reverse=True)
    print(f" {len(runs)}")
    return runs

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;--yel:#fbbf24;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 'Segoe UI',system-ui,sans-serif;overflow:hidden}
.layout{display:flex;height:100vh}

/* sidebar */
.sidebar{width:270px;min-width:180px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;font-size:13px;line-height:1.4}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:11px;margin-top:2px}
.sb-search{padding:7px 10px;border-bottom:1px solid var(--brd)}
.sb-search input{width:100%;padding:5px 9px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-list{overflow-y:auto;flex:1;padding-bottom:12px}
.folder-hdr{display:flex;align-items:center;gap:6px;padding:6px 10px;cursor:pointer;
  font-size:11px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.4px;
  border-bottom:1px solid var(--brd);user-select:none;position:sticky;top:0;
  background:var(--sur);z-index:2}
.folder-hdr:hover{background:var(--sur2)}
.folder-hdr .arr{font-size:9px;flex-shrink:0;transition:transform .15s;display:inline-block}
.folder-hdr.open .arr{transform:rotate(90deg)}
.folder-body{display:none;padding:2px 0}
.folder-hdr.open + .folder-body{display:block}
.pipe-row{display:flex;align-items:center;gap:5px;padding:3px 8px 3px 20px;
  font-size:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  border-radius:4px;margin:1px 4px}
.pipe-row:hover{background:var(--sur2)}
.pipe-row.active{background:var(--brd);color:var(--txt)}

/* main */
.main{flex:1;overflow-y:auto;padding:22px 26px}
h1{font-size:20px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}

/* stat cards */
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:13px 16px;min-width:100px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:24px;font-weight:700;line-height:1}
.sc-l{font-size:11px;color:var(--mut);margin-top:3px}

/* tabs */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);margin-bottom:16px;flex-wrap:wrap}
.tab{padding:7px 14px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;
  color:var(--mut);border:1px solid transparent;border-bottom:none;margin-bottom:-2px;user-select:none}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);border-bottom-color:var(--sur);color:var(--txt)}
.panel{display:none}.panel.active{display:block}

/* search bar */
.srch{margin-bottom:12px}
.srch input{padding:7px 11px;background:var(--sur);border:1px solid var(--brd);
  border-radius:6px;color:var(--txt);font-size:12px;width:100%;max-width:420px;outline:none}
.srch input:focus{border-color:var(--acc)}

/* data tables */
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:7px 11px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
td{padding:5px 11px;border-bottom:1px solid var(--brd);vertical-align:top}
tr:hover td{background:var(--sur)}
tr.clickable{cursor:pointer}
.hidden{display:none!important}

/* activity type badges */
.abadge{font-size:10px;padding:1px 6px;border-radius:3px;white-space:nowrap;font-weight:700;display:inline-block}
.act-copy{background:#1e3a5f;color:#60a5fa}
.act-pipe{background:#2d1e5f;color:#c084fc}
.act-sproc{background:#3a2a1e;color:#fb923c}
.act-lookup{background:#1a3a3a;color:#22d3ee}
.act-dataflow{background:#1a3a2a;color:#4ade80}
.act-control{background:#3a3a1e;color:#fbbf24}
.act-databricks{background:#2a1e1e;color:#f97316}
.act-delete{background:#3a1e1e;color:#f87171}
.act-external{background:#3a1e3a;color:#e879f9}
.act-var{background:#252836;color:#94a3b8}
.act-default{background:#252836;color:#94a3b8}

/* trigger state */
.state-started{color:var(--grn);font-weight:700}
.state-stopped{color:var(--red);font-weight:700}
.state-disabled{color:var(--mut);font-weight:700}

/* overview folder cards */
.folder-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:9px;margin-bottom:18px}
.folder-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:11px 13px;cursor:pointer}
.folder-card:hover{border-color:var(--acc)}
.folder-card h3{font-size:12px;color:var(--acc);margin-bottom:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.folder-card .ct{font-size:11px;color:var(--mut)}
.folder-card .ct strong{color:var(--txt)}

/* tag chips (pipeline list, datasets, etc.) */
.chip{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;white-space:nowrap;margin:1px}
.chip-ls{background:#1e3a5f;color:#60a5fa}
.chip-ds{background:#1a3a2a;color:#4ade80}
.chip-trig{background:#3a1e5f;color:#c084fc}
.chip-pipe{background:#2d1e5f;color:#c084fc}
.chips{display:flex;flex-wrap:wrap;gap:2px}

/* ── lineage tab ── */
.ln-pipe-card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;margin-bottom:12px;overflow:hidden}
.ln-pipe-hdr{display:flex;align-items:center;gap:10px;padding:11px 16px;background:var(--sur2);
  border-bottom:1px solid var(--brd);cursor:pointer;user-select:none}
.ln-pipe-hdr:hover{background:var(--brd)}
.ln-pipe-title{font-size:13px;font-weight:700;flex:1}
.ln-pipe-folder{font-size:10px;color:var(--mut);margin-top:1px}
.ln-pipe-body{padding:10px 16px;display:none}
.ln-pipe-hdr.open + .ln-pipe-body{display:block}
.ln-triggers{font-size:11px;color:var(--mut);margin-bottom:10px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.ln-trig-chip{background:var(--sur2);border:1px solid var(--brd);padding:2px 7px;border-radius:3px;
  color:var(--cyn);font-size:10px}
.ln-trig-sched{color:var(--mut);font-size:10px}
.ln-no-trig{color:var(--mut);font-style:italic;font-size:11px}

/* activity flow within lineage */
.ln-flow{display:flex;flex-direction:column;gap:6px}
.ln-act{display:flex;gap:10px;align-items:flex-start;background:var(--sur2);
  border:1px solid var(--brd);border-radius:6px;padding:8px 10px}
.ln-act-left{display:flex;flex-direction:column;align-items:center;gap:4px;min-width:90px}
.ln-act-num{font-size:9px;color:var(--mut);font-weight:700}
.ln-act-right{flex:1;min-width:0}
.ln-act-name{font-size:12px;font-weight:600;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ln-act-desc{font-size:10px;color:var(--mut);margin-bottom:4px}
.ln-deps{font-size:10px;color:var(--mut);margin-bottom:4px}
.ln-refs{display:flex;flex-wrap:wrap;gap:5px;font-size:11px}
.ln-arrow{color:var(--mut);align-self:center;font-size:12px}
.ln-ds{display:inline-flex;flex-direction:column;gap:1px;background:var(--bg);
  border:1px solid var(--brd);border-radius:4px;padding:3px 7px;font-size:10px}
.ln-ds-name{color:var(--txt);font-weight:600}
.ln-ds-ls{color:var(--org)}
.ln-ref-item{background:var(--bg);border:1px solid var(--brd);border-radius:4px;
  padding:3px 7px;font-size:10px}
.ln-ref-pipe{color:var(--pur)}
.ln-ref-df{color:var(--grn)}
.ln-ref-ls{color:var(--org)}
.ln-connector{text-align:center;font-size:16px;color:var(--brd);line-height:1}

/* modal */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;
  display:flex;align-items:center;justify-content:center;padding:20px}
.modal-box{background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  width:960px;max-width:calc(100vw - 40px);max-height:88vh;
  display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(0,0,0,.7)}
.modal-hdr{display:flex;align-items:center;gap:10px;padding:13px 16px;
  border-bottom:1px solid var(--brd);flex-shrink:0}
.modal-hdr-title{flex:1;font-size:14px;font-weight:700}
.modal-hdr-sub{font-size:11px;color:var(--mut);font-weight:400;margin-top:2px}
.modal-close{background:none;border:none;color:var(--mut);font-size:20px;cursor:pointer;
  padding:1px 7px;border-radius:4px;line-height:1;flex-shrink:0}
.modal-close:hover{background:var(--sur2);color:var(--txt)}
.modal-tabs{display:flex;gap:3px;padding:8px 14px;border-bottom:1px solid var(--brd);flex-shrink:0}
.mtab{padding:5px 13px;background:none;border:1px solid transparent;border-radius:5px;
  color:var(--mut);font-size:12px;font-weight:600;cursor:pointer;user-select:none}
.mtab:hover{color:var(--txt)}
.mtab.active{background:var(--sur2);border-color:var(--brd);color:var(--txt)}
.modal-body{overflow:auto;flex:1;padding:14px 16px}
.modal-empty{color:var(--mut);font-size:13px;padding:12px 0}

/* modal activity table */
.act-tbl{width:100%;border-collapse:collapse;font-size:12px}
.act-tbl th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:1}
.act-tbl td{padding:5px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
.act-tbl tr:hover td{background:var(--sur2)}

/* params table */
.param-tbl{width:100%;border-collapse:collapse;font-size:12px}
.param-tbl th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;border-bottom:2px solid var(--brd)}
.param-tbl td{padding:5px 10px;border-bottom:1px solid var(--brd)}
.param-tbl code{font-size:11px;color:var(--cyn);font-family:monospace}

/* monitor status chips */
.status-chip{display:inline-block;font-size:10px;padding:2px 8px;border-radius:3px;font-weight:700;white-space:nowrap}
.st-succeeded{background:#1a3a2a;color:#4ade80}
.st-failed{background:#3a1e1e;color:#f87171}
.st-running{background:#1e3a5f;color:#60a5fa}
.st-cancelled{background:#3a3a1e;color:#fbbf24}
.st-canceling{background:#3a3a1e;color:#fbbf24}
.st-queued{background:#252836;color:#94a3b8}
.st-other{background:#252836;color:#94a3b8}

/* monitor filter bar */
.mon-filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.mon-btn{padding:4px 12px;background:var(--sur);border:1px solid var(--brd);border-radius:5px;
  color:var(--mut);font-size:11px;font-weight:600;cursor:pointer;user-select:none}
.mon-btn:hover{border-color:var(--acc);color:var(--txt)}
.mon-btn.active{background:var(--sur2);border-color:var(--acc);color:var(--txt)}
.mon-pipeline-link{color:var(--acc);text-decoration:none;font-weight:600}
.mon-pipeline-link:hover{text-decoration:underline}
.mon-error{font-size:10px;color:var(--red);margin-top:4px;line-height:1.45;
  word-break:break-word;max-width:480px;opacity:.9}
.mon-error-toggle{font-size:10px;color:var(--acc);cursor:pointer;margin-top:2px;
  text-decoration:underline;user-select:none}

/* ── hierarchy tab ── */
.htree-section{margin-bottom:28px}
.htree-section-title{font-size:12px;font-weight:700;color:var(--acc);text-transform:uppercase;
  letter-spacing:.4px;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--brd)}
.htree-root-card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  margin-bottom:8px;overflow:hidden}
.htree-card-warn{border-color:#fb923c55}
.htree-root-hdr{display:flex;align-items:center;gap:8px;padding:10px 14px;
  background:var(--sur2);cursor:pointer;user-select:none;flex-wrap:wrap}
.htree-root-hdr:hover{background:var(--brd)}
.htree-root-hdr .arr{font-size:9px;flex-shrink:0;transition:transform .15s}
.htree-root-hdr.open .arr{transform:rotate(90deg)}
.htree-root-body{display:none;padding:10px 14px}
.htree-root-hdr.open + .htree-root-body{display:block}
.htree-pipe-name{font-size:12px;font-weight:700;color:var(--acc);cursor:pointer;
  text-decoration:none}
.htree-pipe-name:hover{text-decoration:underline}
.htree-folder-tag{font-size:10px;color:var(--mut)}
.htree-run-ok{font-size:10px;color:var(--grn)}
.htree-run-fail{font-size:10px;color:var(--red)}
.htree-run-none{font-size:10px;color:var(--mut)}
.htree-children{border-left:2px solid var(--brd);margin-left:14px;padding-left:14px;margin-top:2px}
.htree-child-row{display:flex;align-items:baseline;gap:6px;padding:3px 0;flex-wrap:wrap}
.htree-child-connector{color:var(--brd);font-size:11px;flex-shrink:0;font-family:monospace}
.htree-standalone-card{background:var(--sur);border:1px solid var(--brd);border-radius:6px;
  padding:8px 12px;margin-bottom:5px;display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}
.htree-standalone-name{font-size:12px;font-weight:700;color:var(--acc);cursor:pointer}
.htree-standalone-name:hover{text-decoration:underline}
.htree-standalone-acts{font-size:10px;color:var(--mut);margin-top:3px}
.htree-class-badge{font-size:10px;padding:2px 8px;border-radius:3px;font-weight:700;
  white-space:nowrap;flex-shrink:0}
.htree-cls-missing{background:#3a2a1e;color:#fb923c}
.htree-cls-manual{background:#1e3a5f;color:#60a5fa}
.htree-cls-test{background:#252836;color:#94a3b8}
.htree-cls-training{background:#1a3a2a;color:#4ade80}
.htree-cls-archived{background:#3a1e1e;color:#f87171}
.htree-cls-unknown{background:#3a1e3a;color:#e879f9}
"""

# ── JavaScript ─────────────────────────────────────────────────────────────────

JS = r"""
// ── embedded data ─────────────────────────────────────────────────────────────
const PIPELINE_DATA  = __PIPELINE_DATA__;
const TRIGGER_DATA   = __TRIGGER_DATA__;
const DATASET_DATA   = __DATASET_DATA__;
const LS_DATA        = __LS_DATA__;
const DF_DATA        = __DF_DATA__;
const IR_DATA        = __IR_DATA__;

// derived maps
const DS_MAP  = {};  // dataset name → linked service name
for (const d of DATASET_DATA) DS_MAP[d.name] = d.linked_service;

const PIPE_TRIGGERS = {};  // pipeline name → [{name, schedule, state}]
for (const t of TRIGGER_DATA) {
  for (const p of t.pipelines) {
    if (!PIPE_TRIGGERS[p]) PIPE_TRIGGERS[p] = [];
    PIPE_TRIGGERS[p].push({name: t.name, schedule: t.schedule, state: t.state});
  }
}

// ── helpers ───────────────────────────────────────────────────────────────────
function escH(s){
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function actClass(type){
  const t = (type||'').toLowerCase();
  if (t === 'copy') return 'act-copy';
  if (t === 'executepipeline') return 'act-pipe';
  if (t.includes('storedprocedure') || t.includes('sqlserver') || t === 'sproc') return 'act-sproc';
  if (t === 'lookup') return 'act-lookup';
  if (t.includes('dataflow') || t === 'executedataflow') return 'act-dataflow';
  if (['foreach','ifcondition','switch','until','filter'].includes(t)) return 'act-control';
  if (t.includes('databricks')) return 'act-databricks';
  if (t === 'delete') return 'act-delete';
  if (t.includes('web') || t.includes('function') || t.includes('azureml') || t.includes('ml')) return 'act-external';
  if (['setvariable','appendvariable','wait','getmetadata'].includes(t)) return 'act-var';
  return 'act-default';
}

function actBadge(type){
  return '<span class="abadge ' + actClass(type) + '">' + escH(type||'Unknown') + '</span>';
}

function stateClass(s){
  const t = (s||'').toLowerCase();
  if (t === 'started') return 'state-started';
  if (t === 'stopped') return 'state-stopped';
  return 'state-disabled';
}

// ── tab switching ─────────────────────────────────────────────────────────────
function showTab(id, el){
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c => c.classList.remove('active-card'));
  document.getElementById('p-' + id).classList.add('active');
  const tab = document.getElementById('tab-' + id);
  if(tab) tab.classList.add('active');
  const card = document.getElementById('card-' + id);
  if(card) card.classList.add('active-card');
}

// ── generic table search ──────────────────────────────────────────────────────
function ft(tid, q){
  q = q.toLowerCase().trim();
  document.querySelectorAll('#' + tid + ' tbody tr').forEach(tr => {
    tr.classList.toggle('hidden', !!q && !tr.textContent.toLowerCase().includes(q));
  });
}

// ── monitor tab filtering ─────────────────────────────────────────────────────
let _monStatus = 'all';
function filterMonitorStatus(status, btn){
  _monStatus = status;
  document.querySelectorAll('.mon-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  applyMonitorFilter();
}
function filterMonitorSearch(q){
  applyMonitorFilter(q);
}
function applyMonitorFilter(q){
  q = (q !== undefined ? q : document.getElementById('mon-search').value).toLowerCase().trim();
  document.querySelectorAll('#mon-tbl tbody tr').forEach(tr => {
    const statusMatch = _monStatus === 'all' || (tr.dataset.status||'').toLowerCase() === _monStatus;
    const textMatch   = !q || tr.textContent.toLowerCase().includes(q);
    tr.classList.toggle('hidden', !statusMatch || !textMatch);
  });
}

function toggleMonErr(id){
  const s=document.getElementById(id+'-short');
  const f=document.getElementById(id+'-full');
  if(!s||!f) return;
  const showingFull=f.style.display!=='none';
  s.style.display=showingFull?'':'none';
  f.style.display=showingFull?'none':'';
}

// ── sidebar search ────────────────────────────────────────────────────────────
function filterSB(q){
  q = q.toLowerCase().trim();
  document.querySelectorAll('.folder-item').forEach(item => {
    const rows = item.querySelectorAll('.pipe-row');
    let any = false;
    rows.forEach(r => {
      const show = !q || (r.dataset.n||'').includes(q);
      r.classList.toggle('hidden', !show);
      if (show) any = true;
    });
    const hdr = item.querySelector('.folder-hdr');
    const hdrMatch = !q || (hdr&&hdr.textContent.toLowerCase().includes(q));
    item.classList.toggle('hidden', !!q && !any && !hdrMatch);
    if (q && (any || hdrMatch)) hdr && hdr.classList.add('open');
  });
}

// ── lineage expand/collapse ───────────────────────────────────────────────────
function toggleLn(hdr){
  hdr.classList.toggle('open');
}

// ── hierarchy expand/collapse + search ───────────────────────────────────────
function toggleHTree(hdr){
  hdr.classList.toggle('open');
}

function filterHierarchy(q){
  q = q.toLowerCase().trim();
  // Cards (triggered + orchestrator)
  document.querySelectorAll('.htree-root-card').forEach(card => {
    const name = card.dataset.htreeName || '';
    const text = card.textContent.toLowerCase();
    const show = !q || name.includes(q) || text.includes(q);
    card.classList.toggle('hidden', !show);
    const hdr = card.querySelector('.htree-root-hdr');
    if (show && q && hdr) hdr.classList.add('open');
    if (!q && hdr) hdr.classList.remove('open');
  });
  // Standalone cards
  document.querySelectorAll('.htree-standalone-card').forEach(card => {
    const name = card.dataset.htreeName || '';
    const text = card.textContent.toLowerCase();
    card.classList.toggle('hidden', !!q && !name.includes(q) && !text.includes(q));
  });
  // Hide/show section headers when all their cards are hidden
  document.querySelectorAll('.htree-section').forEach(sec => {
    const cards = sec.querySelectorAll('.htree-root-card, .htree-standalone-card');
    const anyVisible = Array.from(cards).some(c => !c.classList.contains('hidden'));
    sec.style.display = (q && cards.length && !anyVisible) ? 'none' : '';
  });
}

function filterLn(q){
  q = q.toLowerCase().trim();
  document.querySelectorAll('.ln-pipe-card').forEach(card => {
    const txt = card.querySelector('.ln-pipe-title').textContent.toLowerCase();
    const folder = (card.querySelector('.ln-pipe-folder')||{}).textContent || '';
    const show = !q || txt.includes(q) || folder.toLowerCase().includes(q);
    card.classList.toggle('hidden', !show);
    // auto-expand on search hit
    const hdr = card.querySelector('.ln-pipe-hdr');
    if (show && q) hdr && hdr.classList.add('open');
    if (!q) hdr && hdr.classList.remove('open');
  });
}

// ── pipeline modal ────────────────────────────────────────────────────────────
let _activePipeRow = null;

function openPipeline(name, el){
  const pipe = PIPELINE_DATA[name];
  if (!pipe) return;

  document.getElementById('modal-pipe-name').textContent = name;
  const folder = pipe.folder ? '📁 ' + pipe.folder : '';
  document.getElementById('modal-pipe-folder').textContent = folder;
  const desc = pipe.description || '';
  document.getElementById('modal-pipe-desc').textContent = desc;

  // ── activities tab ──
  const acts = pipe.activities || [];
  let actHtml = '';
  if (acts.length) {
    const rows = acts.map((a,i) => {
      const deps = (a.depends_on||[]).map(d=>escH(d.activity)).join(', ') || '<span style="color:var(--mut)">—</span>';
      const inputs  = (a.inputs||[]).map(ds => {
        const ls = DS_MAP[ds] || '';
        return '<span class="chip chip-ds" title="' + escH(ls) + '">' + escH(ds) + '</span>';
      }).join('') || '<span style="color:var(--mut)">—</span>';
      const outputs = (a.outputs||[]).map(ds => {
        const ls = DS_MAP[ds] || '';
        return '<span class="chip chip-ds" title="' + escH(ls) + '">' + escH(ds) + '</span>';
      }).join('') || '<span style="color:var(--mut)">—</span>';
      let refs = '';
      if (a.linked_service) refs += '<span class="chip chip-ls">' + escH(a.linked_service) + '</span>';
      if (a.sub_pipeline)   refs += '<span class="chip chip-pipe">▶ ' + escH(a.sub_pipeline) + '</span>';
      if (a.data_flow)      refs += '<span class="chip" style="background:#1a3a2a;color:#4ade80">~' + escH(a.data_flow) + '</span>';
      if (!refs) refs = '<span style="color:var(--mut)">—</span>';
      return '<tr>'
        + '<td style="color:var(--mut);width:28px">' + (i+1) + '</td>'
        + '<td><strong>' + escH(a.name) + '</strong>'
        + (a.description ? '<br><span style="font-size:10px;color:var(--mut)">' + escH(a.description) + '</span>' : '')
        + '</td>'
        + '<td>' + actBadge(a.type) + '</td>'
        + '<td>' + deps + '</td>'
        + '<td><div class="chips">' + inputs + '</div></td>'
        + '<td><div class="chips">' + outputs + '</div></td>'
        + '<td><div class="chips">' + refs + '</div></td>'
        + '</tr>';
    }).join('');
    actHtml = '<div style="overflow-x:auto"><table class="act-tbl">'
      + '<thead><tr><th>#</th><th>Activity</th><th>Type</th><th>Depends On</th>'
      + '<th>Input Datasets</th><th>Output Datasets</th><th>Linked Service / Pipeline / DataFlow</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table></div>';
  } else {
    actHtml = '<p class="modal-empty">No activities found.</p>';
  }
  document.getElementById('modal-acts-body').innerHTML = actHtml;

  // ── triggers tab ──
  const trigs = PIPE_TRIGGERS[name] || [];
  let trigHtml = '';
  if (trigs.length) {
    trigHtml = trigs.map(t => {
      const sc = t.state ? '<span class="' + stateClass(t.state) + '">' + escH(t.state) + '</span>' : '';
      const sched = t.schedule ? '<span style="color:var(--mut);font-size:11px"> · ' + escH(t.schedule) + '</span>' : '';
      return '<div style="padding:7px 0;border-bottom:1px solid var(--brd)">'
           + '<span class="chip chip-trig">' + escH(t.name) + '</span> ' + sc + sched + '</div>';
    }).join('');
  } else {
    trigHtml = '<p class="modal-empty">No triggers fire this pipeline (may be called by another pipeline or run manually).</p>';
  }
  document.getElementById('modal-trigs-body').innerHTML = trigHtml;

  // ── parameters tab ──
  const params = pipe.parameters || [];
  let paramHtml = '';
  if (params.length) {
    const rows = params.map(p =>
      '<tr><td><strong>' + escH(p.name) + '</strong></td>'
      + '<td><code>' + escH(p.type) + '</code></td>'
      + '<td>' + escH(p.default) + '</td></tr>'
    ).join('');
    paramHtml = '<table class="param-tbl"><thead><tr><th>Name</th><th>Type</th><th>Default</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table>';
  } else {
    paramHtml = '<p class="modal-empty">No parameters defined.</p>';
  }
  document.getElementById('modal-params-body').innerHTML = paramHtml;

  showModalTab('acts');
  document.getElementById('pipe-modal').style.display = 'flex';
  if (_activePipeRow) _activePipeRow.classList.remove('active');
  if (el) { el.classList.add('active'); _activePipeRow = el; }
}

function closeModal(){
  document.getElementById('pipe-modal').style.display = 'none';
  if (_activePipeRow) { _activePipeRow.classList.remove('active'); _activePipeRow = null; }
}

function showModalTab(tab){
  ['acts','trigs','params'].forEach(t => {
    document.getElementById('modal-' + t + '-body').style.display = t===tab ? '' : 'none';
  });
  document.querySelectorAll('.mtab').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('mtab-' + tab);
  if (btn) btn.classList.add('active');
}

document.getElementById('pipe-modal').addEventListener('click', e => {
  if (e.target === document.getElementById('pipe-modal')) closeModal();
});

// fold/unfold sidebar folder
document.querySelectorAll('.folder-hdr').forEach(h => {
  h.addEventListener('click', () => h.classList.toggle('open'));
});

// ── overview card navigation ───────────────────────────────────────────────────
function filterByFolder(folder) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('p-pipelines').classList.add('active');
  document.getElementById('tab-pipelines').classList.add('active');
  const inp = document.getElementById('pipe-search');
  inp.value = folder;
  ft('pipe-tbl', folder);
  inp.focus();
}

function filterByLSType(type) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('p-linkedsvc').classList.add('active');
  document.getElementById('tab-linkedsvc').classList.add('active');
  const inp = document.getElementById('ls-search');
  inp.value = type;
  ft('ls-tbl', type);
  inp.focus();
}

// ── linked service detail modal ───────────────────────────────────────────────
function openLS(name){
  const ls = LS_DATA.find(x => x.name === name);
  if (!ls) return;
  document.getElementById('ls-modal-name').textContent = name;
  document.getElementById('ls-modal-type').textContent = ls.type;

  const conn = ls.conn || {};
  let html = '';

  // Datasets that use this LS
  const dsets = DATASET_DATA.filter(d => d.linked_service === name);
  if (dsets.length) {
    html += '<div style="margin-bottom:14px">'
          + '<div style="font-size:11px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Datasets Using This Linked Service</div>'
          + '<div class="chips">'
          + dsets.map(d => '<span class="chip chip-ds">' + escH(d.name) + '</span>').join('')
          + '</div></div>';
  }

  // Connection properties table
  const entries = Object.entries(conn);
  if (entries.length) {
    html += '<div style="font-size:11px;font-weight:700;color:var(--acc);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Connection Details</div>';
    html += '<table style="width:100%;border-collapse:collapse;font-size:12px">';
    for (const [k, v] of entries) {
      const isIndented = k.startsWith('  ');
      const label = isIndented ? k.trim() : k;
      const rowStyle = isIndented ? 'background:var(--bg)' : '';
      const labelStyle = isIndented ? 'color:var(--mut);padding-left:20px' : 'font-weight:600';
      const valStyle = v.startsWith('[') ? 'color:var(--yel);font-style:italic' :
                       (k.toLowerCase().includes('url') || k.toLowerCase().includes('endpoint') || k.toLowerCase().includes('uri'))
                         ? 'color:var(--cyn);font-family:monospace;font-size:11px;word-break:break-all' :
                       'font-family:monospace;font-size:11px;word-break:break-all';
      html += '<tr style="border-bottom:1px solid var(--brd);' + rowStyle + '">'
            + '<td style="padding:5px 10px;width:200px;' + labelStyle + '">' + escH(label) + '</td>'
            + '<td style="padding:5px 10px;' + valStyle + '">' + escH(v) + '</td>'
            + '</tr>';
    }
    html += '</table>';
  } else {
    html += '<p style="color:var(--mut);font-size:12px">No connection details could be extracted for this linked service type.</p>';
  }

  // Description
  if (ls.description) {
    html += '<div style="margin-top:12px;font-size:12px;color:var(--mut)">' + escH(ls.description) + '</div>';
  }

  document.getElementById('ls-modal-body').innerHTML = html;
  document.getElementById('ls-modal').style.display = 'flex';
}

function closeLSModal(){
  document.getElementById('ls-modal').style.display = 'none';
}
document.getElementById('ls-modal').addEventListener('click', e => {
  if (e.target === document.getElementById('ls-modal')) closeLSModal();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape'){ closeModal(); closeLSModal(); } });
"""

# ── HTML builder ───────────────────────────────────────────────────────────────

def build_html(data, factory_name, generated, runs=None):
    pipelines       = data["pipelines"]
    datasets        = data["datasets"]
    linked_services = data["linked_services"]
    triggers        = data["triggers"]
    data_flows      = data["data_flows"]
    irs             = data["integration_runtimes"]
    runs            = runs or []

    # ── JSON blobs for JS ──────────────────────────────────────────────────────
    pipe_map = {p["name"]: p for p in pipelines}
    pipeline_json = json.dumps(pipe_map,      ensure_ascii=False, separators=(',',':'))
    trigger_json  = json.dumps(triggers,      ensure_ascii=False, separators=(',',':'))
    dataset_json  = json.dumps(datasets,      ensure_ascii=False, separators=(',',':'))
    ls_json       = json.dumps(linked_services, ensure_ascii=False, separators=(',',':'))
    df_json       = json.dumps(data_flows,    ensure_ascii=False, separators=(',',':'))
    ir_json       = json.dumps(irs,           ensure_ascii=False, separators=(',',':'))

    # ── sidebar ────────────────────────────────────────────────────────────────
    by_folder = {}
    for p in sorted(pipelines, key=lambda x: (x["folder"], x["name"])):
        by_folder.setdefault(p["folder"] or "(No Folder)", []).append(p)

    sb_parts = []
    for folder in sorted(by_folder):
        pipes = by_folder[folder]
        sb_parts.append(
            f'<div class="folder-item">'
            f'<div class="folder-hdr"><span class="arr">&#x25B6;</span>{esc(folder)}'
            f' <span style="color:var(--mut);font-size:10px">({len(pipes)})</span></div>'
            f'<div class="folder-body">'
        )
        for p in pipes:
            key = js_esc(p["name"])
            dn  = esc(p["name"])
            sb_parts.append(
                f'<div class="pipe-row" data-n="{esc(p["name"].lower())}" '
                f'onclick="openPipeline(\'{key}\',this)">&#x1F4CB; {dn}</div>'
            )
        sb_parts.append('</div></div>')
    sidebar = '\n'.join(sb_parts)

    # ── overview folder cards ──────────────────────────────────────────────────
    folder_cards = ''.join(
        f'<div class="folder-card" onclick="filterByFolder(\'{js_esc(folder)}\')" title="Click to filter pipelines">'
        f'<h3>&#x1F4C1; {esc(folder)}</h3>'
        f'<div class="ct"><strong>{len(pipes)}</strong> pipeline{"s" if len(pipes)!=1 else ""}</div>'
        f'</div>'
        for folder, pipes in sorted(by_folder.items())
    )

    # ── pipelines table ────────────────────────────────────────────────────────
    # Build trigger-to-pipeline reverse map
    pipe_to_trigs = {}
    for t in triggers:
        for pname in t["pipelines"]:
            pipe_to_trigs.setdefault(pname, []).append(t["name"])

    pipe_rows = []
    for p in sorted(pipelines, key=lambda x: (x["folder"], x["name"])):
        trig_chips = ''.join(
            f'<span class="chip chip-trig">{esc(tn)}</span>'
            for tn in pipe_to_trigs.get(p["name"], [])
        ) or '<span style="color:var(--mut)">—</span>'
        pipe_rows.append(
            f'<tr class="clickable" onclick="openPipeline(\'{js_esc(p["name"])}\',null)">'
            f'<td>{esc(p["folder"] or "(No Folder)")}</td>'
            f'<td><strong>{esc(p["name"])}</strong>'
            f'{"<br><span style=\\'font-size:10px;color:var(--mut)\\'>" + esc(p["description"]) + "</span>" if p["description"] else ""}'
            f'</td>'
            f'<td style="text-align:center">{len(p["activities"])}</td>'
            f'<td><div class="chips">{trig_chips}</div></td>'
            f'</tr>'
        )
    pipe_rows_html = '\n'.join(pipe_rows)

    # ── datasets table ─────────────────────────────────────────────────────────
    ds_rows = '\n'.join(
        f'<tr><td>{esc(d["name"])}</td>'
        f'<td><span class="chip chip-ds">{esc(d["type"])}</span></td>'
        f'<td><span class="chip chip-ls">{esc(d["linked_service"])}</span></td>'
        f'<td>{esc(d["description"])}</td></tr>'
        for d in sorted(datasets, key=lambda x: x["name"])
    )

    # ── linked services table ──────────────────────────────────────────────────
    ls_rows = '\n'.join(
        f'<tr class="clickable" onclick="openLS(\'{js_esc(ls["name"])}\')">'
        f'<td><strong>{esc(ls["name"])}</strong></td>'
        f'<td><span class="abadge act-default">{esc(ls["type"])}</span></td>'
        f'<td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{esc(ls["server"])}">{esc(ls["server"]) or "<span style=\\'color:var(--mut)\\'>—</span>"}</td>'
        f'<td>{esc(ls["database"]) or "<span style=\\'color:var(--mut)\\'>—</span>"}</td>'
        f'<td>{esc(ls["auth"]) or "<span style=\\'color:var(--mut)\\'>—</span>"}</td>'
        f'<td>{esc(ls["integration_runtime"]) or "<span style=\\'color:var(--mut)\\'>Azure IR</span>"}</td>'
        f'<td>{esc(ls["description"])}</td></tr>'
        for ls in sorted(linked_services, key=lambda x: x["name"])
    )

    # ── triggers table ─────────────────────────────────────────────────────────
    trig_rows = []
    for t in sorted(triggers, key=lambda x: x["name"]):
        state_cls = ("state-started" if t["state"].lower()=="started"
                     else "state-stopped" if t["state"].lower()=="stopped"
                     else "state-disabled")
        pipe_chips = ''.join(f'<span class="chip chip-pipe">{esc(p)}</span>' for p in t["pipelines"])
        trig_rows.append(
            f'<tr><td>{esc(t["name"])}</td>'
            f'<td><span class="abadge act-default">{esc(t["type"])}</span></td>'
            f'<td><span class="{state_cls}">{esc(t["state"])}</span></td>'
            f'<td style="color:var(--cyn)">{esc(t["schedule"])}</td>'
            f'<td><div class="chips">{pipe_chips}</div></td>'
            f'<td>{esc(t["description"])}</td></tr>'
        )
    trig_rows_html = '\n'.join(trig_rows)

    # ── data flows table ───────────────────────────────────────────────────────
    df_rows = '\n'.join(
        f'<tr><td>{esc(df["name"])}</td>'
        f'<td><span class="abadge act-dataflow">{esc(df["type"])}</span></td>'
        f'<td><div class="chips">{"".join("<span class=\\'chip chip-ds\\'>" + esc(s) + "</span>" for s in df["sources"])}</div></td>'
        f'<td><div class="chips">{"".join("<span class=\\'chip chip-ds\\'>" + esc(s) + "</span>" for s in df["sinks"])}</div></td>'
        f'<td>{esc(df["description"])}</td></tr>'
        for df in sorted(data_flows, key=lambda x: x["name"])
    )

    # ── integration runtimes table ─────────────────────────────────────────────
    ir_rows = '\n'.join(
        f'<tr><td>{esc(ir["name"])}</td>'
        f'<td><span class="abadge act-default">{esc(ir["type"])}</span></td>'
        f'<td>{esc(ir["description"])}</td></tr>'
        for ir in sorted(irs, key=lambda x: x["name"])
    ) or '<tr><td colspan="3" style="color:var(--mut);padding:12px">No integration runtimes found.</td></tr>'

    # ── monitor runs table ─────────────────────────────────────────────────────
    def status_chip(s):
        cls = {
            "Succeeded": "st-succeeded", "Failed": "st-failed",
            "Running": "st-running", "Cancelled": "st-cancelled",
            "Canceling": "st-canceling", "Queued": "st-queued",
        }.get(s, "st-other")
        return f'<span class="status-chip {cls}">{esc(s)}</span>'

    run_counts = {}
    for r in runs:
        run_counts[r["status"]] = run_counts.get(r["status"], 0) + 1

    mon_rows = []
    for idx, r in enumerate(runs):
        trigger_display = esc(r["triggered_name"])
        if r["triggered_type"] and r["triggered_type"] not in ("Manual",):
            trigger_display += f'<br><span style="font-size:10px;color:var(--mut)">{esc(r["triggered_type"])}</span>'

        # Error detail inline for Failed / Cancelled runs
        error_html = ""
        msg = r.get("message", "").strip()
        if msg and r["status"] in ("Failed", "Cancelled", "Canceling"):
            short = msg[:300]
            if len(msg) > 300:
                full_id = f"err-{idx}"
                error_html = (
                    f'<div class="mon-error" id="{full_id}-short">{esc(short)}'
                    f'… <span class="mon-error-toggle" '
                    f'onclick="toggleMonErr(\'{full_id}\')">show more</span></div>'
                    f'<div class="mon-error" id="{full_id}-full" style="display:none">{esc(msg)}'
                    f' <span class="mon-error-toggle" '
                    f'onclick="toggleMonErr(\'{full_id}\')">show less</span></div>'
                )
            else:
                error_html = f'<div class="mon-error">{esc(msg)}</div>'

        mon_rows.append(
            f'<tr data-status="{esc(r["status"].lower())}">'
            f'<td><a class="mon-pipeline-link" href="{esc(r["url"])}" target="_blank">{esc(r["pipeline_name"])}</a>'
            f'{error_html}</td>'
            f'<td>{status_chip(r["status"])}</td>'
            f'<td style="white-space:nowrap;color:var(--mut);font-size:11px">{esc(r["start"])}</td>'
            f'<td style="white-space:nowrap;color:var(--mut);font-size:11px">{esc(r["end"])}</td>'
            f'<td style="white-space:nowrap">{esc(r["duration"])}</td>'
            f'<td style="font-size:11px">{trigger_display}</td>'
            f'</tr>'
        )
    mon_rows_html = '\n'.join(mon_rows) or \
        '<tr><td colspan="6" style="color:var(--mut);padding:12px">No pipeline runs found for the past 7 days.</td></tr>'

    # Status filter counts for the monitor bar
    status_filter_buttons = []
    all_statuses = ["Succeeded", "Failed", "Running", "Cancelled", "Queued"]
    for s in all_statuses:
        cnt = run_counts.get(s, 0)
        if cnt:
            status_filter_buttons.append(
                f'<button class="mon-btn" onclick="filterMonitorStatus(\'{s.lower()}\',this)">'
                f'{esc(s)} ({cnt})</button>'
            )

    # ── lineage cards ──────────────────────────────────────────────────────────
    ds_to_ls = {d["name"]: d["linked_service"] for d in datasets}

    def ln_act_html(acts):
        parts = []
        for i, a in enumerate(acts):
            dep_txt = ""
            if a["depends_on"]:
                dep_names = ", ".join(d["activity"] for d in a["depends_on"])
                dep_txt = f'<div class="ln-deps">↳ depends on: {esc(dep_names)}</div>'

            refs = []
            for ds in a["inputs"]:
                ls = esc(ds_to_ls.get(ds, ""))
                refs.append(
                    f'<span class="ln-ds"><span class="ln-ds-name">📥 {esc(ds)}</span>'
                    f'{("<span class=\\'ln-ds-ls\\'>via " + ls + "</span>") if ls else ""}'
                    f'</span>'
                )
            for ds in a["outputs"]:
                ls = esc(ds_to_ls.get(ds, ""))
                refs.append(
                    f'<span class="ln-ds"><span class="ln-ds-name">📤 {esc(ds)}</span>'
                    f'{("<span class=\\'ln-ds-ls\\'>via " + ls + "</span>") if ls else ""}'
                    f'</span>'
                )
            if a["linked_service"] and not a["inputs"] and not a["outputs"]:
                refs.append(f'<span class="ln-ref-item ln-ref-ls">🔗 {esc(a["linked_service"])}</span>')
            if a["sub_pipeline"]:
                refs.append(f'<span class="ln-ref-item ln-ref-pipe">▶ calls: {esc(a["sub_pipeline"])}</span>')
            if a["data_flow"]:
                refs.append(f'<span class="ln-ref-item ln-ref-df">~ data flow: {esc(a["data_flow"])}</span>')
            if not refs and not dep_txt:
                refs.append(f'<span style="color:var(--mut);font-size:10px">No dataset references</span>')

            refs_html = '<div class="ln-refs">' + ''.join(refs) + '</div>'
            # activity type badge
            act_type_cls = (
                "act-copy" if a["type"].lower()=="copy" else
                "act-pipe" if a["type"].lower()=="executepipeline" else
                "act-sproc" if "storedprocedure" in a["type"].lower() else
                "act-lookup" if a["type"].lower()=="lookup" else
                "act-dataflow" if "dataflow" in a["type"].lower() else
                "act-control" if a["type"].lower() in ("foreach","ifcondition","switch","until","filter") else
                "act-databricks" if "databricks" in a["type"].lower() else
                "act-delete" if a["type"].lower()=="delete" else
                "act-external" if any(x in a["type"].lower() for x in ("web","function","ml")) else
                "act-var" if a["type"].lower() in ("setvariable","appendvariable","wait","getmetadata") else
                "act-default"
            )
            desc_html = f'<div class="ln-act-desc">{esc(a["description"])}</div>' if a["description"] else ""
            parts.append(
                f'<div class="ln-act">'
                f'<div class="ln-act-left"><span class="ln-act-num">{i+1}</span>'
                f'<span class="abadge {act_type_cls}">{esc(a["type"])}</span></div>'
                f'<div class="ln-act-right">'
                f'<div class="ln-act-name">{esc(a["name"])}</div>'
                f'{desc_html}{dep_txt}{refs_html}</div></div>'
            )
        return '<div class="ln-flow">' + ''.join(parts) + '</div>' if parts else \
               '<p style="color:var(--mut);font-size:12px;padding:8px 0">No activities in this pipeline.</p>'

    ln_cards = []
    for p in sorted(pipelines, key=lambda x: (x["folder"], x["name"])):
        ptrigs = pipe_to_trigs.get(p["name"], [])
        trig_map = {t["name"]: t for t in triggers}
        if ptrigs:
            trig_chips_html = '<span style="color:var(--mut);font-size:11px">Triggered by:</span> ' + ''.join(
                f'<span class="ln-trig-chip">{esc(tn)}</span>'
                f'{"<span class=\\'ln-trig-sched\\'>" + esc(trig_map[tn]["schedule"]) + "</span>" if trig_map.get(tn, {}).get("schedule") else ""}'
                for tn in ptrigs
            )
        else:
            trig_chips_html = '<span class="ln-no-trig">Not triggered (called by another pipeline or manual)</span>'

        folder_html = f'<div class="ln-pipe-folder">📁 {esc(p["folder"])}</div>' if p["folder"] else ""
        desc_html   = f'<div style="font-size:11px;color:var(--mut);margin-top:4px">{esc(p["description"])}</div>' if p["description"] else ""
        acts_html   = ln_act_html(p["activities"])

        ln_cards.append(
            f'<div class="ln-pipe-card" id="ln-{esc(p["name"])}">'
            f'<div class="ln-pipe-hdr" onclick="toggleLn(this)">'
            f'<div><div class="ln-pipe-title">&#x1F4CB; {esc(p["name"])}</div>{folder_html}</div></div>'
            f'<div class="ln-pipe-body">'
            f'{desc_html}'
            f'<div class="ln-triggers">{trig_chips_html}</div>'
            f'{acts_html}</div></div>'
        )
    lineage_html = '\n'.join(ln_cards) or '<p style="color:var(--mut)">No pipelines found.</p>'

    # ── hierarchy analysis ─────────────────────────────────────────────────────
    children_map: dict[str, list[str]] = {}
    for p in pipelines:
        seen: set[str] = set()
        kids: list[str] = []
        for act in p["activities"]:
            child = act.get("sub_pipeline", "")
            if child and child not in seen:
                kids.append(child)
                seen.add(child)
        if kids:
            children_map[p["name"]] = kids

    all_children_set = {c for cs in children_map.values() for c in cs}

    last_run_map: dict[str, dict] = {}
    for r in runs:
        pn = r["pipeline_name"]
        if pn not in last_run_map:
            last_run_map[pn] = r

    hierarchy_html = _build_hierarchy_html(
        pipelines, triggers, pipe_map, pipe_to_trigs,
        children_map, all_children_set, last_run_map,
    )

    # ── activity count across all pipelines ────────────────────────────────────
    total_acts = sum(len(p["activities"]) for p in pipelines)

    # ── JS with data ───────────────────────────────────────────────────────────
    js_with_data = (JS
        .replace('__PIPELINE_DATA__', pipeline_json)
        .replace('__TRIGGER_DATA__',  trigger_json)
        .replace('__DATASET_DATA__',  dataset_json)
        .replace('__LS_DATA__',       ls_json)
        .replace('__DF_DATA__',       df_json)
        .replace('__IR_DATA__',       ir_json))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>ADF Metadata — {esc(factory_name)}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">&#x1F3ED; {esc(factory_name)}<small>Azure Data Factory</small></div>
  <div class="sb-search"><input placeholder="Filter pipelines…" oninput="filterSB(this.value)"/></div>
  <div class="sb-list">{sidebar}</div>
</div>

<!-- MAIN -->
<div class="main">
  <h1>ADF Metadata Report</h1>
  <p class="sub">Factory: <strong>{esc(factory_name)}</strong> &nbsp;|&nbsp; Generated: {esc(generated)}</p>

  <div class="stats">
    <div class="sc" id="card-pipelines" onclick="showTab('pipelines',null)" title="Pipelines are the top-level orchestration workflows in ADF. Each pipeline contains one or more activities and defines what data to move or transform and in what order."><div class="sc-n">{len(pipelines)}</div><div class="sc-l">Pipelines</div></div>
    <div class="sc" id="card-lineage"   onclick="showTab('lineage',null)"   title="Total number of individual activity steps across all pipelines. Activities are the building blocks inside a pipeline — Copy, Notebook, Stored Procedure, Execute Pipeline, etc."><div class="sc-n">{total_acts}</div><div class="sc-l">Activities</div></div>
    <div class="sc" id="card-datasets"  onclick="showTab('datasets',null)"  title="Named pointers to data sources and destinations (tables, files, containers). Datasets are referenced by Copy and Lookup activities to define what to read from or write to."><div class="sc-n">{len(datasets)}</div><div class="sc-l">Datasets</div></div>
    <div class="sc" id="card-linkedsvc" onclick="showTab('linkedsvc',null)" title="Connection definitions that hold credentials and endpoint information for external systems (Azure SQL, ADLS, Databricks, Synapse, REST APIs, etc.). Datasets and activities reference linked services to authenticate."><div class="sc-n">{len(linked_services)}</div><div class="sc-l">Linked Services</div></div>
    <div class="sc" id="card-triggers"  onclick="showTab('triggers',null)"  title="Rules that automatically start pipelines on a schedule (Schedule Trigger), on a tumbling window, or in response to storage events (Event Trigger). Pipelines without a trigger must be run manually."><div class="sc-n">{len(triggers)}</div><div class="sc-l">Triggers</div></div>
    <div class="sc" id="card-dataflows" onclick="showTab('dataflows',null)" title="Data Flows are a visual, code-free Spark transformation designer built into ADF (joins, aggregations, pivots, derived columns, etc.). They run on auto-provisioned Spark clusters. This environment shows 0 because all transformations are handled in Databricks notebooks instead."><div class="sc-n">{len(data_flows)}</div><div class="sc-l">Data Flows</div></div>
    <div class="sc" id="card-irs"       onclick="showTab('irs',null)"       title="Integration Runtimes are the compute infrastructure ADF uses to execute activities. Azure IR is the default managed compute. Self-Hosted IR runs on your own VMs for on-premises or private network access."><div class="sc-n">{len(irs)}</div><div class="sc-l">Integration Runtimes</div></div>
    <div class="sc" id="card-monitor"   onclick="showTab('monitor',null)"   title="All pipeline run executions in the past 7 days across all statuses (Succeeded, Failed, Running, Cancelled). Click to open the Monitor tab and filter by status or pipeline name."><div class="sc-n" style="color:{'var(--red)' if run_counts.get('Failed',0) else 'var(--txt)'}">{len(runs)}</div><div class="sc-l">Runs (7d)</div></div>
  </div>

  <div class="tabs">
    <div class="tab active" id="tab-overview"   onclick="showTab('overview',this)">Overview</div>
    <div class="tab"        id="tab-monitor"    onclick="showTab('monitor',this)">Monitor</div>
    <div class="tab"        id="tab-pipelines"  onclick="showTab('pipelines',this)">Pipelines</div>
    <div class="tab"        id="tab-datasets"   onclick="showTab('datasets',this)">Datasets</div>
    <div class="tab"        id="tab-linkedsvc"  onclick="showTab('linkedsvc',this)">Linked Services</div>
    <div class="tab"        id="tab-triggers"   onclick="showTab('triggers',this)">Triggers</div>
    <div class="tab"        id="tab-dataflows"  onclick="showTab('dataflows',this)">Data Flows</div>
    <div class="tab"        id="tab-irs"        onclick="showTab('irs',this)">Integration Runtimes</div>
    <div class="tab"        id="tab-lineage"    onclick="showTab('lineage',this)">Lineage</div>
    <div class="tab"        id="tab-hierarchy"  onclick="showTab('hierarchy',this)">Hierarchy</div>
  </div>

  <!-- OVERVIEW -->
  <div class="panel active" id="p-overview">
    <p style="font-size:12px;color:var(--mut);margin-bottom:12px">
      Click any pipeline in the sidebar or the Pipelines tab to view its activities and data flow.
      The <strong>Lineage</strong> tab shows the full trigger → pipeline → activity → dataset chain for every pipeline.
    </p>
    <div class="folder-grid">{folder_cards}</div>
    <h2 style="font-size:14px;font-weight:700;margin-bottom:10px">Linked Services by Type</h2>
    <div class="folder-grid">
      {''.join(
        f'<div class="folder-card" onclick="filterByLSType(\'{js_esc(t)}\')" title="Click to filter linked services">'
        f'<h3>{esc(t)}</h3>'
        f'<div class="ct"><strong>{cnt}</strong> service{"s" if cnt!=1 else ""}</div></div>'
        for t, cnt in sorted(
            {ls["type"]: sum(1 for x in linked_services if x["type"]==ls["type"]) for ls in linked_services}.items()
        )
      )}
    </div>
  </div>

  <!-- MONITOR -->
  <div class="panel" id="p-monitor">
    <p style="font-size:12px;color:var(--mut);margin-bottom:10px">
      All pipeline runs for the past 7 days. Click a pipeline name to open the run in ADF Monitor.
    </p>
    <div class="mon-filters">
      <button class="mon-btn active" onclick="filterMonitorStatus('all',this)">All ({len(runs)})</button>
      {''.join(status_filter_buttons)}
      <input id="mon-search" placeholder="Search pipelines…" oninput="filterMonitorSearch(this.value)"
        style="padding:4px 11px;background:var(--sur);border:1px solid var(--brd);border-radius:6px;
               color:var(--txt);font-size:12px;outline:none;margin-left:6px;width:250px"/>
    </div>
    <div class="tw">
      <table id="mon-tbl">
        <thead><tr>
          <th>Pipeline Name</th>
          <th>Status</th>
          <th>Start</th>
          <th>End</th>
          <th>Duration</th>
          <th>Triggered By</th>
        </tr></thead>
        <tbody>{mon_rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- PIPELINES -->
  <div class="panel" id="p-pipelines">
    <div class="srch"><input id="pipe-search" placeholder="Search pipelines…" oninput="ft('pipe-tbl',this.value)"/></div>
    <div class="tw">
      <table id="pipe-tbl">
        <thead><tr><th>Folder</th><th>Pipeline</th><th>Activities</th><th>Triggers</th></tr></thead>
        <tbody>{pipe_rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- DATASETS -->
  <div class="panel" id="p-datasets">
    <div class="srch"><input placeholder="Search datasets…" oninput="ft('ds-tbl',this.value)"/></div>
    <div class="tw">
      <table id="ds-tbl">
        <thead><tr><th>Dataset</th><th>Type</th><th>Linked Service</th><th>Description</th></tr></thead>
        <tbody>{ds_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- LINKED SERVICES -->
  <div class="panel" id="p-linkedsvc">
    <p style="font-size:11px;color:var(--mut);margin-bottom:10px">Click any row to view full connection details.</p>
    <div class="srch"><input id="ls-search" placeholder="Search linked services…" oninput="ft('ls-tbl',this.value)"/></div>
    <div class="tw">
      <table id="ls-tbl">
        <thead><tr><th>Name</th><th>Type</th><th>Server / URL / Host</th><th>Database / Schema</th><th>Auth Type</th><th>Integration Runtime</th><th>Description</th></tr></thead>
        <tbody>{ls_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- TRIGGERS -->
  <div class="panel" id="p-triggers">
    <div class="srch"><input placeholder="Search triggers…" oninput="ft('trig-tbl',this.value)"/></div>
    <div class="tw">
      <table id="trig-tbl">
        <thead><tr><th>Name</th><th>Type</th><th>State</th><th>Schedule</th><th>Pipelines</th><th>Description</th></tr></thead>
        <tbody>{trig_rows_html}</tbody>
      </table>
    </div>
  </div>

  <!-- DATA FLOWS -->
  <div class="panel" id="p-dataflows">
    <div class="srch"><input placeholder="Search data flows…" oninput="ft('df-tbl',this.value)"/></div>
    <div class="tw">
      <table id="df-tbl">
        <thead><tr><th>Name</th><th>Type</th><th>Sources</th><th>Sinks</th><th>Description</th></tr></thead>
        <tbody>{df_rows if df_rows else '<tr><td colspan="5" style="color:var(--mut);padding:12px">No data flows found.</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <!-- INTEGRATION RUNTIMES -->
  <div class="panel" id="p-irs">
    <div class="tw">
      <table id="ir-tbl">
        <thead><tr><th>Name</th><th>Type</th><th>Description</th></tr></thead>
        <tbody>{ir_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- LINEAGE -->
  <div class="panel" id="p-lineage">
    <p style="font-size:12px;color:var(--mut);margin-bottom:12px">
      Full source-to-destination flow for every pipeline. Click a pipeline header to expand.
      Each activity shows what it reads (📥) and writes (📤), and which linked service it uses.
    </p>
    <div class="srch"><input placeholder="Filter by pipeline or folder…" oninput="filterLn(this.value)"/></div>
    {lineage_html}
  </div>

  <!-- HIERARCHY -->
  <div class="panel" id="p-hierarchy">
    <p style="font-size:12px;color:var(--mut);margin-bottom:12px">
      Full pipeline call graph in three sections:
      <strong>Triggered Entry Points</strong> show the complete tree of what each trigger fires and everything it calls.
      <strong>Untriggered Orchestrators</strong> are <code>_MASTER</code>-style pipelines with children but no trigger — likely missing a schedule.
      <strong>Standalone</strong> pipelines have no trigger, no parent, and no children.
      Click any pipeline name to open its activity details.
    </p>
    <div class="srch"><input placeholder="Search pipelines, triggers, or folders…" oninput="filterHierarchy(this.value)"/></div>
    {hierarchy_html}
  </div>

</div><!-- /main -->
</div><!-- /layout -->

<!-- PIPELINE MODAL -->
<div id="pipe-modal" class="modal-overlay" style="display:none">
  <div class="modal-box">
    <div class="modal-hdr">
      <div class="modal-hdr-title">
        <div>&#x1F4CB; <span id="modal-pipe-name"></span></div>
        <div class="modal-hdr-sub"><span id="modal-pipe-folder"></span></div>
        <div class="modal-hdr-sub" style="color:var(--txt)" id="modal-pipe-desc"></div>
      </div>
      <button class="modal-close" onclick="closeModal()" title="Close (Esc)">&#x2715;</button>
    </div>
    <div class="modal-tabs">
      <button id="mtab-acts"   class="mtab active" onclick="showModalTab('acts')">Activities</button>
      <button id="mtab-trigs"  class="mtab"        onclick="showModalTab('trigs')">Triggers</button>
      <button id="mtab-params" class="mtab"        onclick="showModalTab('params')">Parameters</button>
    </div>
    <div class="modal-body">
      <div id="modal-acts-body"></div>
      <div id="modal-trigs-body"  style="display:none"></div>
      <div id="modal-params-body" style="display:none"></div>
    </div>
  </div>
</div>

<!-- LINKED SERVICE DETAIL MODAL -->
<div id="ls-modal" class="modal-overlay" style="display:none">
  <div class="modal-box" style="width:700px">
    <div class="modal-hdr">
      <div class="modal-hdr-title">
        <div>🔗 <span id="ls-modal-name"></span></div>
        <div class="modal-hdr-sub" id="ls-modal-type"></div>
      </div>
      <button class="modal-close" onclick="closeLSModal()" title="Close (Esc)">&#x2715;</button>
    </div>
    <div class="modal-body">
      <div id="ls-modal-body"></div>
    </div>
  </div>
</div>

<script>{js_with_data}</script>
</body>
</html>"""

# ── entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ADF Metadata Report")
    parser.add_argument("--env", choices=["dev","prd"], required=True, help="Environment to report on")
    args = parser.parse_args()

    cfg = ENVIRONMENTS[args.env]
    factory_name   = cfg["factory_name"]
    resource_group = cfg["resource_group"]
    subscription   = cfg["subscription_id"]

    print(f"ADF Metadata Report — {factory_name}")
    print(f"Resource Group: {resource_group}")
    print(f"Authenticating via DefaultAzureCredential…")

    credential = DefaultAzureCredential()
    client     = DataFactoryManagementClient(credential, subscription)

    print("Fetching metadata…")
    data = fetch_all(client, resource_group, factory_name)
    runs = fetch_pipeline_runs(client, resource_group, factory_name, subscription)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("Building HTML…")
    html = build_html(data, factory_name, generated, runs)

    out = f"/home/thedavidporter/adf_metadata_report_{args.env}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    run_counts = {}
    for r in runs:
        run_counts[r["status"]] = run_counts.get(r["status"], 0) + 1
    run_summary = "  ".join(f"{s}={c}" for s, c in sorted(run_counts.items()))

    print(f"\nReport saved to: {out}")
    print(f"\nSummary:")
    print(f"  Pipelines           : {len(data['pipelines'])}")
    print(f"  Total Activities    : {sum(len(p['activities']) for p in data['pipelines'])}")
    print(f"  Datasets            : {len(data['datasets'])}")
    print(f"  Linked Services     : {len(data['linked_services'])}")
    print(f"  Triggers            : {len(data['triggers'])}")
    print(f"  Data Flows          : {len(data['data_flows'])}")
    print(f"  Integration Runtimes: {len(data['integration_runtimes'])}")
    print(f"  Pipeline Runs (7d)  : {len(runs)}  [{run_summary}]")

    try:
        import generate_metadata_index
        generate_metadata_index.main()
        print(f"  Index updated       : index.html")
    except Exception as exc:
        print(f"  Warning: could not update index.html: {exc}")


if __name__ == "__main__":
    main()
