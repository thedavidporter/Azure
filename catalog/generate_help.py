#!/usr/bin/env python3
"""
Generate help.html — a single-page guide covering all IDOH Metadata Marketplace reports.
Called automatically by generate_metadata_index.py after index.html is built.
"""

import json
import os
from datetime import datetime

OUTPUT_PATH    = "/home/thedavidporter/help.html"
CHANGELOG_PATH = "/home/thedavidporter/changelog.json"

CSS = """
:root{
  --bg:#0f1117;--sur:#1a1d27;--sur2:#252836;--brd:#2e3245;
  --txt:#e2e8f0;--mut:#8892a4;--acc:#6c8eff;--grn:#4ade80;
  --red:#f87171;--yel:#fbbf24;--pur:#c084fc;--cyn:#22d3ee;--org:#fb923c;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.7 'Segoe UI',system-ui,sans-serif}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}

/* layout */
.layout{display:flex;height:100vh;overflow:hidden}
.sidebar{width:260px;min-width:200px;background:var(--sur);border-right:1px solid var(--brd);
  overflow-y:auto;padding:24px 0;flex-shrink:0}
.main{flex:1;overflow-y:auto;padding:40px 48px 80px}

/* sidebar nav */
.sb-logo{padding:0 20px 20px;border-bottom:1px solid var(--brd);margin-bottom:16px}
.sb-logo a{font-size:13px;font-weight:700;color:var(--txt)}
.sb-logo .sub{font-size:11px;color:var(--mut);margin-top:2px}
.sb-section{font-size:9px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;
  color:var(--mut);padding:14px 20px 4px}
.sb-item{display:block;padding:6px 20px;font-size:12px;color:var(--mut);cursor:pointer;
  border-left:2px solid transparent;transition:all .12s}
.sb-item:hover{color:var(--txt);background:var(--sur2);border-left-color:var(--brd)}
.sb-item.active{color:var(--acc);background:var(--sur2);border-left-color:var(--acc)}

/* content */
h1{font-size:26px;font-weight:800;margin-bottom:8px}
h2{font-size:17px;font-weight:700;margin:48px 0 16px;padding-bottom:8px;
  border-bottom:1px solid var(--brd);scroll-margin-top:32px}
h3{font-size:14px;font-weight:700;margin:24px 0 8px;color:var(--cyn)}
p{margin-bottom:12px;color:var(--txt)}
ul,ol{padding-left:20px;margin-bottom:12px}
li{margin-bottom:4px}
code{background:var(--sur2);border:1px solid var(--brd);border-radius:4px;
  padding:1px 6px;font-size:12px;font-family:Consolas,monospace;color:var(--cyn)}
.meta{color:var(--mut);font-size:12px;margin-bottom:32px}

/* report cards */
.report-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px}
.report-card{background:var(--sur);border:1px solid var(--brd);border-radius:10px;padding:16px 18px;display:flex;flex-direction:column}
.report-card-cat{font-size:10px;font-weight:700;color:var(--acc);text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:6px}
.report-card h4{font-size:13px;font-weight:700;margin-bottom:6px}
.report-card p{font-size:12px;color:var(--mut);margin:0;flex:1}
.report-card .tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px}
.tag{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px}
.tag-dev{background:#1e2a4a;color:var(--acc)}
.tag-prd{background:#1a3a2a;color:var(--grn)}
.tag-all{background:#2d1e5f;color:var(--pur)}
.tag-link{background:var(--sur2);color:var(--mut);border:1px solid var(--brd)}

/* audience pills */
.audience-row{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
.aud{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;
  border-radius:20px;border:1px solid var(--brd);font-size:12px;background:var(--sur)}
.aud.exec{border-color:var(--pur);color:var(--pur)}
.aud.mgmt{border-color:var(--yel);color:var(--yel)}
.aud.analyst{border-color:var(--grn);color:var(--grn)}
.aud.engineer{border-color:var(--cyn);color:var(--cyn)}

/* Q&A */
.qa-section{margin-bottom:12px}
.qa-q{background:var(--sur);border:1px solid var(--brd);border-radius:8px;
  padding:12px 16px;cursor:pointer;user-select:none;
  display:flex;align-items:flex-start;gap:10px;transition:border-color .12s}
.qa-q:hover{border-color:var(--acc)}
.qa-q.open{border-color:var(--acc);border-bottom-left-radius:0;border-bottom-right-radius:0}
.qa-q .arrow{font-size:11px;margin-top:2px;flex-shrink:0;color:var(--mut);transition:transform .15s}
.qa-q.open .arrow{transform:rotate(90deg);color:var(--acc)}
.qa-q .qtext{font-size:13px;font-weight:600;line-height:1.4}
.qa-q .badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;
  flex-shrink:0;margin-left:auto;white-space:nowrap}
.badge-exec{background:#2d1e5f;color:var(--pur)}
.badge-mgmt{background:#3a300a;color:var(--yel)}
.badge-analyst{background:#1a3a2a;color:var(--grn)}
.badge-engineer{background:#0f2a3a;color:var(--cyn)}
.badge-all{background:var(--sur2);color:var(--mut)}
.qa-a{background:var(--sur2);border:1px solid var(--acc);border-top:none;
  border-bottom-left-radius:8px;border-bottom-right-radius:8px;
  padding:14px 16px 14px 36px;display:none;cursor:pointer}
.qa-a.open{display:block}
.qa-a p{font-size:13px;margin-bottom:8px}
.qa-a p:last-child{margin-bottom:0}
.qa-a ol,.qa-a ul{font-size:13px}
.qa-a .tip{background:var(--sur);border-left:3px solid var(--acc);
  padding:8px 12px;border-radius:0 6px 6px 0;margin-top:8px;font-size:12px;color:var(--mut)}

/* glossary */
.gl-search{width:100%;padding:9px 14px;border-radius:8px;border:1px solid var(--brd);
  background:var(--sur2);color:var(--txt);font-size:13px;margin-bottom:16px;outline:none}
.gl-search:focus{border-color:var(--acc)}
.gl-row{padding:10px 0;border-bottom:1px solid var(--brd)}
.gl-row:last-child{border-bottom:none}
.gl-term{font-weight:700;color:var(--cyn);margin-bottom:3px}
.gl-def{color:var(--mut);font-size:13px;line-height:1.5}
.gl-section{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;
  color:var(--acc);padding:18px 0 6px;border-bottom:2px solid var(--acc);margin-bottom:2px}

/* changelog */
.cl-entry{padding:10px 0;border-bottom:1px solid var(--brd)}
.cl-entry:last-child{border-bottom:none}
.cl-meta{font-size:11px;color:var(--mut);margin-bottom:3px}
.cl-name{font-weight:700;font-size:13px;color:var(--cyn);margin-bottom:3px}
.cl-desc{font-size:12px;color:var(--mut);line-height:1.5}

/* feedback spinner */
.fb-spinner{display:flex;flex-direction:column;align-items:center;gap:10px;padding:28px 0}
.fb-spinner-ring{width:28px;height:28px;border:3px solid var(--brd);border-top-color:var(--acc);border-radius:50%;animation:fb-spin .7s linear infinite}
@keyframes fb-spin{to{transform:rotate(360deg)}}
.fb-spinner-word{font-size:11px;color:var(--mut);font-style:italic;min-width:110px;text-align:center}
.fb-btn-spin{display:inline-block;width:10px;height:10px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;animation:fb-spin .6s linear infinite;vertical-align:middle;margin-right:4px}

/* ── Demo animation card ─────────────────────────────────── */
.dh-wrap{margin:18px 0 4px;user-select:none}
.dh-card{position:relative;width:100%;max-width:620px;height:310px;
  background:#12141d;border:1px solid #2e3245;border-radius:10px 10px 0 0;
  overflow:hidden;font-family:'Segoe UI',system-ui,sans-serif;font-size:12px}
/* chrome bar */
.dh-chrome{display:flex;align-items:center;gap:6px;padding:8px 14px;
  background:#1a1d27;border-bottom:1px solid #2e3245}
.dh-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dh-dot.r{background:#f87171}.dh-dot.y{background:#fbbf24}.dh-dot.g{background:#4ade80}
.dh-win-title{margin-left:6px;font-size:11px;color:#8892a4;flex:1}
.dh-win-badge{font-size:9px;font-weight:700;padding:2px 7px;border-radius:4px;
  background:#1e2a4a;color:#6c8eff}
/* stat chips */
.dh-stats{display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid #2e3245;
  background:#1a1d27;overflow-x:auto}
.dh-sc{background:#252836;border:1px solid #2e3245;border-radius:7px;
  padding:5px 10px;text-align:center;min-width:66px;flex-shrink:0}
.dh-sc-n{font-size:14px;font-weight:700;color:#e2e8f0}
.dh-sc-l{font-size:9px;color:#8892a4;text-transform:uppercase;letter-spacing:.3px}
/* tabs */
.dh-tabs{display:flex;border-bottom:1px solid #2e3245;padding:0 10px;
  background:#1a1d27;overflow-x:auto}
.dh-tab{padding:7px 12px;font-size:11px;font-weight:600;color:#8892a4;
  border-bottom:2px solid transparent;white-space:nowrap;flex-shrink:0}
.dh-tab.dh-active{color:#e2e8f0;border-bottom-color:#6c8eff}
/* panels */
.dh-body{position:relative;height:186px;overflow:hidden}
.dh-panel{position:absolute;inset:0;padding:10px 14px;overflow:hidden}
.dh-panel.dh-hidden{display:none}
@keyframes dh-fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
/* monitor panel */
.dh-mon-hdr{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  color:#525b6b;padding-bottom:5px;border-bottom:1px solid #2e3245;margin-bottom:6px;
  display:grid;grid-template-columns:2fr 1fr 1fr 80px;gap:6px}
.dh-mon-row{display:grid;grid-template-columns:2fr 1fr 1fr 80px;gap:6px;
  padding:4px 4px;border-radius:4px;align-items:center}
.dh-mon-row:hover{background:#1e2231}
.dh-mon-name{color:#c8d0e0;font-size:11px}
.dh-chip{font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;width:fit-content}
.dh-ok{background:#1a3a2a;color:#4ade80}
.dh-fail{background:#3a1a1a;color:#f87171}
.dh-time{color:#8892a4;font-size:10px}
.dh-dur{color:#8892a4;font-size:10px}
/* hierarchy panel */
.dh-hier-sec{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  color:#525b6b;margin-bottom:7px}
.dh-trig-card{background:#1a1d27;border:1px solid #2e3245;border-radius:7px;overflow:hidden}
.dh-trig-hdr{display:flex;align-items:center;gap:8px;padding:8px 11px;
  color:#c8d0e0;cursor:pointer;font-size:11px;transition:background .15s}
.dh-trig-hdr.dh-hdr-open{background:#1b2540;border-bottom:1px solid #2e3245}
.dh-trig-arrow{font-size:10px;color:#8892a4;transition:transform .25s,color .25s;flex-shrink:0}
.dh-sched-badge{font-size:9px;font-weight:700;padding:2px 7px;border-radius:4px;
  background:#252836;color:#fbbf24;flex-shrink:0}
.dh-trig-label{font-size:11px;color:#8892a4}
.dh-trig-label strong{color:#c084fc}
/* tree */
.dh-tree{padding:8px 10px 10px 18px;font-size:10.5px;
  font-family:Consolas,'Courier New',monospace;line-height:1.8}
.dh-tree.dh-hidden{display:none}
.dh-tree-root{color:#e2e8f0;font-weight:700}
.dh-l1{color:#c084fc;transition:opacity .3s}
.dh-l2a,.dh-l2b{color:#6c8eff;transition:opacity .3s}
.dh-l3a,.dh-l3b,.dh-l3c{color:#22d3ee;transition:opacity .3s}
.dh-hidden{display:none!important}
/* cursor */
.dh-cursor{position:absolute;pointer-events:none;z-index:100;
  transition:left .55s cubic-bezier(.4,0,.2,1), top .55s cubic-bezier(.4,0,.2,1)}
@keyframes dh-clicking{0%{transform:scale(1)}40%{transform:scale(.72)}100%{transform:scale(1)}}
.dh-clicking{animation:dh-clicking .28s ease forwards}
/* step bar */
.dh-step-bar{display:flex;align-items:center;gap:8px;padding:9px 14px;
  background:#1a1d27;border:1px solid #2e3245;border-top:none;
  border-bottom-left-radius:10px;border-bottom-right-radius:10px;font-size:12px}
.dh-step-dot{width:7px;height:7px;border-radius:50%;background:#6c8eff;flex-shrink:0;
  animation:dh-pulse 1.6s ease-in-out infinite}
@keyframes dh-pulse{0%,100%{opacity:.4;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
#dh-step-text{color:#8892a4;transition:opacity .22s;font-size:11px}
.dh-replay{margin-top:8px;font-size:11px;padding:4px 12px;background:#1a1d27;
  border:1px solid #2e3245;border-radius:6px;color:#8892a4;cursor:pointer;transition:all .15s}
.dh-replay:hover{border-color:#6c8eff;color:#e2e8f0}
/* screenshot preview */
.qa-screenshot{margin-top:14px}
.qa-ss-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;
  color:var(--mut);margin-bottom:6px}
.qa-ss-img{width:100%;border-radius:8px;border:1px solid var(--brd);display:block}
/* generic demo utilities */
.dm-row{display:grid;gap:6px;padding:5px 8px;border-radius:4px;font-size:11px;align-items:center;margin-bottom:3px;background:#1a1d27}
.dm-chip{font-size:9px;font-weight:700;padding:2px 7px;border-radius:3px;display:inline-block}
.dm-ok{background:#1a3a2a;color:#4ade80}.dm-warn{background:#3a2a10;color:#fbbf24}.dm-err{background:#3a1a1a;color:#f87171}
.dm-search{background:#1a1d27;border:1px solid #6c8eff;border-radius:5px;padding:4px 8px;color:#e2e8f0;font-size:11px;width:100%;margin-bottom:6px;display:block;outline:none}
.dm-sec{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#525b6b;margin-bottom:5px;margin-top:6px}
/* click ripple */
.dh-ripple{position:absolute;width:18px;height:18px;border-radius:50%;
  border:2px solid #6c8eff;opacity:0;pointer-events:none;transform:translate(-50%,-50%)}
@keyframes dh-ripple-anim{0%{opacity:.9;transform:translate(-50%,-50%) scale(0)}
  100%{opacity:0;transform:translate(-50%,-50%) scale(2.5)}}
.dh-ripple.active{animation:dh-ripple-anim .4s ease-out forwards}

/* back nav */
.back-btn{display:inline-flex;align-items:center;gap:6px;font-size:12px;
  color:var(--mut);border:1px solid var(--brd);border-radius:6px;
  padding:5px 12px;margin-bottom:28px;transition:all .12s}
.back-btn:hover{color:var(--txt);border-color:var(--acc);text-decoration:none}

/* filter bar */
.filter-bar{position:sticky;top:0;background:var(--bg);padding:12px 0 8px;
  display:flex;gap:8px;flex-wrap:wrap;z-index:10;border-bottom:1px solid var(--brd);
  margin-bottom:20px}
.filter-btn{font-size:11px;font-weight:700;padding:4px 12px;border-radius:20px;
  border:1px solid var(--brd);background:var(--sur);color:var(--mut);cursor:pointer;transition:all .12s}
.filter-btn:hover{border-color:var(--acc);color:var(--txt)}
.filter-btn.active{background:var(--acc);border-color:var(--acc);color:#fff}

/* feedback widget */
.fb-toggle{position:fixed;bottom:24px;left:24px;z-index:1000;
  background:var(--sur);border:1px solid var(--brd);border-radius:24px;
  padding:8px 16px;cursor:pointer;color:var(--acc);font-size:12px;font-weight:700;
  font-family:inherit;box-shadow:0 2px 12px rgba(0,0,0,.4);transition:border-color .15s}
.fb-toggle:hover{border-color:var(--acc)}
.fb-toggle::after{content:attr(data-tooltip);position:absolute;bottom:calc(100% + 10px);left:0;
  background:#1a2333;color:var(--txt);font-size:11px;font-weight:400;font-style:italic;
  padding:7px 11px;border-radius:7px;border:1px solid var(--brd);
  white-space:normal;width:200px;line-height:1.5;text-align:left;
  opacity:0;pointer-events:none;transition:opacity .18s;box-shadow:0 4px 14px rgba(0,0,0,.4)}
.fb-toggle:hover::after{opacity:1}
.fb-panel{position:fixed;bottom:68px;left:24px;z-index:1001;width:320px;
  background:var(--sur);border:1px solid var(--brd);border-radius:12px;
  box-shadow:0 4px 24px rgba(0,0,0,.5);display:none;flex-direction:column;overflow:hidden}
.fb-panel.open{display:flex}
.fb-panel-hdr{padding:12px 16px;border-bottom:1px solid var(--brd);
  font-size:13px;font-weight:700;color:var(--txt)}
.fb-tabs{display:flex;border-bottom:1px solid var(--brd)}
.fb-tab{flex:1;padding:8px;font-size:11px;font-weight:700;text-align:center;
  cursor:pointer;color:var(--mut);background:none;border:none;font-family:inherit;
  border-bottom:2px solid transparent;transition:color .12s,border-color .12s}
.fb-tab.active{color:var(--acc);border-bottom-color:var(--acc)}
.fb-body{padding:14px 16px;display:flex;flex-direction:column;gap:10px}
.fb-label{font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;color:var(--mut);margin-bottom:3px}
.fb-input{width:100%;padding:7px 10px;border-radius:6px;border:1px solid var(--brd);
  background:var(--sur2);color:var(--txt);font-size:12px;font-family:inherit;outline:none}
.fb-input:focus{border-color:var(--acc)}
.fb-dt{background:var(--sur2);border:1px solid var(--brd);border-radius:6px;
  padding:7px 10px;font-size:12px;color:var(--mut)}
.fb-pri-row{display:flex;gap:6px}
@keyframes pri-pop{
  0%  {transform:scale(1)}
  35% {transform:scale(1.18)}
  65% {transform:scale(.93)}
  82% {transform:scale(1.05)}
  100%{transform:scale(1)}
}
.fb-pri-btn{flex:1;padding:6px 0;border-radius:5px;font-size:10px;font-weight:700;
  cursor:pointer;font-family:inherit;
  transition:background .15s,border-color .15s,color .15s,box-shadow .15s;
  background:var(--sur2);color:var(--mut)}
#fb-pri-Low    {border:1px solid #2d6648}
#fb-pri-Medium {border:1px solid #806010}
#fb-pri-High   {border:1px solid #8a5020}
#fb-pri-Critical{border:1px solid #8a2828}
.fb-pri-btn:hover{background:var(--sur);color:var(--txt)}
.fb-pri-btn.active-low{
  background:#1a3a2a;border-color:var(--grn);color:var(--grn);
  box-shadow:0 0 0 2px rgba(74,222,128,.35),0 0 12px rgba(74,222,128,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-pri-btn.active-medium{
  background:#3a300a;border-color:var(--yel);color:var(--yel);
  box-shadow:0 0 0 2px rgba(251,191,36,.35),0 0 12px rgba(251,191,36,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-pri-btn.active-high{
  background:#3a2a1e;border-color:#fb923c;color:#fb923c;
  box-shadow:0 0 0 2px rgba(251,146,60,.35),0 0 12px rgba(251,146,60,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-pri-btn.active-critical{
  background:#3a1a1a;border-color:var(--red);color:var(--red);
  box-shadow:0 0 0 2px rgba(248,113,113,.35),0 0 12px rgba(248,113,113,.25);
  animation:pri-pop .35s cubic-bezier(.36,.07,.19,.97)}
.fb-submit{padding:8px;border-radius:6px;border:none;background:var(--acc);
  color:#fff;font-size:12px;font-weight:700;cursor:pointer;font-family:inherit;
  transition:opacity .12s}
.fb-submit:hover{opacity:.85}
.fb-log{padding:12px 16px;max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.fb-log-entry{background:var(--sur2);border:1px solid var(--brd);border-radius:7px;padding:9px 11px}
.fb-log-meta{font-size:10px;color:var(--mut);margin-bottom:4px}
.fb-log-comment{font-size:12px;color:var(--txt)}
.fb-log-actions{display:flex;gap:8px;margin-top:8px}
.fb-log-btn{font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;
  border:1px solid var(--brd);background:none;color:var(--mut);cursor:pointer;font-family:inherit}
.fb-log-btn:hover{border-color:var(--acc);color:var(--acc)}
"""

