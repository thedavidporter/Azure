#!/usr/bin/env python3
"""
Generates data_catalog.html — IDOH Data Marketplace catalog of available,
in-review, and requested datasets. Pulls live submissions from REDCap.
"""

import json
import re
import warnings
from datetime import datetime

import requests as _req

_CHECKBOX_RE = re.compile(r'___\d+$')

OUT_FILE            = "/home/thedavidporter/data_catalog.html"
REQUEST_FORM_FILE   = "/home/thedavidporter/data_request_form.html"
EXCLUSIONS_FILE     = "/home/thedavidporter/data_catalog_exclusions.json"
REDCAP_SURVEY       = "https://redcap.isdh.in.gov/surveys/?s=HC9ENNHTX88D88TH"
REDCAP_API_KEY      = "ED9EB1A5BA4D9E3FFCDA758B766280C6"
REDCAP_API_URL      = "https://redcap.isdh.in.gov/api/"
REQUEST_FORM_PAGE   = "data_request_form.html"


def load_exclusions():
    try:
        with open(EXCLUSIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {t.lower() for t in data.get("excluded_titles", [])}
    except FileNotFoundError:
        return set()
    except Exception as exc:
        print(f"  [WARN] Could not load exclusions file: {exc}", flush=True)
        return set()

# ── Dataset registry ──────────────────────────────────────────────────────────
# status: "verified" | "review" | "new" | "steward" | "requested"
# access: "self-serve" | "approval" | "restricted"
DATASETS = [
    {
        "name": "Chronic Disease Registry",
        "domain": "Chronic Disease",
        "icon": "📈",
        "desc": "County-level case data covering diabetes, hypertension, and Parkinson's disease. Includes incidence rates and longitudinal trends by district.",
        "division": "Data Products",
        "steward": "Data Products",
        "cadence": "Weekly",
        "status": "verified",
        "access": "approval",
        "tags": ["chronic", "county", "registry"],
        "last_reviewed": "2026-06-01",
        "source": "PRD Synapse",
    },
    {
        "name": "BRFSS County Indicators",
        "domain": "Surveillance & BRFSS",
        "icon": "📊",
        "desc": "Survey-based behavioral risk indicators by county and year. Covers tobacco use, obesity, physical inactivity, and preventive care utilization.",
        "division": "Epidemiology",
        "steward": "Deputy CDO",
        "cadence": "Annual",
        "status": "verified",
        "access": "self-serve",
        "tags": ["brfss", "survey", "behavioral", "county"],
        "last_reviewed": "2026-05-15",
        "source": "PRD Synapse",
    },
    {
        "name": "Immunization Extract",
        "domain": "Immunization & Registries",
        "icon": "💉",
        "desc": "De-identified coverage rates by district and age cohort. Derived from the Indiana Immunization Registry (CHIRP).",
        "division": "Data Products",
        "steward": "Data Products",
        "cadence": "Monthly",
        "status": "verified",
        "access": "approval",
        "tags": ["immunization", "chirp", "coverage", "registry"],
        "last_reviewed": "2026-06-10",
        "source": "PRD Synapse",
    },
    {
        "name": "Funding Intelligence Tracker",
        "domain": "Grants & Funding",
        "icon": "💰",
        "desc": "Open grant opportunities tied to PHIG sustainability goals. Tracks federal, state, and foundation funding aligned to ODA program areas.",
        "division": "Strategic Partnerships",
        "steward": "Strategic Partnerships",
        "cadence": "Weekly",
        "status": "new",
        "access": "self-serve",
        "tags": ["grants", "phig", "funding", "sustainability"],
        "last_reviewed": "2026-06-20",
        "source": "Manual",
    },
    {
        "name": "RHTP / GROW Outcomes",
        "domain": "Maternal & Child Health",
        "icon": "🎯",
        "desc": "Appendix 3 outcome measures for the RHTP and GROW programs. Specification and data definitions are currently being finalized by ODA governance.",
        "division": "Governance & Business Ops",
        "steward": "ODA Governance",
        "cadence": "Quarterly",
        "status": "review",
        "access": "restricted",
        "tags": ["rhtp", "grow", "maternal", "outcomes"],
        "last_reviewed": "2026-06-05",
        "source": "Pending",
    },
    {
        "name": "HFI County Dashboard",
        "domain": "Maternal & Child Health",
        "icon": "👶",
        "desc": "Healthy Families Indiana enrollment, service utilization, and outcomes by county. Stewardship assignment is pending.",
        "division": "Unassigned",
        "steward": "Unassigned",
        "cadence": "—",
        "status": "steward",
        "access": "restricted",
        "tags": ["hfi", "healthy families", "county"],
        "last_reviewed": "—",
        "source": "Pending",
    },
    {
        "name": "Vital Records — Births",
        "domain": "Vital Records",
        "icon": "📋",
        "desc": "Annual birth certificate extract with demographics, prenatal care indicators, and birth outcomes. De-identified per IDOH data governance policy.",
        "division": "Data Products",
        "steward": "Data Products",
        "cadence": "Annual",
        "status": "verified",
        "access": "approval",
        "tags": ["births", "vital records", "demographics"],
        "last_reviewed": "2026-04-01",
        "source": "PRD Synapse",
    },
    {
        "name": "Vital Records — Deaths",
        "domain": "Vital Records",
        "icon": "📋",
        "desc": "Death certificate extract with cause-of-death coding (ICD-10), demographics, and county of residence. Used for mortality trend analysis.",
        "division": "Data Products",
        "steward": "Data Products",
        "cadence": "Annual",
        "status": "verified",
        "access": "approval",
        "tags": ["deaths", "vital records", "mortality", "icd-10"],
        "last_reviewed": "2026-04-01",
        "source": "PRD Synapse",
    },
    # ── Requested / In Pipeline ───────────────────────────────────────────────
    {
        "name": "Maternal Mortality Review",
        "domain": "Maternal & Child Health",
        "icon": "🔬",
        "desc": "Aggregated committee review data on pregnancy-associated deaths. Requested by program leadership for quality improvement reporting.",
        "division": "TBD",
        "steward": "TBD",
        "cadence": "Ad hoc",
        "status": "requested",
        "access": "restricted",
        "tags": ["maternal mortality", "mmrc", "quality improvement"],
        "last_reviewed": "—",
        "source": "Requested",
    },
    {
        "name": "SNAP / WIC Participation",
        "domain": "Social Determinants",
        "icon": "🥗",
        "desc": "County-level SNAP and WIC participation rates for linkage with health outcomes data. Cross-agency data sharing agreement required.",
        "division": "TBD",
        "steward": "TBD",
        "cadence": "Monthly",
        "status": "requested",
        "access": "restricted",
        "tags": ["snap", "wic", "sdoh", "food security"],
        "last_reviewed": "—",
        "source": "Requested",
    },
    {
        "name": "Syndromic Surveillance (ESSENCE)",
        "domain": "Surveillance & BRFSS",
        "icon": "🏥",
        "desc": "Near real-time ED visit data from ESSENCE for syndromic surveillance monitoring. Integration with PRD Synapse pipeline is under scoping.",
        "division": "Epidemiology",
        "steward": "TBD",
        "cadence": "Daily",
        "status": "review",
        "access": "restricted",
        "tags": ["essence", "syndromic", "ed", "surveillance"],
        "last_reviewed": "—",
        "source": "In Scoping",
    },
]

DOMAINS = sorted({d["domain"] for d in DATASETS})

STATUS_META = {
    "verified":  {"label": "Verified",       "bg": "#1a3a2a", "color": "#4ade80"},
    "review":    {"label": "In Review",       "bg": "#3a300a", "color": "#fbbf24"},
    "new":       {"label": "New",             "bg": "#1e2a4a", "color": "#6c8eff"},
    "steward":   {"label": "Needs Steward",   "bg": "#3a1a3a", "color": "#c084fc"},
    "requested": {"label": "Requested",       "bg": "#3a2a1e", "color": "#fb923c"},
}

ACCESS_META = {
    "self-serve": {"label": "Self-serve",  "icon": "🔓"},
    "approval":   {"label": "Approval req","icon": "🔐"},
    "restricted": {"label": "Restricted",  "icon": "🔒"},
}

# ── REDCap integration ────────────────────────────────────────────────────────

# Field name candidates — first non-empty match wins per record
_RC_FIELDS = {
    "requester":    ["requestor_name"],
    "email":        ["req_contact"],
    "organization": ["req_group_name"],
    "data_request": ["req_title", "req_description"],
    "purpose":      ["req_bus_purpose", "req_description"],
    "date":         ["req_date", "idoh_data_request_form_timestamp"],
    "status":       ["req_progress"],
    "data_source":  ["data_req_source"],
}


_RC_SKIP = {"[not completed]", "n/a", "na", "none"}
_HTML_TAG_RE = re.compile(r'<[^>]+>')

def _strip_html(s):
    """Strip HTML tags REDCap embeds in rich-text field values."""
    return _HTML_TAG_RE.sub(' ', s).strip()

def _pick(record, candidates, default=""):
    for k in candidates:
        v = str(record.get(k, "") or "").strip()
        if v and v.lower() not in _RC_SKIP:
            return _strip_html(v) if '<' in v else v
    return default


def fetch_redcap():
    """Returns (records, field_labels). Gracefully returns ([], {}) on any error."""

    def _post(content, **kw):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # internal cert may not be in default trust store
                r = _req.post(REDCAP_API_URL, data={
                    "token": REDCAP_API_KEY,
                    "content": content,
                    "format": "json",
                    "returnFormat": "json",
                    **kw,
                }, timeout=30, verify=False)
            if r.status_code == 200:
                result = r.json()
                if isinstance(result, dict) and "error" in result:
                    print(f"  [WARN] REDCap {content}: {result['error']}", flush=True)
                    return []
                return result
            print(f"  [WARN] REDCap {content}: HTTP {r.status_code}", flush=True)
        except Exception as exc:
            print(f"  [WARN] REDCap {content} error: {exc}", flush=True)
        return []

    print("  REDCap metadata…", end=" ", flush=True)
    metadata = _post("metadata")
    if not isinstance(metadata, list):
        metadata = []
    labels = {
        m["field_name"]: m.get("field_label", m["field_name"])
        for m in metadata if isinstance(m, dict) and "field_name" in m
    }
    print(f"{len(labels)} fields", flush=True)

    # Build coded value map from metadata select_choices_or_calculations + yesno types
    choices = {}
    for m in metadata:
        if not isinstance(m, dict):
            continue
        fname = m.get("field_name", "")
        ftype = m.get("field_type", "")
        raw_choices = m.get("select_choices_or_calculations", "")
        if ftype == "yesno":
            choices[fname] = {"1": "Yes", "0": "No"}
        elif raw_choices and "|" in raw_choices:
            parsed = {}
            for part in raw_choices.split("|"):
                part = part.strip()
                if "," in part:
                    code, label = part.split(",", 1)
                    parsed[code.strip()] = label.strip()
            if parsed:
                choices[fname] = parsed

    def _decode(field, value):
        """Return human-readable label for a coded value, or the raw value."""
        if not value:
            return ""
        v = str(value).strip()
        return choices.get(field, {}).get(v, v) if v else ""

    def _checkbox_vals(rec, field):
        """Return comma-joined labels for all checked options in a checkbox group."""
        return ", ".join(
            label for code, label in choices.get(field, {}).items()
            if rec.get(f"{field}___{code}") == "1"
        )

    print("  REDCap records…", end=" ", flush=True)
    raw = _post("record", type="flat", exportSurveyFields="true",
                exportDataAccessGroups="false")
    if not isinstance(raw, list):
        raw = []
    print(f"{len(raw)} records", flush=True)

    records = []
    for rec in raw:
        # Compute a human-readable status from admin fields
        status = _pick(rec, _RC_FIELDS["status"])
        if not status:
            if rec.get("data_transfer_date", ""):
                status = "Completed"
            elif str(rec.get("pa_approval", "")) == "0":
                status = "Denied"
            elif rec.get("bus_own_send_approve_date", "") or rec.get("ola_approve_date", ""):
                status = "Approved"
            elif rec.get("pa_approval", "") or rec.get("oda_response", ""):
                status = "In Review"
            else:
                status = "Submitted"

        records.append({
            "record_id":    rec.get("record_id", rec.get("id", "")),
            "requester":    _pick(rec, _RC_FIELDS["requester"]),
            "email":        _pick(rec, _RC_FIELDS["email"]),
            "organization": _pick(rec, _RC_FIELDS["organization"]),
            "data_request": _pick(rec, _RC_FIELDS["data_request"]),
            "data_source":  _pick(rec, _RC_FIELDS["data_source"]),
            "purpose":      _pick(rec, ["req_bus_purpose", "req_description"]),
            "description":  _pick(rec, ["req_description"]),
            "date":         _pick(rec, _RC_FIELDS["date"]),
            "status":       status,
            # Extended fields — coded values decoded to human-readable labels
            "org_type":          _decode("req_org_type",         rec.get("req_org_type", "")),
            "idoh_division":     _decode("owning_div_dir",       rec.get("owning_div_dir", "")),
            "data_direction":    _decode("in_out_data_question", rec.get("in_out_data_question", "")),
            "start_date":        _pick(rec, ["req_start_date"]),
            "end_date":          _pick(rec, ["req_end_date"]),
            "data_fields":       _pick(rec, ["data_fields_req"]),
            "data_format":       _decode("req_data_format",      rec.get("req_data_format", "")),
            "delivery_method":   _decode("req_data_delivery",    rec.get("req_data_delivery", "")),
            "pa_approval":       _decode("pa_approval",          rec.get("pa_approval", "")),
            "ola_involved":      _decode("ola_involved",         rec.get("ola_involved", "")),
            "ola_approve":       _decode("ola_approve",          rec.get("ola_approve", "")),
            "transfer_date":     _pick(rec, ["data_transfer_date"]),
            "completion_days":   _pick(rec, ["data_request_comp_days"]),
            "intended_audience": _pick(rec, ["sti_intended_audience"]),
            "data_type":         _checkbox_vals(rec, "data_type_req"),
            "sharing_frequency": _checkbox_vals(rec, "data_req_frequency"),
            "bus_owner":         _pick(rec, ["bus_own_request"]),
            "tech_owner":        _pick(rec, ["tech_own_req"]),
            # Strip unchecked checkbox sub-fields (___N = "0") to keep raw view clean
            "_raw": {
                k: v for k, v in rec.items()
                if v and str(v).strip()
                and not (_CHECKBOX_RE.search(k) and str(v) == "0")
            },
        })

    # Apply exclusion list (case-insensitive match on request title)
    excluded = load_exclusions()
    if excluded:
        before = len(records)
        records = [r for r in records if r["data_request"].lower() not in excluded]
        print(f"  Excluded {before - len(records)} records by title filter", flush=True)

    # Sort newest first; records with no date go to the end
    records.sort(key=lambda r: r["date"] if r["date"] else "0000", reverse=True)
    api_ok = len(labels) > 0  # False when REDCap was unreachable

    print("  REDCap instruments…", end=" ", flush=True)
    instruments = _post("instrument")
    if not isinstance(instruments, list):
        instruments = []
    print(f"{len(instruments)} instruments", flush=True)

    return records, labels, choices, metadata, instruments, api_ok


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--org:#fb923c;--cyn:#22d3ee}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);
  font:14px/1.6 'Segoe UI',system-ui,sans-serif;min-height:100vh}

