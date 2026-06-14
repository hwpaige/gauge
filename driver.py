"""
Minimal ST7789 SPI display driver for Linux.
Uses spidev for SPI and gpiod character device for GPIO — works on Linux 6.x
where legacy sysfs GPIO export is blocked by pinctrl.
"""
import spidev
import time
import gpiod
from gpiod.line import Direction, Value as GpioValue
import numpy as np

_CHUNK    = 4096
_GPIOCHIP = '/dev/gpiochip0'


class ST7789:
    def __init__(self, port=0, cs=0, dc=73, rst=72,
                 width=240, height=240, spi_speed_hz=16_000_000):
        self._w, self._h = width, height
        self._dc  = dc
        self._rst = rst

        # Claim DC and RST lines via the gpiod character device
        self._gpio = gpiod.request_lines(
            _GPIOCHIP,
            consumer='st7789',
            config={
                dc:  gpiod.LineSettings(direction=Direction.OUTPUT,
                                        output_value=GpioValue.INACTIVE),
                rst: gpiod.LineSettings(direction=Direction.OUTPUT,
                                        output_value=GpioValue.INACTIVE),
            },
        )

        # SPI
        self._spi = spidev.SpiDev()
        self._spi.open(port, cs)
        self._spi.max_speed_hz = spi_speed_hz
        self._spi.mode = 0

        self._reset()
        self._init()

    # ── GPIO ────────────────────────────────────────────────
    def _set(self, pin, value):
        self._gpio.set_value(pin, GpioValue.ACTIVE if value else GpioValue.INACTIVE)

    # ── SPI ─────────────────────────────────────────────────
    def _cmd(self, cmd):
        self._set(self._dc, 0)          # DC low  = command
        self._spi.writebytes([cmd])

    def _data(self, data):
        self._set(self._dc, 1)          # DC high = data
        if isinstance(data, int):
            self._spi.writebytes([data])
        else:
            b = data if isinstance(data, (bytes, bytearray)) else bytes(data)
            for i in range(0, len(b), _CHUNK):
                self._spi.writebytes2(b[i:i + _CHUNK])

    # ── Init ────────────────────────────────────────────────
    def _reset(self):
        self._set(self._rst, 1); time.sleep(0.05)
        self._set(self._rst, 0); time.sleep(0.05)
        self._set(self._rst, 1); time.sleep(0.15)

    def _init(self):
        self._cmd(0x01); time.sleep(0.15)   # software reset
        self._cmd(0x11); time.sleep(0.05)   # sleep out
        self._cmd(0x3A); self._data(0x55)   # 16-bit colour (RGB565)
        self._cmd(0x36); self._data(0x00)   # memory access control
        self._cmd(0x21)                      # display inversion on
        self._cmd(0x13)                      # normal display mode
        self._cmd(0x29); time.sleep(0.10)   # display on

    # ── Frame push ──────────────────────────────────────────
    def display(self, image):
        """Push a PIL RGB image to the display."""
        self._cmd(0x2A)
        self._data(bytes([0x00, 0x00, 0x00, self._w - 1]))   # column range
        self._cmd(0x2B)
        self._data(bytes([0x00, 0x00, 0x00, self._h - 1]))   # row range
        self._cmd(0x2C)

        # RGB888 → RGB565, byte-swapped to big-endian for the display
        arr    = np.frombuffer(image.convert('RGB').tobytes(), dtype=np.uint8)
        arr    = arr.reshape(-1, 3).astype(np.uint16)
        rgb565 = ((arr[:, 0] & 0xF8) << 8) | ((arr[:, 1] & 0xFC) << 3) | (arr[:, 2] >> 3)
        rgb565 = ((rgb565 >> 8) | ((rgb565 & 0xFF) << 8))    # little→big endian
        self._data(rgb565.tobytes())