JS = """
const _mainEl  = document.querySelector('.main');
const _sbItems = document.querySelectorAll('.sb-item[data-target]');

// Renamed from scrollTo to avoid shadowing the native window.scrollTo
function navTo(id){
  const el = document.getElementById(id);
  if(!el) return;
  el.scrollIntoView({behavior:'smooth'});
  _sbItems.forEach(i => i.classList.toggle('active', i.dataset.target === id));
}

// Active sidebar highlight — use getBoundingClientRect relative to .main
_mainEl.addEventListener('scroll', () => {
  const containerTop = _mainEl.getBoundingClientRect().top;
  let current = '';
  document.querySelectorAll('h2[id]').forEach(s => {
    if(s.getBoundingClientRect().top - containerTop < 120) current = s.id;
  });
  _sbItems.forEach(i => i.classList.toggle('active', i.dataset.target === current));
});

// Q&A accordion — toggle on click anywhere in question or answer
function _qaToggle(q){
  const a = q.nextElementSibling;
  const open = q.classList.contains('open');
  q.classList.toggle('open', !open);
  a.classList.toggle('open', !open);
}
document.querySelectorAll('.qa-q').forEach(q => {
  q.addEventListener('click', () => _qaToggle(q));
});
document.querySelectorAll('.qa-a').forEach(a => {
  a.addEventListener('click', () => _qaToggle(a.previousElementSibling));
});

// audience filter
let activeFilter = 'all';
function filterQA(audience){
  activeFilter = audience;
  document.querySelectorAll('.filter-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.filter === audience));
  document.querySelectorAll('.qa-section').forEach(sec => {
    const aud = sec.dataset.audience;
    sec.style.display = (audience === 'all' || aud === audience || aud === 'all') ? '' : 'none';
  });
}

// animations removed — Q&A items now show report screenshots

// glossary search
function filterGlossary(q){
  const s = q.toLowerCase();
  document.querySelectorAll('#gl-list .gl-row').forEach(row => {
    const text = row.textContent.toLowerCase();
    row.style.display = (!s || text.includes(s)) ? '' : 'none';
  });
}
"""


# ── screenshot-based demo system ─────────────────────────────────────────────
# Maps demo ID → screenshot filename (served from screenshots/ blob folder)
_SCREENSHOT_MAP = {
    # Synapse Metadata
    'ace':       'ss_synapse_dev.png',
    'viewdef':   'ss_synapse_dev.png',
    'procdef':   'ss_synapse_dev.png',
    'tabledef':  'ss_synapse_dev.png',
    'layer':     'ss_synapse_dev.png',
    'colsearch': 'ss_synapse_dev.png',
    'fk':        'ss_synapse_dev.png',
    'deps':      'ss_synapse_dev.png',
    'schemas':   'ss_synapse_dev.png',
    'emptyrows': 'ss_synapse_dev.png',
    'progfilter':'ss_synapse_dev.png',
    'coltype':   'ss_synapse_dev.png',
    'largetbl':  'ss_synapse_dev.png',
    'ddl':       'ss_synapse_dev.png',
    'deprev':    'ss_synapse_dev.png',
    'programs':  'ss_synapse_dev.png',
    # Synapse Delta
    'delta':     'ss_synapse_delta.png',
    'promotion': 'ss_synapse_delta.png',
    'multiday':  'ss_synapse_delta.png',
    # ADF
    'adfmon':    'ss_adf_dev.png',
    'adftrig':   'ss_adf_dev.png',
    'lnksvc':    'ss_adf_dev.png',
    'lastrun':   'ss_adf_dev.png',
    'nightly':   'ss_adf_dev.png',
    'dataflows': 'ss_adf_dev.png',
    'datasets':  'ss_adf_dev.png',
    'cxpipeline':'ss_adf_dev.png',
    'ir':        'ss_adf_dev.png',
    # Hub index
    'envcount':  'ss_index.png',
    'refresh':   'ss_index.png',
    'synvssql':  'ss_index.png',
    # VNet
    'vnetrisk':  'ss_vnet.png',
    'pe':        'ss_vnet.png',
    # Databricks
    'dbricks':   'ss_databricks.png',
    'dbat':      'ss_databricks.png',
    # ADLS
    'adls':      'ss_adls.png',
    'storage':   'ss_adls.png',
    # Key Vault
    'kvsec':     'ss_keyvault.png',
    'kvexp':     'ss_keyvault.png',
    'kvsecok':   'ss_keyvault.png',
    # DevOps
    'branches':  'ss_ado.png',
    'prs':       'ss_ado.png',
    'cibuild':   'ss_ado.png',
    # SQL DW
    'sqldist':   'ss_sql_dw.png',
    'rr':        'ss_sql_dw.png',
    # Logic Apps
    'logicapp':  'ss_logic_apps.png',
}

# Report titles shown under each screenshot
_REPORT_TITLES = {
    'ss_synapse_dev.png':   'Synapse Metadata Report — DEV',
    'ss_synapse_delta.png': 'Synapse Delta Report — DEV',
    'ss_adf_dev.png':       'ADF Metadata Report — DEV',
    'ss_index.png':         'Metadata Marketplace — Index',
    'ss_vnet.png':          'VNet Metadata Report',
    'ss_databricks.png':    'Databricks Metadata Report',
    'ss_adls.png':          'ADLS Gen2 Metadata Report',
    'ss_keyvault.png':      'Key Vault Metadata Report — DEV',
    'ss_ado.png':           'Azure DevOps Metadata Report',
    'ss_sql_dw.png':        'SQL DW Metadata Report — DEV',
    'ss_logic_apps.png':    'Logic Apps Metadata Report — DEV',
}