/* ── Layout ── */
.layout{display:flex;height:100vh;overflow:hidden}
.sidebar{width:220px;min-width:160px;background:var(--sur);border-right:1px solid var(--brd);
  overflow-y:auto;padding:20px 0;flex-shrink:0;display:flex;flex-direction:column}
.main{flex:1;overflow-y:auto;padding:28px 32px 60px}

/* ── Sidebar ── */
.sb-logo{padding:0 18px 18px;border-bottom:1px solid var(--brd);margin-bottom:14px}
.sb-logo a{font-size:13px;font-weight:700;color:var(--txt);text-decoration:none}
.sb-logo .sub{font-size:10px;color:var(--mut);margin-top:2px}
.sb-sec{font-size:9px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;
  color:var(--mut);padding:12px 18px 4px}
.sb-item{display:block;padding:6px 18px;font-size:12px;color:var(--mut);
  cursor:pointer;border-left:2px solid transparent;transition:all .12s;
  text-decoration:none;background:none;border-top:none;border-right:none;
  border-bottom:none;text-align:left;width:100%;font-family:inherit}
.sb-item:hover{color:var(--txt);background:var(--sur2);border-left-color:var(--brd)}
.sb-item.active{color:var(--acc);background:var(--sur2);border-left-color:var(--acc)}
.sb-count{float:right;font-size:10px;background:var(--sur2);
  border-radius:10px;padding:1px 7px;color:var(--mut)}
