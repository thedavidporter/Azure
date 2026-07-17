import os
#!/usr/bin/env python3
"""
Azure DevOps Metadata Report
Collects repos, branches (with ahead/behind), build pipelines, run history,
pull requests, branch policies, variable groups, and environments for all
projects in the in-idoh-oda ADO organization.

Usage:
  python3 ado_metadata_report.py
"""

import json
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import requests

# ── config ─────────────────────────────────────────────────────────────────────

ORG           = "in-idoh-oda"
ADO_RESOURCE  = "499b84ac-1321-427f-aa17-267ca6975798"
BASE           = f"https://dev.azure.com/{ORG}"
VSRM           = f"https://vsrm.dev.azure.com/{ORG}"
VSSPS          = "https://app.vssps.visualstudio.com"
API            = "7.1"
OUT_FILE       = "/home/thedavidporter/ado_metadata_report.html"

# Branches not touched in > 180d are "stale". Active = < 60d.
ACTIVE_DAYS    = 60
STALE_DAYS     = 180
MAX_RUNS_PER_PIPELINE = 25   # recent build runs to collect per pipeline
MAX_PRS_COMPLETED     = 60   # recent completed PRs

# ── auth ───────────────────────────────────────────────────────────────────────

def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource", ADO_RESOURCE],
        capture_output=True, text=True, check=True
    )
    return json.loads(r.stdout)["accessToken"]

def hdrs(token):
    return {"Authorization": f"Bearer {token}"}

# ── REST helpers ───────────────────────────────────────────────────────────────

def get(url, token, params=None):
    p = {"api-version": API}
    if params:
        p.update(params)
    for attempt in range(4):
        try:
            r = requests.get(url, headers=hdrs(token), params=p, timeout=30)
            if r.status_code in (400, 403, 404):
                return {}
            r.raise_for_status()
            if not r.text.strip():
                return {}
            return r.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < 3:
                wait = 2 ** attempt  # 1s, 2s, 4s
                print(f"\n  [retry {attempt+1}/3 in {wait}s: {type(e).__name__}]", end="", flush=True)
                time.sleep(wait)
            else:
                raise

def get_paged(url, token, params=None):
    p = {"api-version": API}
    if params:
        p.update(params)
    results = []
    while url:
        r = requests.get(url, headers=hdrs(token), params=p)
        if r.status_code in (404, 403):
            break
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("value", []))
        url = data.get("nextLink") or data.get("continuationToken") and None
        p = {}
    return results

# ── time helpers ───────────────────────────────────────────────────────────────