_demo_inits = []  # kept for build_html compatibility (no longer used)


def D(did):
    """Return a screenshot preview block for the given demo ID."""
    img = _SCREENSHOT_MAP.get(did, '')
    if not img:
        return ''
    title = _REPORT_TITLES.get(img, '')
    return (
        '<div class="qa-screenshot">'
        f'<div class="qa-ss-label">&#128247; {title}</div>'
        f'<img src="screenshots/{img}" alt="{title}" class="qa-ss-img">'
        '</div>'
    )


# ── Q&A demo lookup map ───────────────────────────────────────────────────────
_QA_DEMOS = {
    "What tables have the <code>ACE_TIMESTAMP</code> column?": "ace",
    "What objects have changed since yesterday in Synapse?": "delta",
    "What is the definition (code) of a view in Synapse?": "viewdef",
    "What is the definition (code) of a stored procedure in Synapse?": "procdef",
    "What is the definition (columns, types, row count) for a table in Synapse?": "tabledef",
    "Does this data exist in the data mart, source/staging, reporting layer, or the ACE warehouse?": "layer",
    "How many ADF pipelines ran in the last 7 days, and how many failed?": "adfmon",
    "How many tables, views, and stored procedures does IDOH manage across environments?": "envcount",
    "Is there any security or data-exfiltration risk in our network configuration?": "vnetrisk",
    "How do I find what triggers are scheduled in ADF and when they run?": "adftrig",
    "How do I search for a column across all tables to find where a piece of data lives?": "colsearch",
    "How do I find foreign key relationships between tables in Synapse?": "fk",
    "How do I see dependencies between views, procedures, and tables in Synapse?": "deps",
    "What Databricks clusters and jobs exist, and are they running?": "dbricks",
    "How do I find what data is stored in ADLS and how large each container is?": "adls",
    "How do I find what linked services are configured in ADF?": "lnksvc",
    "How do I see what secrets are configured in Key Vault?": "kvsec",
    "How do I find stale or inactive branches in DevOps?": "branches",
    "How do I see the distribution type and index type of a table in SQL DW?": "sqldist",
    "What Logic Apps are failing, and when did they last run successfully?": "logicapp",
    "How do I tell if data in DEV has been promoted to PRD?": "promotion",
    "Where can I see pull requests and code review activity in DevOps?": "prs",
    "How do I see when reports were last refreshed?": "refresh",
    "How do I see which ADF pipeline last ran and whether it succeeded?": "lastrun",
    "What schemas exist and what do they contain?": "schemas",
    "How do I know if a nightly data load completed successfully?": "nightly",
    "What data programs and health domains are represented in the warehouse?": "programs",
    "Are there any secrets or certificates expiring soon that could cause an outage?": "kvexp",
    "What is the difference between the Synapse Metadata Report and the SQL DW Metadata Report?": "synvssql",
    "How do I find tables that are empty or have very few rows?": "emptyrows",
    "How do I find all tables related to a specific program (e.g. CHIRP, Syndromic, Hospital Discharge)?": "progfilter",
    "How do I find all columns with a specific data type (e.g. all date columns)?": "coltype",
    "How do I identify the largest tables by row count?": "largetbl",
    "Can I see the DDL (CREATE TABLE statement) for a table in Synapse?": "ddl",
    "How do I find which views or procedures depend on a specific table?": "deprev",
    "How do I see what data flows exist in ADF and what they transform?": "dataflows",
    "How do I find which ADF datasets reference a specific linked service or storage location?": "datasets",
    "How do I find tables using Round Robin distribution that might be candidates for redistribution?": "rr",
    "How do I check if a Databricks cluster is configured to auto-terminate?": "dbat",
    "How do I see private endpoints and verify services are accessed over the private network?": "pe",
    "How do I see build pipeline run history and whether CI/CD is healthy in DevOps?": "cibuild",
    "How do I find which ADF pipelines have the most activities (most complex pipelines)?": "cxpipeline",
    "How do I see the integration runtime used by a pipeline or dataset?": "ir",
    "How do I verify a Key Vault secret has not expired before running a pipeline?": "kvsecok",
    "How do I track changes to a specific object across multiple days in Synapse?": "multiday",
    "How do I check overall storage usage across the data lake?": "storage",
}



def qa(qtext, answer_html, audience="all"):
    badge_map = {
        "exec":     ("badge-exec",     "Executive"),
        "mgmt":     ("badge-mgmt",     "Data Management"),
        "analyst":  ("badge-analyst",  "Business Analyst"),
        "engineer": ("badge-engineer", "Data Engineer"),
        "all":      ("badge-all",      "Everyone"),
    }
    cls, label = badge_map.get(audience, ("badge-all", audience))
    demo_html = D(_QA_DEMOS.get(qtext, ''))
    return f"""
<div class="qa-section" data-audience="{audience}">
  <div class="qa-q">
    <span class="arrow">&#9654;</span>
    <span class="qtext">{qtext}</span>
    <span class="badge {cls}">{label}</span>
  </div>
  <div class="qa-a">{answer_html}{demo_html}</div>
</div>"""