.sb-footer{margin-top:auto;padding:16px 18px;border-top:1px solid var(--brd)}
.sb-footer a{font-size:11px;color:var(--acc);text-decoration:none}
.sb-footer a:hover{text-decoration:underline}

/* ── Hero ── */
.hero{margin-bottom:24px}
.hero h1{font-size:22px;font-weight:800;margin-bottom:4px}
.hero p{color:var(--mut);font-size:13px}

/* ── Toolbar ── */
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.search-wrap{position:relative;flex:1;min-width:200px;max-width:360px}
.search-wrap input{width:100%;padding:8px 12px 8px 34px;border-radius:8px;
  border:1px solid var(--brd);background:var(--sur2);color:var(--txt);
  font-size:13px;font-family:inherit;outline:none}
.search-wrap input:focus{border-color:var(--acc)}
.search-icon{position:absolute;left:10px;top:50%;transform:translateY(-50%);
  color:var(--mut);font-size:14px;pointer-events:none}
.request-btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;
  border-radius:8px;background:var(--acc);color:#fff;font-size:13px;font-weight:700;
  border:none;cursor:pointer;font-family:inherit;text-decoration:none;
  transition:opacity .15s;white-space:nowrap}
.request-btn:hover{opacity:.85}

/* ── Filter chips ── */
.filter-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.fchip{padding:5px 14px;border-radius:999px;border:1px solid var(--brd);
  background:var(--sur);color:var(--mut);font-size:12px;cursor:pointer;
  font-family:inherit;transition:all .12s;white-space:nowrap}
.fchip:hover{border-color:var(--acc);color:var(--txt)}
.fchip.active{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}

/* ── Section header ── */
.sec-hdr{font-size:11px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;
  color:var(--mut);margin:28px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--brd);
  display:flex;align-items:center;justify-content:space-between}
.sec-hdr:first-of-type{margin-top:0}
.sec-count{font-size:11px;font-weight:400;color:var(--mut)}

/* ── Card grid ── */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;
  margin-bottom:8px}

/* ── Card ── */
.card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:16px;display:flex;flex-direction:column;gap:0;
  transition:border-color .15s,background .15s}
.card:hover{border-color:var(--acc);background:var(--sur2)}
.card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:10px}
.card-title-row{display:flex;align-items:center;gap:8px}
.card-icon{font-size:17px;flex-shrink:0}
.card-title{font-size:13px;font-weight:700;line-height:1.3}
.status-badge{font-size:10px;font-weight:700;padding:3px 8px;border-radius:5px;
  white-space:nowrap;flex-shrink:0}
.card-desc{font-size:12px;color:var(--mut);line-height:1.5;flex:1;margin-bottom:10px}
.card-meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.meta-pill{font-size:10px;padding:2px 8px;border-radius:4px;
  background:var(--sur2);border:1px solid var(--brd);color:var(--mut);
  display:inline-flex;align-items:center;gap:3px}
.card-actions{display:flex;gap:8px;margin-top:auto}
.card-btn{flex:1;padding:6px 8px;border-radius:6px;border:1px solid var(--brd);
  background:none;color:var(--mut);font-size:11px;font-weight:700;cursor:pointer;
  font-family:inherit;transition:all .12s;text-align:center;text-decoration:none;
  display:flex;align-items:center;justify-content:center}
.card-btn:hover{border-color:var(--acc);color:var(--acc)}
.card-btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.card-btn.primary:hover{opacity:.85}
.card-btn:disabled,.card-btn[disabled]{opacity:.35;cursor:not-allowed;pointer-events:none}

/* ── REDCap requests table ── */
.rc-table{width:100%;border-collapse:collapse;font-size:12px}
.rc-table th{background:var(--sur);padding:7px 10px;text-align:left;font-size:10px;
  font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);
  border-bottom:2px solid var(--brd);position:sticky;top:0;z-index:1}
.rc-table td{padding:6px 10px;border-bottom:1px solid var(--brd);vertical-align:top}
.rc-table tr:hover td{background:var(--sur);cursor:pointer}
.rc-trunc{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rc-live{display:inline-block;width:7px;height:7px;border-radius:50%;
  background:var(--grn);margin-right:6px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* ── Detail modal ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;
  display:flex;align-items:center;justify-content:center;padding:20px;display:none}
.modal-overlay.open{display:flex}
.modal{background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  width:100%;max-width:560px;max-height:90vh;overflow-y:auto;padding:24px}
.modal-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:16px}
.modal-title{font-size:17px;font-weight:800}
.modal-close{background:none;border:none;color:var(--mut);font-size:20px;
  cursor:pointer;line-height:1;padding:0 2px}
.modal-close:hover{color:var(--txt)}
.modal-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.modal-section{margin-bottom:16px}
.modal-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  color:var(--mut);margin-bottom:4px}
.modal-value{font-size:13px;color:var(--txt)}
.modal-desc{font-size:13px;color:var(--mut);line-height:1.6}
.modal-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.modal-actions{display:flex;gap:10px;padding-top:16px;border-top:1px solid var(--brd)}
.modal-actions a,.modal-actions button{flex:1;padding:9px;border-radius:7px;
  font-size:13px;font-weight:700;cursor:pointer;font-family:inherit;
  text-align:center;text-decoration:none;display:inline-block;border:1px solid var(--brd);
  color:var(--mut);background:none;transition:all .12s}
.modal-actions a:hover,.modal-actions button:hover{border-color:var(--acc);color:var(--acc)}
.modal-actions a.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.modal-actions a.primary:hover{opacity:.85}

/* ── Empty state ── */
.empty{text-align:center;padding:48px 24px;color:var(--mut)}
.empty-icon{font-size:32px;margin-bottom:12px}

/* ── Help fab ── */
.help-fab{position:fixed;bottom:22px;right:22px;width:38px;height:38px;
  border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;
  display:flex;align-items:center;justify-content:center;
  text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);
  opacity:.8;transition:opacity .15s;line-height:1}
.help-fab:hover{opacity:1}
"""

JS = """
const DATASETS    = __DATASETS__;
const RC_RECORDS  = __RC_RECORDS__;
const RC_LABELS   = __RC_LABELS__;
const REDCAP_SURVEY = '__REDCAP_SURVEY__';
const REQUEST_FORM  = 'data_request_form.html';

let activeDomain = 'all';
let searchQ = '';

const STATUS = {
  verified:  {label:'Verified',     bg:'#1a3a2a', color:'#4ade80'},
  review:    {label:'In Review',    bg:'#3a300a', color:'#fbbf24'},
  new:       {label:'New',          bg:'#1e2a4a', color:'#6c8eff'},
  steward:   {label:'Needs Steward',bg:'#3a1a3a', color:'#c084fc'},
  requested: {label:'Requested',    bg:'#3a2a1e', color:'#fb923c'},
};
const ACCESS = {
  'self-serve': {label:'Self-serve', icon:'🔓'},
  'approval':   {label:'Approval req',icon:'🔐'},
  'restricted': {label:'Restricted', icon:'🔒'},
};

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function stripHtml(s){ return String(s||'').replace(/<[^>]*>/g,' ').replace(/[ \\t\\n\\r]+/g,' ').trim(); }

function badge(status){
  const m = STATUS[status] || {label:status, bg:'var(--sur2)', color:'var(--mut)'};
  return `<span class="status-badge" style="background:${m.bg};color:${m.color}">${m.label}</span>`;
}

