#!/usr/bin/env python3
"""Fleet Doctor: a read-only report card for Boyd's Grok Bot fleet.

Reads (never writes) bot metadata under /home/box/agent-data/agents: profile.json, settings.json,
automations/*/automation.json + runs.json, audit.jsonl (tool-call status only), store.db (row count only,
opened read-only). Reuses the Bot Island collector + checks (../bot-island/collect.py, doctor.py) and adds
per-bot grades, extra checks and suggestion-only fixes. Prompt text and message content are never output.

Usage: python3 fleet_doctor.py            -> writes data/report.json + dist/fleet-doctor-snapshot.html
"""
import collections
import glob
import html
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BI = os.path.join(os.path.dirname(HERE), "bot-island")
sys.path.insert(0, BI)
import collect  # noqa: E402  (read-only collector)
import doctor   # noqa: E402  (analysis only; we never call doctor.main, which writes bot-island history)

ROOT = "/home/box/agent-data/agents"
TZ = doctor.TZ
PEN = {"high": 25, "medium": 12, "low": 5, "info": 0}
SERVER = {"user-Google-drive": "Google Drive", "user-Notion": "Notion", "user-X": "X", "user-Gmail": "Gmail",
          "user-Gmail--miranda": "Gmail (Miranda)", "user-Slack": "Slack", "user-Google-calendar": "Calendar",
          "user-Composio": "Composio", "user-Finance-xai": "Finance", "cursor": "built-in"}


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None


def fmt_day(dt):
    return dt.astimezone(TZ).strftime("%a %b %-d") if dt else "never"


def short_tool(tool):
    for k in sorted(SERVER, key=len, reverse=True):
        if (tool or "").startswith(k + "-"):
            return tool[len(k) + 1:]
    return tool or "?"


def connector(tool):
    for k in sorted(SERVER, key=len, reverse=True):
        if (tool or "").startswith(k + "-"):
            return SERVER[k]
    return "other tool"


def extra_metadata():
    """Per-bot facts the Bot Island collector doesn't keep: tool errors, chat length, watch state."""
    out = {}
    for prof in sorted(glob.glob(os.path.join(ROOT, "*", "profile.json"))):
        adir = os.path.dirname(prof)
        p = collect.read_json(prof, {}) or {}
        info = {"id": os.path.basename(adir), "name": p.get("name") or os.path.basename(adir)[:8],
                "hasDescription": bool((p.get("description") or "").strip()), "toolCalls": 0, "toolErrors": [],
                "chatEntries": None, "watches": [], "settings": collect.read_json(os.path.join(adir, "settings.json"), None)}
        audit = os.path.join(adir, "audit.jsonl")
        if os.path.exists(audit):
            with open(audit, errors="replace") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    if e.get("type") == "mcp_tool_call":
                        info["toolCalls"] += 1
                        if e.get("status") and e.get("status") != "ok":
                            info["toolErrors"].append({"ts": e.get("ts"), "tool": e.get("toolName") or "", "app": connector(e.get("toolName"))})
        db = os.path.join(adir, "store.db")
        if os.path.exists(db):
            try:
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
                info["chatEntries"] = con.execute("select count(*) from transcript_entries").fetchone()[0]
                con.close()
            except Exception:
                info["chatEntries"] = None
        for apath in glob.glob(os.path.join(adir, "automations", "*", "automation.json")):
            a = collect.read_json(apath, {}) or {}
            runs = collect.read_json(os.path.join(os.path.dirname(apath), "runs.json"), []) or []
            notes = [r for r in runs if isinstance(r, dict) and "found" in r]
            hits = [r for r in notes if r.get("found") is True]
            if notes:
                info["watches"].append({"routine": a.get("name"), "enabled": a.get("enabled", True), "cron": a.get("schedule"),
                                        "checks": len(notes), "foundAt": hits[-1].get("at") if hits else None})
        out[info["id"]] = info
    return out


def attribute(f, names):
    if f.get("bot"):
        return [f["bot"]]
    t = f["title"] + " " + f["detail"]
    hit = [n for n in names if n in t]
    if f["title"].startswith("Leftover paused copy"):
        hit = [n for n in hit if f"{n} (paused)" in f["detail"]] or hit
    if f["title"].startswith("Unfinished bot"):
        hit = [f["title"].split(": ", 1)[1]]
    return hit or ["Fleet-wide"]


