# 🩺 Fleet Doctor: a report card for Boyd's Grok Bot fleet

TokenMax build night, Oct 7 2026 (shortlist idea #2, "pstack for bots").

Fleet Doctor reads the bot metadata on this box **read-only** and grades every bot from A to F. Each grade comes with plain-English reasons, what's going well, and **suggested** prompt or cadence fixes. It changes nothing and sends nothing. Boyd decides what to apply. For the kids' tutor routines, Boyd and Miranda decide.

## What it checks
- **Duplicate or overlapping routines**: the same routine name on two bots, prompts that overlap heavily by vocabulary (scored in memory only), and back-to-back routines on one bot.
- **Failed or errored work**: run statuses in `runs.json` and errored tool calls in `audit.jsonl`, broken down by app.
- **Missed schedules**: expected cron fires compared with logged runs, within the logged window.
- **Stale routines and finished watches**: for example, a watch whose own state log says it found its answer but is still enabled.
- **Token-heavy or overly frequent routines**: agent-minutes per week, more-than-hourly crons, "weeknight" routines that also fire on weekends, and long chats.
- **Quiet bots**: no routines and little logged activity, plus unfinished hatchery bots.

## Honesty notes
- The run logs and tool logs on disk end **Tue Sep 15, 9:35 PM ET**, because the box was restored from a snapshot on Oct 3. Anything after that can't be measured from disk, and the report says so.
- Real token counts per bot aren't stored on disk. Agent-minutes (median run length × fires per week) stand in for them.
- The `routine-healthcheck` skill prefers live transcripts over disk copies. A live healthcheck by the main bot would cover Sep 16 to today.

## Run
```bash
./run.sh            # regenerates the snapshot and starts http://127.0.0.1:4259 under nohup (pid in data/server.pid)
./make_outputs.sh   # rebuilds dist/fleet-doctor-snapshot.html, dist/fleet-doctor-report.pdf and screenshots/
python3 tests/smoke_test.py
```
Endpoints: `/` (live report, re-analyzed at most every 60 s), `/?view=findings`, `/api/report` (JSON), `/health`.

## Files
- `fleet_doctor.py`: the analysis. It reuses `../bot-island/collect.py` and `doctor.py`, then adds grades and extra checks.
- `server.py`: stdlib HTTP server on port 4259.
- `dist/fleet-doctor-snapshot.html`: offline single-file report.
- `dist/fleet-doctor-report.pdf`: printable report.
- `screenshots/00-best-desktop-report-cards.png`, `desktop-02-findings.png`, `phone-01-top.png`
- `data/report.json`: machine-readable findings. It contains no prompt text or message content.

## Data sources (read-only)
These live under `/home/box/agent-data/agents/*/`:
- `profile.json`
- `settings.json`
- `automations/*/automation.json` and `runs.json`
- `audit.jsonl` (type and status fields only)
- `store.db` (opened with `mode=ro`, row count only)

Also used: `/workspace/newspaper/edition-log.jsonl` (print status) and output-file modification times, both through the Bot Island collector.


---
## Repo notes
- **Built:** Oct 7 2026, by TokenMax (Boyd's Grok Bot) on the Grok Bot box.
- **Run:** `./run.sh` (then open the localhost URL it prints).
- Imported from `/workspace/tokenmax/fleet-doctor` on Oct 23, 2026. node_modules, virtualenvs, logs and files over 20 MB were left out.