// ── Catalog cards ─────────────────────────────────────────────────────────────
function renderCards(datasets, containerId){
  const el = document.getElementById(containerId);
  if(!datasets.length){
    el.innerHTML = '<div class="empty"><div class="empty-icon">🔍</div><p>No datasets match your filters.</p></div>';
    return;
  }
  el.innerHTML = datasets.map((d,i) => {
    const acc = ACCESS[d.access] || {label:d.access, icon:'❓'};
    const canRequest = d.status !== 'steward';
    return `<div class="card" onclick="openModal(${i})">
      <div class="card-top">
        <div class="card-title-row">
          <span class="card-icon">${d.icon}</span>
          <span class="card-title">${esc(d.name)}</span>
        </div>
        ${badge(d.status)}
      </div>
      <p class="card-desc">${esc(d.desc)}</p>
      <div class="card-meta">
        <span class="meta-pill">👤 ${esc(d.steward)}</span>
        <span class="meta-pill">🔄 ${esc(d.cadence)}</span>
        <span class="meta-pill">${acc.icon} ${acc.label}</span>
      </div>
      <div class="card-actions" onclick="event.stopPropagation()">
        <button class="card-btn" onclick="openModal(${i})">View details</button>
        ${canRequest
          ? `<a class="card-btn primary" href="${REQUEST_FORM}">Request ↗</a>`
          : `<button class="card-btn primary" disabled>Request ↗</button>`}
      </div>
    </div>`;
  }).join('');
}

function applyFilters(){
  const q = searchQ.toLowerCase();
  const filtered = DATASETS.filter(d => {
    const domainMatch = activeDomain === 'all' || d.domain === activeDomain;
    const searchMatch = !q ||
      d.name.toLowerCase().includes(q) ||
      d.desc.toLowerCase().includes(q) ||
      (d.tags||[]).some(t => t.toLowerCase().includes(q)) ||
      d.domain.toLowerCase().includes(q);
    return domainMatch && searchMatch;
  });
  const available  = filtered.filter(d => d.status !== 'requested');
  const requested  = filtered.filter(d => d.status === 'requested');
  renderCards(available, 'grid-available');
  renderCards(requested, 'grid-requested');
  document.getElementById('count-available').textContent = available.length;
  document.getElementById('count-requested').textContent = requested.length;
  document.querySelectorAll('.sb-item[data-domain]').forEach(btn => {
    const dom = btn.dataset.domain;
    const n = dom === 'all' ? DATASETS.length : DATASETS.filter(d => d.domain === dom).length;
    const sc = btn.querySelector('.sb-count');
    if(sc) sc.textContent = n;
  });
}

function setDomain(domain){
  activeDomain = domain;
  document.querySelectorAll('.fchip').forEach(c =>
    c.classList.toggle('active', c.dataset.domain === domain));
  document.querySelectorAll('.sb-item[data-domain]').forEach(b =>
    b.classList.toggle('active', b.dataset.domain === domain));
  applyFilters();
}

// ── REDCap submissions cards ──────────────────────────────────────────────────
let rcDomains  = new Set(); // empty = show all
let rcStatuses = new Set(); // empty = show all

function rcStatusStyle(s){
  const sl = (s||'').toLowerCase();
  if(sl.includes('complet')||sl.includes('fulfill'))  return {bg:'#1a3a2a',color:'#4ade80'};
  if(sl.includes('approv'))                           return {bg:'#2e1a2e',color:'#f472b6'};
  if(sl.includes('review')||sl.includes('pending')||sl.includes('progress')) return {bg:'#3a300a',color:'#fbbf24'};
  if(sl.includes('deni')||sl.includes('deny')||sl.includes('reject')||sl.includes('declin')) return {bg:'#3a1010',color:'#f87171'};
  if(sl.includes('submit'))                           return {bg:'#1e2a4a',color:'#6c8eff'};
  return {bg:'var(--sur2)',color:'var(--mut)'};
}

function rcStatusChip(s){
  if(!s) return '<span style="color:var(--mut);font-size:10px">—</span>';
  const st = rcStatusStyle(s);
  return `<span class="status-badge" style="background:${st.bg};color:${st.color}">${esc(s)}</span>`;
}

function buildRcStatusFilters(){
  const counts = {};
  RC_RECORDS.forEach(r => { const s = r.status||'Unknown'; counts[s]=(counts[s]||0)+1; });
  const el = document.getElementById('rc-status-filters');
  if(!el) return;
  el.innerHTML = Object.entries(counts)
    .sort((a,b) => b[1]-a[1])
    .map(([s,n]) => {
      const st = rcStatusStyle(s);
      return `<button class="fchip" data-rc-status="${esc(s)}"
        data-st-bg="${esc(st.bg)}" data-st-color="${esc(st.color)}"
        onclick="setRcStatus(this.dataset.rcStatus)">
        <span style="display:inline-block;width:7px;height:7px;border-radius:50%;
          background:${st.color};margin-right:5px;vertical-align:middle;flex-shrink:0"></span>${esc(s)
        } <span style="opacity:.65;font-size:10px;font-weight:400">${n}</span>
      </button>`;
    }).join('');
}

function setRcStatus(status){
  if(rcStatuses.has(status)) rcStatuses.delete(status);
  else rcStatuses.add(status);
  document.querySelectorAll('[data-rc-status]').forEach(btn => {
    const active = rcStatuses.has(btn.dataset.rcStatus);
    btn.classList.toggle('active', active);
    btn.style.background   = active ? btn.dataset.stBg    : '';
    btn.style.borderColor  = active ? btn.dataset.stColor : '';
    btn.style.color        = active ? btn.dataset.stColor : '';
  });
  renderRC();
}

function buildRcDomains(){
  const sources = [...new Set(RC_RECORDS.map(r => r.data_request).filter(Boolean))].sort();
  const el = document.getElementById('rc-domain-chips');
  if(!el) return;
  el.innerHTML = `<button class="fchip active" data-rc-domain="all" onclick="setRcDomain(this.dataset.rcDomain)">All sources <span style="opacity:.65;font-size:10px;font-weight:400">${RC_RECORDS.length}</span></button>`
    + sources.map(s => {
        const n = RC_RECORDS.filter(r => r.data_request === s).length;
        return `<button class="fchip" data-rc-domain="${esc(s)}" onclick="setRcDomain(this.dataset.rcDomain)">${esc(s)} <span style="opacity:.65;font-size:10px;font-weight:400">${n}</span></button>`;
      }).join('');
}

function setRcDomain(domain){
  if(domain === 'all'){
    rcDomains.clear();
  } else {
    if(rcDomains.has(domain)) rcDomains.delete(domain);
    else rcDomains.add(domain);
  }
  document.querySelectorAll('[data-rc-domain]').forEach(c => {
    const d = c.dataset.rcDomain;
    c.classList.toggle('active', d === 'all' ? rcDomains.size === 0 : rcDomains.has(d));
  });
  renderRC();
}

