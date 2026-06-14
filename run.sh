#!/bin/bash
# ─────────────────────────────────────────────────────────
#  Launch the CHT gauge — renders offscreen, pushes to SPI
# ─────────────────────────────────────────────────────────

cd /root/gauge
source venv/bin/activate

python3 gauge.py