def build():
    data = collect.collect()
    rep = doctor.analyze(data)
    meta = extra_metadata()
    now = datetime.now(timezone.utc)
    log_end = ts(rep["logEnd"])
    names = sorted([b["name"] for b in data["bots"]] + [e["name"] for e in data["eggs"]], key=len, reverse=True)
    findings = []
    for f in rep["findings"]:
        if f["title"].startswith("Local activity logs end"):
            continue  # replaced by the more specific data-gap note below
        f = dict(f, bots=attribute(f, names), source="Bot Island checks")
        findings.append(f)

    def add(cat, sev, bot, title, detail, fix, key):
        findings.append({"id": key, "category": cat, "severity": sev, "bot": bot, "bots": [bot] if bot else ["Fleet-wide"], "title": title,
                         "detail": detail, "fix": fix, "source": "Fleet Doctor checks"})

    by_name = {m["name"]: m for m in meta.values()}
    # finished watches still scheduled
    for m in meta.values():
        for w in m["watches"]:
            if w["foundAt"] and w["enabled"]:
                fires = len(doctor.cron_fires(w["cron"] or "", now, now + timedelta(days=7)))
                add("failing", "medium", m["name"], f"{m['name']}: watch “{w['routine']}” already found its answer but is still scheduled",
                    f"Its own state log says the condition was met on {fmt_day(ts(w['foundAt']))} ({w['checks']} checks logged). It is still enabled on cron “{w['cron']}”, about {fires} wake(s) a week.",
                    "Suggestion: pause or delete this routine, or give the prompt an end date (\"stop after the pick is found\").", f"watch-{w['routine']}")
    # tool errors
    for m in meta.values():
        errs = m["toolErrors"]
        if not errs:
            continue
        by_app = collections.Counter(e["app"] for e in errs)
        days = sorted({ts(e["ts"]).astimezone(TZ).strftime("%b %-d") if e.get("ts") else "?" for e in errs}, key=lambda d: datetime.strptime(d + " 2026", "%b %d %Y") if d != "?" else datetime.min)
        top_app, top_n = by_app.most_common(1)[0]
        top_tool = collections.Counter(e["tool"] for e in errs if e["app"] == top_app).most_common(1)[0][0]
        sev = "medium" if top_n >= 5 else "low"
        parts = ", ".join(f"{a} {n}" for a, n in by_app.most_common())
        add("failing", sev, m["name"], f"{m['name']}: {len(errs)} tool calls errored ({len(errs)} of {m['toolCalls']} logged)",
            f"By app: {parts}. Spread over {len(days)} day(s), {days[0]} to {days[-1]} ET. Most common: {short_tool(top_tool)} ({top_app}).",
            f"Suggestion: check the {top_app} connection is still authorized, and add a line to the routine prompt like \"if {top_app} errors, say so once and stop instead of retrying\".",
            f"err-{m['name']}")
    # cadence vs. name ("weeknight", "weekday")
    for r in rep["routines"]:
        if r["enabled"] is False or not r["cron"]:
            continue
        nm = r["name"].lower()
        dow = r["cron"].split()[4] if len(r["cron"].split()) >= 5 else "*"
        if ("weeknight" in nm or "weekday" in nm) and dow == "*":
            wk = sum(1 for t in doctor.cron_fires(r["cron"], now, now + timedelta(days=7)) if t.weekday() >= 5)
            add("heavy", "low", r["bot"], f"{r['bot']}: “{r['name']}” also runs on weekends",
                f"The name says weeknight, but cron “{r['cron']}” fires every day: {wk} weekend wake(s) a week (~{round(wk * (r['medianMinutes'] or 0), 1)} agent-min).",
                "Suggestion: if weekends really are off, Boyd could change the day field to Sun–Thu (0-4) for school nights. Boyd and Miranda decide what the kids' schedule should be.",
                f"weekend-{r['name']}")
        if r["firesPerWeek"] > 7 * 24:
            add("heavy", "high", r["bot"], f"{r['bot']}: “{r['name']}” fires more than hourly", f"{r['firesPerWeek']} wakes a week on cron “{r['cron']}”.",
                "Suggestion: use an event trigger, or limit it to waking hours.", f"freq-{r['name']}")
    # long chats carrying routines
    for b in data["bots"]:
        m = meta.get(b["id"], {})
        n = m.get("chatEntries")
        active = [r for r in b["routines"] if r.get("enabled") is not False]
        if n and n > 500:
            sev = "medium" if active else "info"
            add("heavy", sev, b["name"], f"{b['name']} has a long chat ({n} transcript entries)",
                (f"It also carries {len(active)} active routine(s), so every wake re-reads a big history." if active else "Its routines are paused, so this costs nothing on a schedule, but each new message re-reads a long history."),
                "Suggestion: move standing routines to a fresh short-chat bot, or start a new chat for new projects.", f"long-{b['name']}")
    # data gap
    add("data", "info", None, "Run history can only be measured through " + doctor.fmt(log_end),
        f"The routine run logs and tool logs on this box stop on {fmt_day(log_end)} (the box was restored from a snapshot on Oct 3). Missed or failed runs after that date can't be measured from disk, so they are not counted. Output files (newest {doctor.fmt(ts(data['sources'].get('filesNewest')))}) are used where available.",
        "No fix needed. A live check (the routine-healthcheck skill reads live transcripts) would cover Sep 16 to today.", "data-gap")

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: (order[f["severity"]], f["category"]))
    # ---------- per-bot grades ----------
    rt_by = collections.defaultdict(list)
    for r in rep["routines"]:
        rt_by[r["bot"]].append(r)
    brow = {b["name"]: b for b in rep["bots"]}
    cards = []
    for b in data["bots"] + [dict(e, title="(unfinished)", routines=[]) for e in data["eggs"]]:
        nm = b["name"]
        m = by_name.get(nm, {})
        fs = [f for f in findings if nm in f["bots"]]
        score = max(0, 100 - sum(PEN[f["severity"]] for f in fs))
        reasons, good = [], []
        rts = rt_by.get(nm, [])
        act = [r for r in rts if r["enabled"] is not False]
        runs = sum(r["runs"] for r in rts)
        ok = sum(r["ok"] for r in rts)
        last = brow.get(nm, {}).get("lastActive")
        last_dt = ts(last) if last else None
        quiet_days = (now - last_dt).days if last_dt else None
        ev30 = brow.get(nm, {}).get("events30", 0)
        if not act and not b.get("observedRhythm") and nm not in ("TokenMax",):
            if quiet_days is None:
                score = min(score, 55)
                reasons.append("No routines, tool calls, messages or output files found on this box.")
            elif ev30 < 20:
                score -= 15
                gap = (log_end - last_dt).days if last_dt <= log_end else 0
                reasons.append(f"No routines and only {ev30} logged tool calls/messages in the 30 days before the logs end (last seen {fmt_day(last_dt)}, {gap} days before the logs stop; activity since then can't be measured).")
        for f in fs:
            if f["severity"] != "info":
                reasons.append(f["title"].split(": ", 1)[-1] if f["title"].startswith(nm + ":") else f["title"])
        if runs:
            good.append(f"{ok} of {runs} logged routine runs finished OK")
        hit = sum(r["hit"] for r in act)
        exp = sum(r["expected"] for r in act)
        if exp:
            good.append(f"{hit} of {exp} scheduled runs found in the log (on enabled routines)")
        if b.get("observedRhythm"):
            good.append("writes its daily output on a steady schedule (from file times)")
        score = max(0, min(100, score))
        grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
        fixes = [f["fix"] for f in fs if f["severity"] != "info"]
        if not fixes and reasons:
            fixes = ["Suggestion: give it a small weekly job that fits its role, or archive it to cut clutter."]
        cards.append({"name": nm, "title": b.get("title") or "", "grade": grade, "score": score,
                      "activeRoutines": len(act), "pausedRoutines": len(rts) - len(act), "runs": runs, "runsOk": ok,
                      "toolCalls": m.get("toolCalls"), "toolErrors": len(m.get("toolErrors", [])),
                      "chatEntries": m.get("chatEntries"), "lastActive": last, "quietDays": quiet_days,
                      "agentMinPerWeek": round(sum(r["minutesPerWeek"] for r in act), 1),
                      "reasons": reasons or ["Nothing flagged in the logged window."], "good": good, "fixes": fixes,
                      "findingIds": [f["id"] for f in fs]})
    cards.sort(key=lambda c: (c["score"], c["name"]))
    gpa_map = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    gpa = round(sum(gpa_map[c["grade"]] for c in cards) / max(1, len(cards)), 2)
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in order}
    return {"generatedAt": now.isoformat(), "logEnd": rep["logEnd"], "fleetScore": rep["score"], "gpa": gpa,
            "counts": counts, "findings": findings, "routines": rep["routines"], "cards": cards,
            "categories": doctor.CAT, "sources": data["sources"], "botCount": len(cards),
            "routineCount": len(rep["routines"]), "activeRoutineCount": sum(1 for r in rep["routines"] if r["enabled"] is not False),
            "runCount": sum(r["runs"] for r in rep["routines"])}