function renderRC(){
  if(document.getElementById('rc-search'))
    document.getElementById('rc-search').oninput = renderRC;

  const q = (document.getElementById('rc-search')?.value || '').toLowerCase();
  const hasChips  = rcDomains.size > 0;
  const hasStatus = rcStatuses.size > 0;
  const hasQ = q.length > 0;
  let recs = RC_RECORDS;
  if(hasChips || hasStatus || hasQ){
    recs = recs.filter(r => {
      const chipMatch   = hasChips  && rcDomains.has(r.data_request);
      const statusMatch = hasStatus && rcStatuses.has(r.status);
      const searchMatch = hasQ && (
        (r.requester      || '').toLowerCase().includes(q) ||
        (r.organization   || '').toLowerCase().includes(q) ||
        (r.data_request   || '').toLowerCase().includes(q) ||
        (r.purpose        || '').toLowerCase().includes(q) ||
        (r.data_source    || '').toLowerCase().includes(q) ||
        (r.status         || '').toLowerCase().includes(q) ||
        (r.idoh_division  || '').toLowerCase().includes(q) ||
        (r.org_type       || '').toLowerCase().includes(q) ||
        (r.data_fields    || '').toLowerCase().includes(q) ||
        (r.data_direction || '').toLowerCase().includes(q)
      );
      return chipMatch || statusMatch || searchMatch;
    });
  }

  const el = document.getElementById('grid-rc');
  if(!el) return;

  if(!RC_RECORDS.length){
    el.innerHTML = '<div class="empty"><div class="empty-icon">📋</div><p>No submissions found in REDCap.</p></div>';
    document.getElementById('rc-count').textContent = '0';
    return;
  }
  if(!recs.length){
    el.innerHTML = '<div class="empty"><div class="empty-icon">🔍</div><p>No requests match your filters.</p></div>';
    document.getElementById('rc-count').textContent = `0 of ${RC_RECORDS.length}`;
    return;
  }

  el.innerHTML = recs.map(r => {
    const idx = RC_RECORDS.indexOf(r);
    const desc = r.purpose || '';
    const descTrunc = desc.length > 140 ? desc.slice(0, 140) + '…' : desc;
    return `<div class="card" onclick="openRC(${idx})">
      <div class="card-top">
        <div class="card-title-row">
          <span class="card-icon">📋</span>
          <span class="card-title">${esc(r.data_request || 'Untitled Request')}</span>
        </div>
        ${rcStatusChip(r.status)}
      </div>
      <p class="card-desc">${descTrunc ? esc(descTrunc) : '<span style="opacity:.5">No description provided.</span>'}</p>
      <div class="card-meta">
        ${r.requester     ? `<span class="meta-pill">👤 ${esc(r.requester)}</span>` : ''}
        ${r.organization  ? `<span class="meta-pill">🏢 ${esc(r.organization)}</span>` : ''}
        ${r.idoh_division ? `<span class="meta-pill">🏛 ${esc(r.idoh_division)}</span>` : ''}
        ${r.data_direction? `<span class="meta-pill">↔ ${esc(r.data_direction)}</span>` : ''}
        ${r.date          ? `<span class="meta-pill">📅 ${esc(r.date.slice(0,10))}</span>` : ''}
      </div>
      <div class="card-actions" onclick="event.stopPropagation()">
        <button class="card-btn" onclick="openRC(${idx})">View details</button>
      </div>
    </div>`;
  }).join('');

  document.getElementById('rc-count').textContent =
    recs.length === RC_RECORDS.length
      ? `${RC_RECORDS.length}`
      : `${recs.length} of ${RC_RECORDS.length}`;
}

function openRC(idx){
  const r = RC_RECORDS[idx];
  if(!r) return;
  const raw = r._raw || {};
  const allFields = Object.entries(raw)
    .map(([k, v]) => `<div style="display:flex;gap:12px;padding:9px 14px;border-bottom:1px solid var(--brd);align-items:flex-start">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);width:160px;flex-shrink:0;padding-top:2px;line-height:1.4">${esc(stripHtml(RC_LABELS[k] || k))}</div>
      <div style="font-size:13px;color:var(--txt);min-width:0;flex:1;line-height:1.5;overflow-wrap:anywhere">${esc(v.includes('<') ? stripHtml(v) : v)}</div>
    </div>`).join('');

  const mRow = (label, val) => val
    ? `<div><div class="modal-label">${label}</div><div class="modal-value">${esc(val)}</div></div>`
    : '';
  const mPara = (label, val) => val
    ? `<div class="modal-section"><div class="modal-label">${label}</div><div class="modal-desc" style="white-space:pre-wrap">${esc(val)}</div></div>`
    : '';
  const mSub = (text) =>
    `<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin:16px 0 8px;padding-top:12px;border-top:1px solid var(--brd)">${text}</div>`;

  const hasDataSpec = r.start_date || r.end_date || r.data_fields || r.data_type || r.delivery_method || r.data_format || r.sharing_frequency || r.intended_audience;
  const hasReview   = r.pa_approval || r.ola_involved || r.ola_approve || r.bus_owner || r.tech_owner || r.transfer_date || r.completion_days;

  document.getElementById('modal-body').innerHTML = `
    <div class="modal-top">
      <div>
        <div style="font-size:11px;color:var(--mut);margin-bottom:4px">Data Sharing Request #${esc(r.record_id)}</div>
        <div class="modal-title">${esc(r.data_request || 'Untitled Request')}</div>
      </div>
      <button class="modal-close" onclick="closeModal()">&times;</button>
    </div>
    <div class="modal-row" style="margin-bottom:16px">
      ${r.date          ? `<span class="meta-pill">📅 ${esc(r.date.slice(0,10))}</span>` : ''}
      ${r.data_direction? `<span class="meta-pill">↔ ${esc(r.data_direction)}</span>` : ''}
      ${r.idoh_division ? `<span class="meta-pill">🏛 ${esc(r.idoh_division)}</span>` : ''}
      ${rcStatusChip(r.status)}
    </div>

    ${mSub('Requester')}
    <div class="modal-grid">
      ${mRow('Name', r.requester)}
      <div><div class="modal-label">Email</div><div class="modal-value">${r.email ? `<a href="mailto:${esc(r.email)}" style="color:var(--acc)">${esc(r.email)}</a>` : '—'}</div></div>
      ${mRow('Organization', r.organization)}
      ${mRow('Org Type', r.org_type)}
      ${mRow('Data Source / Owner', r.data_source)}
      ${mRow('Submitted', r.date ? r.date.slice(0,10) : '')}
    </div>

    ${mPara('Business Purpose', r.purpose)}
    ${r.description && r.description !== r.purpose ? mPara('Description / Scope', r.description) : ''}

    ${hasDataSpec ? mSub('Data Specifics') : ''}
    ${hasDataSpec ? `<div class="modal-grid">
      ${mRow('Requested Start Date', r.start_date)}
      ${mRow('Requested End Date', r.end_date)}
      ${mRow('Delivery Method', r.delivery_method)}
      ${mRow('Data Format', r.data_format)}
      ${mRow('Sharing Frequency', r.sharing_frequency)}
      ${mRow('Intended Audience', r.intended_audience)}
    </div>
    ${mPara('Specific Fields Requested', r.data_fields)}
    ${r.data_type ? `<div class="modal-section"><div class="modal-label">Data Type(s)</div><div class="modal-value">${esc(r.data_type)}</div></div>` : ''}` : ''}

    ${hasReview ? mSub('Review &amp; Processing') : ''}
    ${hasReview ? `<div class="modal-grid">
      ${mRow('Program Area Approval', r.pa_approval)}
      ${mRow('OLA Involvement', r.ola_involved)}
      ${mRow('OLA Approval', r.ola_approve)}
      ${mRow('Data Transfer Date', r.transfer_date)}
      ${mRow('Days to Complete', r.completion_days)}
    </div>
    ${mPara('Business Owner Contact', r.bus_owner)}
    ${mPara('Technical Owner Contact', r.tech_owner)}` : ''}

    ${allFields ? `
    <details style="margin-top:16px">
      <summary style="font-size:11px;color:var(--mut);cursor:pointer;padding:6px 0;user-select:none">
        All REDCap fields <span style="color:var(--acc)">(${Object.keys(raw).length})</span>
      </summary>
      <div style="margin-top:8px;border:1px solid var(--brd);border-radius:8px;overflow:hidden">${allFields}</div>
    </details>` : ''}
    <div class="modal-actions">
      <button onclick="closeModal()">Close</button>
    </div>`;
  document.getElementById('modal-overlay').classList.add('open');
}

// ── Dataset detail modal ───────────────────────────────────────────────────────
function openModal(idx){
  const d = DATASETS[idx];
  if(!d) return;
  const acc = ACCESS[d.access] || {label:d.access, icon:'❓'};
  const canRequest = d.status !== 'steward';
  document.getElementById('modal-body').innerHTML = `
    <div class="modal-top">
      <div>
        <div style="font-size:24px;margin-bottom:6px">${d.icon}</div>
        <div class="modal-title">${esc(d.name)}</div>
      </div>
      <button class="modal-close" onclick="closeModal()">&times;</button>
    </div>
    <div class="modal-row">
      ${badge(d.status)}
      <span class="meta-pill">${acc.icon} ${acc.label}</span>
      <span class="meta-pill">🏷 ${esc(d.domain)}</span>
    </div>
    <div class="modal-section">
      <div class="modal-label">Description</div>
      <div class="modal-desc">${esc(d.desc)}</div>
    </div>
    <div class="modal-grid">
      <div>
        <div class="modal-label">Owning Division</div>
        <div class="modal-value">${esc(d.division)}</div>
      </div>
      <div>
        <div class="modal-label">Named Steward</div>
        <div class="modal-value">${esc(d.steward)}</div>
      </div>
      <div>
        <div class="modal-label">Refresh Cadence</div>
        <div class="modal-value">${esc(d.cadence)}</div>
      </div>
      <div>
        <div class="modal-label">Source System</div>
        <div class="modal-value">${esc(d.source)}</div>
      </div>
      <div>
        <div class="modal-label">Last Reviewed</div>
        <div class="modal-value">${esc(d.last_reviewed)}</div>
      </div>
      <div>
        <div class="modal-label">Tags</div>
        <div class="modal-value">${(d.tags||[]).map(t=>`<span class="meta-pill">${esc(t)}</span>`).join(' ')}</div>
      </div>
    </div>
    <div class="modal-actions">
      <button onclick="closeModal()">Close</button>
      ${canRequest
        ? `<a class="primary" href="${REQUEST_FORM}">Request Access ↗</a>`
        : `<button disabled style="opacity:.35;cursor:not-allowed">Request Unavailable</button>`}
    </div>`;
  document.getElementById('modal-overlay').classList.add('open');
}

