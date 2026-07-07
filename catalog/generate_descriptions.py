#!/usr/bin/env python3
"""
generate_descriptions.py — Plain-English descriptions for Synapse objects.

Two modes:
  Rule-based (default, free): parses schema prefixes, name tokens, and column
    patterns against IDOH domain knowledge — no API key needed.
  AI-enhanced (--api-key):    uses Claude API for richer, context-aware descriptions.

Descriptions are cached by content hash — only regenerated when the object changes.
Cache is saved after every batch so progress is never lost if the run is interrupted.

Output files (consumed by synapse_metadata_report_*.py):
  view_descriptions.json    key: "schema||name"
  proc_descriptions.json    key: "schema||name"
  table_descriptions.json   key: "schema||name"
  column_descriptions.json  key: "schema||table||column"

Usage:
  python3 generate_descriptions.py --env dev                  # rule-based, all types
  python3 generate_descriptions.py --env dev --type tables    # tables only
  python3 generate_descriptions.py --env dev --api-key KEY    # AI mode
  python3 generate_descriptions.py --env dev --force          # ignore cache
"""

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
from datetime import datetime

import pyodbc

# ── Config ─────────────────────────────────────────────────────────────────────

ENVS = {
    "dev": {
        "server":   "zus1-idoh-dev-v2-sql-server",
        "database": "zus1-idoh-dev-v2-sql-dw",
        "label":    "DEV",
    },
    "prd": {
        "server":   "zus1-idoh-prd-v1-sql-server",
        "database": "zus1-idoh-prd-v1-sql-dw",
        "label":    "PRD",
    },
}

DRIVER    = "{ODBC Driver 18 for SQL Server}"
CACHE_DIR = "/home/thedavidporter"
MODEL     = "claude-sonnet-4-6"

BATCH_VIEWS_PROCS = 5
BATCH_TABLES      = 10
BATCH_COLUMNS     = 30
API_PAUSE         = 0.5

# ── Rule-based domain knowledge ─────────────────────────────────────────────────

SCHEMA_LAYERS = {
    "SM_":        "Source/staging layer — raw data ingested directly from {src} source systems.",
    "DM_":        "Data mart layer — curated, analytics-ready {src} data.",
    "Reporting_": "Reporting layer — finalized {src} data for reports and dashboards.",
    "HUB_":       "Hub/integration layer — shared reference {src} data used across domains.",
    "Audits_DBA": "Internal audit and database administration log.",
    "ACE_":       "Legacy ACE warehouse data for {src}.",
}