GCOL = {"A": "#4ade80", "B": "#a3e635", "C": "#fbbf24", "D": "#fb923c", "F": "#f87171"}
SCOL = {"high": "#f87171", "medium": "#fbbf24", "low": "#60a5fa", "info": "#94a3b8"}


def render(rep, live=False, view=None):
    e = html.escape
    gen = datetime.fromisoformat(rep["generatedAt"]).astimezone(TZ)
    c = rep["counts"]
    cards = "".join(f"""<div class="card" id="bot-{e(k['name'].replace(' ', '-'))}"><div class="ch"><div class="g" style="background:{GCOL[k['grade']]}">{k['grade']}</div>
<div><b>{e(k['name'])}</b><div class="t">{e(k['title'])} · score {k['score']}</div></div></div>
<div class="stats"><span>{k['activeRoutines']} active{f" · {k['pausedRoutines']} paused" if k['pausedRoutines'] else ''} routines</span><span>{k['runsOk']}/{k['runs']} runs OK</span>
<span>{k['toolErrors']}/{k['toolCalls'] or 0} tool errors</span><span>{'—' if k['chatEntries'] is None else k['chatEntries']} chat entries</span>
<span>{k['agentMinPerWeek']} agent-min/wk</span><span>last seen {e(fmt_day(ts(k['lastActive']))) if k['lastActive'] else 'never'}</span></div>
<div class="sec">Why this grade</div><ul>{''.join(f'<li>{e(r)}</li>' for r in k['reasons'])}</ul>
{('<div class="sec ok">Going well</div><ul>' + ''.join(f'<li>{e(g)}</li>' for g in k['good']) + '</ul>') if k['good'] else ''}
{('<div class="sec fx">Suggested fixes (suggestions only)</div><ul class="fx">' + ''.join(f'<li>{e(x)}</li>' for x in dict.fromkeys(k['fixes'])) + '</ul>') if k['fixes'] else ''}
</div>""" for k in rep["cards"])
    rows = []
    for cat, label in rep["categories"].items():
        fs = [f for f in rep["findings"] if f["category"] == cat]
        if not fs:
            continue
        rows.append(f"<h3>{e(label)} <span class=n>{len(fs)}</span></h3>")
        for f in fs:
            rows.append(f"""<div class="f sev-{f['severity']}"><span class="sev" style="background:{SCOL[f['severity']]}">{f['severity']}</span>
<div><b>{e(f['title'])}</b><div class="d">{e(f['detail'])}</div><div class="fix">💊 {e(f['fix'])}</div><div class="who">{e(', '.join(f['bots']))}</div></div></div>""")
    rt = "".join(f"<tr><td>{e(r['bot'])}</td><td>{e(r['name'])}{'' if r['enabled'] is not False else ' <i>(paused)</i>'}</td><td><code>{e(r['cron'] or '')}</code></td><td>{r['ok']}/{r['runs']}</td><td>{r['hit']}/{r['expected']}</td><td>{r['medianMinutes'] or ''}</td><td>{r['firesPerWeek']}</td><td>{r['minutesPerWeek']:.0f}</td><td>{r['toolCallsPerRun']:.0f}</td><td>{e(fmt_day(ts(r['lastRun'])))}</td></tr>"
                 for r in sorted(rep["routines"], key=lambda r: (r['enabled'] is False, -r["minutesPerWeek"])))
    top = [f for f in rep["findings"] if f["severity"] != "info"][:5]
    tops = "".join(f'<li><b>{e(f["title"])}</b> <span class="d">{e(f["detail"])}</span></li>' for f in top)
    grades = collections.Counter(k["grade"] for k in rep["cards"])
    gbar = "".join(f'<span class="gp" style="background:{GCOL[g]}">{g} × {grades[g]}</span>' for g in "ABCDF" if grades[g])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Doctor · {gen:%b %-d, %Y}</title><style>