function closeModal(){
  document.getElementById('modal-overlay').classList.remove('open');
}

document.addEventListener('DOMContentLoaded', () => {
  applyFilters();
  buildRcStatusFilters();
  buildRcDomains();
  renderRC();
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if(e.target === e.currentTarget) closeModal();
  });
});
"""


def build_html(generated, rc_records, rc_labels):
    domains = DOMAINS
    data_json    = json.dumps(DATASETS,    ensure_ascii=False)
    rc_json      = json.dumps(rc_records,  ensure_ascii=False, default=str)
    labels_json  = json.dumps(rc_labels,   ensure_ascii=False)

    js = (JS
          .replace("__DATASETS__",    data_json)
          .replace("__RC_RECORDS__",  rc_json)
          .replace("__RC_LABELS__",   labels_json)
          .replace("__REDCAP_SURVEY__", REDCAP_SURVEY))

    n_available = sum(1 for d in DATASETS if d["status"] != "requested")
    n_requested = sum(1 for d in DATASETS if d["status"] == "requested")
    n_rc        = len(rc_records)

    sb_domains = ''.join(
        f'<button class="sb-item" data-domain="{d}" onclick="setDomain(\'{d}\')">'
        f'{d}<span class="sb-count">{sum(1 for ds in DATASETS if ds["domain"] == d)}</span>'
        f'</button>'
        for d in sorted(domains)
    )

    chips = (
        '<button class="fchip active" data-domain="all" onclick="setDomain(\'all\')">All domains</button>'
        + ''.join(
            f'<button class="fchip" data-domain="{d}" onclick="setDomain(\'{d}\')">{d}</button>'
            for d in sorted(domains)
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>IDOH Data Catalog — Marketplace</title>
<style>{CSS}</style>
</head>
<body>

<div class="layout">

  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sb-logo">
      <a href="index.html">IDOH Metadata Marketplace</a>
      <div class="sub">Data Catalog</div>
    </div>

    <div class="sb-sec">Browse by Domain</div>
    <button class="sb-item active" data-domain="all" onclick="setDomain('all')">
      All datasets<span class="sb-count">{len(DATASETS)}</span>
    </button>
    {sb_domains}

    <div class="sb-sec">Status</div>
    <button class="sb-item" onclick="setDomain('all')">Available <span class="sb-count">{n_available}</span></button>
    <button class="sb-item" onclick="document.getElementById('sec-requested').scrollIntoView({{behavior:'smooth'}})">Requested <span class="sb-count">{n_requested}</span></button>
    <button class="sb-item" onclick="document.getElementById('sec-redcap').scrollIntoView({{behavior:'smooth'}})">Data Sharing Requests <span class="sb-count" id="rc-count">{n_rc}</span></button>

    <div class="sb-footer">
      <a href="{REQUEST_FORM_PAGE}">+ Submit a data request</a><br/>
      <a href="index.html" style="color:var(--mut);margin-top:6px;display:block">← Back to Marketplace</a>
    </div>
  </div>

  <!-- Main -->
  <div class="main">

    <div class="hero">
      <h1>Data Catalog</h1>
      <p>Browse available datasets, check stewardship and refresh cadence, or submit a request for data not yet in the catalog. &nbsp;·&nbsp; Last updated: {generated}</p>
    </div>

    <div class="toolbar">
      <div class="search-wrap">
        <span class="search-icon">🔍</span>
        <input id="search-input" type="text" placeholder="Search datasets, domains, or tags…"
          oninput="searchQ=this.value;applyFilters()"/>
      </div>
      <a class="request-btn" href="{REQUEST_FORM_PAGE}">+ Request a dataset</a>
    </div>

    <div class="filter-row">{chips}</div>

    <!-- Available datasets -->
    <div class="sec-hdr">
      Available &amp; In Progress
      <span class="sec-count" id="count-available">{n_available}</span>
    </div>
    <div class="grid" id="grid-available"></div>

    <!-- Requested datasets -->
    <div class="sec-hdr" id="sec-requested" style="margin-top:36px">
      Requested / In Pipeline
      <span class="sec-count" id="count-requested">{n_requested}</span>
    </div>
    <p style="font-size:12px;color:var(--mut);margin-bottom:14px">
      These datasets have been formally requested but are not yet available.
      Submit the intake form to add a new request.
    </p>
    <div class="grid" id="grid-requested"></div>

    <!-- REDCap submissions -->
    <div class="sec-hdr" id="sec-redcap" style="margin-top:36px">
      <span>
        <span class="rc-live"></span>
        Data Sharing Requests
        <span style="font-size:9px;color:var(--acc);margin-left:4px;font-weight:400;letter-spacing:0">Live · REDCap</span>
      </span>
      <span class="sec-count" id="rc-count">{n_rc}</span>
    </div>
    <p style="font-size:12px;color:var(--mut);margin-bottom:16px">
      Submissions from the IDOH Data Sharing Request Form. Click any card to view full details.
    </p>
    <div class="toolbar" style="margin-bottom:12px">
      <div class="search-wrap">
        <span class="search-icon">🔍</span>
        <input id="rc-search" type="text" placeholder="Search requests…"/>
      </div>
    </div>
    <div class="filter-row" id="rc-status-filters" style="margin-bottom:10px"></div>
    <div class="filter-row" id="rc-domain-chips"></div>
    <div class="grid" id="grid-rc"></div>

  </div><!-- /main -->
</div><!-- /layout -->

<!-- Detail modal -->
<div class="modal-overlay" id="modal-overlay">
  <div class="modal" id="modal-body"></div>
</div>

<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>

<script>{js}</script>
</body>
</html>"""


_FORM_SKIP_TYPES   = {"calc", "file"}
_FORM_SKIP_FIELDS  = {"record_id"}
_CLEAN_HTML_RE     = re.compile(r'<[^>]+>')

def _clean(s):
    return _CLEAN_HTML_RE.sub(' ', s or '').replace('  ', ' ').strip()

