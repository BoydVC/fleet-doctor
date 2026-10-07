#!/usr/bin/env bash
# Start (or restart) Fleet Doctor on port 4259 under nohup. Regenerates the offline snapshot first.
cd "$(dirname "$0")"
python3 fleet_doctor.py -q
if [ -f data/server.pid ]; then kill "$(cat data/server.pid)" 2>/dev/null; sleep 0.5; fi
nohup python3 server.py > data/server.log 2>&1 &
echo $! > data/server.pid
sleep 1.5; curl -s http://127.0.0.1:4259/health && echo " → http://127.0.0.1:4259/"
