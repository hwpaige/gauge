#!/bin/bash
# ─────────────────────────────────────────────────────────
#  Launch the CHT gauge — renders to kernel /dev/fb0 (fbcon)
#  Requires kernel ST7789 panel driver to own the display
#  (provides smooth, tear-free updates; no GPIO busy errors).
# ─────────────────────────────────────────────────────────

cd /root/gauge
source venv/bin/activate

python3 gauge.py