def _build_form_fields(metadata, choices, instrument_name):
    """Walk REDCap metadata for one instrument and return (html, required_fields)."""
    parts = []
    required_fields = []

    for field in metadata:
        fname   = field.get("field_name", "")
        ftype   = field.get("field_type", "")
        finst   = field.get("form_name", "")
        label   = _clean(field.get("field_label", ""))
        note    = _clean(field.get("field_note", ""))
        req     = field.get("required_y_n", "") == "y"
        valid   = field.get("text_validation_type_or_show_slider_number", "")

        if finst != instrument_name:
            continue
        if ftype in _FORM_SKIP_TYPES or fname in _FORM_SKIP_FIELDS:
            continue
        if not label:
            label = fname

        if ftype == "descriptive":
            parts.append(f'<div class="form-desc">{label}</div>')
            continue

        if req:
            required_fields.append(fname)

        req_star = ' <span class="req-star">*</span>' if req else ''
        req_attr = ' required' if req else ''

        out = f'<div class="form-field" id="ff-{fname}">\n'
        out += f'  <label class="form-label" for="{fname}">{label}{req_star}</label>\n'
        if note:
            out += f'  <div class="form-note">{note}</div>\n'

        if ftype == "text":
            if valid in ("date_ymd", "date_mdy", "date_dmy"):
                out += f'  <input class="form-input" type="date" id="{fname}" name="{fname}"{req_attr}/>\n'
            elif valid == "email":
                out += f'  <input class="form-input" type="email" id="{fname}" name="{fname}"{req_attr} placeholder="name@example.com"/>\n'
            elif valid in ("phone", "phone_australia"):
                out += f'  <input class="form-input" type="tel" id="{fname}" name="{fname}"{req_attr}/>\n'
            elif valid in ("number", "integer"):
                out += f'  <input class="form-input" type="number" id="{fname}" name="{fname}"{req_attr}/>\n'
            else:
                out += f'  <input class="form-input" type="text" id="{fname}" name="{fname}"{req_attr}/>\n'

        elif ftype == "notes":
            out += f'  <textarea class="form-input form-textarea" id="{fname}" name="{fname}" rows="4"{req_attr}></textarea>\n'

        elif ftype == "dropdown":
            fc = choices.get(fname, {})
            opts = ''.join(f'<option value="{k}">{v}</option>' for k, v in fc.items())
            out += f'  <select class="form-input form-select" id="{fname}" name="{fname}"{req_attr}>\n'
            out += f'    <option value="">— Select —</option>\n    {opts}\n  </select>\n'

        elif ftype == "radio":
            fc = choices.get(fname, {})
            out += '  <div class="form-radios">\n'
            for code, lbl in fc.items():
                out += f'    <label class="form-choice"><input type="radio" name="{fname}" value="{code}"{req_attr}/><span>{lbl}</span></label>\n'
            out += '  </div>\n'

        elif ftype == "checkbox":
            fc = choices.get(fname, {})
            out += '  <div class="form-checkboxes">\n'
            for code, lbl in fc.items():
                out += f'    <label class="form-choice"><input type="checkbox" name="{fname}___{code}" value="1"/><span>{lbl}</span></label>\n'
            out += '  </div>\n'

        elif ftype == "yesno":
            out += '  <div class="form-radios">\n'
            out += f'    <label class="form-choice"><input type="radio" name="{fname}" value="1"{req_attr}/><span>Yes</span></label>\n'
            out += f'    <label class="form-choice"><input type="radio" name="{fname}" value="0"/><span>No</span></label>\n'
            out += '  </div>\n'

        else:
            out += f'  <input class="form-input" type="text" id="{fname}" name="{fname}"{req_attr}/>\n'

        out += '</div>'
        parts.append(out)

    return '\n'.join(parts), required_fields