# Expanded forms for known abbreviations found in table/column names
ABBREVS = {
    "BRFSS":    "Behavioral Risk Factor Surveillance Survey",
    "CHIRP":    "Children and Hoosiers Immunization Registry Program",
    "CHIRPV":   "CHIRP vaccine",
    "WIC":      "Women Infants and Children nutrition program",
    "MCH":      "Maternal and Child Health",
    "HFI":      "Healthy Families Indiana",
    "RHTP":     "Reducing High Risk Teen Pregnancy",
    "GROW":     "GROW program",
    "ESSENCE":  "Electronic Surveillance System for Early Notification of Community-based Epidemics",
    "PHIG":     "Public Health Infrastructure Grant",
    "NPI":      "National Provider Identifier",
    "ICD":      "ICD diagnosis code",
    "ICD10":    "ICD-10 diagnosis code",
    "FIPS":     "FIPS county code",
    "PHI":      "Protected Health Information",
    "ETL":      "ETL pipeline",
    "COVID":    "COVID-19",
    "COVID19":  "COVID-19",
    "EH":       "Environmental Health",
    "LEAD":     "lead poisoning",
    "CANCER":   "cancer registry",
    "VR":       "vital records",
    "BIRTH":    "birth record",
    "DEATH":    "death record",
    "IMM":      "immunization",
    "IMMUN":    "immunization",
    "VAX":      "vaccination",
    "VACC":     "vaccination",
    "VAERS":    "Vaccine Adverse Event Reporting System",
    "SYNDROMIC":"syndromic surveillance",
    "CD":       "communicable disease",
    "STD":      "sexually transmitted disease",
    "TB":       "tuberculosis",
    "HIV":      "HIV/AIDS",
    "EPI":      "epidemiology",
    "EPID":     "epidemiology",
    "SURV":     "surveillance",
    "LAB":      "laboratory",
    "LABS":     "laboratory results",
    "HOSP":     "hospital",
    "PROVIDER": "provider",
    "PROV":     "provider",
    "PATIENT":  "patient",
    "PAT":      "patient",
    "MBR":      "member",
    "MEMBER":   "member",
    "ENROLL":   "enrollment",
    "CLAIM":    "claim",
    "MEDICAID": "Medicaid",
    "MEDICARE": "Medicare",
    "COUNTY":   "county",
    "CNTY":     "county",
    "STATE":    "state",
    "ZIP":      "ZIP code",
    "ADDR":     "address",
    "ADDRESS":  "address",
    "DEMO":     "demographic",
    "DEMOG":    "demographic",
    "REF":      "reference",
    "XREF":     "cross-reference",
    "DIM":      "dimension",
    "FACT":     "fact",
    "STG":      "staging",
    "STAGE":    "staging",
    "HIST":     "history",
    "HISTORY":  "history",
    "ARCHIVE":  "archive",
    "LOG":      "log",
    "AUDIT":    "audit",
    "CONFIG":   "configuration",
    "LOOKUP":   "lookup",
    "LKP":      "lookup",
    "MAP":      "mapping",
    "MAPPING":  "mapping",
    "XWALK":    "crosswalk",
    "RPT":      "report",
    "REPORT":   "report",
    "SUMMARY":  "summary",
    "SUM":      "summary",
    "AGG":      "aggregate",
    "AGGR":     "aggregate",
    "CALC":     "calculated",
    "FLAT":     "flattened",
    "FINAL":    "final",
    "CURR":     "current",
    "CURRENT":  "current",
    "PREV":     "previous",
    "YTD":      "year-to-date",
    "MTD":      "month-to-date",
    "QTD":      "quarter-to-date",
    "RPT":      "reporting",
    "GEO":      "geographic",
    "GEOG":     "geographic",
    "TRACT":    "census tract",
    "CENSUS":   "census",
    "RACE":     "race",
    "ETHNICITY":"ethnicity",
    "GENDER":   "gender",
    "SEX":      "sex",
    "AGE":      "age",
    "DOB":      "date of birth",
    "DOD":      "date of death",
    "DOE":      "date of event",
    "ISDH":     "Indiana State Department of Health",
    "IDOH":     "Indiana Department of Health",
    "IN":       "Indiana",
    "PHD":      "public health district",
    "LHD":      "local health department",
    "WS":       "web service",
    "API":      "API",
    "JSON":     "JSON",
    "XML":      "XML",
    "DT":       "date",
    "TS":       "timestamp",
    "ID":       "identifier",
    "CD":       "code",
    "NM":       "name",
    "TXT":      "text",
    "DESC":     "description",
    "AMT":      "amount",
    "CNT":      "count",
    "QTY":      "quantity",
    "IND":      "indicator",
    "FLG":      "flag",
    "YN":       "yes/no",
    "NUM":      "number",
    "NBR":      "number",
    "NO":       "number",
    "PK":       "primary key",
    "FK":       "foreign key",
    "SRC":      "source",
    "TGT":      "target",
    "LST":      "last",
    "LAST":     "last",
    "FIRST":    "first",
    "FST":      "first",
    "MID":      "middle",
    "SUFF":     "suffix",
    "PREF":     "prefix",
    "PHONE":    "phone number",
    "EMAIL":    "email address",
    "FAX":      "fax number",
    "SSN":      "Social Security Number",
    "DOC":      "document",
    "FILE":     "file",
    "IMG":      "image",
    "NOTE":     "note",
    "COMMENT":  "comment",
    "REASON":   "reason",
    "STATUS":   "status",
    "STAT":     "status",
    "TYPE":     "type",
    "CAT":      "category",
    "CATEG":    "category",
    "GRP":      "group",
    "GROUP":    "group",
    "CLASS":    "classification",
    "CLASSIF":  "classification",
    "DIAG":     "diagnosis",
    "DX":       "diagnosis",
    "PROC":     "procedure",
    "PX":       "procedure",
    "RX":       "prescription",
    "MED":      "medication",
    "DRUG":     "drug",
    "DOSE":     "dose",
    "DOSAGE":   "dosage",
    "ALLERGY":  "allergy",
    "ALGY":     "allergy",
    "VISIT":    "visit",
    "ENCOUNTER":"encounter",
    "ENC":      "encounter",
    "ADMIT":    "admission",
    "DISCH":    "discharge",
    "LOS":      "length of stay",
    "ICU":      "ICU",
    "ER":       "emergency room",
    "ED":       "emergency department",
    "INPAT":    "inpatient",
    "OUTPAT":   "outpatient",
    "FACIL":    "facility",
    "FAC":      "facility",
    "FACILITY": "facility",
    "ORG":      "organization",
    "DEPT":     "department",
    "DIVISION": "division",
    "DIV":      "division",
    "REGION":   "region",
    "DISTRICT": "district",
    "DIST":     "district",
    "CITY":     "city",
    "SCHOOL":   "school",
    "CHILDCARE":"childcare",
    "FOOD":     "food safety",
    "WATER":    "water quality",
    "AIR":      "air quality",
    "RADON":    "radon",
    "ASBESTOS": "asbestos",
    "MOLD":     "mold",
    "PEST":     "pesticide",
    "CHEM":     "chemical",
    "HAZARD":   "hazard",
    "HAZ":      "hazard",
    "RISK":     "risk",
    "OUTCOME":  "outcome",
    "MEASURE":  "measure",
    "METRIC":   "metric",
    "INDICATOR":"indicator",
    "RATE":     "rate",
    "RATIO":    "ratio",
    "PCT":      "percent",
    "PERCENT":  "percent",
    "POP":      "population",
    "POPUL":    "population",
    "ESTIMATE": "estimate",
    "EST":      "estimate",
    "PROJ":     "projection",
    "FORECAST": "forecast",
    "TREND":    "trend",
    "CHANGE":   "change",
    "DIFF":     "difference",
    "DELTA":    "delta",
    "PREV":     "previous",
    "PRIOR":    "prior",
    "CUR":      "current",
    "ACT":      "actual",
    "EXP":      "expected",
    "PRED":     "predicted",
    "OBS":      "observed",
    "OBSERVED": "observed",
    "EXPECTED": "expected",
    "EXCESS":   "excess",
    "BASELINE": "baseline",
    "BASE":     "baseline",
    "BENCH":    "benchmark",
    "TARGET":   "target",
    "GOAL":     "goal",
    "OBJECT":   "objective",
    "OBJ":      "objective",
}

