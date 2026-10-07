#!/usr/bin/env bash
# Regenerate snapshot, PDF and screenshots with headless Chrome.
cd "$(dirname "$0")"
python3 fleet_doctor.py -q
C="google-chrome --headless=new --no-sandbox --disable-gpu --hide-scrollbars --user-data-dir=/tmp/fleet-doctor-chrome"
$C --no-pdf-header-footer --print-to-pdf=dist/fleet-doctor-report.pdf "file://$PWD/dist/fleet-doctor-snapshot.html" 2>/dev/null
U=http://127.0.0.1:4259/
$C --window-size=1440,1800 --force-device-scale-factor=1 --screenshot=screenshots/00-best-desktop-report-cards.png "$U" 2>/dev/null
$C --window-size=1440,2000 --force-device-scale-factor=1 --screenshot=screenshots/desktop-02-findings.png "${U}?view=findings" 2>/dev/null
$C --window-size=390,2400 --force-device-scale-factor=2 --screenshot=screenshots/phone-01-top.png "$U" 2>/dev/null
ls -la dist screenshots