def parse_dt(s):
    if not s:
        return None
    s = s.rstrip("Z") + "+00:00" if s.endswith("Z") else s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def fmt_dt(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d")

def days_ago(dt):
    if not dt:
        return None
    now = datetime.now(tz=timezone.utc)
    return (now - dt).days

def duration_str(start_s, end_s):
    s = parse_dt(start_s)
    e = parse_dt(end_s)
    if not s or not e:
        return "—"
    secs = int((e - s).total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs//60}m {secs%60}s"
    return f"{secs//3600}h {(secs%3600)//60}m"

def branch_age_class(dt):
    d = days_ago(dt)
    if d is None:
        return "age-unk"
    if d <= ACTIVE_DAYS:
        return "age-active"
    if d <= STALE_DAYS:
        return "age-recent"
    return "age-stale"

# ── data collection ─────────────────────────────────────────────────────────────

def list_projects(token):
    data = get(f"{BASE}/_apis/projects", token, {"$top": 200, "stateFilter": "wellFormed"})
    return data.get("value", [])

def list_repos(project, token):
    data = get(f"{BASE}/{project}/_apis/git/repositories", token)
    return data.get("value", [])

def get_branch_stats(repo_id, project, token):
    """Returns all branches with ahead/behind counts and last commit detail."""
    data = get(f"{BASE}/{project}/_apis/git/repositories/{repo_id}/stats/branches", token)
    return data.get("value", [])

def get_repo_last_commit(repo_id, project, branch, token):
    branch_name = branch.replace("refs/heads/", "")
    data = get(
        f"{BASE}/{project}/_apis/git/repositories/{repo_id}/commits",
        token,
        {"$top": 1, "searchCriteria.itemVersion.version": branch_name},
    )
    commits = data.get("value", [])
    return commits[0] if commits else {}

def get_build_definitions(project, token):
    data = get(
        f"{BASE}/{project}/_apis/build/definitions",
        token,
        {"$top": 500, "includeLatestBuilds": "true", "queryOrder": "definitionNameAscending"},
    )
    return data.get("value", [])

def get_build_definition_detail(def_id, project, token):
    return get(f"{BASE}/{project}/_apis/build/definitions/{def_id}", token)

def get_builds(def_id, project, token, top=25):
    data = get(
        f"{BASE}/{project}/_apis/build/builds",
        token,
        {"definitions": str(def_id), "$top": str(top), "queryOrder": "queueTimeDescending"},
    )
    return data.get("value", [])

def get_build_timeline(build_id, project, token):
    data = get(f"{BASE}/{project}/_apis/build/builds/{build_id}/timeline", token)
    return data.get("records", [])

def get_all_recent_builds(project, token, top=200):
    data = get(
        f"{BASE}/{project}/_apis/build/builds",
        token,
        {"$top": str(top), "queryOrder": "queueTimeDescending"},
    )
    return data.get("value", [])

def get_pull_requests(project, token, status="active", top=100):
    data = get(
        f"{BASE}/{project}/_apis/git/pullrequests",
        token,
        {
            "searchCriteria.status": status,
            "$top": str(top),
            "searchCriteria.includeLinks": "false",
        },
    )
    return data.get("value", [])

def get_policies(project, token):
    data = get(f"{BASE}/{project}/_apis/policy/configurations", token, {"$top": 500})
    return data.get("value", [])

def get_policy_types(project, token):
    data = get(f"{BASE}/{project}/_apis/policy/types", token)
    return {t["id"]: t["displayName"] for t in data.get("value", [])}

def get_variable_groups(project, token):
    data = get(f"{BASE}/{project}/_apis/distributedtask/variablegroups", token, {"$top": 500})
    return data.get("value", [])

def get_environments(project, token):
    data = get(f"{BASE}/{project}/_apis/distributedtask/environments", token, {"$top": 500})
    return data.get("value", [])

def get_repo_policies(project, token):
    """Return policies keyed by (repoId, refName)."""
    raw = get_policies(project, token)
    out = defaultdict(list)
    for cfg in raw:
        settings = cfg.get("settings", {})
        for scope in settings.get("scope", []):
            repo_id  = scope.get("repositoryId", "__project__")
            ref_name = scope.get("refName", "")
            out[(repo_id, ref_name)].append({
                "type":      cfg.get("type", {}).get("displayName", "?"),
                "enabled":   cfg.get("isEnabled", False),
                "blocking":  cfg.get("isBlocking", False),
                "settings":  settings,
            })
    return out

# ── process helpers ────────────────────────────────────────────────────────────

def clean_build(b, stages_map):
    build_id = b.get("id")
    defn     = b.get("definition", {})
    req      = b.get("requestedFor", {})
    return {
        "id":          build_id,
        "number":      b.get("buildNumber", ""),
        "pipeline_id": defn.get("id"),
        "pipeline":    defn.get("name", ""),
        "status":      b.get("status", ""),
        "result":      b.get("result", ""),
        "reason":      b.get("reason", ""),
        "branch":      b.get("sourceBranch", "").replace("refs/heads/", ""),
        "repo":        (b.get("repository") or {}).get("name", ""),
        "by":          req.get("displayName", ""),
        "queued":      fmt_dt(parse_dt(b.get("queueTime"))),
        "started":     fmt_dt(parse_dt(b.get("startTime"))),
        "finished":    fmt_dt(parse_dt(b.get("finishTime"))),
        "duration":    duration_str(b.get("startTime"), b.get("finishTime")),
        "stages":      stages_map.get(build_id, []),
    }

def clean_pr(pr):
    return {
        "id":        pr.get("pullRequestId"),
        "title":     pr.get("title", ""),
        "repo":      pr.get("repository", {}).get("name", ""),
        "project":   pr.get("repository", {}).get("project", {}).get("name", ""),
        "source":    pr.get("sourceRefName", "").replace("refs/heads/", ""),
        "target":    pr.get("targetRefName", "").replace("refs/heads/", ""),
        "status":    pr.get("status", ""),
        "created":   fmt_dt(parse_dt(pr.get("creationDate"))),
        "closed":    fmt_dt(parse_dt(pr.get("closedDate"))),
        "by":        pr.get("createdBy", {}).get("displayName", ""),
        "reviewers": [r.get("displayName", "") for r in pr.get("reviewers", [])],
        "vote_map":  {r.get("displayName",""): r.get("vote", 0)
                      for r in pr.get("reviewers", [])},
        "merge_status": pr.get("mergeStatus", ""),
        "is_draft":  pr.get("isDraft", False),
        "labels":    [l.get("name","") for l in pr.get("labels", [])],
    }

def clean_branch(b, repo_name, project, default_branch):
    commit    = b.get("commit", {})
    author    = commit.get("author", {})
    committer = commit.get("committer", {})
    # Use committer date — it reflects when the commit was applied to the branch
    # (author date stays frozen on rebases/cherry-picks, making branches look stale)
    dt   = parse_dt(committer.get("date") or author.get("date"))
    days = days_ago(dt)
    return {
        "name":       b.get("name", ""),
        "repo":       repo_name,
        "project":    project,
        "is_default": b.get("isBaseVersion", False),
        "ahead":      b.get("aheadCount", 0),
        "behind":     b.get("behindCount", 0),
        "last_commit_date":    fmt_dt(dt),
        "last_commit_author":  author.get("name", ""),
        "last_commit_message": commit.get("comment", "")[:80],
        "last_commit_id":      commit.get("commitId", "")[:7],
        "days_ago":   days if days is not None else 9999,
        "age_class":  branch_age_class(dt),
    }

def extract_stages(records):
    stages = []
    for r in records:
        if r.get("type") in ("Stage", "Job") and r.get("type") == "Stage":
            stages.append({
                "name":   r.get("name", ""),
                "result": r.get("result", ""),
                "state":  r.get("state", ""),
            })
    # Fall back to Job level if no stages
    if not stages:
        for r in records:
            if r.get("type") == "Job" and r.get("name") != "Finalize build":
                stages.append({
                    "name":   r.get("name", ""),
                    "result": r.get("result", ""),
                    "state":  r.get("state", ""),
                })
    return stages

def clean_pipeline_def(d, detail):
    proc  = detail.get("process") or d.get("process") or {}
    repo  = detail.get("repository") or d.get("repository") or {}
    lb    = d.get("latestBuild") or {}
    lc    = d.get("latestCompletedBuild") or {}

    # collect triggers
    trig_types = list({t.get("triggerType", "") for t in
                       (detail.get("triggers") or d.get("triggers") or [])})

    # variable groups referenced
    vg_ids = [vg.get("id") for vg in (detail.get("variableGroups") or [])]

    # environments from YAML stages (not always resolvable — best effort)
    return {
        "id":           d.get("id"),
        "name":         d.get("name", ""),
        "path":         d.get("path", "\\"),
        "quality":      d.get("quality", "definition"),
        "type":         "YAML" if proc.get("type") == 2 else "Classic",
        "yaml_file":    proc.get("yamlFilename", ""),
        "repo":         repo.get("name", "") or (repo.get("properties") or {}).get("fullName", ""),
        "default_branch": repo.get("defaultBranch", "").replace("refs/heads/", ""),
        "queue":        (detail.get("queue") or d.get("queue") or {}).get("name", ""),
        "triggers":     trig_types,
        "variable_groups": vg_ids,
        "created":      fmt_dt(parse_dt(d.get("createdDate"))),
        "latest_build_id":     lb.get("id"),
        "latest_build_number": lb.get("buildNumber", ""),
        "latest_status":       lb.get("status", ""),
        "latest_result":       lb.get("result", ""),
        "latest_by":           (lb.get("requestedFor") or {}).get("displayName", ""),
        "latest_queued":       fmt_dt(parse_dt(lb.get("queueTime"))),
        "latest_duration":     duration_str(lb.get("startTime"), lb.get("finishTime")),
        "last_success":        fmt_dt(parse_dt(lc.get("queueTime"))),
    }

def clean_vg(vg):
    # Only expose variable names, not values
    var_names = list((vg.get("variables") or {}).keys())
    return {
        "id":        vg.get("id"),
        "name":      vg.get("name", ""),
        "type":      vg.get("type", ""),
        "description": vg.get("description", ""),
        "var_count": len(var_names),
        "var_names": var_names,
        "modified":  fmt_dt(parse_dt(vg.get("modifiedOn"))),
    }

def clean_env(e):
    return {
        "id":          e.get("id"),
        "name":        e.get("name", ""),
        "description": e.get("description", ""),
        "created":     fmt_dt(parse_dt(e.get("createdOn"))),
        "modified":    fmt_dt(parse_dt(e.get("lastModifiedOn"))),
    }

def clean_policy(cfg, repo_map):
    settings = cfg.get("settings", {})
    scopes = settings.get("scope", [])
    resolved_scopes = []
    for s in scopes:
        rid = s.get("repositoryId")
        resolved_scopes.append({
            "repo":     repo_map.get(rid, rid or "All Repos"),
            "ref":      s.get("refName", "").replace("refs/heads/", "") or "(any)",
            "match":    s.get("matchKind", "exact"),
        })

    # Reviewer policy specifics — field is a list of ID strings or dicts
    raw_req = settings.get("requiredReviewerIds", [])
    req_reviewers = []
    for r in raw_req:
        if isinstance(r, dict):
            req_reviewers.append(r.get("displayName", "") or r.get("id", ""))
        else:
            req_reviewers.append(str(r))

    return {
        "id":             cfg.get("id"),
        "type":           cfg.get("type", {}).get("displayName", "?"),
        "enabled":        cfg.get("isEnabled", False),
        "blocking":       cfg.get("isBlocking", False),
        "scopes":         resolved_scopes,
        "min_reviewers":  settings.get("minimumApproverCount"),
        "req_reviewers":  req_reviewers,
        "allow_requestor_approval": settings.get("allowDownvotes", False),
        "reset_on_push":  settings.get("resetOnSourcePush", False),
        "comment_req":    settings.get("requireVoteOnLastIteration", False),
    }

# ── collect everything ─────────────────────────────────────────────────────────

def collect(token):
    print(f"\n=== Azure DevOps Metadata — {ORG} ===")

    # projects
    print("Fetching projects…")
    projects = list_projects(token)
    print(f"  {len(projects)} projects: {[p['name'] for p in projects]}")

    all_repos     = []
    all_branches  = []
    all_pipelines = []
    all_runs      = []
    all_prs_active    = []
    all_prs_completed = []
    all_policies  = []
    all_vgs       = []
    all_envs      = []

    for proj in projects:
        pname = proj["name"]
        print(f"\n--- Project: {pname} ---")

        # ── repos ──────────────────────────────────────────────────────────────
        print("  Fetching repos…", end="", flush=True)
        repos = list_repos(pname, token)
        print(f" {len(repos)}")

        # Build a repoId → name map for policy resolution
        repo_id_map = {r["id"]: r["name"] for r in repos}

        # ── branch stats (parallel) ────────────────────────────────────────────
        print(f"  Fetching branch stats for {len(repos)} repos (parallel)…")

        def fetch_repo_branches(repo):
            rid     = repo["id"]
            rname   = repo["name"]
            defbr   = repo.get("defaultBranch", "refs/heads/main")
            defname = defbr.replace("refs/heads/", "")
            stats   = get_branch_stats(rid, pname, token)
            branches_out = []
            for b in stats:
                cb = clean_branch(b, rname, pname, defname)
                branches_out.append(cb)
            return repo, branches_out

        repo_branch_map = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(fetch_repo_branches, r): r for r in repos}
            done = 0
            for fut in as_completed(futs):
                repo, branches = fut.result()
                repo_branch_map[repo["id"]] = branches
                done += 1
                if done % 5 == 0 or done == len(repos):
                    print(f"    {done}/{len(repos)} repos…", end="\r", flush=True)
        print()

        # Assemble repo records
        for r in repos:
            rid   = r["id"]
            branches_for_repo = repo_branch_map.get(rid, [])
            defbr = r.get("defaultBranch", "refs/heads/main").replace("refs/heads/", "")

            # find last commit on default branch
            def_branch = next((b for b in branches_for_repo if b["name"] == defbr), None)
            all_repos.append({
                "id":             rid,
                "name":           r["name"],
                "project":        pname,
                "url":            r.get("remoteUrl", ""),
                "default_branch": defbr,
                "size_kb":        r.get("size", 0) // 1024,
                "branch_count":   len(branches_for_repo),
                "active_branches": sum(1 for b in branches_for_repo
                                       if b["age_class"] == "age-active"),
                "last_commit_date":   def_branch["last_commit_date"] if def_branch else "",
                "last_commit_author": def_branch["last_commit_author"] if def_branch else "",
                "last_commit_message": def_branch["last_commit_message"] if def_branch else "",
                "description":    r.get("description", ""),
            })
            all_branches.extend(branches_for_repo)

        # ── build pipelines ────────────────────────────────────────────────────
        print("  Fetching build pipelines…", end="", flush=True)
        defs = get_build_definitions(pname, token)
        print(f" {len(defs)}")

        if defs:
            print(f"  Fetching pipeline details + runs ({len(defs)} pipelines)…")

            def fetch_pipeline(d):
                def_id  = d["id"]
                detail  = get_build_definition_detail(def_id, pname, token)
                pipe    = clean_pipeline_def(d, detail)
                runs_raw = get_builds(def_id, pname, token, MAX_RUNS_PER_PIPELINE)

                # Get timeline for the latest completed build (stages/jobs)
                runs_out = []
                for i, b in enumerate(runs_raw):
                    stages = []
                    if i == 0:  # only fetch timeline for most recent run
                        tl = get_build_timeline(b["id"], pname, token)
                        stages = extract_stages(tl)
                    runs_out.append(clean_build(b, {b["id"]: stages}))

                return pipe, runs_out

            with ThreadPoolExecutor(max_workers=6) as ex:
                futs = [ex.submit(fetch_pipeline, d) for d in defs]
                done = 0
                for fut in as_completed(futs):
                    pipe, runs = fut.result()
                    all_pipelines.append(pipe)
                    all_runs.extend(runs)
                    done += 1
                    print(f"    {done}/{len(defs)} pipelines…", end="\r", flush=True)
            print()

        # ── pull requests ──────────────────────────────────────────────────────
        print("  Fetching active PRs…", end="", flush=True)
        active_prs = get_pull_requests(pname, token, "active", 200)
        print(f" {len(active_prs)}")
        all_prs_active.extend(clean_pr(pr) for pr in active_prs)

        print("  Fetching recently completed PRs…", end="", flush=True)
        comp_prs = get_pull_requests(pname, token, "completed", MAX_PRS_COMPLETED)
        print(f" {len(comp_prs)}")
        all_prs_completed.extend(clean_pr(pr) for pr in comp_prs)

        # ── policies ───────────────────────────────────────────────────────────
        print("  Fetching branch policies…", end="", flush=True)
        raw_policies = get_policies(pname, token)
        print(f" {len(raw_policies)}")
        for cfg in raw_policies:
            all_policies.append(clean_policy(cfg, repo_id_map))

        # ── variable groups ────────────────────────────────────────────────────
        print("  Fetching variable groups…", end="", flush=True)
        vgs = get_variable_groups(pname, token)
        print(f" {len(vgs)}")
        for vg in vgs:
            v = clean_vg(vg)
            v["project"] = pname
            all_vgs.append(v)

        # ── environments ───────────────────────────────────────────────────────
        print("  Fetching environments…", end="", flush=True)
        envs = get_environments(pname, token)
        print(f" {len(envs)}")
        for e in envs:
            ev = clean_env(e)
            ev["project"] = pname
            all_envs.append(ev)

    return {
        "org":        ORG,
        "projects":   [p["name"] for p in projects],
        "repos":      all_repos,
        "branches":   all_branches,
        "pipelines":  all_pipelines,
        "runs":       all_runs,
        "prs_active": all_prs_active,
        "prs_completed": all_prs_completed,
        "policies":   all_policies,
        "variable_groups": all_vgs,
        "environments": all_envs,
    }

# ── HTML / CSS / JS ────────────────────────────────────────────────────────────

CSS = """
:root{
  --bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;
  --sb:240px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--txt);font:13px/1.5 'Segoe UI',system-ui,sans-serif}

/* ── LAYOUT ── */
.layout{display:flex;height:100vh}
.sidebar{width:var(--sb);min-width:180px;background:var(--sur);border-right:1px solid var(--brd);
  display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.main{flex:1;overflow:hidden;display:flex;flex-direction:column;min-width:0}

/* ── SIDEBAR ── */
.sb-hdr{padding:12px 14px;border-bottom:1px solid var(--brd);font-weight:700;
  font-size:14px;flex-shrink:0}
.sb-hdr small{display:block;color:var(--mut);font-weight:400;font-size:10px;margin-top:2px}
.sb-search{padding:8px 10px;border-bottom:1px solid var(--brd);flex-shrink:0}
.sb-search input{width:100%;padding:5px 8px;background:var(--sur2);border:1px solid var(--brd);
  border-radius:5px;color:var(--txt);font-size:11px;outline:none}
.sb-search input:focus{border-color:var(--acc)}
.sb-body{overflow-y:auto;flex:1;padding:6px 0}
.sb-section{font-size:10px;font-weight:700;color:var(--mut);padding:8px 14px 3px;
  text-transform:uppercase;letter-spacing:.05em}
.sb-item{padding:5px 14px;font-size:11px;cursor:pointer;display:flex;align-items:center;
  gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sb-item:hover{background:var(--sur2)}
.sb-item.active{background:var(--sur2);border-left:3px solid var(--acc);
  padding-left:11px;color:var(--txt)}
.sb-item .sb-badge{margin-left:auto;flex-shrink:0;font-size:9px;
  background:var(--sur2);border:1px solid var(--brd);border-radius:3px;
  padding:1px 4px;color:var(--mut)}
.sb-item.active .sb-badge{border-color:var(--acc)}

/* ── MAIN HEADER ── */
.main-hdr{padding:16px 24px 0;flex-shrink:0}
h1{font-size:19px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:11px;margin-bottom:12px}

/* ── STAT CARDS ── */
.stats{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}
.sc{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:10px 14px;min-width:95px;cursor:pointer;transition:border-color .15s}
.sc:hover{border-color:var(--acc)}
.sc.active-card{border-color:var(--acc);background:var(--sur2)}
.sc-n{font-size:20px;font-weight:700;line-height:1.1}
.sc-l{font-size:10px;color:var(--mut);margin-top:2px}

/* ── TABS ── */
.tabs{display:flex;gap:2px;border-bottom:2px solid var(--brd);padding:0 24px;
  flex-shrink:0;flex-wrap:wrap;background:var(--bg)}
.tab{padding:6px 12px;cursor:pointer;border-radius:6px 6px 0 0;font-size:12px;
  font-weight:600;color:var(--mut);border:1px solid transparent;
  border-bottom:none;margin-bottom:-2px;user-select:none;white-space:nowrap}
.tab:hover{color:var(--txt);background:var(--sur)}
.tab.active{background:var(--sur);border-color:var(--brd);
  border-bottom-color:var(--sur);color:var(--txt)}

/* ── CONTENT ── */
.content{flex:1;overflow-y:auto;padding:14px 24px}
.panel{display:none}.panel.active{display:block}

/* ── SEARCH / FILTER ROW ── */
.filter-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.filter-row input,.filter-row select{
  padding:5px 10px;background:var(--sur);border:1px solid var(--brd);
  border-radius:5px;color:var(--txt);font-size:12px;outline:none}
.filter-row input:focus,.filter-row select:focus{border-color:var(--acc)}
.filter-row input{width:240px}
.filter-row select{min-width:140px;max-width:220px}
.filter-row label{font-size:11px;color:var(--mut);display:flex;align-items:center;gap:4px;
  cursor:pointer;user-select:none}
.filter-row label input[type=checkbox]{cursor:pointer;accent-color:var(--acc)}

/* ── TABLES ── */
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:var(--sur2);padding:6px 10px;text-align:left;font-weight:700;
  border-bottom:2px solid var(--brd);white-space:nowrap;position:sticky;top:0;z-index:2}
td{padding:5px 10px;border-bottom:1px solid var(--brd);vertical-align:middle}
tr:hover td{background:var(--sur)}
.hidden{display:none!important}
.mono{font-family:monospace;font-size:11px;color:var(--mut)}
.mut{color:var(--mut);font-size:11px}
.trunc{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.clickrow{cursor:pointer}
.clickrow:hover td{background:#1e2535}
th.sortable{cursor:pointer;user-select:none}
th.sortable:hover{color:var(--acc)}
th.sort-asc::after{content:" ▲";font-size:9px}
th.sort-desc::after{content:" ▼";font-size:9px}

/* ── CHIPS / STATUS ── */
.chip{display:inline-block;font-size:10px;padding:2px 7px;border-radius:3px;
  font-weight:700;white-space:nowrap}
.chip-ok  {background:#1a3a2a;color:#4ade80}
.chip-fail{background:#3a1a1a;color:#f87171}
.chip-warn{background:#3a2a0a;color:#fbbf24}
.chip-run {background:#1a2050;color:#6c8eff}
.chip-can {background:#2a2030;color:#c084fc}
.chip-skip{background:var(--sur2);color:var(--mut)}
.chip-yaml{background:#0d2a3a;color:#22d3ee;border:1px solid #1a3a4a}
.chip-cls {background:#2a1a3a;color:#c084fc;border:1px solid #3a2a4a}
.chip-draft{background:var(--sur2);color:var(--mut);border:1px solid var(--brd)}
.chip-en  {background:#1a3a2a;color:#4ade80}
.chip-dis {background:#2a2a2a;color:var(--mut)}
.chip-block{background:#3a1a1a;color:#f87171}
.chip-advise{background:#1e2a4a;color:#6c8eff}

/* ── BRANCH AGE ── */
.age-active {color:var(--grn);font-size:11px}
.age-recent {color:var(--yel);font-size:11px}
.age-stale  {color:var(--red);font-size:11px}
.age-unk    {color:var(--mut);font-size:11px}

/* ── AHEAD/BEHIND ── */
.ab{font-family:monospace;font-size:11px}
.ab-a{color:var(--acc)}
.ab-b-hi{color:var(--red)}
.ab-b-md{color:var(--yel)}
.ab-b-lo{color:var(--grn)}

/* ── RESULT DOTS (pipeline sparkline) ── */
.sparks{display:flex;gap:3px;flex-wrap:wrap}
.dot{width:10px;height:10px;border-radius:2px;display:inline-block;flex-shrink:0}
.dot-ok   {background:var(--grn)}
.dot-fail {background:var(--red)}
.dot-warn {background:var(--yel)}
.dot-run  {background:var(--acc)}
.dot-can  {background:var(--pur)}
.dot-unk  {background:var(--brd)}

/* ── OVERVIEW CARDS ── */
.ov-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-bottom:18px}
.ov-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:11px 13px;cursor:pointer;transition:border-color .15s}
.ov-card:hover{border-color:var(--acc)}
.ov-card h3{font-size:12px;color:var(--acc);margin-bottom:5px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ov-card .kv{font-size:11px;color:var(--mut);line-height:1.6}
.ov-card .kv b{color:var(--txt)}
h2{font-size:13px;font-weight:700;margin:14px 0 8px;
  padding-bottom:4px;border-bottom:1px solid var(--brd)}
.proj-badge{font-size:10px;padding:2px 7px;border-radius:3px;font-weight:700;
  background:#1e2a4a;color:var(--acc)}

/* ── PIPELINE CARD (detail) ── */
.pipe-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:9px}
.pipe-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:12px 14px;cursor:pointer;transition:border-color .15s}
.pipe-card:hover{border-color:var(--acc)}
.pipe-card.result-succeeded{border-left:3px solid var(--grn)}
.pipe-card.result-failed   {border-left:3px solid var(--red)}
.pipe-card.result-canceled {border-left:3px solid var(--pur)}
.pipe-card.result-none     {border-left:3px solid var(--brd)}
.pipe-card h3{font-size:12px;font-weight:700;margin-bottom:6px}
.pipe-meta{font-size:11px;color:var(--mut);display:flex;flex-direction:column;gap:3px}
.pipe-meta strong{color:var(--txt)}
.pipe-foot{margin-top:8px;display:flex;align-items:center;gap:8px}
.stages-row{display:flex;gap:4px;flex-wrap:wrap;margin-top:5px}
.stage-chip{font-size:10px;padding:2px 7px;border-radius:3px;font-weight:600}
.stage-ok   {background:#1a3a2a;color:#4ade80}
.stage-fail {background:#3a1a1a;color:#f87171}
.stage-can  {background:#2a2030;color:#c084fc}
.stage-run  {background:#1a2050;color:#6c8eff}
.stage-skip {background:var(--sur2);color:var(--mut)}

/* ── PR CARDS ── */
.pr-card{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  margin-bottom:8px;padding:11px 14px}
.pr-title{font-size:13px;font-weight:700;margin-bottom:4px}
.pr-meta{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:var(--mut)}
.pr-meta b{color:var(--txt)}
.reviewers{margin-top:6px;display:flex;gap:5px;flex-wrap:wrap}
.reviewer{font-size:10px;padding:2px 7px;border-radius:3px;border:1px solid var(--brd);
  background:var(--sur2)}
.reviewer.vote-10 {background:#1a3a2a;color:#4ade80;border-color:#2a4a3a}
.reviewer.vote-5  {background:#1e2a4a;color:#6c8eff;border-color:#2a3a5a}
.reviewer.vote--5 {background:#3a2a0a;color:#fbbf24;border-color:#4a3a1a}
.reviewer.vote--10{background:#3a1a1a;color:#f87171;border-color:#4a2a2a}

/* ── MODAL ── */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;
  align-items:center;justify-content:center}
.modal-overlay.open{display:flex}
.modal{background:var(--sur);border:1px solid var(--brd);border-radius:10px;
  width:min(860px,95vw);max-height:88vh;overflow-y:auto;padding:22px 26px}
.modal h2{font-size:15px;font-weight:700;margin-bottom:14px;
  display:flex;justify-content:space-between;align-items:center}
.modal-close{cursor:pointer;color:var(--mut);font-size:18px;line-height:1;
  padding:4px 8px;border-radius:4px;background:var(--sur2)}
.modal-close:hover{color:var(--txt)}
.kv-table{width:100%;font-size:12px;border-collapse:collapse}
.kv-table td{padding:5px 10px;border-bottom:1px solid var(--brd)}
.kv-table td:first-child{color:var(--mut);width:160px;white-space:nowrap;font-weight:700}
.section-label{font-size:11px;font-weight:700;color:var(--mut);margin:12px 0 6px;
  text-transform:uppercase;letter-spacing:.05em}

/* ── VIRTUAL SCROLL ── */
.vs-sentinel{height:1px;width:100%}
.page-info{font-size:11px;color:var(--mut);margin-bottom:6px}
"""

JS = r"""
// ── utility ───────────────────────────────────────────────────────────────────
function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── tab / card switching ──────────────────────────────────────────────────────
function showTab(id){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.sc').forEach(c=>c.classList.remove('active-card'));
  const pan=document.getElementById('p-'+id);
  const tab=document.getElementById('tab-'+id);
  const card=document.getElementById('card-'+id);
  if(pan) pan.classList.add('active');
  if(tab) tab.classList.add('active');
  if(card) card.classList.add('active-card');
  if(!panelInited[id]){ panelInited[id]=true; renderPanel(id); }
}
const panelInited={};

function showActiveBranches(){
  document.getElementById('branch-active-only').checked=true;
  document.getElementById('branch-stale-only').checked=false;
  showTab('branches');
  applyBranchFilters();
}
function showStaleBranches(){
  document.getElementById('branch-stale-only').checked=true;
  document.getElementById('branch-active-only').checked=false;
  showTab('branches');
  applyBranchFilters();
}

// ── sidebar filter ────────────────────────────────────────────────────────────
let sidebarFilter='__all__';
let sidebarProject='__all__';
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('sb-search').addEventListener('input', filterSidebar);
});
function filterSidebar(){
  const q=(document.getElementById('sb-search').value||'').toLowerCase();
  document.querySelectorAll('.sb-item[data-repo]').forEach(el=>{
    el.classList.toggle('hidden', !!q && !el.dataset.repo.toLowerCase().includes(q));
  });
}
function sbSelect(type, val, el){
  document.querySelectorAll('.sb-item').forEach(e=>e.classList.remove('active'));
  if(el) el.classList.add('active');
  if(type==='all'){  sidebarFilter='__all__'; sidebarProject='__all__'; }
  if(type==='proj'){ sidebarFilter='__all__'; sidebarProject=val; }
  if(type==='repo'){ sidebarFilter=val; sidebarProject='__all__'; }
  // Re-render the current active panel
  const active=document.querySelector('.panel.active');
  if(active){ const id=active.id.replace('p-',''); renderPanel(id); }
}

// ── result → classes ─────────────────────────────────────────────────────────
function resultChip(r,s){
  if(s==='inProgress'||s==='running') return `<span class="chip chip-run">Running</span>`;
  const m={succeeded:'chip chip-ok',failed:'chip chip-fail',
           canceled:'chip chip-can',partiallySucceeded:'chip chip-warn'};
  return `<span class="${m[r]||'chip chip-skip'}">${esc(r||s||'—')}</span>`;
}
function dot(r,s){
  if(s==='inProgress') return 'dot-run';
  const m={succeeded:'dot-ok',failed:'dot-fail',canceled:'dot-can',partiallySucceeded:'dot-warn'};
  return m[r]||'dot-unk';
}
function stageChip(stage){
  const cl={succeeded:'stage-ok',failed:'stage-fail',canceled:'stage-can',
            inProgress:'stage-run'}[stage.result||stage.state]||'stage-skip';
  return `<span class="stage-chip ${cl}">${esc(stage.name)}</span>`;
}
function ageChip(cls,daysAgo,date){
  const label=daysAgo>=9999?'no commits':daysAgo===0?'today':daysAgo+'d ago';
  return `<span class="${esc(cls)}" title="${esc(date)}">${label}</span>`;
}
function abChip(ahead,behind){
  let ac='ab-a', bc='ab-b-lo';
  if(behind>200) bc='ab-b-hi';
  else if(behind>50) bc='ab-b-md';
  return `<span class="ab"><span class="${ac}">↑${ahead}</span> <span class="${bc}">↓${behind}</span></span>`;
}
function voteClass(v){ if(v>=10) return 'vote-10'; if(v>=5) return 'vote-5'; if(v<=-10) return 'vote--10'; if(v<0) return 'vote--5'; return ''; }

// ── filtering helpers ─────────────────────────────────────────────────────────
function matchSidebar(item){
  if(sidebarFilter!=='__all__') return item.repo===sidebarFilter || item.name===sidebarFilter;
  if(sidebarProject!=='__all__') return item.project===sidebarProject;
  return true;
}
function matchRepo(item){ return matchSidebar(item); }

// ── virtual scroll ────────────────────────────────────────────────────────────
const PAGE=400;
function mkVS(tableId, data, rowFn, emptyMsg){
  let offset=0, filtered=data;
  const tbody=document.getElementById(tableId);
  if(!tbody){ return { setData(d){ filtered=d; offset=0; render(); } }; }
  const sentinel=document.getElementById(tableId+'-sentinel');
  const info=document.getElementById(tableId+'-info');
  function render(){
    const slice=filtered.slice(0,offset+PAGE);
    tbody.innerHTML=slice.map(rowFn).join('')||
      `<tr><td colspan="99" class="mut" style="padding:12px">${emptyMsg||'No results.'}</td></tr>`;
    if(info) info.textContent=`Showing ${Math.min(slice.length,filtered.length)} of ${filtered.length}`;
  }
  function loadMore(entries){
    if(entries[0]?.isIntersecting && offset+PAGE<filtered.length){
      offset+=PAGE; render();
    }
  }
  if(sentinel){ const obs=new IntersectionObserver(loadMore,{rootMargin:'200px'});
    obs.observe(sentinel); }
  render();
  return {
    setData(d){ filtered=d; offset=0; render(); },
    refresh(){ render(); }
  };
}

// ── panel renderers ───────────────────────────────────────────────────────────
const vs={};

function renderPanel(id){
  if(id==='overview')  renderOverview();
  if(id==='repos')     renderRepos();
  if(id==='branches')  renderBranches();
  if(id==='pipelines') renderPipelines();
  if(id==='runs')      renderRuns();
  if(id==='prs')       renderPRs();
  if(id==='policies')  renderPolicies();
  if(id==='devops')    renderDevOps();
}

// ── OVERVIEW ──────────────────────────────────────────────────────────────────
function renderOverview(){
  const repos=DATA.repos.filter(matchRepo);
  const pipes=DATA.pipelines;
  const prs=DATA.prs_active;
  const allBranches=DATA.branches.filter(matchRepo);

  let recentFail=0, recentOK=0;
  DATA.runs.slice(0,100).forEach(r=>{ if(r.result==='failed') recentFail++; else if(r.result==='succeeded') recentOK++; });

  // project summary
  const projects=[...new Set(DATA.repos.map(r=>r.project))];
  let projCards=projects.map(p=>{
    const pr=DATA.repos.filter(r=>r.project===p);
    const br=DATA.branches.filter(b=>b.project===p);
    const activeBr=br.filter(b=>b.age_class==='age-active').length;
    const pp=DATA.pipelines;
    const openPR=DATA.prs_active.filter(pr2=>pr2.project===p).length;
    return `<div class="ov-card" onclick="sbSelect('proj','${esc(p)}',null)">
      <h3>📁 ${esc(p)}</h3>
      <div class="kv">
        <div>Repos: <b>${pr.length}</b></div>
        <div>Branches: <b>${br.length}</b> &nbsp;·&nbsp; Active: <b style="color:var(--grn)">${activeBr}</b></div>
        <div>Open PRs: <b style="color:var(--yel)">${openPR}</b></div>
      </div></div>`;
  }).join('');

  // top active repos
  const sortedRepos=[...repos].sort((a,b)=>b.active_branches-a.active_branches).slice(0,12);
  let repoCards=sortedRepos.map(r=>`
    <div class="ov-card" onclick="sbSelect('repo','${esc(r.name)}',null);showTab('repos')">
      <h3>📦 ${esc(r.name)}</h3>
      <div class="kv">
        <div>Last commit: <b>${esc(r.last_commit_date)||'—'}</b></div>
        <div>Branches: <b>${r.branch_count}</b> &nbsp;·&nbsp; Active: <b style="color:var(--grn)">${r.active_branches}</b></div>
        <div class="trunc" title="${esc(r.last_commit_message)}" style="color:var(--mut)">
          ${esc(r.last_commit_message)||'—'}
        </div>
      </div></div>`).join('');

  // pipeline health
  const pipeHealth=pipes.map(p=>{
    const cls='pipe-card result-'+(p.latest_result||'none');
    const myRuns=DATA.runs.filter(r=>r.pipeline_id===p.id).slice(0,10);
    const sparks=myRuns.map(r=>`<span class="dot ${dot(r.result,r.status)}" title="${r.result} ${r.queued}"></span>`).join('');
    return `<div class="${cls}" onclick="openPipeModal(${p.id})">
      <h3>${esc(p.name)}</h3>
      <div class="pipe-meta">
        <span>${p.type==='YAML'?'<span class="chip chip-yaml">YAML</span>':'<span class="chip chip-cls">Classic</span>'}
          ${p.quality==='draft'?'<span class="chip chip-draft">Draft</span>':''}</span>
        <span>Repo: <strong>${esc(p.repo)||'—'}</strong></span>
        ${p.yaml_file?`<span class="mono">${esc(p.yaml_file)}</span>`:''}
        <span>Latest: ${resultChip(p.latest_result,p.latest_status)}
          <span class="mut">${esc(p.latest_queued)||'—'} &nbsp;${esc(p.latest_duration)}</span></span>
        ${p.stages&&p.stages.length?`<div class="stages-row">${p.stages.map(stageChip).join('')}</div>`:''}
      </div>
      <div class="pipe-foot"><div class="sparks">${sparks}</div></div>
    </div>`;
  }).join('');

  document.getElementById('ov-projects').innerHTML=projCards;
  document.getElementById('ov-repos').innerHTML=repoCards;
  document.getElementById('ov-pipes').innerHTML=pipeHealth;
}

// ── REPOS ─────────────────────────────────────────────────────────────────────
function renderRepos(){
  const data=DATA.repos.filter(matchRepo);
  const open=DATA.prs_active;
  function openPRCount(name){ return open.filter(p=>p.repo===name).length; }
  function row(r){
    const prCnt=openPRCount(r.name);
    const da=r.last_commit_date?(Math.floor((Date.now()-new Date(r.last_commit_date))/(864e5))):'';
    const ac=!da?'age-unk':da<=60?'age-active':da<=180?'age-recent':'age-stale';
    return `<tr class="clickrow" onclick="openRepoModal('${esc(r.name)}','${esc(r.project)}')">
      <td><b>${esc(r.name)}</b></td>
      <td><span class="proj-badge">${esc(r.project)}</span></td>
      <td class="mono">${esc(r.default_branch)}</td>
      <td class="mut" style="text-align:right">${r.branch_count}</td>
      <td style="text-align:right"><span style="color:var(--grn)">${r.active_branches}</span></td>
      <td class="${ac}">${da!==''?da+'d ago':'—'}</td>
      <td class="mono mut trunc" style="max-width:200px">${esc(r.last_commit_author)}</td>
      <td class="trunc" style="max-width:220px;color:var(--mut)">${esc(r.last_commit_message)}</td>
      <td style="text-align:right">${prCnt>0?`<span style="color:var(--yel);font-weight:700">${prCnt}</span>`:'<span class="mut">0</span>'}</td>
      <td class="mut" style="text-align:right">${r.size_kb>1024?(r.size_kb/1024).toFixed(1)+' MB':r.size_kb+' KB'}</td>
    </tr>`;
  }
  if(!vs.repos) vs.repos=mkVS('repos-tbody',data,row,'No repos.');
  else vs.repos.setData(data);

  // search
  document.getElementById('repos-search').oninput=function(){
    const q=this.value.toLowerCase();
    vs.repos.setData(data.filter(r=>!q||r.name.toLowerCase().includes(q)||r.project.toLowerCase().includes(q)));
  };
}

// ── BRANCHES ─────────────────────────────────────────────────────────────────
let branchData=[];
function renderBranches(){
  branchData=DATA.branches.filter(matchRepo);
  applyBranchFilters();

  // populate repo dropdown
  const repos=[...new Set(DATA.branches.filter(matchRepo).map(b=>b.repo))].sort();
  const sel=document.getElementById('branch-repo-sel');
  if(sel.options.length<=1){
    repos.forEach(r=>{ const o=document.createElement('option'); o.value=r; o.text=r; sel.add(o); });
  }
  document.getElementById('branch-search').oninput=applyBranchFilters;
  document.getElementById('branch-repo-sel').onchange=applyBranchFilters;
  document.getElementById('branch-active-only').onchange=applyBranchFilters;
  document.getElementById('branch-stale-only').onchange=applyBranchFilters;

  function row(b){
    return `<tr>
      <td><b>${esc(b.name)}</b>${b.is_default?'<span class="chip chip-en" style="margin-left:6px;font-size:9px">DEFAULT</span>':''}</td>
      <td class="mut" style="font-size:10px">${esc(b.repo)}</td>
      <td class="mut" style="font-size:10px">${esc(b.project)}</td>
      <td>${abChip(b.ahead,b.behind)}</td>
      <td>${ageChip(b.age_class,b.days_ago,b.last_commit_date)}</td>
      <td class="mono mut" style="font-size:10px">${esc(b.last_commit_author)}</td>
      <td class="trunc mut" style="max-width:240px;font-size:11px">${esc(b.last_commit_message)}</td>
      <td class="mono mut" style="font-size:10px">${esc(b.last_commit_id)}</td>
    </tr>`;
  }
  if(!vs.branches) vs.branches=mkVS('branch-tbody',branchData,row,'No branches.');
  else vs.branches.setData(branchData);
}
function applyBranchFilters(){
  const q=(document.getElementById('branch-search')?.value||'').toLowerCase();
  const rep=(document.getElementById('branch-repo-sel')?.value)||'';
  const activeOnly=document.getElementById('branch-active-only')?.checked;
  const staleOnly=document.getElementById('branch-stale-only')?.checked;
  let d=DATA.branches.filter(matchRepo);
  if(q) d=d.filter(b=>b.name.toLowerCase().includes(q)||b.last_commit_author.toLowerCase().includes(q));
  if(rep) d=d.filter(b=>b.repo===rep);
  if(activeOnly) d=d.filter(b=>b.age_class==='age-active');
  if(staleOnly)  d=d.filter(b=>b.age_class==='age-stale');
  if(vs.branches) vs.branches.setData(d);
}

// ── PIPELINES ─────────────────────────────────────────────────────────────────
function renderPipelines(){
  const pipes=DATA.pipelines;
  let html=pipes.map(p=>{
    const myRuns=DATA.runs.filter(r=>r.pipeline_id===p.id).slice(0,12);
    const sparks=myRuns.map(r=>`<span class="dot ${dot(r.result,r.status)}" title="${esc(r.result||r.status)} ${esc(r.queued)}"></span>`).join('');
    const cls='pipe-card result-'+(p.latest_result||'none');
    const trig=p.triggers.length?p.triggers.map(t=>`<span class="chip chip-skip" style="font-size:9px">${esc(t)}</span>`).join(' '):'<span class="mut">—</span>';
    const vgNames=p.variable_groups.map(id=>{
      const g=DATA.variable_groups.find(v=>v.id===id);
      return g?g.name:String(id);
    });
    return `<div class="${cls}" onclick="openPipeModal(${p.id})">
      <h3>${esc(p.name)}</h3>
      <div class="pipe-meta">
        <span>${p.type==='YAML'?'<span class="chip chip-yaml">YAML</span>':'<span class="chip chip-cls">Classic</span>'}
          ${p.quality==='draft'?'<span class="chip chip-draft">Draft</span>':''}
          ${p.path!=='\\'&&p.path?`<span class="mono" style="font-size:10px">${esc(p.path)}</span>`:''}
        </span>
        <span>Repo: <strong>${esc(p.repo)||'—'}</strong>  Branch: <strong>${esc(p.default_branch)||'—'}</strong></span>
        ${p.yaml_file?`<span class="mono">${esc(p.yaml_file)}</span>`:''}
        <span>Triggers: ${trig}</span>
        ${vgNames.length?`<span>Var Groups: <strong>${vgNames.map(esc).join(', ')}</strong></span>`:''}
        <span>Latest: ${resultChip(p.latest_result,p.latest_status)}
          <span class="mut">${esc(p.latest_queued)||'—'} &nbsp; dur: ${esc(p.latest_duration)}</span></span>
        ${p.stages&&p.stages.length?`<div class="stages-row">${p.stages.map(stageChip).join('')}</div>`:''}
        <span>Created: <strong>${esc(p.created)||'—'}</strong></span>
      </div>
      <div class="pipe-foot" style="margin-top:8px">
        <div class="sparks" title="Last ${myRuns.length} runs (newest→oldest)">${sparks}</div>
        <span class="mut" style="font-size:10px;margin-left:6px">${myRuns.length} runs</span>
      </div>
    </div>`;
  }).join('');
  document.getElementById('pipe-container').innerHTML=html||'<p class="mut">No pipelines found.</p>';
}

// ── RUN HISTORY ───────────────────────────────────────────────────────────────
function renderRuns(){
  let data=DATA.runs;
  const pipes=[...new Set(data.map(r=>r.pipeline))].sort();
  const psel=document.getElementById('run-pipe-sel');
  if(psel&&psel.options.length<=1){
    pipes.forEach(p=>{ const o=document.createElement('option'); o.value=p; o.text=p; psel.add(o); });
  }
  document.getElementById('run-search').oninput=applyRunFilters;
  document.getElementById('run-pipe-sel').onchange=applyRunFilters;
  document.getElementById('run-result-sel').onchange=applyRunFilters;

  function row(r){
    return `<tr>
      <td class="mono" style="font-size:11px">${esc(r.number)}</td>
      <td><b>${esc(r.pipeline)}</b></td>
      <td>${resultChip(r.result,r.status)}</td>
      <td class="mono mut" style="font-size:11px">${esc(r.branch)}</td>
      <td class="mut">${esc(r.by)}</td>
      <td class="mut">${esc(r.queued)}</td>
      <td class="mut">${esc(r.duration)}</td>
      <td class="mut" style="text-transform:capitalize">${esc(r.reason)}</td>
      <td>${r.stages&&r.stages.length?`<div class="stages-row">${r.stages.map(stageChip).join('')}</div>`:'<span class="mut">—</span>'}</td>
    </tr>`;
  }
  if(!vs.runs) vs.runs=mkVS('runs-tbody',data,row,'No runs.');
  else vs.runs.setData(data);
}
function applyRunFilters(){
  const q=(document.getElementById('run-search')?.value||'').toLowerCase();
  const pipe=(document.getElementById('run-pipe-sel')?.value)||'';
  const result=(document.getElementById('run-result-sel')?.value)||'';
  let d=DATA.runs;
  if(q) d=d.filter(r=>r.pipeline.toLowerCase().includes(q)||r.branch.toLowerCase().includes(q)||r.by.toLowerCase().includes(q)||r.number.includes(q));
  if(pipe) d=d.filter(r=>r.pipeline===pipe);
  if(result) d=d.filter(r=>r.result===result||r.status===result);
  if(vs.runs) vs.runs.setData(d);
}

// ── PULL REQUESTS ─────────────────────────────────────────────────────────────
let prTab='active';
function renderPRs(){
  document.getElementById('pr-tab-active').onclick=()=>{ prTab='active'; renderPRs(); };
  document.getElementById('pr-tab-completed').onclick=()=>{ prTab='completed'; renderPRs(); };
  document.getElementById('pr-tab-active').className='tab'+(prTab==='active'?' active':'');
  document.getElementById('pr-tab-completed').className='tab'+(prTab==='completed'?' active':'');

  let data=prTab==='active'?DATA.prs_active:DATA.prs_completed;
  data=data.filter(matchRepo);

  const q=(document.getElementById('pr-search')?.value||'').toLowerCase();
  if(q) data=data.filter(p=>p.title.toLowerCase().includes(q)||p.repo.toLowerCase().includes(q)||p.by.toLowerCase().includes(q)||p.source.toLowerCase().includes(q));
  document.getElementById('pr-search').oninput=renderPRs;

  const html=data.map(pr=>{
    const reviewerHtml=pr.reviewers.map(r=>{
      const vc=voteClass(pr.vote_map[r]||0);
      const vl=pr.vote_map[r]>=10?'✓':pr.vote_map[r]>0?'~':pr.vote_map[r]<0?'✗':'';
      return `<span class="reviewer ${vc}">${esc(r)}${vl?' '+vl:''}</span>`;
    }).join('');

    return `<div class="pr-card">
      <div class="pr-title">#${pr.id} &nbsp;${esc(pr.title)}</div>
      <div class="pr-meta">
        <span>📦 <b>${esc(pr.repo)}</b></span>
        <span>🔀 <b>${esc(pr.source)}</b> → <b>${esc(pr.target)}</b></span>
        <span>👤 <b>${esc(pr.by)}</b></span>
        <span>📅 <b>${esc(pr.created)}</b></span>
        ${pr.is_draft?'<span class="chip chip-draft">Draft</span>':''}
        ${pr.status==='completed'?`<span class="chip chip-ok">Merged</span> <span class="mut">${esc(pr.closed)}</span>`:''}
        <span class="proj-badge">${esc(pr.project)}</span>
      </div>
      ${reviewerHtml?`<div class="reviewers">${reviewerHtml}</div>`:''}
    </div>`;
  }).join('')||'<p class="mut" style="padding:12px">No pull requests.</p>';
  document.getElementById('pr-list').innerHTML=html;
}

// ── POLICIES ─────────────────────────────────────────────────────────────────
function renderPolicies(){
  let data=DATA.policies;
  const q=(document.getElementById('pol-search')?.value||'').toLowerCase();
  if(q) data=data.filter(p=>
    p.type.toLowerCase().includes(q)||
    p.scopes.some(s=>s.repo.toLowerCase().includes(q)||s.ref.toLowerCase().includes(q))
  );
  document.getElementById('pol-search').oninput=renderPolicies;

  function row(p){
    const scopeStr=p.scopes.map(s=>`<span class="mono" style="font-size:10px">${esc(s.repo)}${s.ref&&s.ref!=='(any)'?' @ '+esc(s.ref):''}</span>`).join('<br>');
    const reqRev=p.req_reviewers.length?p.req_reviewers.map(r=>`<span class="reviewer">${esc(r)}</span>`).join(' '):'';
    return `<tr>
      <td>${esc(p.type)}</td>
      <td>${p.enabled?'<span class="chip chip-en">On</span>':'<span class="chip chip-dis">Off</span>'}</td>
      <td>${p.blocking?'<span class="chip chip-block">Blocking</span>':'<span class="chip chip-advise">Advisory</span>'}</td>
      <td>${scopeStr||'<span class="mut">All</span>'}</td>
      <td>${p.min_reviewers!=null?`<b>${p.min_reviewers}</b> required`:'<span class="mut">—</span>'}</td>
      <td>${reqRev||'<span class="mut">—</span>'}</td>
    </tr>`;
  }
  if(!vs.policies) vs.policies=mkVS('pol-tbody',data,row,'No policies.');
  else vs.policies.setData(data);
}

// ── DEVOPS (Var Groups + Environments) ───────────────────────────────────────
function renderDevOps(){
  // Variable Groups
  const vgHtml=DATA.variable_groups.map(vg=>`
    <tr>
      <td><b>${esc(vg.name)}</b></td>
      <td class="mut">${esc(vg.project)}</td>
      <td class="mut">${esc(vg.type)}</td>
      <td class="mut">${vg.var_count} vars</td>
      <td class="mono mut" style="font-size:10px;max-width:300px">${vg.var_names.map(esc).join(', ')}</td>
      <td class="mut">${esc(vg.modified)||'—'}</td>
    </tr>`).join('')||'<tr><td colspan="6" class="mut" style="padding:12px">None.</td></tr>';
  document.getElementById('vg-tbody').innerHTML=vgHtml;

  // Environments
  const envHtml=DATA.environments.map(e=>`
    <tr>
      <td><b>${esc(e.name)}</b></td>
      <td class="mut">${esc(e.project)}</td>
      <td class="mut">${esc(e.description)||'—'}</td>
      <td class="mut">${esc(e.created)||'—'}</td>
      <td class="mut">${esc(e.modified)||'—'}</td>
    </tr>`).join('')||'<tr><td colspan="5" class="mut" style="padding:12px">None.</td></tr>';
  document.getElementById('env-tbody').innerHTML=envHtml;
}

// ── MODALS ────────────────────────────────────────────────────────────────────
function closeModal(id){ document.getElementById(id).classList.remove('open'); }
document.addEventListener('keydown',e=>{ if(e.key==='Escape'){
  document.querySelectorAll('.modal-overlay.open').forEach(m=>m.classList.remove('open')); }});

function openRepoModal(name, project){
  const repo=DATA.repos.find(r=>r.name===name&&r.project===project);
  if(!repo) return;
  const branches=DATA.branches.filter(b=>b.repo===name&&b.project===project)
    .sort((a,b)=>a.days_ago-b.days_ago);
  const prs=DATA.prs_active.filter(p=>p.repo===name);
  const policies=DATA.policies.filter(p=>p.scopes.some(s=>s.repo===name));

  const branchRows=branches.slice(0,200).map(b=>`<tr>
    <td>${esc(b.name)}${b.is_default?' <span class="chip chip-en" style="font-size:9px">DEF</span>':''}</td>
    <td>${abChip(b.ahead,b.behind)}</td>
    <td>${ageChip(b.age_class,b.days_ago,b.last_commit_date)}</td>
    <td class="mono mut" style="font-size:10px">${esc(b.last_commit_author)}</td>
    <td class="mut" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(b.last_commit_message)}</td>
  </tr>`).join('');

  const prRows=prs.map(pr=>`<tr>
    <td><b>#${pr.id}</b></td>
    <td>${esc(pr.title)}</td>
    <td class="mono" style="font-size:11px">${esc(pr.source)} → ${esc(pr.target)}</td>
    <td class="mut">${esc(pr.by)}</td>
    <td class="mut">${esc(pr.created)}</td>
  </tr>`).join('');

  const polRows=policies.map(p=>`<tr>
    <td>${esc(p.type)}</td>
    <td>${p.enabled?'<span class="chip chip-en">On</span>':'<span class="chip chip-dis">Off</span>'}</td>
    <td>${p.blocking?'<span class="chip chip-block">Blocking</span>':'<span class="chip chip-advise">Advisory</span>'}</td>
    <td>${p.scopes.map(s=>esc(s.ref||'any')).join(', ')}</td>
    <td>${p.min_reviewers!=null?p.min_reviewers+' required':'—'}</td>
  </tr>`).join('');

  document.getElementById('repo-modal-body').innerHTML=`
    <table class="kv-table">
      <tr><td>Project</td><td><span class="proj-badge">${esc(repo.project)}</span></td></tr>
      <tr><td>Default Branch</td><td class="mono">${esc(repo.default_branch)}</td></tr>
      <tr><td>Size</td><td>${repo.size_kb>1024?(repo.size_kb/1024).toFixed(1)+' MB':repo.size_kb+' KB'}</td></tr>
      <tr><td>Total Branches</td><td>${repo.branch_count}</td></tr>
      <tr><td>Active Branches (≤60d)</td><td style="color:var(--grn)">${repo.active_branches}</td></tr>
      <tr><td>Last Commit</td><td>${esc(repo.last_commit_date)} by ${esc(repo.last_commit_author)}</td></tr>
      <tr><td>Last Commit Message</td><td class="mut">${esc(repo.last_commit_message)}</td></tr>
      <tr><td>Clone URL</td><td class="mono" style="font-size:10px">${esc(repo.url)}</td></tr>
    </table>

    <div class="section-label" style="margin-top:14px">Branches (${branches.length} total — showing newest first)</div>
    <div style="max-height:220px;overflow-y:auto">
      <table><thead><tr><th>Branch</th><th>Ahead/Behind</th><th>Last Commit</th><th>Author</th><th>Message</th></tr></thead>
      <tbody>${branchRows}</tbody></table>
      ${branches.length>200?`<p class="mut" style="padding:6px">… and ${branches.length-200} more. Use the Branches tab to see all.</p>`:''}
    </div>

    ${prs.length?`
    <div class="section-label" style="margin-top:14px">Open Pull Requests (${prs.length})</div>
    <table><thead><tr><th>ID</th><th>Title</th><th>Branches</th><th>Author</th><th>Created</th></tr></thead>
    <tbody>${prRows}</tbody></table>`:''}

    ${policies.length?`
    <div class="section-label" style="margin-top:14px">Branch Policies (${policies.length})</div>
    <table><thead><tr><th>Policy</th><th>Enabled</th><th>Type</th><th>Ref</th><th>Reviewers</th></tr></thead>
    <tbody>${polRows}</tbody></table>`:''}
  `;
  document.getElementById('repo-modal-title').textContent=name;
  document.getElementById('repo-modal').classList.add('open');
}

function openPipeModal(id){
  const pipe=DATA.pipelines.find(p=>p.id===id);
  if(!pipe) return;
  const runs=DATA.runs.filter(r=>r.pipeline_id===id);
  const vgNames=pipe.variable_groups.map(vid=>{
    const g=DATA.variable_groups.find(v=>v.id===vid);
    return g?g.name:String(vid);
  });
  const runRows=runs.map(r=>`<tr>
    <td class="mono">${esc(r.number)}</td>
    <td>${resultChip(r.result,r.status)}</td>
    <td class="mono mut" style="font-size:11px">${esc(r.branch)}</td>
    <td class="mut">${esc(r.by)}</td>
    <td class="mut">${esc(r.queued)}</td>
    <td class="mut">${esc(r.duration)}</td>
    <td class="mut" style="text-transform:capitalize">${esc(r.reason)}</td>
    <td>${r.stages&&r.stages.length?r.stages.map(stageChip).join(' '):'—'}</td>
  </tr>`).join('');

  document.getElementById('pipe-modal-body').innerHTML=`
    <table class="kv-table">
      <tr><td>Type</td><td>${pipe.type==='YAML'?'<span class="chip chip-yaml">YAML</span>':'<span class="chip chip-cls">Classic</span>'}</td></tr>
      <tr><td>Quality</td><td>${pipe.quality==='draft'?'<span class="chip chip-draft">Draft</span>':'<span class="chip chip-en">Published</span>'}</td></tr>
      <tr><td>Path</td><td class="mono">${esc(pipe.path)}</td></tr>
      ${pipe.yaml_file?`<tr><td>YAML File</td><td class="mono">${esc(pipe.yaml_file)}</td></tr>`:''}
      <tr><td>Linked Repo</td><td>${esc(pipe.repo)||'—'}</td></tr>
      <tr><td>Default Branch</td><td class="mono">${esc(pipe.default_branch)||'—'}</td></tr>
      <tr><td>Agent Queue</td><td>${esc(pipe.queue)||'—'}</td></tr>
      <tr><td>Triggers</td><td>${pipe.triggers.map(esc).join(', ')||'None configured'}</td></tr>
      ${vgNames.length?`<tr><td>Variable Groups</td><td>${vgNames.map(esc).join(', ')}</td></tr>`:''}
      <tr><td>Created</td><td>${esc(pipe.created)||'—'}</td></tr>
      <tr><td>Latest Run</td><td>${resultChip(pipe.latest_result,pipe.latest_status)} ${esc(pipe.latest_queued)} by ${esc(pipe.latest_by)} — ${esc(pipe.latest_duration)}</td></tr>
    </table>

    <div class="section-label" style="margin-top:14px">Recent Runs (${runs.length})</div>
    <div style="max-height:280px;overflow-y:auto">
      <table><thead><tr><th>Build #</th><th>Result</th><th>Branch</th><th>By</th><th>Queued</th><th>Duration</th><th>Trigger</th><th>Stages</th></tr></thead>
      <tbody>${runRows}</tbody></table>
    </div>
  `;
  document.getElementById('pipe-modal-title').textContent=pipe.name;
  document.getElementById('pipe-modal').classList.add('open');
}

// ── INIT ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',()=>{
  showTab('overview');
});
"""

# ── build HTML ─────────────────────────────────────────────────────────────────

def esc(s):
    if s is None: return ""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def build_html(data, generated):
    projects = data["projects"]
    repos    = data["repos"]
    branches = data["branches"]
    runs     = data["runs"]
    prs_a    = data["prs_active"]
    prs_c    = data["prs_completed"]
    pipes    = data["pipelines"]
    policies = data["policies"]
    vgs      = data["variable_groups"]
    envs     = data["environments"]

    n_active_branches = sum(1 for b in branches if b["age_class"] == "age-active")
    n_stale_branches  = sum(1 for b in branches if b["age_class"] == "age-stale")
    n_succeeded = sum(1 for r in runs[:100] if r.get("result") == "succeeded")
    n_failed    = sum(1 for r in runs[:100] if r.get("result") == "failed")

    # sidebar repo items
    sb_items = ""
    for proj in projects:
        proj_repos = [r for r in repos if r["project"] == proj]
        sb_items += f'<div class="sb-section">{esc(proj)}</div>'
        for r in sorted(proj_repos, key=lambda x: x["last_commit_date"] or "", reverse=True):
            da = ""
            if r["last_commit_date"]:
                from datetime import datetime
                try:
                    d = (datetime.now() - datetime.strptime(r["last_commit_date"], "%Y-%m-%d")).days
                    da = f"{d}d" if d < 365 else f"{d//365}y"
                except Exception:
                    da = ""
            sb_items += (
                f'<div class="sb-item" data-repo="{esc(r["name"])}" '
                f'onclick="sbSelect(\'repo\',\'{r["name"]}\',this)">'
                f'<span class="trunc" style="max-width:145px">{esc(r["name"])}</span>'
                f'<span class="sb-badge">{da}</span></div>'
            )

    data_json = json.dumps(data, ensure_ascii=False, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>Azure DevOps Metadata — {esc(ORG)}</title>
<style>{CSS}.help-fab{{position:fixed;bottom:22px;right:22px;width:38px;height:38px;border-radius:50%;background:var(--acc);color:#fff;font-size:17px;font-weight:700;display:flex;align-items:center;justify-content:center;text-decoration:none;z-index:9999;box-shadow:0 2px 10px rgba(0,0,0,.5);opacity:.8;transition:opacity .15s;line-height:1}}.help-fab:hover{{opacity:1}}</style>
</head>
<body>
<a href="help.html" class="help-fab" title="Help &amp; Guide">?</a>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-hdr">Azure DevOps<small>{esc(ORG)}</small></div>
  <div class="sb-search"><input id="sb-search" placeholder="Filter repos…"/></div>
  <div class="sb-body">
    <div class="sb-section">Navigation</div>
    <div class="sb-item" onclick="sbSelect('all','',this)">All Projects</div>
    {"".join(f'<div class="sb-item" onclick="sbSelect(\'proj\',\'{esc(p)}\',this)">{esc(p)}</div>' for p in projects)}
    {sb_items}
  </div>
</div>

<!-- MAIN -->
<div class="main">
  <div class="main-hdr">
    <h1>Azure DevOps — {esc(ORG)}</h1>
    <p class="sub">{len(projects)} projects &nbsp;·&nbsp; Generated: <span id="gen-ts" data-ts="{esc(generated)}">&#x21BB; {esc(generated)}</span><script>(function(){{var s=document.getElementById(\'gen-ts\'),h=(Date.now()-new Date(s.dataset.ts.replace(\' \',\'T\')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

    <div class="stats">
      <div class="sc" id="card-overview"  onclick="showTab('overview')"   title="Git repositories in this Azure DevOps project. Each repo holds the source code, notebooks, pipeline YAML, and configuration for a workload. Repos are the unit of access control — permissions are granted at the repo or branch level.">
        <div class="sc-n">{len(repos)}</div><div class="sc-l">Repositories</div></div>
      <div class="sc" id="card-branches"  onclick="showActiveBranches()"  title="Branches with a commit in the last {ACTIVE_DAYS} days. Active branches typically represent in-progress feature work, hotfixes, or release candidates. A high active branch count with few open PRs may indicate work that is not being reviewed or merged.">
        <div class="sc-n" style="color:var(--grn)">{n_active_branches}</div>
        <div class="sc-l">Active Branches</div></div>
      <div class="sc"                     onclick="showStaleBranches()"   title="Branches with no commits in over {STALE_DAYS} days. Stale branches are candidates for deletion — they accumulate merge conflicts over time and create confusion about what is current. Click to review and clean up.">
        <div class="sc-n" style="color:var(--red)">{n_stale_branches}</div>
        <div class="sc-l">Stale Branches</div></div>
      <div class="sc" id="card-pipelines" onclick="showTab('pipelines')"  title="Azure Pipelines defined in this project — both YAML-based (stored in the repo) and classic UI-based pipelines. These handle CI (build, test, lint) and CD (deploy to DEV/PRD) automation triggered by commits, PRs, or schedules.">
        <div class="sc-n" style="color:var(--acc)">{len(pipes)}</div>
        <div class="sc-l">Pipelines</div></div>
      <div class="sc"                     onclick="showTab('runs')"       title="Pipeline runs that completed successfully in the recent lookback window. Click to see the full run history across all pipelines.">
        <div class="sc-n" style="color:var(--grn)">{n_succeeded}</div>
        <div class="sc-l">Recent Successes</div></div>
      <div class="sc"                     onclick="showTab('runs')"       title="Pipeline runs that failed in the recent lookback window. Failures may indicate broken builds, failed deployments, or flaky tests. Click to review which pipelines are failing and how often.">
        <div class="sc-n" style="color:var(--red)">{n_failed}</div>
        <div class="sc-l">Recent Failures</div></div>
      <div class="sc" id="card-prs"       onclick="showTab('prs')"        title="Pull requests currently open and awaiting review or completion. Long-lived open PRs accumulate merge conflicts and slow down delivery. Each PR should be reviewed, approved, and merged or abandoned in a timely manner.">
        <div class="sc-n" style="color:var(--yel)">{len(prs_a)}</div>
        <div class="sc-l">Open PRs</div></div>
      <div class="sc" id="card-policies"  onclick="showTab('policies')"   title="Branch policies enforce code quality gates on protected branches (typically main/master). Common policies: require a minimum number of reviewers, require linked work items, require a successful build before merge, and block force pushes.">
        <div class="sc-n" style="color:var(--pur)">{len(policies)}</div>
        <div class="sc-l">Branch Policies</div></div>
    </div>
  </div>

  <div class="tabs">
    <div class="tab" id="tab-overview"  onclick="showTab('overview')">Overview</div>
    <div class="tab" id="tab-repos"     onclick="showTab('repos')">Repos ({len(repos)})</div>
    <div class="tab" id="tab-branches"  onclick="showTab('branches')">Branches ({len(branches)})</div>
    <div class="tab" id="tab-pipelines" onclick="showTab('pipelines')">Pipelines ({len(pipes)})</div>
    <div class="tab" id="tab-runs"      onclick="showTab('runs')">Run History ({len(runs)})</div>
    <div class="tab" id="tab-prs"       onclick="showTab('prs')">Pull Requests ({len(prs_a)} open)</div>
    <div class="tab" id="tab-policies"  onclick="showTab('policies')">Policies ({len(policies)})</div>
    <div class="tab" id="tab-devops"    onclick="showTab('devops')">Var Groups &amp; Envs</div>
  </div>

  <div class="content">

    <!-- OVERVIEW -->
    <div class="panel" id="p-overview">
      <h2>Projects</h2>
      <div id="ov-projects" class="ov-grid"></div>
      <h2>Most Active Repositories</h2>
      <div id="ov-repos" class="ov-grid"></div>
      <h2>Pipeline Health</h2>
      <div id="ov-pipes" class="pipe-cards"></div>
    </div>

    <!-- REPOS -->
    <div class="panel" id="p-repos">
      <div class="filter-row">
        <input id="repos-search" placeholder="Search repos…"/>
      </div>
      <div id="repos-info" class="page-info"></div>
      <table>
        <thead><tr>
          <th>Repository</th><th>Project</th><th>Default Branch</th>
          <th style="text-align:right">Branches</th>
          <th style="text-align:right">Active</th>
          <th>Last Commit</th><th>Author</th><th>Message</th>
          <th style="text-align:right">Open PRs</th>
          <th style="text-align:right">Size</th>
        </tr></thead>
        <tbody id="repos-tbody"></tbody>
      </table>
      <div id="repos-tbody-sentinel" class="vs-sentinel"></div>
    </div>

    <!-- BRANCHES -->
    <div class="panel" id="p-branches">
      <div class="filter-row">
        <input id="branch-search" placeholder="Search branches…"/>
        <select id="branch-repo-sel"><option value="">All Repos</option></select>
        <label><input type="checkbox" id="branch-active-only"/> Active only (&le;{ACTIVE_DAYS}d)</label>
        <label><input type="checkbox" id="branch-stale-only"/> Stale only (&gt;{STALE_DAYS}d)</label>
      </div>
      <div id="branch-tbody-info" class="page-info"></div>
      <table>
        <thead><tr>
          <th>Branch</th><th>Repository</th><th>Project</th>
          <th>Ahead / Behind</th><th>Last Commit</th>
          <th>Author</th><th>Message</th><th>SHA</th>
        </tr></thead>
        <tbody id="branch-tbody"></tbody>
      </table>
      <div id="branch-tbody-sentinel" class="vs-sentinel"></div>
    </div>

    <!-- PIPELINES -->
    <div class="panel" id="p-pipelines">
      <div id="pipe-container" class="pipe-cards"></div>
    </div>

    <!-- RUN HISTORY -->
    <div class="panel" id="p-runs">
      <div class="filter-row">
        <input id="run-search" placeholder="Search runs…"/>
        <select id="run-pipe-sel"><option value="">All Pipelines</option></select>
        <select id="run-result-sel">
          <option value="">All Results</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="canceled">Canceled</option>
          <option value="partiallySucceeded">Partial</option>
        </select>
      </div>
      <div id="runs-tbody-info" class="page-info"></div>
      <table>
        <thead><tr>
          <th>Build #</th><th>Pipeline</th><th>Result</th>
          <th>Branch</th><th>By</th><th>Queued</th>
          <th>Duration</th><th>Trigger</th><th>Stages</th>
        </tr></thead>
        <tbody id="runs-tbody"></tbody>
      </table>
      <div id="runs-tbody-sentinel" class="vs-sentinel"></div>
    </div>

    <!-- PULL REQUESTS -->
    <div class="panel" id="p-prs">
      <div class="filter-row">
        <input id="pr-search" placeholder="Search PRs…"/>
        <div class="tabs" style="border:none;padding:0;margin:0">
          <div class="tab active" id="pr-tab-active">Active ({len(prs_a)})</div>
          <div class="tab" id="pr-tab-completed">Completed ({len(prs_c)})</div>
        </div>
      </div>
      <div id="pr-list"></div>
    </div>

    <!-- POLICIES -->
    <div class="panel" id="p-policies">
      <div class="filter-row">
        <input id="pol-search" placeholder="Search policies…"/>
      </div>
      <div id="pol-tbody-info" class="page-info"></div>
      <table>
        <thead><tr>
          <th>Policy Type</th><th>Enabled</th><th>Enforcement</th>
          <th>Scope (Repo @ Branch)</th>
          <th>Min Reviewers</th><th>Required Reviewers</th>
        </tr></thead>
        <tbody id="pol-tbody"></tbody>
      </table>
      <div id="pol-tbody-sentinel" class="vs-sentinel"></div>
    </div>

    <!-- VAR GROUPS + ENVS -->
    <div class="panel" id="p-devops">
      <h2>Variable Groups ({len(vgs)})</h2>
      <p class="mut" style="margin-bottom:8px;font-size:11px">Variable names only — values are never collected.</p>
      <table>
        <thead><tr>
          <th>Name</th><th>Project</th><th>Type</th>
          <th>Variables</th><th>Variable Names</th><th>Last Modified</th>
        </tr></thead>
        <tbody id="vg-tbody"></tbody>
      </table>
      <h2 style="margin-top:20px">Deployment Environments ({len(envs)})</h2>
      <table>
        <thead><tr>
          <th>Name</th><th>Project</th><th>Description</th>
          <th>Created</th><th>Last Modified</th>
        </tr></thead>
        <tbody id="env-tbody"></tbody>
      </table>
    </div>

  </div>
</div>
</div>

<!-- REPO MODAL -->
<div class="modal-overlay" id="repo-modal" onclick="if(event.target===this) closeModal('repo-modal')">
  <div class="modal">
    <h2><span id="repo-modal-title"></span>
      <span class="modal-close" onclick="closeModal('repo-modal')">✕</span></h2>
    <div id="repo-modal-body"></div>
  </div>
</div>

<!-- PIPELINE MODAL -->
<div class="modal-overlay" id="pipe-modal" onclick="if(event.target===this) closeModal('pipe-modal')">
  <div class="modal">
    <h2><span id="pipe-modal-title"></span>
      <span class="modal-close" onclick="closeModal('pipe-modal')">✕</span></h2>
    <div id="pipe-modal-body"></div>
  </div>
</div>

<script>
const DATA = {data_json};
{JS}
</script>
</body>
</html>"""

# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("Authenticating with Azure DevOps…")
    token = get_token()

    data = collect(token)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("\nBuilding HTML…")
    html = build_html(data, generated)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    total_branches = len(data["branches"])
    active_branches = sum(1 for b in data["branches"] if b["age_class"] == "age-active")
    stale_branches  = sum(1 for b in data["branches"] if b["age_class"] == "age-stale")

    print(f"\nSaved: {OUT_FILE}")
    print(f"  Projects      : {len(data['projects'])}")
    print(f"  Repos         : {len(data['repos'])}")
    print(f"  Branches      : {total_branches}  (active: {active_branches}, stale: {stale_branches})")
    print(f"  Pipelines     : {len(data['pipelines'])}")
    print(f"  Pipeline runs : {len(data['runs'])}")
    print(f"  Open PRs      : {len(data['prs_active'])}")
    print(f"  Completed PRs : {len(data['prs_completed'])}")
    print(f"  Policies      : {len(data['policies'])}")
    print(f"  Var Groups    : {len(data['variable_groups'])}")
    print(f"  Environments  : {len(data['environments'])}")


    if not os.environ.get('PUBLISH_RUNNING'):
        try:
            import generate_metadata_index
            generate_metadata_index.main()
            print("  Index updated       : index.html")
        except Exception as exc:
            print(f"  Warning: could not update index.html: {exc}")

if __name__ == "__main__":
    main()