# Column name suffix/prefix patterns → what that column typically contains
COL_PATTERNS = [
    (r'(?:^|_)(?:PK|ID)$',                    'Unique identifier / primary key.'),
    (r'(?:^|_)(?:FK|_ID)$',                   'Foreign key linking to a related record.'),
    (r'(?:^|_)(?:DT|DATE|DATETIME)$',         'Date or date/time value.'),
    (r'(?:^|_)(?:TS|TIMESTAMP|DTTM)$',        'Timestamp recording when the event occurred.'),
    (r'(?:^|_)(?:CD|CODE)$',                  'Coded value — refer to the associated reference table for decoding.'),
    (r'(?:^|_)(?:NM|NAME)$',                  'Name or label.'),
    (r'(?:^|_)(?:DESC|DESCR|DESCRIPTION)$',   'Descriptive text or narrative.'),
    (r'(?:^|_)(?:TXT|TEXT|NOTE|NOTES|COMMENT)$', 'Free-text field.'),
    (r'(?:^|_)(?:AMT|AMOUNT|DOLLARS|DOLLAR)$','Dollar amount.'),
    (r'(?:^|_)(?:CNT|COUNT|QTY|QUANTITY)$',   'Count or quantity.'),
    (r'(?:^|_)(?:IND|FLAG|FLG|YN|BIT)$',      'Yes/No or true/false indicator.'),
    (r'(?:^|_)(?:PCT|PERCENT|RATE|RATIO)$',   'Percentage or rate value.'),
    (r'(?:^|_)(?:NUM|NBR|NUMBER|NO)$',        'Numeric identifier or sequential number.'),
    (r'(?:^|_)(?:ADDR|ADDRESS|STREET|ADDR1|ADDR2)$', 'Street address.'),
    (r'(?:^|_)(?:CITY)$',                     'City name.'),
    (r'(?:^|_)(?:STATE|ST)$',                 'State code or name.'),
    (r'(?:^|_)(?:ZIP|ZIPCD|ZIPCODE|POSTAL)$', 'ZIP or postal code.'),
    (r'(?:^|_)(?:COUNTY|CNTY|FIPS)$',         'County name or FIPS county code.'),
    (r'(?:^|_)(?:PHONE|PHN|TEL|FAX)$',        'Phone or fax number.'),
    (r'(?:^|_)(?:EMAIL)$',                    'Email address.'),
    (r'(?:^|_)(?:SSN)$',                      'Social Security Number (protected health information).'),
    (r'(?:^|_)(?:DOB|BIRTHDATE|BIRTH_DT)$',   'Date of birth.'),
    (r'(?:^|_)(?:DOD|DEATHDATE|DEATH_DT)$',   'Date of death.'),
    (r'(?:^|_)(?:AGE|AGE_YRS|AGE_MOS)$',      'Age (in years unless otherwise noted).'),
    (r'(?:^|_)(?:SEX|GENDER)$',               'Sex or gender.'),
    (r'(?:^|_)(?:RACE|ETHNICITY|RACE_CD)$',   'Race or ethnicity code.'),
    (r'(?:^|_)(?:NPI)$',                      'National Provider Identifier (NPI).'),
    (r'(?:^|_)(?:ICD|DX|DIAG|DIAGNOSIS)$',    'ICD diagnosis code.'),
    (r'(?:^|_)(?:SRC|SOURCE)$',               'Source system or originating data source.'),
    (r'(?:^|_)(?:STATUS|STAT)$',              'Status code or value.'),
    (r'(?:^|_)(?:TYPE|TYP)$',                 'Type classification.'),
    (r'(?:^|_)(?:CREATED|CREATE_DT|CREAT_DT)$','Record creation date/time.'),
    (r'(?:^|_)(?:UPDATED|UPDATE_DT|UPDT_DT|MODIFIED|MOD_DT)$', 'Date/time this record was last updated.'),
    (r'(?:^|_)(?:DELETED|DEL_DT|PURGE)$',     'Deletion or purge date — soft-delete indicator.'),
    (r'(?:^|_)(?:LOAD_DT|LOADED_DT|ETL_DT|BATCH_DT)$', 'Date/time this record was loaded by the ETL process.'),
    (r'(?:^|_)(?:ROW_ID|ROWID|SEQ|SEQNO|SEQ_NO)$', 'Internal row sequence number.'),
    (r'(?:^|_)(?:HASH|CHECKSUM|CRC)$',        'Hash or checksum value used for change detection.'),
    (r'(?:^|_)(?:ACTIVE|IS_ACTIVE|ENABLED)$', 'Indicates whether the record is currently active.'),
    (r'(?:^|_)(?:VALID|IS_VALID|VALIDATED)$', 'Indicates whether the record has passed validation.'),
    (r'(?:^|_)(?:LATITUDE|LAT)$',             'Geographic latitude coordinate.'),
    (r'(?:^|_)(?:LONGITUDE|LON|LONG|LNG)$',   'Geographic longitude coordinate.'),
    (r'(?:^|_)(?:GEOM|GEOMETRY|SHAPE)$',      'Geographic geometry or shape data.'),
    (r'(?:^|_)(?:URL|URI|LINK)$',             'URL or URI link.'),
    (r'(?:^|_)(?:VERSION|VER|VER_NO)$',       'Version number for record versioning.'),
    (r'(?:^|_)(?:COMMENT|COMMENTS|REMARKS)$', 'Free-text comments or remarks.'),
    (r'(?:^|_)(?:PRIORITY|PRI|RANK)$',        'Priority or rank ordering.'),
    (r'(?:^|_)(?:WEIGHT|WT)$',                'Weight value (units depend on context).'),
    (r'(?:^|_)(?:HEIGHT|HT)$',                'Height value (units depend on context).'),
    (r'(?:^|_)(?:BMI)$',                      'Body Mass Index (BMI).'),
    (r'(?:^|_)(?:WEEKS|WKS|GEST)$',           'Gestational age in weeks.'),
]

