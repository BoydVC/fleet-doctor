#!/usr/bin/env python3
"""Fleet Doctor smoke test: data integrity, read-only guarantees, server endpoints, deliverables."""
import glob
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = "/home/box/agent-data/agents"
sys.path.insert(0, HERE)
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name)


def tree_hash():
    h = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(ROOT, "*", "**"), recursive=True)):
        if os.path.isfile(p) and not p.endswith(("-shm", "-wal")):
            st = os.stat(p)
            h.update(f"{p}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()


before = tree_hash()
import fleet_doctor  # noqa: E402
rep = fleet_doctor.build()
html = fleet_doctor.render(rep)
after = tree_hash()
check("agent-data unchanged by a full analysis (size+mtime hash)", before == after)

# counts must match the raw files
prof = glob.glob(os.path.join(ROOT, "*", "profile.json"))
check(f"one report card per bot profile ({len(prof)})", len(rep["cards"]) == len(prof))
autos = glob.glob(os.path.join(ROOT, "*", "automations", "*", "automation.json"))
check(f"routine count matches automation.json files ({len(autos)})", rep["routineCount"] == len(autos))
raw_runs = 0
for a in autos:
    rr = json.load(open(os.path.join(os.path.dirname(a), "runs.json")))
    raw_runs += sum(1 for r in rr if r.get("startedAt"))
check(f"run count matches runs.json entries with startedAt ({raw_runs})", rep["runCount"] == raw_runs)
enabled = sum(1 for a in autos if json.load(open(a)).get("enabled", True) is not False)
check(f"enabled routine count matches ({enabled})", rep["activeRoutineCount"] == enabled)
for c in rep["cards"]:
    aid = [os.path.dirname(p) for p in prof if json.load(open(p)).get("name") == c["name"]][0]
    au = os.path.join(aid, "audit.jsonl")
    errs = calls = 0
    if os.path.exists(au):
        for line in open(au, errors="replace"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("type") == "mcp_tool_call":
                calls += 1
                errs += bool(e.get("status") and e.get("status") != "ok")
    check(f"{c['name']}: tool errors {c['toolErrors']}/{c['toolCalls']} match audit.jsonl", (c["toolErrors"], c["toolCalls"]) == (errs, calls))
    check(f"{c['name']}: grade {c['grade']} consistent with score {c['score']}", c["grade"] == ("A" if c["score"] >= 90 else "B" if c["score"] >= 80 else "C" if c["score"] >= 70 else "D" if c["score"] >= 60 else "F"))
    check(f"{c['name']}: has plain-English reasons", bool(c["reasons"]) and all(isinstance(r, str) and r for r in c["reasons"]))
check("every fix is phrased as a suggestion (no 'I changed'/'applied')", not any(w in json.dumps(rep).lower() for w in ("i changed", "i deleted", "i paused", "was applied")))
check("data-gap note present", any(f["id"] == "data-gap" for f in rep["findings"]))
check("kids' routines: Boyd and Miranda decide", all("Boyd and Miranda decide" in f["fix"] for f in rep["findings"] if f["id"].startswith("weekend-")))
# no prompt text leaks
leak = False
for a in autos:
    pr = (json.load(open(a)).get("prompt") or "").strip()
    for chunk in [pr[i:i + 60] for i in range(0, len(pr) - 60, 200)]:
        if len(chunk) == 60 and chunk in html:
            leak = True
check("no routine prompt text in the HTML", not leak)
# deliverables
for f in ["dist/fleet-doctor-snapshot.html", "dist/fleet-doctor-report.pdf", "README.md", "run.sh"]:
    check(f"deliverable exists: {f}", os.path.exists(os.path.join(HERE, f)) and os.path.getsize(os.path.join(HERE, f)) > 200)
check("best screenshot exists", bool(glob.glob(os.path.join(HERE, "screenshots", "00-best-*.png"))))
check("phone screenshot exists", bool(glob.glob(os.path.join(HERE, "screenshots", "phone-*.png"))))
snap = open(os.path.join(HERE, "dist", "fleet-doctor-snapshot.html")).read()
check("snapshot is offline (no external src/href)", "http://" not in snap.replace("http://127.0.0.1", "") and "https://" not in snap)
try:
    pages = subprocess.run(["pdfinfo", os.path.join(HERE, "dist", "fleet-doctor-report.pdf")], capture_output=True, text=True).stdout
    check("PDF has pages", "Pages:" in pages)
except Exception:
    check("PDF has pages", False)
# server
try:
    h = json.loads(urllib.request.urlopen("http://127.0.0.1:4259/health", timeout=5).read())
    check("server /health ok on 4259", h.get("ok") is True)
    r = json.loads(urllib.request.urlopen("http://127.0.0.1:4259/api/report", timeout=20).read())
    check("server /api/report has cards", len(r["cards"]) == len(rep["cards"]))
    page = urllib.request.urlopen("http://127.0.0.1:4259/", timeout=20).read().decode()
    check("server / renders report cards", "Report cards" in page and "Top 5 findings" in page)
except Exception as ex:
    check(f"server reachable ({ex})", False)
check("agent-data still unchanged after server calls", tree_hash() == before)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
