#!/bin/bash
# ─────────────────────────────────────────────────────────
#  Launch the CHT gauge on the SPI display framebuffer
# ─────────────────────────────────────────────────────────

cd /root/gauge
source venv/bin/activate

# Direct pygame to the SPI display framebuffer, not a desktop
export SDL_VIDEODRIVER=fbcon
export SDL_FBDEV=/dev/fb0

python3 gauge.py