# ── Name tokenizer ─────────────────────────────────────────────────────────────

def _split_name(name):
    """Split a SQL name (CamelCase or UNDER_SCORE) into word tokens."""
    name = re.sub(r'^(?:SM|DM|Reporting|HUB|ACE|Audits_DBA)_', '', name, flags=re.IGNORECASE)
    # Handle all-caps runs before a capitalized word: WICEnrollment → WIC_Enrollment
    name = re.sub(r'([A-Z]{2,})([A-Z][a-z])', r'\1_\2', name)
    # Handle lowercase→uppercase boundary: summaryWord → summary_Word
    name = re.sub(r'([a-z])([A-Z])', r'\1_\2', name)
    parts = name.split('_')
    return [p.strip() for p in parts if p.strip()]

def _expand_tokens(tokens):
    """Expand known abbreviations in token list."""
    expanded = []
    for t in tokens:
        up = t.upper()
        expanded.append(ABBREVS.get(up, t))
    return expanded

def _humanize(name):
    """Convert a SQL object name to a readable phrase."""
    tokens  = _split_name(name)
    words   = _expand_tokens(tokens)
    return ' '.join(words).lower()

# ── Rule-based description generators ─────────────────────────────────────────

def _schema_layer(schema):
    for prefix, tmpl in SCHEMA_LAYERS.items():
        if schema.startswith(prefix):
            return tmpl
    return "Database object in the {src} area."

def rule_table_desc(schema, table):
    human = _humanize(table)
    layer_tmpl = _schema_layer(schema)
    layer = layer_tmpl.format(src=human)
    return layer