*{{box-sizing:border-box}}body{{font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:radial-gradient(1200px 600px at 20% -10%,#152544 0%,#0b1324 55%);color:#e8eefc;margin:0;min-height:100vh}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 28px}}h1{{margin:0 0 4px;font-size:28px;letter-spacing:-.02em}}.sub{{color:#8fa3c9}}
.hero{{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0}}.kpi{{background:linear-gradient(180deg,#142038,#111c33);border:1px solid #2a3d6a;border-radius:14px;padding:12px 16px;min-width:140px;box-shadow:0 8px 24px rgba(0,0,0,.25)}}
.kpi .v{{font-size:30px;font-weight:800}}.kpi .l{{color:#8fa3c9;font-size:12px}}.gp{{color:#111;font-weight:700;border-radius:8px;padding:2px 8px;margin-right:6px;display:inline-block;margin-top:6px}}
h2{{margin:28px 0 10px;color:#ffb547}}h3{{font-size:15px;margin:18px 0 6px;color:#ffd08a}}h3 .n{{background:#22325a;color:#e8eefc;border-radius:999px;padding:0 8px;font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}}
.card{{background:linear-gradient(165deg,#142038,#0f1a2e);border:1px solid #2a3d6a;border-radius:14px;padding:14px;break-inside:avoid;box-shadow:0 6px 18px rgba(0,0,0,.22);transition:border-color .15s}}.card:hover{{border-color:#3d5a94}}.ch{{display:flex;gap:12px;align-items:center}}
.g{{width:48px;height:48px;border-radius:12px;color:#111;font-weight:900;font-size:28px;display:flex;align-items:center;justify-content:center;flex:none;box-shadow:0 2px 8px rgba(0,0,0,.3)}}
.t{{color:#8fa3c9;font-size:12px}}.stats{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px}}.stats span{{background:#16233f;border-radius:999px;padding:2px 8px;font-size:11.5px;color:#c3d1ee}}
.sec{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#fca5a5;margin-top:8px}}.sec.ok{{color:#86efac}}.sec.fx{{color:#93c5fd}}
ul{{margin:4px 0 0 18px;padding:0}}li{{margin:2px 0}}ul.fx li{{color:#bfdbfe}}
.f{{display:grid;grid-template-columns:64px 1fr;gap:10px;background:linear-gradient(165deg,#142038,#0f1a2e);border:1px solid #2a3d6a;border-radius:12px;padding:10px 12px;margin:8px 0;break-inside:avoid}}
.sev{{color:#111;font-weight:700;font-size:11px;text-transform:uppercase;border-radius:6px;padding:2px 6px;height:fit-content;text-align:center}}
.d{{color:#c3d1ee;margin-top:2px}}.fix{{margin-top:6px;color:#bbf7d0}}.who{{margin-top:4px;color:#8fa3c9;font-size:12px}}
.tw{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:12.5px}}th,td{{text-align:left;padding:5px 6px;border-bottom:1px solid #1c2a4b;white-space:nowrap}}th{{color:#8fa3c9}}
code{{color:#fde68a}}.note{{background:linear-gradient(135deg,#1a1530,#151a2e);border:1px solid #3b2f63;border-radius:12px;padding:10px 14px;color:#ddd6fe;margin:12px 0}}
.foot{{color:#8fa3c9;margin-top:24px;font-size:12px}}ol.top{{margin:0 0 0 20px;padding:0}}ol.top li{{margin:6px 0}}ol.top .d{{display:block}}nav a{{color:#93c5fd;margin-right:14px;text-decoration:none}}nav a:hover{{color:#bfdbfe;text-decoration:underline}}
@media (max-width:640px){{.wrap{{padding:14px}}h1{{font-size:22px}}.grid{{grid-template-columns:1fr}}.kpi{{min-width:calc(50% - 7px);flex:1}}.kpi .v{{font-size:24px}}.f{{grid-template-columns:1fr}}.sev{{width:fit-content}}}}
@media print{{body{{background:#fff;color:#111}}.wrap{{max-width:none;padding:0}}.card,.f,.kpi{{background:#fff;border-color:#bbb}}.sub,.t,.who,.kpi .l,th{{color:#555}}
.d,.stats span,ul.fx li{{color:#222}}.stats span{{background:#eee}}.fix{{color:#065f46}}h2{{color:#b45309}}h3{{color:#92400e}}h3 .n{{background:#eee;color:#111}}code{{color:#7c2d12}}
.note{{background:#f5f3ff;color:#3b0764;border-color:#c4b5fd}}nav{{display:none}}.sec{{color:#b91c1c}}.sec.ok{{color:#15803d}}.sec.fx{{color:#1d4ed8}}td,th{{border-color:#ddd}}body{{font-size:11.5px}}.grid{{display:block;columns:2;column-gap:12px}}.card{{display:inline-block;width:100%;margin:0 0 10px}}h2{{break-after:avoid}}.hero{{margin:10px 0}}}}
@page{{margin:12mm}}
</style></head><body><div class="wrap">
<h1>🩺 Fleet Doctor · report card</h1>
<div class="sub">Boyd's Grok Bot fleet · generated {gen:%a %b %-d, %Y %-I:%M %p} ET{' · live (refreshes on reload)' if live else ' · offline snapshot'} · read-only: nothing was changed, nothing was sent</div>
<nav style="margin-top:10px"><a href="#cards">Report cards</a><a href="#findings">Findings</a><a href="#routines">Routines</a><a href="#method">How it's measured</a></nav>
<div class="hero"><div class="kpi"><div class="v">{rep['gpa']}</div><div class="l">fleet GPA (4.0 scale)</div><div>{gbar}</div></div>
<div class="kpi"><div class="v">{rep['botCount']}</div><div class="l">bots graded</div></div>
<div class="kpi"><div class="v">{rep['activeRoutineCount']}<span style="font-size:16px;color:#8fa3c9"> / {rep['routineCount']}</span></div><div class="l">routines enabled / total</div></div>
<div class="kpi"><div class="v">{rep['runCount']}</div><div class="l">routine runs logged</div></div>
<div class="kpi"><div class="v">{c['high']}·{c['medium']}·{c['low']}</div><div class="l">high · medium · low findings</div></div></div>
<div class="note">⚠️ Logged run history on this box ends <b>{e(doctor.fmt(ts(rep['logEnd'])))}</b> (restored snapshot). Runs after that can't be measured here, so "missed" and "failed" counts cover only the logged window. Grades are suggestions for Boyd to review; no routine was touched.</div>
<h2>Top 5 findings</h2><ol class="top">{tops}</ol>
{"" if view == "findings" else f'<h2 id="cards">Report cards (worst first)</h2><div class="grid">{cards}</div>'}
<h2 id="findings">All findings</h2>{''.join(rows)}
<h2 id="routines">Routines</h2><div class="tw"><table><tr><th>Bot</th><th>Routine</th><th>Cron</th><th>Runs OK</th><th>On schedule</th><th>Median min</th><th>Fires/wk</th><th>Agent-min/wk</th><th>Tools/run</th><th>Last run</th></tr>{rt}</table></div>
<h2 id="method">How it's measured</h2><ul>
<li>Sources (read-only): <code>/home/box/agent-data/agents/*/</code> profile.json, settings.json, automations/*/automation.json + runs.json, audit.jsonl (tool status only), store.db (row count only). Reuses the Bot Island collector and checks.</li>
<li>Grade = 100 minus 25 per high, 12 per medium and 5 per low finding tied to that bot, minus 15 for no routines and 14+ days of silence. A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F below.</li>
<li>Agent-minutes per week = median run length × scheduled fires per week, a stand-in for token spend. Real token counts per bot aren't stored on disk, so they can't be measured.</li>
<li>"On schedule" = expected cron fires between the first logged run and the end of the logs that have a run within 3 hours.</li>
<li>Prompt text and message content are read only in memory (for overlap scoring) and never written out.</li></ul>
<div class="foot">Made by TokenMax · Fleet Doctor · fleet_doctor.py</div></div></body></html>"""


def main():
    rep = build()
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    with open(os.path.join(HERE, "data", "report.json"), "w") as f:
        json.dump(rep, f, indent=1, default=str)
    with open(os.path.join(HERE, "dist", "fleet-doctor-snapshot.html"), "w") as f:
        f.write(render(rep))
    if "-q" not in sys.argv:
        print(f"GPA {rep['gpa']} · {rep['counts']} · {len(rep['cards'])} bots")
        for k in rep["cards"]:
            print(f"  {k['grade']} {k['score']:>3} {k['name']}: {'; '.join(k['reasons'])[:160]}")
        for f in rep["findings"]:
            print(f"  [{f['severity']}] {f['title']}")


if __name__ == "__main__":
    main()