def build_request_form_html(metadata, choices, instruments):
    main_inst = instruments[0]["instrument_name"] if instruments else ""
    main_inst_label = instruments[0].get("instrument_label", "Data Sharing Request Form") if instruments else "Data Sharing Request Form"

    fields_html, required_fields = _build_form_fields(metadata, choices, main_inst)
    required_json = json.dumps(required_fields)

    FORM_CSS = """
/* ── Form elements ── */
.form-section{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  padding:24px 28px;margin-bottom:20px}
.form-section-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
  color:var(--mut);margin-bottom:18px;padding-bottom:10px;border-bottom:1px solid var(--brd)}
.form-field{margin-bottom:18px}
.form-label{display:block;font-size:12px;font-weight:700;color:var(--txt);margin-bottom:5px;line-height:1.4}
.req-star{color:var(--red)}
.form-note{font-size:11px;color:var(--mut);margin-bottom:6px;line-height:1.4}
.form-desc{font-size:12px;color:var(--mut);background:var(--sur2);border-left:3px solid var(--acc);
  padding:10px 14px;border-radius:0 6px 6px 0;margin-bottom:18px;line-height:1.5}
.form-input{width:100%;padding:9px 12px;border-radius:7px;border:1px solid var(--brd);
  background:var(--sur2);color:var(--txt);font-size:13px;font-family:inherit;
  outline:none;transition:border-color .12s;appearance:none}
.form-input:focus{border-color:var(--acc)}
.form-input.error{border-color:var(--red)}
.form-textarea{resize:vertical;min-height:90px}
.form-select{cursor:pointer}
.form-radios,.form-checkboxes{display:flex;flex-direction:column;gap:8px;margin-top:4px}
.form-choice{display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:8px 12px;
  border-radius:7px;border:1px solid var(--brd);background:var(--sur2);
  font-size:13px;transition:all .12s}
.form-choice:hover{border-color:var(--acc)}
.form-choice input{margin-top:2px;flex-shrink:0;accent-color:var(--acc)}
.form-choice span{color:var(--txt);line-height:1.4}
.form-submit-row{display:flex;gap:12px;align-items:center;margin-top:8px}
.form-submit-btn{padding:11px 28px;border-radius:8px;background:var(--acc);color:#fff;
  font-size:14px;font-weight:700;border:none;cursor:pointer;font-family:inherit;transition:opacity .15s}
.form-submit-btn:hover{opacity:.85}
.form-submit-btn:disabled{opacity:.45;cursor:not-allowed}
.form-cancel{font-size:13px;color:var(--mut);text-decoration:none}
.form-cancel:hover{color:var(--txt)}
.field-error{font-size:11px;color:var(--red);margin-top:4px;display:none}
/* ── States ── */
.state-panel{display:none;flex-direction:column;align-items:center;justify-content:center;
  gap:16px;padding:60px 24px;text-align:center}
.state-panel.visible{display:flex}
.state-icon{font-size:40px}
.state-title{font-size:18px;font-weight:800}
.state-msg{font-size:13px;color:var(--mut);max-width:420px;line-height:1.6}
/* ── Info cards ── */
.form-info{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));
  gap:12px;margin-bottom:28px}
.form-info-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;padding:14px 16px}
.fi-icon{font-size:20px;margin-bottom:8px}
.fi-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  color:var(--mut);margin-bottom:4px}
.fi-value{font-size:12px;color:var(--txt);line-height:1.5}
.spinner{width:20px;height:20px;border:3px solid var(--brd);border-top-color:var(--acc);
  border-radius:50%;animation:spin .7s linear infinite;display:inline-block}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
"""

    FORM_JS = f"""
const REQUIRED_FIELDS = {required_json};
const RC_API_URL = '{REDCAP_API_URL}';
const RC_API_KEY = '{REDCAP_API_KEY}';

function showState(state, detail) {{
  document.getElementById('form-wrap').style.display   = state === 'form'      ? '' : 'none';
  document.getElementById('state-submitting').classList.toggle('visible', state === 'submitting');
  document.getElementById('state-success').classList.toggle('visible',    state === 'success');
  document.getElementById('state-error').classList.toggle('visible',      state === 'error');
  document.getElementById('state-cors').classList.toggle('visible',       state === 'cors');
  if(state === 'success' && detail)
    document.getElementById('success-id').textContent = `Your request has been assigned ID #${{detail}}.`;
  if(state === 'error' && detail)
    document.getElementById('error-detail').textContent = detail;
}}

function collectData() {{
  const data = {{}};
  document.querySelectorAll('#request-form input, #request-form textarea, #request-form select').forEach(el => {{
    const n = el.name;
    if(!n) return;
    if(el.type === 'checkbox') {{
      data[n] = el.checked ? '1' : '0';
    }} else if(el.type === 'radio') {{
      if(el.checked) data[n] = el.value;
    }} else {{
      if(el.value !== '') data[n] = el.value;
    }}
  }});
  // Auto-set today as req_date if empty
  if(!data['req_date']) data['req_date'] = new Date().toISOString().slice(0,10);
  return data;
}}

function validateForm() {{
  let ok = true;
  document.querySelectorAll('.field-error').forEach(el => {{ el.style.display='none'; }});
  document.querySelectorAll('.form-input.error').forEach(el => el.classList.remove('error'));
  REQUIRED_FIELDS.forEach(fname => {{
    const inputs = document.querySelectorAll(`[name="${{fname}}"]`);
    let filled = false;
    inputs.forEach(el => {{
      if(el.type==='radio'||el.type==='checkbox') {{ if(el.checked) filled=true; }}
      else if(el.value.trim()) filled=true;
    }});
    if(!filled) {{
      ok = false;
      const wrap = document.getElementById('ff-'+fname);
      if(wrap) {{
        const errEl = wrap.querySelector('.field-error');
        if(errEl) errEl.style.display='block';
        const inp = wrap.querySelector('.form-input');
        if(inp) inp.classList.add('error');
        if(!document.querySelector('.field-error[style*="block"]:first-of-type') || wrap === document.querySelector('[id^=ff-]'))
          wrap.scrollIntoView({{behavior:'smooth',block:'center'}});
      }}
    }}
  }});
  return ok;
}}

async function submitRequest(e) {{
  e.preventDefault();
  if(!validateForm()) return;

  const data = collectData();
  const body = new URLSearchParams({{
    token:           RC_API_KEY,
    content:         'record',
    format:          'json',
    type:            'flat',
    action:          'import',
    forceAutoNumber: 'true',
    returnContent:   'ids',
    returnFormat:    'json',
    data:            JSON.stringify([data]),
  }});

  showState('submitting');
  try {{
    const res = await fetch(RC_API_URL, {{
      method:  'POST',
      headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
      body:    body.toString(),
    }});
    const result = await res.json();
    if(Array.isArray(result) && result.length > 0) {{
      showState('success', result[0]);
    }} else if(result && result.error) {{
      showState('error', result.error);
    }} else {{
      showState('success');
    }}
  }} catch(err) {{
    if(err instanceof TypeError) {{
      showState('cors');
    }} else {{
      showState('error', err.message);
    }}
  }}
}}

document.addEventListener('DOMContentLoaded', () => {{
  document.getElementById('request-form').addEventListener('submit', submitRequest);
  // Add field-error divs after every required field
  REQUIRED_FIELDS.forEach(fname => {{
    const wrap = document.getElementById('ff-'+fname);
    if(wrap && !wrap.querySelector('.field-error')) {{
      const div = document.createElement('div');
      div.className = 'field-error';
      div.textContent = 'This field is required.';
      wrap.appendChild(div);
    }}
  }});
}});
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Request a Dataset — IDOH Data Catalog</title>
<style>{CSS}{FORM_CSS}</style>
</head>
<body>
<div class="layout">

  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sb-logo">
      <a href="data_catalog.html">IDOH Metadata Marketplace</a>
      <div class="sub">Data Catalog</div>
    </div>
    <div class="sb-sec">Submit a Request</div>
    <button class="sb-item active">Data Request Form</button>
    <div class="sb-sec">Sections</div>
    <button class="sb-item" onclick="document.getElementById('sec-about').scrollIntoView({{behavior:'smooth'}})">About this form</button>
    <button class="sb-item" onclick="document.getElementById('sec-form').scrollIntoView({{behavior:'smooth'}})">Fill out the form</button>
    <div class="sb-footer">
      <a href="data_catalog.html">← Back to Catalog</a><br/>
      <a href="{REDCAP_SURVEY}" target="_blank" style="color:var(--mut);margin-top:6px;display:block">Open native REDCap form ↗</a>
    </div>
  </div>

  <!-- Main -->
  <div class="main">
    <div class="hero">
      <h1>Request a Dataset</h1>
      <p>Submit a formal data sharing request to IDOH. Reviewed by the program area and the Office of Data Architecture.</p>
    </div>

    <div class="form-info" id="sec-about">
      <div class="form-info-card">
        <div class="fi-icon">📋</div>
        <div class="fi-label">Step 1 — Submit</div>
        <div class="fi-value">Complete the form below with your contact info, organization, and a description of the data you need.</div>
      </div>
      <div class="form-info-card">
        <div class="fi-icon">🔍</div>
        <div class="fi-label">Step 2 — Review</div>
        <div class="fi-value">IDOH program area and ODA review for feasibility, data governance, and privacy considerations.</div>
      </div>
      <div class="form-info-card">
        <div class="fi-icon">✅</div>
        <div class="fi-label">Step 3 — Approval</div>
        <div class="fi-value">Approved requests are fulfilled by the data steward. You will be notified via the email provided.</div>
      </div>
      <div class="form-info-card">
        <div class="fi-icon">📊</div>
        <div class="fi-label">Track your request</div>
        <div class="fi-value">Submissions appear in <a href="data_catalog.html" style="color:var(--acc)">Data Sharing Requests</a> in the catalog after processing.</div>
      </div>
    </div>

    <div class="sec-hdr" id="sec-form" style="margin-bottom:20px">
      {main_inst_label}
      <span class="sec-count">Required fields marked <span style="color:var(--red)">*</span></span>
    </div>

    <!-- Form -->
    <div id="form-wrap">
      <form id="request-form" novalidate>
        <div class="form-section">
          {fields_html}
        </div>
        <div class="form-submit-row">
          <button type="submit" class="form-submit-btn">Submit Request</button>
          <a href="data_catalog.html" class="form-cancel">Cancel</a>
        </div>
      </form>
    </div>

    <!-- State: submitting -->
    <div class="state-panel" id="state-submitting">
      <div class="spinner"></div>
      <div class="state-title">Submitting…</div>
      <div class="state-msg">Sending your request to REDCap.</div>
    </div>

    <!-- State: success -->
    <div class="state-panel" id="state-success">
      <div class="state-icon">✅</div>
      <div class="state-title">Request Submitted</div>
      <div class="state-msg" id="success-id"></div>
      <div class="state-msg">Thank you — IDOH staff will review your request and contact you at the email address provided.</div>
      <div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap;justify-content:center">
        <a href="data_catalog.html" class="request-btn" style="text-decoration:none">← Back to Catalog</a>
        <button class="card-btn" onclick="location.reload()">Submit another</button>
      </div>
    </div>

    <!-- State: API error -->
    <div class="state-panel" id="state-error">
      <div class="state-icon">⚠️</div>
      <div class="state-title">Submission Failed</div>
      <div class="state-msg" id="error-detail"></div>
      <div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap;justify-content:center">
        <button class="form-submit-btn" onclick="showState('form')">Try again</button>
        <a href="{REDCAP_SURVEY}" target="_blank" class="card-btn" style="text-decoration:none">Open REDCap directly ↗</a>
      </div>
    </div>

    <!-- State: CORS blocked -->
    <div class="state-panel" id="state-cors">
      <div class="state-icon">🔒</div>
      <div class="state-title">Network Restriction</div>
      <div class="state-msg">The REDCap server blocked this request (CORS policy). This usually happens when accessing the catalog from outside the ISDH network or VPN. Use the native REDCap form to submit your request.</div>
      <div style="display:flex;gap:12px;margin-top:8px;flex-wrap:wrap;justify-content:center">
        <a href="{REDCAP_SURVEY}" target="_blank" class="request-btn" style="text-decoration:none">Open REDCap form ↗</a>
        <button class="card-btn" onclick="showState('form')">← Back to form</button>
      </div>
    </div>

  </div><!-- /main -->
</div><!-- /layout -->

<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<script>{FORM_JS}</script>
</body>
</html>"""


def main():
    import os
    print("\n=== Data Catalog ===")
    print("Fetching REDCap data…")
    rc_records, rc_labels, rc_choices, rc_metadata, rc_instruments, api_ok = fetch_redcap()

    if not api_ok and os.path.exists(OUT_FILE):
        print(f"  [WARN] REDCap API unreachable — preserving existing {OUT_FILE}, skipping write.")
        print(f"  Catalog datasets : {len(DATASETS)} ({sum(1 for d in DATASETS if d['status'] != 'requested')} available, {sum(1 for d in DATASETS if d['status'] == 'requested')} requested)")
        print(f"  REDCap requests  : (previous data preserved)")
        return

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_html(generated, rc_records, rc_labels)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    form_html = build_request_form_html(rc_metadata, rc_choices, rc_instruments)
    with open(REQUEST_FORM_FILE, "w", encoding="utf-8") as f:
        f.write(form_html)

    print(f"Saved: {OUT_FILE}")
    print(f"  Catalog datasets : {len(DATASETS)} ({sum(1 for d in DATASETS if d['status'] != 'requested')} available, {sum(1 for d in DATASETS if d['status'] == 'requested')} requested)")
    print(f"  REDCap requests  : {len(rc_records)}")

    try:
        import generate_metadata_index
        generate_metadata_index.main()
        print("  Index updated: index.html")
    except Exception as exc:
        print(f"  Warning: could not update index.html: {exc}")


if __name__ == "__main__":
    main()