def rule_view_desc(schema, name, sql=""):
    human = _humanize(name)
    layer_tmpl = _schema_layer(schema)
    layer = layer_tmpl.format(src=human)
    joins = len(re.findall(r'\bJOIN\b', sql, re.IGNORECASE))
    hint  = f" Combines data from approximately {joins} joined sources." if joins > 1 else ""
    return layer + hint

def rule_proc_desc(schema, name, sql=""):
    human = _humanize(name)
    if re.search(r'\bINSERT\b', sql, re.IGNORECASE):
        action = "Loads or inserts"
    elif re.search(r'\bUPDATE\b', sql, re.IGNORECASE):
        action = "Updates"
    elif re.search(r'\bDELETE\b|\bTRUNCATE\b', sql, re.IGNORECASE):
        action = "Removes or truncates"
    elif re.search(r'\bSELECT\b', sql, re.IGNORECASE):
        action = "Queries and returns"
    else:
        action = "Processes"
    layer_tmpl = _schema_layer(schema)
    src = _humanize(name)
    return f"{action} {src} data. {layer_tmpl.format(src=src)}"

def rule_col_desc(col_name, data_type, schema="", table=""):
    col_up = col_name.upper()
    for pattern, desc in COL_PATTERNS:
        if re.search(pattern, col_up):
            human_col = _humanize(col_name)
            return f"{human_col.capitalize()} — {desc}"
    # fallback: just humanize the name
    human = _humanize(col_name)
    type_hint = {
        "int": " Integer value.", "bigint": " Large integer.",
        "varchar": " Text value.", "nvarchar": " Unicode text.",
        "datetime": " Date/time.", "datetime2": " Date/time.",
        "date": " Date value.", "bit": " Boolean (0/1) flag.",
        "decimal": " Decimal number.", "float": " Floating-point number.",
        "uniqueidentifier": " GUID identifier.",
    }.get(data_type.lower(), "")
    return f"{human.capitalize()}.{type_hint}"

# ── Rule-based batch generators ─────────────────────────────────────────────────

def generate_rule_def_descriptions(defs, cache, force):
    views = {f"{d['schema_name']}||{d['obj_name']}": d for d in defs if d["obj_type"] == "VIEW"}
    procs = {f"{d['schema_name']}||{d['obj_name']}": d for d in defs
             if "TABLE" not in d["obj_type"] and d["obj_type"] != "VIEW"}

    view_cache = cache.get("views", {})
    proc_cache = cache.get("procs", {})

    def process(objects, obj_cache, label, fn):
        pending = [(k, o) for k, o in objects.items()
                   if force or k not in obj_cache]
        if not pending:
            print(f"  {label}: all {len(objects)} cached — skipping.")
            return
        print(f"  {label}: generating {len(pending)} of {len(objects)} descriptions…")
        for key, o in pending:
            sql  = (o.get("definition") or "")[:4000]
            desc = fn(o["schema_name"], o["obj_name"], sql)
            obj_cache[key] = {"description": desc, "_hash": "rule"}
        print(f"    done ({len(pending)} generated).")

    process(views, view_cache, "Views",          rule_view_desc)
    process(procs, proc_cache, "Procs/Functions",rule_proc_desc)

    cache["views"] = view_cache
    cache["procs"] = proc_cache

def generate_rule_table_descriptions(tables, columns, cache, force):
    col_map = {}
    for c in columns:
        k = f"{c['schema_name']}||{c['table_name']}"
        col_map.setdefault(k, []).append(c["column_name"])

    tbl_cache = cache.get("tables", {})
    pending = [(f"{t['schema_name']}||{t['table_name']}", t)
               for t in tables
               if force or f"{t['schema_name']}||{t['table_name']}" not in tbl_cache]

    if not pending:
        print(f"  Tables: all {len(tables)} cached — skipping.")
        return

    print(f"  Tables: generating {len(pending)} of {len(tables)} descriptions…")
    for key, t in pending:
        desc = rule_table_desc(t["schema_name"], t["table_name"])
        tbl_cache[key] = {"description": desc, "_hash": "rule"}
    print(f"    done ({len(pending)} generated).")

    cache["tables"] = tbl_cache

def generate_rule_column_descriptions(columns, cache, force):
    col_cache = cache.get("columns", {})
    pending = []
    for c in columns:
        ck = f"{c['schema_name']}||{c['table_name']}||{c['column_name']}"
        if force or ck not in col_cache:
            pending.append((ck, c))

    if not pending:
        print(f"  Columns: all {len(columns)} cached — skipping.")
        return

    print(f"  Columns: generating {len(pending)} of {len(columns)} descriptions…")
    for ck, c in pending:
        desc = rule_col_desc(c["column_name"], c["data_type"],
                             c["schema_name"], c["table_name"])
        col_cache[ck] = {"description": desc, "_hash": "rule"}
    print(f"    done ({len(pending)} generated).")

    cache["columns"] = col_cache

