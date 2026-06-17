#!/usr/bin/env python3
"""
Generate /lib/firmware/panel-mipi-dbi-spi.bin for the ST7789v 240x280 panel.

The panel-mipi-dbi kernel driver (drivers/gpu/drm/tiny/panel-mipi-dbi.c)
loads a firmware file named after the first compatible string:
  panel-mipi-dbi-spi  ->  /lib/firmware/panel-mipi-dbi-spi.bin

Binary format (from the driver source):
  [15-byte magic "MIPI DBI\0\0\0\0\0\0\0"] [1-byte version=1]
  then repeated command groups: [cmd] [num_params] [param ...]
  delay: NOP (0x00) with 1 param = delay in ms

Init sequence for ST7789v, BGR565, 240x280 GRAM:
  COLMOD=0x55  -> RGB565 pixel format
  MADCTL=0x08  -> BGR mode (bit 3) so the panel maps the 565 bits as BGR
                  which matches gauge.py's BGR565 encoding
  INVON        -> required by this panel variant to display correctly
"""

import os

MAGIC    = b'MIPI DBI\x00\x00\x00\x00\x00\x00\x00'   # 15 bytes
VERSION  = b'\x01'

COMMANDS = bytes([
    0x01, 0x00,              # SWRESET — 0 params
    0x00, 0x01, 150,         # delay 150 ms
    0x11, 0x00,              # SLPOUT — 0 params
    0x00, 0x01, 120,         # delay 120 ms
    0x3A, 0x01, 0x55,        # COLMOD  — RGB565
    0x36, 0x01, 0x08,        # MADCTL  — BGR
    0x21, 0x00,              # INVON   — 0 params
    0x13, 0x00,              # NORON   — 0 params
    0x29, 0x00,              # DISPON  — 0 params
    0x00, 0x01, 50,          # delay 50 ms
])

fw  = MAGIC + VERSION + COMMANDS
out = '/lib/firmware/panel-mipi-dbi-spi.bin'

os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, 'wb') as f:
    f.write(fw)

print(f"Written {len(fw)} bytes to {out}")