def build_html(generated):
    global _demo_inits
    _demo_inits = []
    q_and_a = ""

    # ── user-provided Q&A ──────────────────────────────────────────────────────
    q_and_a += qa(
        "What tables have the <code>ACE_TIMESTAMP</code> column?",
        """<p>Open the <strong>Synapse Metadata Report</strong> for the desired environment (DEV or PRD).</p>
<ol>
  <li>Click the <strong>Columns</strong> stat card at the top.</li>
  <li>In the search box, type <code>ACE_TIMESTAMP</code>.</li>
  <li>Every table that contains that column will appear in the list, along with its schema, data type, and nullability.</li>
</ol>
<p class="tip">Tip: the column search matches any part of the name, so searching <code>ACE_</code> will find all ACE-prefixed columns across every table and view.</p>""",
        "analyst"
    )

    q_and_a += qa(
        "What objects have changed since yesterday in Synapse?",
        """<p>Open the <strong>Synapse Delta Report</strong> for the desired environment.</p>
<ol>
  <li>At the top of the page, purple <em>pursue cards</em> summarize objects added, removed, and modified since the previous snapshot.</li>
  <li>Click any pursue card to jump directly to that change category.</li>
  <li>You will see objects added, objects removed, columns added or dropped, and tables whose row count changed significantly.</li>
  <li>To investigate a changed object further: copy its name, switch to the <strong>Synapse Metadata Report</strong> for the same environment, and paste the name into the search box.</li>
</ol>
<p class="tip">The delta is calculated from the previous day's snapshot, so it always reflects what changed in the last 24-hour window.</p>""",
        "mgmt"
    )

    q_and_a += qa(
        "What is the definition (code) of a view in Synapse?",
        """<p>Open the <strong>Synapse Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Click the <strong>Views</strong> stat card.</li>
  <li>Browse the list or use the search box to find the view by name.</li>
  <li>Click on the view row — a detail panel expands below it.</li>
  <li>Click <strong>Show / Hide Definition</strong> to reveal the full T-SQL code.</li>
  <li>Below the code you will also see a <em>plain-English explanation</em> of what the view does, written automatically from the SQL logic.</li>
</ol>""",
        "analyst"
    )

    q_and_a += qa(
        "What is the definition (code) of a stored procedure in Synapse?",
        """<p>Open the <strong>Synapse Metadata Report</strong> (SMR) for the desired environment.</p>
<ol>
  <li>Click the <strong>Procs / Functions</strong> stat card.</li>
  <li>Search for the procedure by name, or scroll the list.</li>
  <li>Click the row to expand the detail panel and view the full T-SQL definition.</li>
</ol>
<p class="tip">The same approach applies to user-defined functions — they appear in the same Procs / Functions tab.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "What is the definition (columns, types, row count) for a table in Synapse?",
        """<p>Open the <strong>Synapse Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Use the left sidebar search to type the table name — results narrow as you type.</li>
  <li>Click the table in the sidebar to open its detail panel on the right.</li>
  <li>You will see: all columns with data types and nullability, DDL (CREATE TABLE statement), creation date, last-modified date, and row count.</li>
</ol>
<p class="tip">Row counts are live values fetched from the Synapse distribution stats at the time the report was generated. Check the report's "Generated" timestamp at the top to know how recent they are.</p>""",
        "analyst"
    )

    q_and_a += qa(
        "How do I look up the lineage or call hierarchy of a pipeline?",
        """<p>Open the <strong>ADF Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Click the <strong>Hierarchy</strong> tab at the top of the report.</li>
  <li>The first section shows <em>Triggered Entry Points</em> — pipelines that run on a schedule or event. Each one expands to show the full call tree it kicks off (e.g. <code>PL_MASTER → PL_INTERMEDIATE → PL_CHILD_A, PL_CHILD_B</code>).</li>
  <li>The second section lists <em>Untriggered Orchestrators</em> — pipelines that call children but have no trigger of their own (likely called manually or from a parent not yet captured).</li>
  <li>The third section shows <em>Standalone Pipelines</em> — no trigger and no parent — grouped by likely purpose (Test, Manual, Archive, etc.).</li>
</ol>
<p>Alternatively, click the <strong>Activities</strong> stat card and search for an <em>Execute Pipeline</em> activity to see which pipeline is the caller.</p>
<p class="tip">The Lineage tab on the same report shows a visual graph of dataset flow across linked services.</p>"""
        + D('adfmon'),
        "engineer"
    )

    q_and_a += qa(
        "Does this data exist in the data mart, source/staging, reporting layer, or the ACE warehouse?",
        """<p>Every schema in Synapse and SQL DW is classified into a <em>layer</em> based on its naming prefix:</p>
<ul>
  <li><strong>SM_*</strong> — Source / Staging: raw or lightly transformed data loaded from source systems.</li>
  <li><strong>DM_*</strong> — Data Mart: curated, modelled datasets ready for analysis and reporting.</li>
  <li><strong>Reporting_*</strong> — Reporting: final-layer objects aligned to specific programs or business units.</li>
  <li><strong>HUB_*</strong> — Hub: shared reference or integration objects.</li>
  <li><strong>*_DBA</strong> — Operations: DBA utilities and maintenance objects.</li>
  <li><strong>—</strong> (Other): schemas that do not follow the above conventions (e.g. <code>Record_Linkage</code>, <code>COMMON_REFERENCE</code>, <code>dbo</code>).</li>
</ul>
<p>To check a specific dataset:</p>
<ol>
  <li>Open the <strong>Synapse Metadata Report</strong> or <strong>SQL DW Metadata Report</strong>.</li>
  <li>Use the <strong>Overview</strong> tab to filter by layer using the colored layer pills, or search for the table directly.</li>
  <li>The <strong>Layer</strong> column in every table shows the classification at a glance.</li>
</ol>
<p class="tip">For ACE-specific data, search the <strong>Columns</strong> tab for <code>ACE_</code> to find all tables that carry ACE-sourced fields.</p>""",
        "analyst"
    )

    # ── additional Q&A ──────────────────────────────────────────────────────────
    q_and_a += qa(
        "How many ADF pipelines ran in the last 7 days, and how many failed?",
        """<p>Open the <strong>ADF Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Click the <strong>Monitor</strong> tab.</li>
  <li>The table shows all pipeline runs from the last 7 days with status (Succeeded / Failed / In Progress), start time, duration, and a direct link to the run in the ADF portal.</li>
  <li>Use the status filter chips at the top to isolate failures.</li>
</ol>
<p class="tip">For ongoing failure alerts, the team also runs an automated <code>adf_pipeline_failure_check.py</code> that posts to the Teams channel when failures are detected.</p>""",
        "exec"
    )

    q_and_a += qa(
        "How many tables, views, and stored procedures does IDOH manage across environments?",
        """<p>The <strong>index page</strong> (this Marketplace) shows all reports with their last-refresh timestamps. For detailed counts:</p>
<ul>
  <li><strong>Synapse Analytics</strong>: open the Synapse Metadata Report for DEV or PRD — the stat cards at the top show counts for tables, views, procs, foreign keys, and columns.</li>
  <li><strong>SQL Data Warehouse</strong>: open the SQL DW Metadata Report — same stat card layout.</li>
</ul>
<p>DEV and PRD are maintained separately; comparing them shows what has been promoted to production.</p>""",
        "exec"
    )

    q_and_a += qa(
        "Is there any security or data-exfiltration risk in our network configuration?",
        """<p>Open the <strong>VNet Metadata Report</strong>.</p>
<ol>
  <li>The report covers all Virtual Networks, subnets, and Network Security Group (NSG) rules.</li>
  <li>Look for the <em>risk indicators</em> section — the report flags NSG rules that allow broad inbound or outbound internet access, which can indicate data-exfiltration exposure.</li>
  <li>Private endpoints are also listed so you can confirm that services (SQL pools, Key Vaults, Storage) are accessed over the private network rather than the public internet.</li>
</ol>""",
        "exec"
    )

    q_and_a += qa(
        "How do I find what triggers are scheduled in ADF and when they run?",
        """<p>Open the <strong>ADF Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Click the <strong>Triggers</strong> stat card.</li>
  <li>Each trigger shows its type (Schedule or Tumbling Window), recurrence (e.g. Daily at 02:00), enabled/disabled status, and which pipeline(s) it fires.</li>
</ol>
<p class="tip">The Hierarchy tab also shows triggers as the root nodes of each pipeline call tree.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I search for a column across all tables to find where a piece of data lives?",
        """<p>Open the <strong>Synapse Metadata Report</strong> (or <strong>SQL DW Metadata Report</strong> for the ACE warehouse) for the desired environment.</p>
<ol>
  <li>Click the <strong>Columns</strong> stat card.</li>
  <li>Type any part of the column name into the search box — partial matches work.</li>
  <li>Results show every table and view that has a matching column, along with the data type, nullability, and schema layer.</li>
</ol>
<p class="tip">This is the fastest way to answer "does any table store patient address?" or "where is the encounter date column?"</p>""",
        "analyst"
    )

    q_and_a += qa(
        "How do I find foreign key relationships between tables in Synapse?",
        """<p>Open the <strong>Synapse Metadata Report</strong> for the desired environment and click the <strong>Foreign Keys</strong> stat card.</p>
<p>The table lists every defined FK relationship: the referencing table and column, the referenced table and column, and the schema layer of each.</p>
<p class="tip">Note: in Synapse Dedicated SQL Pool, foreign keys are <em>not enforced</em> at runtime — they are metadata-only hints used by query optimizers and BI tools for auto-join suggestions. Data can still be loaded that violates them.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see dependencies between views, procedures, and tables in Synapse?",
        """<p>Open the <strong>Synapse Metadata Report</strong> and click the <strong>Dependencies</strong> stat card.</p>
<p>The dependencies table shows which objects reference other objects — for example, a view that reads from a specific table, or a stored procedure that calls another procedure.</p>
<p>This is essential before renaming or dropping an object: you can see exactly what else would break.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "What Databricks clusters and jobs exist, and are they running?",
        """<p>Open the <strong>Databricks Metadata Report</strong> (covers IZ-DEV, DEV, and PRD workspaces).</p>
<ol>
  <li>The <strong>Clusters</strong> section shows all clusters with their state (Running / Terminated), Databricks Runtime version, node type, and auto-termination settings.</li>
  <li>The <strong>Jobs</strong> section lists scheduled and triggered jobs with last-run status and schedule.</li>
  <li>The <strong>SQL Warehouses</strong> section covers serverless and pro SQL compute endpoints.</li>
</ol>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I find what data is stored in ADLS and how large each container is?",
        """<p>Open the <strong>ADLS Gen2 Metadata Report</strong>.</p>
<ol>
  <li>The report covers all HNS-enabled storage accounts across environments.</li>
  <li>For each storage account you can see the filesystems (containers), top-level directory trees, file counts, and storage sizes.</li>
  <li>This is useful for understanding data lake layout, identifying unused containers, and estimating storage costs.</li>
</ol>""",
        "mgmt"
    )

    q_and_a += qa(
        "How do I find what linked services are configured in ADF?",
        """<p>Open the <strong>ADF Metadata Report</strong> and click the <strong>Linked Services</strong> stat card.</p>
<p>Linked services define the connection strings and authentication method for every external system ADF connects to — SQL pools, storage accounts, Databricks workspaces, REST APIs, and more.</p>
<p class="tip">No credentials or secrets are shown — only the service name, type, and connection endpoint.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see what secrets are configured in Key Vault?",
        """<p>Open the <strong>Key Vault Metadata Report</strong> for the desired environment.</p>
<p>The report lists all secrets, keys, and certificates with their names, enabled/disabled status, and expiry dates. <strong>No values are shown</strong> — only metadata.</p>
<p>Use this report to audit which secrets are near expiry, which are disabled, and what access policies are in place.</p>""",
        "mgmt"
    )

    q_and_a += qa(
        "How do I find stale or inactive branches in DevOps?",
        """<p>Open the <strong>DevOps Metadata Report</strong>.</p>
<ol>
  <li>The <strong>Branches</strong> section shows every branch across all repositories, colour-coded by activity (active = recent commit, stale = no recent commit).</li>
  <li>Ahead/behind counts show how far a branch has diverged from its base branch.</li>
  <li>Stale branches are candidates for clean-up to reduce repository clutter.</li>
</ol>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see the distribution type and index type of a table in SQL DW?",
        """<p>Open the <strong>SQL DW Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Click the <strong>Tables</strong> stat card.</li>
  <li>Each table row shows: schema, table name, distribution type (<em>Hash, Round Robin, or Replicated</em>), the hash distribution column (if applicable), and index type (<em>Clustered Columnstore, Heap, or Clustered</em>).</li>
</ol>
<p>Distribution type determines how data is spread across the 60 compute nodes. Hash distribution on a join key eliminates data movement. Replicated tables are small lookup tables copied to every node. Round Robin is a default that distributes rows evenly but without join optimization.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "What Logic Apps are failing, and when did they last run successfully?",
        """<p>Open the <strong>Logic Apps Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>The report lists all workflows with their trigger type (HTTP, schedule, event), recent run history, and success/failure status.</li>
  <li>Failed runs are highlighted so you can quickly identify which workflows need attention.</li>
</ol>
<p class="tip">Logic Apps are often used for notification workflows (e.g. Teams alerts, email triggers) and API orchestration. If a notification is not being received, start here to check the workflow run status.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I tell if data in DEV has been promoted to PRD?",
        """<p>The Synapse Metadata Reports for DEV and PRD are separate. To compare:</p>
<ol>
  <li>Open the <strong>Synapse Delta Report — PRD</strong> and look for recent additions that match objects you deployed.</li>
  <li>Alternatively, open both Synapse Metadata Reports side-by-side and search for the object by name in each.</li>
  <li>The object's <em>Created</em> date in PRD will confirm when it was promoted.</li>
</ol>""",
        "mgmt"
    )

    q_and_a += qa(
        "Where can I see pull requests and code review activity in DevOps?",
        """<p>Open the <strong>DevOps Metadata Report</strong>.</p>
<p>The <strong>Pull Requests</strong> section lists open and recently completed PRs across all repositories — including title, author, target branch, creation date, and current status.</p>
<p>Branch policies (required reviewers, build validation) are also shown so you can verify governance rules are in place.</p>""",
        "mgmt"
    )

    q_and_a += qa(
        "How do I see when reports were last refreshed?",
        """<p>The <strong>index page</strong> (the Marketplace you are on now) shows a "↻ YYYY-MM-DD HH:MM" timestamp on every report card.</p>
<ul>
  <li>A <span style="color:var(--grn)">green</span> timestamp means the report is current (refreshed within the last 25 hours).</li>
  <li>A <span style="color:var(--yel)">yellow</span> timestamp means the report is stale — it has not been refreshed in over 25 hours.</li>
  <li>A <span style="color:var(--red)">red</span> "Never generated" label means the report file does not yet exist.</li>
</ul>
<p>Reports are typically regenerated by the nightly <code>publish_synapse_metadata.sh</code> run.</p>""",
        "all"
    )

    q_and_a += qa(
        "How do I see which ADF pipeline last ran and whether it succeeded?",
        """<p>Open the <strong>ADF Metadata Report</strong> and click the <strong>Monitor</strong> tab.</p>
<p>The run history table shows the most recent 7 days of pipeline runs sorted newest-first, with status chips (Succeeded / Failed / InProgress), start time, and duration. Click the run ID link to open that specific run directly in the ADF portal for detailed activity logs.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "What schemas exist and what do they contain?",
        """<p>Open the <strong>Synapse Metadata Report</strong> or <strong>SQL DW Metadata Report</strong> and click the <strong>Schemas</strong> tab (or Overview tab).</p>
<p>The Overview shows a layer breakdown with colored pills and a table count per layer. Clicking a layer pill filters the schema list to show only schemas in that layer. Each schema row shows its table count, view count, and procedure count.</p>""",
        "analyst"
    )

    # ── additional Q&A round 2 ──────────────────────────────────────────────────

    q_and_a += qa(
        "How do I know if a nightly data load completed successfully?",
        """<p>Open the <strong>ADF Metadata Report</strong> for the desired environment and click the <strong>Monitor</strong> tab.</p>
<ol>
  <li>The table shows all pipeline runs from the last 7 days, newest first.</li>
  <li>Look for your nightly pipeline (often ending in <code>_MASTER</code> or <code>_INGEST</code>) and check its <em>Status</em> chip — green Succeeded or red Failed.</li>
  <li>The <em>Started</em> and <em>Duration</em> columns confirm what time it ran and how long it took.</li>
  <li>Click the Run ID link to open that specific run in the ADF portal for step-by-step activity logs.</li>
</ol>
<p class="tip">If the pipeline does not appear in the last 7 days, the trigger may be disabled. Check the <strong>Triggers</strong> tab to verify the trigger is enabled and its schedule.</p>""",
        "exec"
    )

    q_and_a += qa(
        "What data programs and health domains are represented in the warehouse?",
        """<p>The schema naming conventions reveal which programs own which data. Open either the <strong>Synapse Metadata Report</strong> or <strong>SQL DW Metadata Report</strong> and look at the Overview or Schemas tab:</p>
<ul>
  <li>Schema names beginning with the program abbreviation identify the domain — e.g. <code>SM_CHIRP_01</code> (CHIRP immunizations), <code>SM_Hospital_Discharge_01</code> (hospital discharge), <code>DM_SyndromicSurveillance_01</code> (syndromic surveillance).</li>
  <li>The <strong>sidebar search</strong> filters schemas as you type — type <code>CHIRP</code>, <code>Syndromic</code>, <code>RL</code>, or any program keyword to see all related schemas at once.</li>
  <li>The <strong>Columns</strong> tab search is useful too — searching a program-specific column prefix (e.g. <code>ACE_</code>, <code>HL7_</code>) surfaces every table that carries data from that source system.</li>
</ul>""",
        "exec"
    )

    q_and_a += qa(
        "Are there any secrets or certificates expiring soon that could cause an outage?",
        """<p>Open the <strong>Key Vault Metadata Report</strong> for DEV and PRD.</p>
<ol>
  <li>The report lists every secret, key, and certificate with its <em>Expires</em> date and current <em>Enabled</em> status.</li>
  <li>Sort or scan the Expires column for dates within the next 30–90 days.</li>
  <li>Disabled secrets are flagged — if a pipeline or application references a disabled secret, it will fail at runtime.</li>
</ol>
<p class="tip">Pipelines that authenticate to external APIs or databases typically reference Key Vault secrets for connection strings and credentials. An expired secret in PRD will cause those pipelines to fail without warning.</p>""",
        "mgmt"
    )

    q_and_a += qa(
        "What is the difference between the Synapse Metadata Report and the SQL DW Metadata Report?",
        """<p>Both reports cover Synapse Dedicated SQL Pools, but they point to <em>different databases</em>:</p>
<ul>
  <li><strong>Synapse Metadata Report</strong> — covers the primary Synapse Analytics workspace pool (<code>zus1-idoh-dev/prd-v2-syn-dw</code>). This is the current, actively developed analytics environment. Schema naming follows the SM_ / DM_ / Reporting_ / HUB_ conventions. It also includes foreign keys, object dependencies, and AI-generated view explanations.</li>
  <li><strong>SQL DW Metadata Report</strong> — covers the legacy Azure SQL Data Warehouse pool (<code>zus1-idoh-dev/prd-v1/v2-sql-dw</code>), sometimes referred to as the ACE warehouse. It follows the same layer conventions and shows distribution type and index type per table.</li>
</ul>
<p>If you are unsure which pool holds the data you need, search for the table name in both reports.</p>""",
        "all"
    )

    q_and_a += qa(
        "How do I find tables that are empty or have very few rows?",
        """<p>Open the <strong>Synapse Metadata Report</strong> or <strong>SQL DW Metadata Report</strong> and click the <strong>Tables</strong> tab.</p>
<ol>
  <li>The <em>Row Count</em> column shows the current row count for every table.</li>
  <li>Click the <strong>Row Count</strong> column header to sort ascending — empty tables (0 rows) and near-empty tables will rise to the top.</li>
  <li>Empty tables in a production environment may indicate a failed data load, a staging table that was never cleaned up, or a table created in advance of a new data feed.</li>
</ol>
<p class="tip">Combine with the Synapse Delta Report — if a table shows 0 rows today but had rows yesterday, the drop will appear as a row-count change in the delta.</p>""",
        "mgmt"
    )

    q_and_a += qa(
        "How do I find all tables related to a specific program (e.g. CHIRP, Syndromic, Hospital Discharge)?",
        """<p>Open the <strong>Synapse Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Type the program name into the <strong>sidebar search box</strong> (e.g. <code>CHIRP</code>, <code>Syndromic</code>, <code>Hospital</code>). The sidebar filters to matching schemas and tables in real time.</li>
  <li>Click any schema in the filtered list to jump to all tables within it.</li>
  <li>Alternatively, click the <strong>Columns</strong> stat card and search for a program-specific column prefix — this surfaces every table across all schemas that stores that program's data, even when it lives in a shared schema like <code>dbo</code>.</li>
</ol>""",
        "analyst"
    )

    q_and_a += qa(
        "How do I find all columns with a specific data type (e.g. all date columns)?",
        """<p>Open the <strong>Synapse Metadata Report</strong> or <strong>SQL DW Metadata Report</strong> and click the <strong>Columns</strong> stat card.</p>
<ol>
  <li>The columns table shows every column with its <em>Data Type</em>.</li>
  <li>Use the search box and type a data type keyword — e.g. <code>date</code>, <code>varchar</code>, <code>int</code>, <code>decimal</code> — to filter to all columns of that type across the entire database.</li>
  <li>This is useful for finding candidate join keys, identifying columns that store timestamps, or auditing columns that should be typed as <code>date</code> but are stored as <code>varchar</code>.</li>
</ol>""",
        "analyst"
    )

    q_and_a += qa(
        "How do I identify the largest tables by row count?",
        """<p>Open the <strong>Synapse Metadata Report</strong> or <strong>SQL DW Metadata Report</strong> and click the <strong>Tables</strong> tab.</p>
<ol>
  <li>Click the <strong>Row Count</strong> column header to sort descending — the largest tables appear at the top.</li>
  <li>The Overview tab also shows a <em>Top Tables by Row Count</em> widget that lists the 10 largest tables without any clicking.</li>
</ol>
<p class="tip">Large tables with ROUND_ROBIN distribution can cause significant data movement during joins. If a large table appears, check its Distribution Type — if it is Round Robin, redistributing it on a join key could improve query performance.</p>""",
        "analyst"
    )

    q_and_a += qa(
        "Can I see the DDL (CREATE TABLE statement) for a table in Synapse?",
        """<p>Yes. Open the <strong>Synapse Metadata Report</strong> for the desired environment.</p>
<ol>
  <li>Find the table using the sidebar search or the All Objects tab.</li>
  <li>Click the table row to open its detail panel.</li>
  <li>The detail panel includes a <strong>DDL</strong> section showing the full <code>CREATE TABLE</code> statement — columns, data types, nullability, distribution clause, and index type.</li>
</ol>
<p class="tip">The DDL is useful for recreating a table in another environment, comparing schema between DEV and PRD, or onboarding a new team member who needs to understand the physical table structure.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I find which views or procedures depend on a specific table?",
        """<p>Open the <strong>Synapse Metadata Report</strong> and click the <strong>Dependencies</strong> stat card.</p>
<ol>
  <li>Search for the table name in the search box.</li>
  <li>The results show every object that references the table — views that SELECT from it, stored procedures that read or write to it, and other tables referenced through views.</li>
</ol>
<p>This is essential <em>before dropping or renaming a table</em> — any object that depends on it will break if the table disappears. The dependency list tells you exactly what would need to be updated first.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see what data flows exist in ADF and what they transform?",
        """<p>Open the <strong>ADF Metadata Report</strong> and click the <strong>Data Flows</strong> stat card.</p>
<ol>
  <li>The list shows all mapping data flows defined in ADF with their source and sink types.</li>
  <li>Data flows appear as activities inside pipelines — if a pipeline uses a data flow, you will see a <em>ExecuteDataFlow</em> activity type in the Activities card for that pipeline.</li>
</ol>
<p class="tip">Data flows are Spark-based transformations that run on the integration runtime's compute cluster. If a data flow is slow or failing, the ADF Monitor tab will show the run duration and error details for the parent pipeline that executed it.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I find which ADF datasets reference a specific linked service or storage location?",
        """<p>Open the <strong>ADF Metadata Report</strong> and click the <strong>Datasets</strong> stat card.</p>
<ol>
  <li>Each dataset row shows the linked service it connects through, the dataset type (e.g. Azure SQL, ADLS Parquet, Delimited Text), and the file path or table name it points to.</li>
  <li>Search for a linked service name (e.g. the name of a storage account or SQL server) to see all datasets that use it.</li>
</ol>
<p class="tip">If you are decommissioning a linked service or changing a connection string in Key Vault, checking the Datasets list first tells you which pipelines will be affected — any pipeline with an activity that uses one of those datasets.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I find tables using Round Robin distribution that might be candidates for redistribution?",
        """<p>Open the <strong>SQL DW Metadata Report</strong> and click the <strong>Tables</strong> tab.</p>
<ol>
  <li>The <em>Distribution</em> column shows <code>Round Robin</code>, <code>Hash</code>, or <code>Replicated</code> for each table.</li>
  <li>Sort or filter by the Distribution column to isolate Round Robin tables.</li>
  <li>Large Round Robin tables (high row counts) joined frequently to other large tables are prime candidates for HASH distribution on the most common join key — this eliminates data movement at query time.</li>
  <li>Small lookup or reference tables are better suited to <code>REPLICATED</code> distribution.</li>
</ol>
<p class="tip">The <em>Distribution Column</em> in the same table shows which column is used as the hash key — useful when reviewing whether the key is optimal for the workload.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I check if a Databricks cluster is configured to auto-terminate?",
        """<p>Open the <strong>Databricks Metadata Report</strong> and navigate to the <strong>Clusters</strong> section for the relevant workspace (IZ-DEV, DEV, or PRD).</p>
<ol>
  <li>Each cluster row shows its <em>Auto-Terminate</em> setting (the idle timeout in minutes before the cluster shuts down).</li>
  <li>Clusters with no auto-termination set will continue running indefinitely when idle, incurring unnecessary compute costs.</li>
  <li>The <em>State</em> column shows whether the cluster is currently Running or Terminated.</li>
</ol>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see private endpoints and verify services are accessed over the private network?",
        """<p>Open the <strong>VNet Metadata Report</strong>.</p>
<ol>
  <li>The <strong>Private Endpoints</strong> section lists every private endpoint across all VNets — the service it connects to (e.g. Key Vault, SQL pool, Storage), the private IP address, and its approval state.</li>
  <li>Services with a private endpoint should have their public network access disabled. If a service appears without a private endpoint, traffic may be going over the public internet.</li>
  <li>VNet peerings are also shown — confirm that DEV and PRD VNets are peered correctly for any cross-environment communication.</li>
</ol>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see build pipeline run history and whether CI/CD is healthy in DevOps?",
        """<p>Open the <strong>DevOps Metadata Report</strong>.</p>
<ol>
  <li>The <strong>Build Pipelines</strong> section lists all Azure Pipelines definitions with their last run result (Succeeded / Failed / Partially Succeeded) and timestamp.</li>
  <li>Failed builds are highlighted — click through to the Azure DevOps portal for detailed logs.</li>
  <li>The <strong>Deployment Environments</strong> section shows configured environments (DEV, PRD) and any pending approvals.</li>
</ol>
<p class="tip">If a pipeline deployment to PRD is blocked, it may be waiting on a required approval in the DevOps Environments section. The report will show the environment's approval policy so you know who needs to sign off.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I find which ADF pipelines have the most activities (most complex pipelines)?",
        """<p>Open the <strong>ADF Metadata Report</strong> and click the <strong>Pipelines</strong> stat card.</p>
<ol>
  <li>The pipeline list includes an <em>Activity Count</em> column.</li>
  <li>Sort by Activity Count descending to surface the most complex pipelines.</li>
  <li>Click a pipeline to see the breakdown of activity types it uses (Copy, Execute Pipeline, Stored Procedure, Lookup, etc.).</li>
</ol>
<p class="tip">Pipelines with a very high activity count may be candidates for refactoring into child pipelines — the Hierarchy tab shows whether a pipeline already delegates to children, or whether all logic is in one flat sequence.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I see the integration runtime used by a pipeline or dataset?",
        """<p>Open the <strong>ADF Metadata Report</strong> and click the <strong>Integration Runtimes</strong> stat card.</p>
<ol>
  <li>The list shows all IRs with their type (Azure, Self-Hosted), region, and current status.</li>
  <li>To see which IR a specific pipeline uses: open the <strong>Datasets</strong> card, find a dataset used by the pipeline, and check its linked service — the linked service configuration determines which IR handles the connection.</li>
  <li>Self-Hosted IRs are used for on-premises or private network data sources. If an SHIR is offline, any pipeline using it will fail.</li>
</ol>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I verify a Key Vault secret has not expired before running a pipeline?",
        """<p>Open the <strong>Key Vault Metadata Report</strong> for the appropriate environment.</p>
<ol>
  <li>Find the secret by name — secrets are listed with their <em>Enabled</em> flag and <em>Expires</em> date.</li>
  <li>A secret that is disabled or past its expiry date will return an authorization error when ADF or any application tries to read it, causing pipeline failures.</li>
  <li>If a secret shows as expired, it needs to be renewed in the Azure portal and the new version may need to be referenced in the linked service or Key Vault reference used by the pipeline.</li>
</ol>
<p class="tip">ADF linked services that use Key Vault for credential storage reference a specific secret name. If the secret is rotated (new version created), ADF automatically picks up the latest version as long as the secret name stays the same.</p>""",
        "engineer"
    )

    q_and_a += qa(
        "How do I track changes to a specific object across multiple days in Synapse?",
        """<p>The <strong>Synapse Delta Reports</strong> capture one day of change at a time. To track an object over multiple days:</p>
<ol>
  <li>Check today's <strong>Synapse Delta Report</strong> for the object — if it appears in Added, Modified, or Removed, that is the most recent change.</li>
  <li>For longer history, use the <strong>Synapse Metadata Report</strong> to see the object's <em>Created</em> and <em>Last Modified</em> dates directly — these timestamps come from the SQL pool's system catalog and reflect the actual DDL history.</li>
  <li>For code-level change history on stored procedures or views, check the <strong>DevOps Metadata Report</strong> — if the object definition is managed in Git, the commit history shows every change with author and date.</li>
</ol>""",
        "mgmt"
    )

    q_and_a += qa(
        "How do I check overall storage usage across the data lake?",
        """<p>Open the <strong>ADLS Gen2 Metadata Report</strong>.</p>
<ol>
  <li>The report shows each storage account with a breakdown by filesystem (container).</li>
  <li>Each container row shows the total number of files, subdirectory count, and total storage size.</li>
  <li>Drill into a container to see the top-level directory tree with sizes — this helps pinpoint which data domains or pipelines are consuming the most storage.</li>
</ol>
<p class="tip">Containers that are growing rapidly but not being read by any active pipeline may be accumulating data without a retention policy. Cross-reference with the ADF report to check whether those paths are referenced by any active datasets.</p>""",
        "mgmt"
    )

    # read changelog
    try:
        with open(CHANGELOG_PATH, encoding="utf-8") as _f:
            _cl_entries = json.load(_f)
    except Exception:
        _cl_entries = []
    def _cl_row(e):
        return (f'    <div class="cl-entry">'
                f'<div class="cl-meta">{e.get("date","")} &nbsp;·&nbsp; {e.get("time","")}</div>'
                f'<div class="cl-name">{e.get("name","")}</div>'
                f'<div class="cl-desc">{e.get("description","")}</div>'
                f'</div>')
    changelog_html = "\n".join(_cl_row(e) for e in _cl_entries) if _cl_entries else \
        '    <div class="cl-entry"><div class="cl-desc">No entries yet.</div></div>'

    # build page
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Help &amp; Guide — IDOH Metadata Marketplace</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">

<!-- SIDEBAR -->
<div class="sidebar">
  <div class="sb-logo">
    <a href="index.html">&#8592; Back to Marketplace</a>
    <div class="sub">IDOH Metadata Help</div>
  </div>

  <div class="sb-section">Navigation</div>
  <a class="sb-item" data-target="overview" onclick="navTo('overview')">Overview</a>
  <a class="sb-item" data-target="reports" onclick="navTo('reports')">Report Directory</a>
  <a class="sb-item" data-target="quickstart" onclick="navTo('quickstart')">Quick Start by Role</a>
  <a class="sb-item" data-target="qa" onclick="navTo('qa')">How Do I&hellip; (Q&amp;A)</a>
  <a class="sb-item" data-target="glossary" onclick="navTo('glossary')">Glossary</a>
  <a class="sb-item" data-target="changelog" onclick="navTo('changelog')">Changelog</a>
</div>

<!-- MAIN -->
<div class="main">

  <a class="back-btn" href="index.html">&#8592; Back to Metadata Marketplace</a>

  <h1>Help &amp; Guide</h1>
  <p class="meta">IDOH Azure Metadata Marketplace &nbsp;&middot;&nbsp; Generated: <span id="gen-ts" data-ts="{generated}">&#x21BB; {generated}</span><script>(function(){{var s=document.getElementById('gen-ts'),h=(Date.now()-new Date(s.dataset.ts.replace(' ','T')))/36e5;s.style.color=h<25?'var(--grn)':h<168?'var(--yel)':'var(--red)';s.style.fontWeight='700';}})();</script></p>

  <!-- OVERVIEW -->
  <h2 id="overview">Overview</h2>
  <p>The <strong>IDOH Metadata Marketplace</strong> is a centralized, self-service documentation portal for the Office of Data Analytics at the Indiana Department of Health. It provides up-to-date, automatically generated reports covering every major Azure data platform component — from raw storage through pipelines, data warehouses, analytics workspaces, and the supporting infrastructure.</p>
  <p>Reports are refreshed automatically each night and published to a secure Azure Static Web App. You do not need access to the Azure portal to browse metadata — everything is surfaced here in a readable, searchable format.</p>
  <div class="audience-row">
    <div class="aud exec">&#127775; Executives &amp; Directors</div>
    <div class="aud mgmt">&#128203; Data Management</div>
    <div class="aud analyst">&#128200; Business Analysts</div>
    <div class="aud engineer">&#9881;&#65039; Data Engineers</div>
  </div>

  <!-- REPORT DIRECTORY -->
  <h2 id="reports">Report Directory</h2>

  <div class="report-grid">

    <div class="report-card">
      <div class="report-card-cat">&#128450;&#65039; Data Catalog</div>
      <h4>IDOH Data Catalog</h4>
      <p>The business-facing front door to the data platform. Browse available datasets by domain, view data stewardship and refresh cadence, track datasets that are requested or in review, and submit new data-sharing requests. Answers "what data do we have and how do I get access?" without requiring any Azure knowledge.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="data_catalog.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#129521; Azure Synapse Analytics</div>
      <h4>Synapse Metadata Report</h4>
      <p>Complete inventory of the Synapse Dedicated SQL Pool: schemas, tables (with row counts and column details), views with full T-SQL definitions and plain-English explanations, stored procedures, foreign keys, object dependencies, and all columns. The primary place to explore what data exists and how objects relate to each other.</p>
      <div class="tags">
        <span class="tag tag-dev">DEV</span>
        <span class="tag tag-prd">PRD</span>
        <span class="tag tag-link"><a href="synapse_metadata_report_dev.html">Open DEV &#8599;</a></span>
        <span class="tag tag-link"><a href="synapse_metadata_report_prd.html">Open PRD &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#129521; Azure Synapse Analytics</div>
      <h4>Synapse Delta Report</h4>
      <p>Day-over-day change tracking for the Synapse SQL Pool. Shows objects added, removed, or modified since the previous snapshot, plus column-level changes and tables whose row count changed significantly. Use this to answer "what changed overnight?"</p>
      <div class="tags">
        <span class="tag tag-dev">DEV</span>
        <span class="tag tag-prd">PRD</span>
        <span class="tag tag-link"><a href="synapse_metadata_delta_dev.html">Open DEV &#8599;</a></span>
        <span class="tag tag-link"><a href="synapse_metadata_delta_prd.html">Open PRD &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#128257; Azure Data Factory</div>
      <h4>ADF Metadata Report</h4>
      <p>Complete inventory of Azure Data Factory pipelines and orchestration. Covers pipelines with all activities, datasets, linked services, triggers (schedule &amp; event), data flows, integration runtimes, and 7-day run history. The Hierarchy tab shows the full pipeline call tree from triggers through master, intermediate, and child pipelines.</p>
      <div class="tags">
        <span class="tag tag-dev">DEV</span>
        <span class="tag tag-prd">PRD</span>
        <span class="tag tag-link"><a href="adf_metadata_report_dev.html">Open DEV &#8599;</a></span>
        <span class="tag tag-link"><a href="adf_metadata_report_prd.html">Open PRD &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#128452;&#65039; Azure Data Lake Storage Gen2</div>
      <h4>ADLS Gen2 Metadata Report</h4>
      <p>Inventory of all HNS-enabled Azure Data Lake Storage accounts: filesystems (containers), directory trees, file and folder counts, and storage sizes. Use this to understand the raw data lake layout, identify unused containers, and estimate storage costs.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="adls_metadata_report.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#9889; Azure Logic Apps</div>
      <h4>Logic Apps Metadata Report</h4>
      <p>Inventory of Azure Logic Apps workflows: trigger types (HTTP, schedule, event), action counts, API connections used, and recent run history with success/failure status. Logic Apps are used for notification workflows (Teams alerts, email) and lightweight API integrations.</p>
      <div class="tags">
        <span class="tag tag-dev">DEV</span>
        <span class="tag tag-prd">PRD</span>
        <span class="tag tag-link"><a href="logic_apps_metadata_report_dev.html">Open DEV &#8599;</a></span>
        <span class="tag tag-link"><a href="logic_apps_metadata_report_prd.html">Open PRD &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#9881;&#65039; Azure Databricks</div>
      <h4>Databricks Metadata Report</h4>
      <p>Covers all three Databricks workspaces (IZ-DEV, DEV, and PRD): clusters with state and runtime version, scheduled jobs, Git-linked repos, SQL warehouses, cluster policies, and secret scope names (no values). Use this to see what compute exists and which jobs are scheduled.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="databricks_metadata_report.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#127963;&#65039; Azure SQL Data Warehouse</div>
      <h4>SQL DW Metadata Report</h4>
      <p>Complete inventory of the legacy Azure SQL Data Warehouse (Synapse Dedicated SQL Pool — ACE warehouse). Covers schemas grouped by layer (SM / DM / Reporting), tables with distribution type and index type, accurate row counts, views, stored procedures, and all columns. The schema Layer column identifies which tier of the data architecture each object belongs to.</p>
      <div class="tags">
        <span class="tag tag-dev">DEV</span>
        <span class="tag tag-prd">PRD</span>
        <span class="tag tag-link"><a href="sql_dw_metadata_report_dev.html">Open DEV &#8599;</a></span>
        <span class="tag tag-link"><a href="sql_dw_metadata_report_prd.html">Open PRD &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#127760; Azure Networking</div>
      <h4>VNet Metadata Report</h4>
      <p>Azure Virtual Network security and topology inventory: VNets, subnets, NSG rules (inbound and outbound), private endpoints, VNet peerings, and data-exfiltration risk indicators. Use this to confirm that services are accessed over private endpoints and that NSG rules do not expose data to the public internet.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="vnet_metadata_report.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#128421;&#65039; Azure Virtual Desktop</div>
      <h4>AVD Session Host Inventory</h4>
      <p>Inventory of all 142 host pools in the ECAE Shared Production subscription: session host status, last heartbeat, active session counts, assigned users, and identification of stale or unresponsive machines. Use this to confirm virtual desktop availability and find hosts that need attention.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="avd_metadata_report.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#128272; Azure Security &amp; Access</div>
      <h4>Security Groups &amp; Access Report</h4>
      <p>All Entra ID security groups with their Azure role assignments across every subscription — group members, roles held, and which Synapse, Databricks, ADF, Key Vault, and storage resources each group can access. Use this to audit who has access to what and confirm least-privilege assignments.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="azure_security_groups_report.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#128295; Azure DevOps</div>
      <h4>DevOps Metadata Report</h4>
      <p>Azure DevOps project and repository inventory: repos with size and last activity, branches (active/stale, ahead/behind main), build pipelines and recent run history, open and completed pull requests, branch protection policies, and deployment environments. Use this for development velocity tracking and branch hygiene audits.</p>
      <div class="tags">
        <span class="tag tag-all">ALL ENVS</span>
        <span class="tag tag-link"><a href="ado_metadata_report.html">Open &#8599;</a></span>
      </div>
    </div>

    <div class="report-card">
      <div class="report-card-cat">&#128273; Azure Key Vault</div>
      <h4>Key Vault Metadata Report</h4>
      <p>Secret, key, and certificate inventory (names and metadata only — no values are ever exposed). Shows enabled/disabled status, expiry dates, and access policies. Use this to audit secrets nearing expiry and confirm that access is correctly restricted.</p>
      <div class="tags">
        <span class="tag tag-dev">DEV</span>
        <span class="tag tag-prd">PRD</span>
        <span class="tag tag-link"><a href="keyvault_metadata_report_dev.html">Open DEV &#8599;</a></span>
        <span class="tag tag-link"><a href="keyvault_metadata_report_prd.html">Open PRD &#8599;</a></span>
      </div>
    </div>

  </div>

  <!-- QUICK START -->
  <h2 id="quickstart">Quick Start by Role</h2>

  <h3>&#127775; Executive / Director</h3>
  <ul>
    <li><strong>Data asset inventory</strong>: Synapse Metadata Report → stat cards show total tables, views, procs, and columns at a glance.</li>
    <li><strong>What changed this week</strong>: Synapse Delta Report → purple pursue cards summarize additions, removals, and modifications.</li>
    <li><strong>Pipeline health</strong>: ADF Metadata Report → Monitor tab for 7-day run history and failure counts.</li>
    <li><strong>Security posture</strong>: VNet Metadata Report → risk indicators highlight open NSG rules and missing private endpoints.</li>
    <li><strong>Report freshness</strong>: index page → every card shows the last-refresh timestamp in green (current) or yellow (stale).</li>
  </ul>

  <h3>&#128203; Data Management</h3>
  <ul>
    <li><strong>Schema inventory by layer</strong>: Synapse or SQL DW Metadata Report → Overview tab → layer pills filter by SM / DM / Reporting.</li>
    <li><strong>Object change tracking</strong>: Synapse Delta Report → track what was added, removed, or modified day over day.</li>
    <li><strong>Data promotion (DEV → PRD)</strong>: compare DEV and PRD Synapse Metadata Reports side-by-side, or check the PRD Delta Report for recent additions.</li>
    <li><strong>Storage usage</strong>: ADLS Gen2 Metadata Report → container-level file counts and sizes.</li>
    <li><strong>Secret expiry audit</strong>: Key Vault Metadata Report → filter by expiry date.</li>
  </ul>

  <h3>&#128200; Business Analyst</h3>
  <ul>
    <li><strong>Find a column across all tables</strong>: Synapse or SQL DW Metadata Report → Columns tab → type any part of the column name.</li>
    <li><strong>Understand a view</strong>: Synapse Metadata Report → Views tab → click a view → Show / Hide Definition shows code + plain-English explanation.</li>
    <li><strong>Find where a dataset lives</strong>: check the Layer column — SM_ = source/staging, DM_ = data mart, Reporting_ = final reporting layer.</li>
    <li><strong>Row counts</strong>: Synapse or SQL DW Metadata Report → Tables tab → Row Count column shows live distribution-level counts.</li>
    <li><strong>ACE-sourced columns</strong>: Columns tab → search <code>ACE_</code> to find all ACE-prefixed fields.</li>
  </ul>

  <h3>&#9881;&#65039; Data Engineer</h3>
  <ul>
    <li><strong>Pipeline call hierarchy</strong>: ADF Metadata Report → Hierarchy tab shows full trigger → master → child tree.</li>
    <li><strong>Table distribution &amp; index type</strong>: SQL DW Metadata Report → Tables tab.</li>
    <li><strong>Foreign keys &amp; dependencies</strong>: Synapse Metadata Report → Foreign Keys and Dependencies tabs — essential before dropping or renaming objects.</li>
    <li><strong>Linked services &amp; datasets</strong>: ADF Metadata Report → Linked Services and Datasets cards.</li>
    <li><strong>Databricks cluster/job state</strong>: Databricks Metadata Report.</li>
    <li><strong>Branch hygiene</strong>: DevOps Metadata Report → Branches section → stale branches flagged.</li>
    <li><strong>Logic App run failures</strong>: Logic Apps Metadata Report → run history with failure highlights.</li>
  </ul>

  <!-- Q&A -->
  <div class="qa-wrapper">
  <h2 id="qa">How Do I&hellip;</h2>
  <div class="filter-bar">
    <button class="filter-btn active" data-filter="all"     onclick="filterQA('all')">All Questions</button>
    <button class="filter-btn"        data-filter="exec"    onclick="filterQA('exec')">Executive</button>
    <button class="filter-btn"        data-filter="mgmt"    onclick="filterQA('mgmt')">Data Management</button>
    <button class="filter-btn"        data-filter="analyst" onclick="filterQA('analyst')">Business Analyst</button>
    <button class="filter-btn"        data-filter="engineer" onclick="filterQA('engineer')">Data Engineer</button>
  </div>
  {q_and_a}
  </div><!-- /qa-wrapper -->

  <!-- GLOSSARY -->
  <h2 id="glossary">Glossary</h2>
  <input class="gl-search" id="gl-search" placeholder="Search terms…" oninput="filterGlossary(this.value)" autocomplete="off"/>
  <div id="gl-list">
    <div class="gl-row"><div class="gl-term">SM_ (Source / Staging)</div><div class="gl-def">Schema naming prefix for raw or lightly transformed data loaded directly from source systems. These are the first landing zone for ingested data.</div></div>
    <div class="gl-row"><div class="gl-term">DM_ (Data Mart)</div><div class="gl-def">Schema naming prefix for curated, business-modelled datasets ready for analysis, reporting, and BI tools.</div></div>
    <div class="gl-row"><div class="gl-term">Reporting_</div><div class="gl-def">Schema naming prefix for final-layer objects aligned to specific programs or business units — the datasets that feed dashboards and reports.</div></div>
    <div class="gl-row"><div class="gl-term">HUB_</div><div class="gl-def">Schema naming prefix for shared reference or integration objects used across multiple domains.</div></div>
    <div class="gl-row"><div class="gl-term">CCI (Clustered Columnstore Index)</div><div class="gl-def">The default and recommended index type for large Synapse tables. Stores data column-by-column (rather than row-by-row), enabling highly compressed storage and fast analytical queries. Shown as "Clustered Columnstore" in the index type column.</div></div>
    <div class="gl-row"><div class="gl-term">HEAP</div><div class="gl-def">A table with no index — rows are stored in no particular order. Typically used as a staging table for bulk loads before being transformed into a CCI table.</div></div>
    <div class="gl-row"><div class="gl-term">HASH Distribution</div><div class="gl-def">Rows are distributed across the 60 compute nodes based on a hash of a chosen column (the distribution key). Optimal when large tables are joined on the distribution column, as data movement is minimized.</div></div>
    <div class="gl-row"><div class="gl-term">ROUND ROBIN Distribution</div><div class="gl-def">Rows are spread evenly across the 60 compute nodes in round-robin order. Good for loading speed and staging, but may require data movement during joins.</div></div>
    <div class="gl-row"><div class="gl-term">REPLICATED</div><div class="gl-def">The entire table is copied to every compute node. Best for small dimension/lookup tables that are joined frequently.</div></div>
    <div class="gl-row"><div class="gl-term">Private Endpoint</div><div class="gl-def">A network interface that connects an Azure service (e.g. SQL pool, Key Vault, Storage) to a VNet using a private IP address, keeping traffic off the public internet.</div></div>
    <div class="gl-row"><div class="gl-term">NSG (Network Security Group)</div><div class="gl-def">A set of inbound and outbound network rules that control traffic to Azure resources. The VNet report flags rules that may expose resources to broad internet access.</div></div>
    <div class="gl-row"><div class="gl-term">Pipeline Hierarchy</div><div class="gl-def">The parent-child call structure of ADF pipelines. A trigger fires a master pipeline, which calls intermediate pipelines via Execute Pipeline activities, which may call further child pipelines. The Hierarchy tab in the ADF report shows this full tree.</div></div>
    <div class="gl-row"><div class="gl-term">Delta Report</div><div class="gl-def">A day-over-day snapshot comparison that shows what objects were added, removed, or modified between two consecutive report generations.</div></div>
    <div class="gl-row"><div class="gl-term">DEV / PRD</div><div class="gl-def">Development and Production environments. DEV is used for building and testing data assets; PRD contains the promoted, production-grade data. Reports for each environment are generated and published separately.</div></div>
    <div class="gl-row"><div class="gl-term">ACE Warehouse</div><div class="gl-def">Refers to the legacy Azure SQL Data Warehouse (Synapse Dedicated SQL Pool) environment. Covered by the SQL DW Metadata Reports. ACE-sourced columns can be identified by searching for the ACE_ prefix in the Columns tab.</div></div>
    <div class="gl-row"><div class="gl-term">Dedicated SQL Pool</div><div class="gl-def">The provisioned compute and storage engine inside Azure Synapse Analytics used for large-scale analytical workloads. It uses Massively Parallel Processing (MPP) to distribute queries across 60 nodes.</div></div>
    <div class="gl-row"><div class="gl-term">Serverless SQL Pool</div><div class="gl-def">An on-demand query service in Synapse that runs T-SQL directly against files in ADLS Gen2 without provisioning dedicated resources. Billed per terabyte processed.</div></div>
    <div class="gl-row"><div class="gl-term">Integration Runtime (IR)</div><div class="gl-def">The compute infrastructure used by Azure Data Factory to run data movement and transformation activities. Can be Azure-hosted (shared), self-hosted (on-premises), or Azure-SSIS (for SSIS package execution).</div></div>
    <div class="gl-row"><div class="gl-term">Linked Service</div><div class="gl-def">A connection definition in ADF or Synapse that stores the connection string and authentication details for an external data source or compute target (e.g. SQL Server, ADLS, Databricks).</div></div>
    <div class="gl-row"><div class="gl-term">Dataset</div><div class="gl-def">A named pointer to data within a linked service. Defines the structure, location, and format of data used as input or output in ADF pipeline activities.</div></div>
    <div class="gl-row"><div class="gl-term">Trigger</div><div class="gl-def">A mechanism that defines when an ADF pipeline runs — either on a schedule (Schedule trigger), in response to a storage event (Storage Event trigger), or as part of a tumbling window.</div></div>
    <div class="gl-row"><div class="gl-term">Mapping Data Flow</div><div class="gl-def">A visually designed ETL transformation in ADF that runs on Spark clusters without writing code. Used for complex column-level transformations, joins, pivots, and aggregations.</div></div>
    <div class="gl-row"><div class="gl-term">ADLS Gen2 (Azure Data Lake Storage Generation 2)</div><div class="gl-def">Microsoft's scalable data lake built on Azure Blob Storage with Hierarchical Namespace (HNS) enabled. Supports POSIX-style permissions and is the primary storage layer for IDOH's data platform.</div></div>
    <div class="gl-row"><div class="gl-term">Hierarchical Namespace (HNS)</div><div class="gl-def">A feature of ADLS Gen2 that enables directory and file semantics (rename, move, ACLs) on top of blob storage, making it compatible with Hadoop and analytics frameworks.</div></div>
    <div class="gl-row"><div class="gl-term">Filesystem / Container</div><div class="gl-def">The top-level organizational unit in an ADLS Gen2 storage account. Equivalent to a Blob container. Filesystems contain directories and files and can have independent access policies.</div></div>
    <div class="gl-row"><div class="gl-term">Databricks Cluster</div><div class="gl-def">A set of cloud VMs managed by Databricks that run Apache Spark workloads. Clusters can be all-purpose (interactive notebooks) or job clusters (ephemeral, spun up per job run).</div></div>
    <div class="gl-row"><div class="gl-term">Unity Catalog</div><div class="gl-def">Databricks' centralized governance layer for data and AI assets. Provides a three-level namespace (catalog → schema → table), fine-grained access control, data lineage, and audit logging across all workspaces.</div></div>
    <div class="gl-row"><div class="gl-term">SQL Warehouse (Databricks)</div><div class="gl-def">A serverless or classic compute resource in Databricks optimized for SQL analytics. Powers Databricks SQL, BI tool connections, and ad-hoc queries against Delta tables.</div></div>
    <div class="gl-row"><div class="gl-term">Delta Lake</div><div class="gl-def">An open-source storage layer on ADLS Gen2 that adds ACID transactions, schema enforcement, time travel (versioned reads), and data quality checks to Parquet files. The standard table format in the IDOH Databricks environment.</div></div>
    <div class="gl-row"><div class="gl-term">Key Vault Secret</div><div class="gl-def">A securely stored string value in Azure Key Vault — typically a password, connection string, or API key. The Key Vault report shows secret names and metadata but never the secret values themselves.</div></div>
    <div class="gl-row"><div class="gl-term">Access Policy (Key Vault)</div><div class="gl-def">A permission model in Azure Key Vault that controls which identities (users, service principals, managed identities) can perform Get, List, Set, or Delete operations on secrets, keys, and certificates.</div></div>
    <div class="gl-row"><div class="gl-term">Managed Identity</div><div class="gl-def">An Azure Active Directory identity automatically managed by Azure for a service (e.g. ADF, Synapse, Databricks). Used to authenticate to other Azure services without storing credentials in code or config files.</div></div>
    <div class="gl-row"><div class="gl-term">VNet (Virtual Network)</div><div class="gl-def">An isolated network in Azure that provides private IP address space, subnets, and routing for Azure resources. VNet peering connects separate VNets so resources can communicate privately.</div></div>
    <div class="gl-row"><div class="gl-term">Subnet</div><div class="gl-def">A range of IP addresses within a VNet used to segment resources. NSG rules and service endpoints are applied at the subnet level.</div></div>
    <div class="gl-row"><div class="gl-term">VNet Peering</div><div class="gl-def">A low-latency, private network connection between two Azure VNets that enables resources in each to communicate using private IP addresses without a gateway or public internet.</div></div>
    <div class="gl-row"><div class="gl-term">ADO (Azure DevOps)</div><div class="gl-def">Microsoft's platform for source control, CI/CD pipelines, work item tracking, and artifact management. The ADO Metadata report covers repos, branches, build pipelines, pull requests, and deployment environments.</div></div>
    <div class="gl-row"><div class="gl-term">Branch Policy</div><div class="gl-def">Rules enforced on a Git branch in Azure DevOps — for example, requiring pull request reviews, passing build validations, or comment resolution before a merge is allowed.</div></div>
    <div class="gl-row"><div class="gl-term">Logic App</div><div class="gl-def">A serverless workflow automation service in Azure. Logic Apps connect services using pre-built connectors and run on a trigger (HTTP request, schedule, service bus message, etc.). The Logic Apps report covers workflow definitions, trigger types, and recent run history.</div></div>
    <div class="gl-row"><div class="gl-term">Stored Procedure</div><div class="gl-def">A reusable block of T-SQL logic stored in the database and executed by name. Commonly used in Synapse and SQL DW for ETL transformations, data loads, and business rule enforcement.</div></div>
    <div class="gl-row"><div class="gl-term">View</div><div class="gl-def">A named SQL query stored in the database that behaves like a table. Views abstract underlying table complexity, enforce access control, and can be used as the source for BI reports.</div></div>
    <div class="gl-row"><div class="gl-term">Foreign Key</div><div class="gl-def">A referential integrity constraint that links a column in one table to the primary key of another. In Synapse Dedicated SQL Pools, foreign keys are declared but not enforced — they exist for documentation and query optimization hints only.</div></div>

    <div class="gl-section">Data Catalog — Dataset Status</div>
    <div class="gl-row"><div class="gl-term">Verified</div><div class="gl-def">Data is visible in the <strong>Reporting layer</strong> (<code>Reporting_*</code> schemas) of the Synapse PRD snapshot with non-zero row counts. The dataset has been ingested, modeled, and surfaced through all three layers (Source → Mart → Reporting). <em>Note: all status values are estimates inferred from the PRD snapshot and are pending formal validation by the dataset steward.</em></div></div>
    <div class="gl-row"><div class="gl-term">In Review</div><div class="gl-def">No Data Mart or Reporting layer schemas were found for this dataset, or those schemas exist but contain zero rows. Data may be present in the Source layer only. Indicates the dataset needs steward review to confirm whether ingestion is complete and the data is usable. <em>Estimated — pending steward validation.</em></div></div>
    <div class="gl-row"><div class="gl-term">Needs Steward</div><div class="gl-def">The dataset is likely present in the data warehouse based on source system knowledge, but no identified data steward has been recorded. Ownership, data quality, and access decisions cannot be made until a steward is assigned. <em>Estimated — pending steward identification.</em></div></div>
    <div class="gl-row"><div class="gl-term">New</div><div class="gl-def">Recently ingested or recently added to the catalog. Not yet fully validated or reviewed by a steward. Status may change to Verified or In Review after the first review cycle.</div></div>
    <div class="gl-row"><div class="gl-term">Requested</div><div class="gl-def">The dataset has been identified as a stakeholder need but has not yet been ingested into the data warehouse. No Synapse schemas exist for this dataset. A data request or integration project is required to bring it in.</div></div>

    <div class="gl-section">Data Catalog — Refresh Cadence</div>
    <div class="gl-row"><div class="gl-term">Cadence (how it is determined)</div><div class="gl-def">Cadence values in the dataset registry are <strong>inferred</strong> from two sources: (1) table or column name patterns in the Synapse PRD snapshot containing keywords such as <code>ANNUAL</code>, <code>QUARTERLY</code>, or <code>WEEKLY</code>; and (2) known source system reporting cycles (e.g. BRFSS is an annual telephone survey; hospital discharge data is submitted monthly; EMS/syndromic surveillance feeds are near-daily). These are estimates — the authoritative schedule lives in the ADF pipeline trigger for each data source.</div></div>
    <div class="gl-row"><div class="gl-term">Annual</div><div class="gl-def">Data is refreshed once per year. Typical for survey-based programs (BRFSS, YRBS), vital statistics (birth/death certificates), and compliance reporting that follow a calendar or fiscal year cycle.</div></div>
    <div class="gl-row"><div class="gl-term">Quarterly</div><div class="gl-def">Data is refreshed four times per year. Typical for grant reporting, some hospital quality measures, and data sources with quarterly submission requirements.</div></div>
    <div class="gl-row"><div class="gl-term">Monthly</div><div class="gl-def">Data is refreshed each month. Common for immunization registry snapshots, hospital discharge submissions, WIC participation data, and similar program-level reports.</div></div>
    <div class="gl-row"><div class="gl-term">Weekly</div><div class="gl-def">Data is refreshed each week. Typical for syndromic surveillance, notifiable disease case reporting, and environmental monitoring programs where timeliness is important.</div></div>
    <div class="gl-row"><div class="gl-term">Daily / Near Real-time</div><div class="gl-def">Data is refreshed daily or on a near-continuous basis. Typical for electronic lab reporting (ELR), EMS run reports, and event-driven HL7/FHIR feeds where lag of more than 24 hours would affect public health response.</div></div>

    <div class="gl-section">Data Catalog — Access Levels</div>
    <div class="gl-row"><div class="gl-term">Self-Serve</div><div class="gl-def">Data is available in the <strong>Reporting layer</strong> (<code>Reporting_*</code> schemas) as aggregated, suppressed, or de-identified summaries. No individual-level records are exposed. IDOH staff with standard data warehouse access can query these tables directly or through a BI tool without submitting a formal data request. <em>Inferred from presence of <code>Reporting_*</code> schemas in the PRD snapshot.</em></div></div>
    <div class="gl-row"><div class="gl-term">Approval Required</div><div class="gl-def">Data is in the <strong>Data Mart layer</strong> (<code>DM_*</code> schemas) and may include individual-level de-identified records, sensitive program data, or analytic tables not yet promoted to a public reporting layer. Access requires a completed <a href="data_request_form.html">data request form</a> and approval through the IDOH data governance process. <em>Inferred from presence of <code>DM_*</code> schemas without a corresponding <code>Reporting_*</code> layer, or from program sensitivity.</em></div></div>
    <div class="gl-row"><div class="gl-term">Restricted</div><div class="gl-def">Data is in the <strong>Source / Staging layer</strong> (<code>SM_*</code> schemas) or involves Protected Health Information (PHI), sensitive programs (e.g. HIV/AIDS, STI, behavioral health, child abuse), or data covered by specific Data Use Agreements (DUAs) or federal regulations (e.g. 42 CFR Part 2). Access requires executive approval, legal review, and in some cases IRB involvement. <em>Inferred from <code>SM_*</code> schema presence or known program sensitivity.</em></div></div>
  </div>

  <!-- CHANGELOG -->
  <h2 id="changelog">Changelog</h2>
  <div id="cl-list">
{changelog_html}
  </div>

</div><!-- /main -->
</div><!-- /layout -->

<!-- Feedback widget -->
<button class="fb-toggle" onclick="fbToggle()" data-tooltip="…you can also find recent updates to this app in the Changelog">💬 Feedback / Suggestions</button>
<div class="fb-panel" id="fb-panel">
  <div class="fb-panel-hdr" style="display:flex;align-items:center;justify-content:space-between">
    <span>Feedback &amp; Suggestions</span>
    <button onclick="fbToggle()" title="Close" style="background:none;border:none;color:var(--mut);font-size:18px;cursor:pointer;line-height:1;padding:0 2px">&times;</button>
  </div>
  <div class="fb-tabs">
    <button class="fb-tab active" id="fb-tab-new" onclick="fbShowTab('new')">New Entry</button>
    <button class="fb-tab" id="fb-tab-log" onclick="fbShowTab('log')">Submission Log</button>
  </div>
  <div id="fb-pane-new">
    <div class="fb-body">
      <div>
        <div class="fb-label">Your Name</div>
        <input class="fb-input" id="fb-name" placeholder="Enter your name" autocomplete="off" oninput="this.style.borderColor=''"/>
      </div>
      <div>
        <div class="fb-label">Date &amp; Time</div>
        <div class="fb-dt" id="fb-dt"></div>
      </div>
      <div>
        <div class="fb-label">Priority</div>
        <div class="fb-pri-row">
          <button class="fb-pri-btn" id="fb-pri-Low"      onclick="fbSetPri('Low')">Low</button>
          <button class="fb-pri-btn" id="fb-pri-Medium"   onclick="fbSetPri('Medium')">Medium</button>
          <button class="fb-pri-btn" id="fb-pri-High"     onclick="fbSetPri('High')">High</button>
          <button class="fb-pri-btn" id="fb-pri-Critical" onclick="fbSetPri('Critical')">Critical</button>
        </div>
      </div>
      <div>
        <div class="fb-label">Comment / Suggestion</div>
        <textarea class="fb-input" id="fb-comment" rows="4" placeholder="Describe your suggestion or issue…" style="resize:vertical" oninput="this.style.borderColor=''"></textarea>
      </div>
      <button class="fb-submit" onclick="fbSubmit()">Submit</button>
    </div>
  </div>
  <div id="fb-pane-log" style="display:none">
    <div style="display:flex;gap:8px;padding:10px 16px;border-bottom:1px solid var(--brd);flex-wrap:wrap">
      <button class="fb-log-btn" onclick="fbToggleDeleted()" id="fb-show-del">Show Deleted</button>
      <button class="fb-log-btn" style="margin-left:auto" onclick="fbLoadAndRender()">Refresh</button>
    </div>
    <div class="fb-log" id="fb-log"></div>
  </div>
</div>

<script>
let fbPri = 'Low';
let fbShowDeleted = false;
let fbEntries = [];

function fbToggle(){{
  const p = document.getElementById('fb-panel');
  const open = p.classList.toggle('open');
  if(open){{
    document.getElementById('fb-dt').textContent = new Date().toLocaleString();
    fbShowTab('new');
    fbSetPri('Low');
  }}
}}
function fbShowTab(t){{
  document.getElementById('fb-pane-new').style.display = t==='new' ? '' : 'none';
  document.getElementById('fb-pane-log').style.display = t==='log' ? '' : 'none';
  document.getElementById('fb-tab-new').classList.toggle('active', t==='new');
  document.getElementById('fb-tab-log').classList.toggle('active', t==='log');
  if(t==='log') fbLoadAndRender();
}}
function fbSetPri(p){{
  fbPri = p;
  ['Low','Medium','High','Critical'].forEach(v => {{
    const btn = document.getElementById('fb-pri-'+v);
    btn.classList.remove('active-low','active-medium','active-high','active-critical');
    if(v===p) btn.classList.add('active-'+v.toLowerCase());
  }});
}}

const FB_WORDS = ['Thinking…','Pondering…','Querying…','Fetching…','Analyzing…','Processing…','Computing…','Deliberating…','Ruminating…','Synthesizing…'];
let _fbWordTimer = null;
function fbShowSpinner(el){{
  let i = 0;
  el.innerHTML = '<div class="fb-spinner"><div class="fb-spinner-ring"></div><div class="fb-spinner-word">' + FB_WORDS[0] + '</div></div>';
  const wordEl = el.querySelector('.fb-spinner-word');
  _fbWordTimer = setInterval(() => {{ i = (i+1) % FB_WORDS.length; wordEl.textContent = FB_WORDS[i]; }}, 600);
}}
function fbClearSpinner(){{
  if(_fbWordTimer) {{ clearInterval(_fbWordTimer); _fbWordTimer = null; }}
}}

async function fbLoadAndRender(){{
  const el = document.getElementById('fb-log');
  fbClearSpinner();
  fbShowSpinner(el);
  try {{
    const r = await fetch('/api/feedback');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    fbEntries = await r.json();
  }} catch(e) {{
    fbClearSpinner();
    el.innerHTML = '<p style="color:var(--red);font-size:12px">Could not load feedback: ' + e.message + '</p>';
    return;
  }}
  fbClearSpinner();
  fbRenderLog();
}}

function fbRenderLog(){{
  const el = document.getElementById('fb-log');
  const visible = fbShowDeleted ? fbEntries : fbEntries.filter(e => !e.deleted);
  if(!visible.length){{
    el.innerHTML='<p style="color:var(--mut);font-size:12px">' +
      (fbEntries.length && !fbShowDeleted ? 'All entries have been deleted. Click "Show Deleted" to view them.' : 'No submissions yet.') +
      '</p>';
    return;
  }}
  const PRI_COLOR = {{Low:'var(--grn)',Medium:'var(--yel)',High:'#fb923c',Critical:'var(--red)'}};
  el.innerHTML = visible.map((e) => {{
    const isDeleted = e.deleted;
    const pageLabel = e.page === 'index' ? '&nbsp;·&nbsp;<span style="color:var(--mut);font-size:10px">Index</span>' : '';
    return `<div class="fb-log-entry" style="${{isDeleted ? 'opacity:.45;border-style:dashed' : ''}}">
      <div class="fb-log-meta">
        <b style="color:var(--txt)">${{e.name}}</b> &nbsp;·&nbsp; ${{e.dt}}
        ${{e.priority ? `&nbsp;·&nbsp;<span style="color:${{PRI_COLOR[e.priority]||'var(--mut)'}};font-weight:700">${{e.priority}}</span>` : ''}}
        ${{pageLabel}}
        ${{isDeleted ? `&nbsp;·&nbsp;<span style="color:var(--red);font-size:10px">deleted ${{e.deletedAt||''}}</span>` : ''}}
      </div>
      <div class="fb-log-comment">${{e.comment}}</div>
      <div class="fb-log-actions">
        ${{isDeleted
          ? `<button class="fb-log-btn" onclick="fbRestore(${{e.id}})">Restore</button>`
          : `<button class="fb-log-btn" style="color:var(--red)" onclick="fbDelete(${{e.id}})">Delete</button>`
        }}
      </div>
    </div>`;
  }}).join('');
}}

async function fbSubmit(){{
  const nameEl    = document.getElementById('fb-name');
  const commentEl = document.getElementById('fb-comment');
  const name      = nameEl.value.trim();
  const comment   = commentEl.value.trim();
  const dt        = document.getElementById('fb-dt').textContent;
  let errors = [];
  const flag = (el, msg) => {{ el.style.borderColor='var(--red)'; errors.push(msg); }};
  nameEl.style.borderColor    = '';
  commentEl.style.borderColor = '';
  if(!name)    flag(nameEl,    'Your Name is required.');
  if(!fbPri)   errors.push('Please select a Priority.');
  if(!comment) flag(commentEl, 'Comment / Suggestion is required.');
  if(errors.length){{ alert(errors.join('\\n')); return; }}
  const btn = document.querySelector('.fb-submit');
  btn.disabled = true; btn.innerHTML = '<span class="fb-btn-spin"></span>Saving…';
  try {{
    const r = await fetch('/api/feedback', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{name, dt, priority: fbPri, comment, page: 'help'}})
    }});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const entry = await r.json();
    fbEntries.unshift(entry);
    nameEl.value = '';
    commentEl.value = '';
    fbSetPri('Low');
    fbShowTab('log');
  }} catch(e) {{
    alert('Failed to save: ' + e.message);
  }} finally {{
    btn.disabled = false; btn.innerHTML = 'Submit';
  }}
}}

async function fbDelete(entryId){{
  const idx = fbEntries.findIndex(e => e.id === entryId);
  if(idx < 0) return;
  try {{
    const r = await fetch('/api/feedback/' + entryId, {{
      method: 'PATCH',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{deleted: true}})
    }});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    fbEntries[idx].deleted = true;
    fbEntries[idx].deletedAt = new Date().toLocaleString();
    fbRenderLog();
  }} catch(e) {{ alert('Failed to delete: ' + e.message); }}
}}

async function fbRestore(entryId){{
  const idx = fbEntries.findIndex(e => e.id === entryId);
  if(idx < 0) return;
  try {{
    const r = await fetch('/api/feedback/' + entryId, {{
      method: 'PATCH',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{deleted: false}})
    }});
    if(!r.ok) throw new Error('HTTP ' + r.status);
    fbEntries[idx].deleted = false;
    fbEntries[idx].deletedAt = null;
    fbRenderLog();
  }} catch(e) {{ alert('Failed to restore: ' + e.message); }}
}}

function fbToggleDeleted(){{
  fbShowDeleted = !fbShowDeleted;
  const btn = document.getElementById('fb-show-del');
  btn.textContent = fbShowDeleted ? 'Hide Deleted' : 'Show Deleted';
  btn.style.color = fbShowDeleted ? 'var(--acc)' : '';
  fbRenderLog();
}}
</script>

<script>{JS}</script>
</body>
</html>"""


def main():
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_html(generated)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Help page written   : {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