# ── AI-based generators (optional, requires --api-key) ─────────────────────────

SYSTEM_PROMPT = """You are a data documentation specialist for the Indiana Department of Health (IDOH)
Office of Data Analytics (ODA). Your job is to write clear, plain-English descriptions of
database objects in IDOH's Azure Synapse Dedicated SQL Pool.

Context:
- Schema prefixes: SM_ = Source/Staging, DM_ = Data Mart, Reporting_ = Reporting layer,
  HUB_ = shared reference/integration, Audits_DBA = audit/logging, ACE_ = legacy.
- Domains: vital records, immunization (CHIRP), BRFSS, maternal/child health (WIC, HFI),
  syndromic surveillance (ESSENCE), grants (PHIG), COVID-19, cancer registry, lead, EH.
- Abbreviations: FIPS = county code, ICD10 = diagnosis code, NPI = provider ID.
- Audience: program analysts and epidemiologists — not DBAs.

Rules:
- 1–3 concise plain-English sentences. No SQL jargon.
- Lead with WHAT it contains, then WHY or WHO uses it.
- For columns: say what the value represents, units, or coding standard if known.
- Never say "this stored procedure" or "this view" — just describe what it does.
- Return ONLY valid JSON."""

def call_claude(client, user_message):
    import anthropic as _ant
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text = resp.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(text)
        except json.JSONDecodeError as e:
            if attempt == 0:
                print(f"    JSON parse error, retrying… ({e})")
                time.sleep(2)
            else:
                print(f"    JSON parse failed twice — skipping batch.")
                return {}
        except Exception as e:
            if attempt == 0:
                print(f"    API error, retrying… ({e})")
                time.sleep(5)
            else:
                print(f"    API error twice — skipping batch.")
                return {}

def generate_ai_def_descriptions(client, defs, cache, force, cache_path):
    views = {f"{d['schema_name']}||{d['obj_name']}": d for d in defs if d["obj_type"] == "VIEW"}
    procs = {f"{d['schema_name']}||{d['obj_name']}": d for d in defs
             if "TABLE" not in d["obj_type"] and d["obj_type"] != "VIEW"}
    view_cache = cache.get("views", {})
    proc_cache = cache.get("procs", {})

    def process_batch(objects, obj_cache, label):
        pending = []
        for key, obj in objects.items():
            defn = (obj.get("definition") or "")[:4000]
            h = content_hash(defn)
            if force or key not in obj_cache or obj_cache[key].get("_hash") != h:
                pending.append((key, obj, defn, h))
        if not pending:
            print(f"  {label}: all {len(objects)} cached — skipping.")
            return
        print(f"  {label}: generating {len(pending)} of {len(objects)} descriptions…")
        for i in range(0, len(pending), BATCH_VIEWS_PROCS):
            batch = pending[i:i + BATCH_VIEWS_PROCS]
            bn = i // BATCH_VIEWS_PROCS + 1
            tb = (len(pending) + BATCH_VIEWS_PROCS - 1) // BATCH_VIEWS_PROCS
            print(f"    batch {bn}/{tb} ({len(batch)} objects)…", end="", flush=True)
            items = [{"key": k, "schema": o["schema_name"], "name": o["obj_name"],
                      "type": o["obj_type"], "sql": d} for k, o, d, h in batch]
            prompt = ("Write a plain-English description for each object.\n"
                      "Return JSON: {key: description_string}\n\n" + json.dumps(items, indent=2))
            result = call_claude(client, prompt)
            time.sleep(API_PAUSE)
            for key, obj, defn, h in batch:
                if key in result:
                    obj_cache[key] = {"description": result[key], "_hash": h}
                    print(".", end="", flush=True)
                else:
                    print("?", end="", flush=True)
            print()
            save_cache(cache_path, cache)  # save after every batch

    process_batch(views, view_cache, "Views")
    process_batch(procs, proc_cache, "Procs/Functions")
    cache["views"] = view_cache
    cache["procs"] = proc_cache

