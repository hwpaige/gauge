"""
test_display.py — Quick SPI display sanity check.
Fills the screen red. If it works, wiring and SPI are good.
Run from inside the venv: python3 test_display.py
"""

import st7789
from PIL import Image

display = st7789.ST7789(
    port=0,
    cs=st7789.BG_SPI_CS_FRONT,  # BCM 7  / pin 24
    dc=9,                         # BCM 9  / pin 21
    backlight=19,                 # BCM 19 / pin 35
    rotation=0,
    width=240,
    height=240,
    offset_left=0,
    offset_top=0
)

img = Image.new('RGB', (240, 240), color=(255, 0, 0))
display.display(img)
print("Screen should be solid red. If so, SPI and wiring are good!")