def generate_ai_table_descriptions(client, tables, columns, cache, force, cache_path):
    col_map = {}
    for c in columns:
        k = f"{c['schema_name']}||{c['table_name']}"
        col_map.setdefault(k, []).append(c["column_name"])
    tbl_cache = cache.get("tables", {})
    pending = []
    for t in tables:
        key = f"{t['schema_name']}||{t['table_name']}"
        cols = col_map.get(key, [])
        fp = content_hash(t["schema_name"] + t["table_name"] + ",".join(cols[:50]))
        if force or key not in tbl_cache or tbl_cache[key].get("_hash") != fp:
            pending.append((key, t, cols, fp))
    if not pending:
        print(f"  Tables: all {len(tables)} cached — skipping.")
        return
    print(f"  Tables: generating {len(pending)} of {len(tables)} descriptions…")
    for i in range(0, len(pending), BATCH_TABLES):
        batch = pending[i:i + BATCH_TABLES]
        bn = i // BATCH_TABLES + 1
        tb = (len(pending) + BATCH_TABLES - 1) // BATCH_TABLES
        print(f"    batch {bn}/{tb} ({len(batch)} tables)…", end="", flush=True)
        items = [{"key": k, "schema": t["schema_name"], "table": t["table_name"],
                  "columns": cols[:60]} for k, t, cols, _ in batch]
        prompt = ("Write a plain-English description for each table.\n"
                  "Return JSON: {key: description_string}\n\n" + json.dumps(items, indent=2))
        result = call_claude(client, prompt)
        time.sleep(API_PAUSE)
        for key, t, cols, fp in batch:
            if key in result:
                tbl_cache[key] = {"description": result[key], "_hash": fp}
                print(".", end="", flush=True)
            else:
                print("?", end="", flush=True)
        print()
        save_cache(cache_path, cache)  # save after every batch
    cache["tables"] = tbl_cache

def generate_ai_column_descriptions(client, columns, cache, force, cache_path):
    by_table = {}
    for c in columns:
        tk = f"{c['schema_name']}||{c['table_name']}"
        by_table.setdefault(tk, []).append(c)
    col_cache = cache.get("columns", {})
    pending = []
    for tk, cols in by_table.items():
        for c in cols:
            ck = f"{c['schema_name']}||{c['table_name']}||{c['column_name']}"
            fp = content_hash(tk + c["column_name"] + c["data_type"])
            if force or ck not in col_cache or col_cache[ck].get("_hash") != fp:
                pending.append((ck, tk, c, fp))
    if not pending:
        print(f"  Columns: all {len(columns)} cached — skipping.")
        return
    print(f"  Columns: generating {len(pending)} of {len(columns)} descriptions…")
    pending_by_table = {}
    for ck, tk, c, h in pending:
        pending_by_table.setdefault(tk, []).append((ck, c, h))
    total_batches = sum((len(v) + BATCH_COLUMNS - 1) // BATCH_COLUMNS
                        for v in pending_by_table.values())
    batch_num = 0
    for tk, items in pending_by_table.items():
        schema, table = tk.split("||")
        for i in range(0, len(items), BATCH_COLUMNS):
            batch = items[i:i + BATCH_COLUMNS]
            batch_num += 1
            print(f"    batch {batch_num}/{total_batches} — {table} ({len(batch)} cols)…",
                  end="", flush=True)
            col_items = [{"column": c["column_name"], "type": c["data_type"],
                          "nullable": c["is_nullable"]} for _, c, _ in batch]
            prompt = (f"Table: {schema}.{table}\n"
                      "Write a plain-English description for each column.\n"
                      "Return JSON: {column_name: description_string}\n\n"
                      + json.dumps(col_items, indent=2))
            result = call_claude(client, prompt)
            time.sleep(API_PAUSE)
            for ck, c, h in batch:
                col_name = c["column_name"]
                if col_name in result:
                    col_cache[ck] = {"description": result[col_name], "_hash": h}
                    print(".", end="", flush=True)
                else:
                    print("?", end="", flush=True)
            print()
            save_cache(cache_path, cache)  # save after every batch
    cache["columns"] = col_cache

# ── Database connection ─────────────────────────────────────────────────────────

def get_connection(server, database):
    result = subprocess.run(
        ["az", "account", "get-access-token",
         "--resource", "https://database.windows.net",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"az login required: {result.stderr.strip()}")
    token        = result.stdout.strip()
    token_bytes  = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
    conn_str = (
        f"Driver={DRIVER};Server=tcp:{server}.database.windows.net,1433;"
        f"Database={database};Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct})

def query(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

# ── Cache helpers ───────────────────────────────────────────────────────────────

def content_hash(text):
    return hashlib.md5(text.encode()).hexdigest()[:12]

def load_cache(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ── SQL queries ────────────────────────────────────────────────────────────────

DEFS_SQL = """
SELECT s.name AS schema_name, o.name AS obj_name, o.type_desc AS obj_type,
       m.definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.type IN ('V','P','FN','TF','IF')
ORDER BY s.name, o.name
"""

TABLES_SQL = """
SELECT s.name AS schema_name, t.name AS table_name
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE s.name NOT IN ('sys','INFORMATION_SCHEMA','Audits_DBA')
ORDER BY s.name, t.name
"""

COLUMNS_SQL = """
SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name,
       tp.name AS data_type, c.is_nullable
FROM sys.columns c
JOIN sys.tables t ON t.object_id = c.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.types tp ON tp.user_type_id = c.user_type_id
WHERE s.name NOT IN ('sys','INFORMATION_SCHEMA','Audits_DBA')
ORDER BY s.name, t.name, c.column_id
"""

# ── Write output files ──────────────────────────────────────────────────────────

def write_output_files(cache):
    view_out = {k: v["description"] for k, v in cache.get("views",   {}).items()}
    proc_out = {k: v["description"] for k, v in cache.get("procs",   {}).items()}
    tbl_out  = {k: v["description"] for k, v in cache.get("tables",  {}).items()}
    col_out  = {k: v["description"] for k, v in cache.get("columns", {}).items()}
    files = {
        f"{CACHE_DIR}/view_descriptions.json":   view_out,
        f"{CACHE_DIR}/proc_descriptions.json":   proc_out,
        f"{CACHE_DIR}/table_descriptions.json":  tbl_out,
        f"{CACHE_DIR}/column_descriptions.json": col_out,
    }
    for path, data in files.items():
        save_cache(path, data)
        print(f"  Wrote {len(data):>6,} descriptions → {os.path.basename(path)}")

# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate plain-English descriptions for Synapse objects.")
    parser.add_argument("--env",   choices=["dev", "prd"], required=True)
    parser.add_argument("--type",  default="views,procs,tables,columns",
                        help="Comma-separated: views,procs,tables,columns")
    parser.add_argument("--force", action="store_true", help="Ignore cache — regenerate all")
    parser.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY"),
                        help="Anthropic API key for AI mode (omit for free rule-based mode)")
    args = parser.parse_args()

    use_ai = bool(args.api_key)
    types  = {t.strip() for t in args.type.split(",")}
    env    = ENVS[args.env]
    cache_path = f"{CACHE_DIR}/descriptions_cache_{args.env}.json"

    print(f"\n=== Synapse Description Generator — {env['label']} ===")
    print(f"  Mode     : {'AI (Claude)' if use_ai else 'Rule-based (free)'}")
    print(f"  Cache    : {cache_path}")
    print(f"  Types    : {', '.join(sorted(types))}")
    print(f"  Force    : {args.force}")
    print()

    cache = load_cache(cache_path)

    print("Connecting to Synapse…", end="", flush=True)
    try:
        conn = get_connection(env["server"], env["database"])
        print(" connected.\n")
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    defs    = query(conn, DEFS_SQL)    if ("views" in types or "procs" in types) else []
    tables  = query(conn, TABLES_SQL)  if "tables"  in types else []
    columns = query(conn, COLUMNS_SQL) if ("columns" in types or "tables" in types) else []
    conn.close()

    print(f"Fetched: {len(defs)} definitions, {len(tables)} tables, {len(columns)} columns\n")

    client = None
    if use_ai:
        import anthropic as _ant
        client = _ant.Anthropic(api_key=args.api_key)

    start = datetime.now()

    if "views" in types or "procs" in types:
        print("─ Views & Procs ─")
        if use_ai:
            generate_ai_def_descriptions(client, defs, cache, args.force, cache_path)
        else:
            generate_rule_def_descriptions(defs, cache, args.force)
        save_cache(cache_path, cache)
        print()

    if "tables" in types:
        print("─ Tables ─")
        if use_ai:
            generate_ai_table_descriptions(client, tables, columns, cache, args.force, cache_path)
        else:
            generate_rule_table_descriptions(tables, columns, cache, args.force)
        save_cache(cache_path, cache)
        print()

    if "columns" in types:
        print("─ Columns ─")
        if use_ai:
            generate_ai_column_descriptions(client, columns, cache, args.force, cache_path)
        else:
            generate_rule_column_descriptions(columns, cache, args.force)
        save_cache(cache_path, cache)
        print()

    elapsed = (datetime.now() - start).seconds
    print(f"Completed in {elapsed // 60}m {elapsed % 60}s")
    print("\nWriting output files…")
    write_output_files(cache)
    print("\nDone. Re-run the Synapse report scripts to pick up the new descriptions.")

if __name__ == "__main__":
    main()
