import spidev, time, gpiod, numpy as np
from gpiod.line import Direction, Value

# BPI M4 Zero v2 — Allwinner H618, gpiochip1 = 300b000.pinctrl (288 lines)
# SPI1 bus: PH6=CLK(pin23)  PH7=MOSI(pin19)  PH8=MISO(pin21)  PH5=CS(pin24)
# DC=PI16(272)=pin18  RST=PC2(66)=pin22  (from WiringPi phyToGpio table)
# TE (tearing effect): wire display TE pin to a free header GPIO, pass te=<offset>.
# Hardware CS is kernel-managed — no software CS needed.
_CHIP  = '/dev/gpiochip1'
_CHUNK = 4096


class ST7789:
    def __init__(self, dc, rst, port=1, cs=0,
                 width=240, height=240, speed_hz=16_000_000,
                 x_off=0, y_off=0, te=None):
        self._w, self._h = width, height
        self._x_off, self._y_off = x_off, y_off
        self._dc = dc
        self._te = te

        pin_cfg = {
            dc:  gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE),
            rst: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE),
        }
        if te is not None:
            pin_cfg[te] = gpiod.LineSettings(direction=Direction.INPUT)

        self._gpio = gpiod.request_lines(_CHIP, consumer='st7789', config=pin_cfg)

        self._spi = spidev.SpiDev()
        self._spi.open(port, cs)
        self._spi.max_speed_hz = speed_hz
        self._spi.mode = 0

        self._reset(rst)
        self._init()
        if te is not None:
            self._cmd(0x35); self._data(0x00)  # TEON: vsync pulse only
        self._set_window()
        self._clear()
        self._frame_started = False

    def _set_window(self):
        xs, xe = self._x_off, self._x_off + self._w - 1
        ys, ye = self._y_off, self._y_off + self._h - 1
        self._cmd(0x2A); self._data(bytes([xs >> 8, xs & 0xFF, xe >> 8, xe & 0xFF]))
        self._cmd(0x2B); self._data(bytes([ys >> 8, ys & 0xFF, ye >> 8, ye & 0xFF]))

    def _clear(self):
        self._cmd(0x2C)
        self._data(b'\x00\x00' * (self._w * self._h))

    def _set(self, pin, high):
        self._gpio.set_value(pin, Value.ACTIVE if high else Value.INACTIVE)

    def _reset(self, rst):
        self._set(rst, 1); time.sleep(0.05)
        self._set(rst, 0); time.sleep(0.05)
        self._set(rst, 1); time.sleep(0.15)

    def _cmd(self, cmd):
        self._set(self._dc, 0)
        self._spi.writebytes([cmd])

    def _data(self, data):
        self._set(self._dc, 1)
        if isinstance(data, int):
            self._spi.writebytes([data])
        else:
            b = bytes(data) if not isinstance(data, (bytes, bytearray)) else data
            self._spi.writebytes2(b)

    def _init(self):
        self._cmd(0x01); time.sleep(0.15)   # software reset
        self._cmd(0x11); time.sleep(0.12)   # sleep out
        self._cmd(0x3A); self._data(0x05)   # RGB565
        self._cmd(0x36); self._data(0x00)
        self._cmd(0xB2); self._data([0x0C, 0x0C, 0x00, 0x33, 0x33])
        self._cmd(0xB7); self._data(0x35)
        self._cmd(0xBB); self._data(0x28)
        self._cmd(0xC0); self._data(0x0C)
        self._cmd(0xC2); self._data(0x01)
        self._cmd(0xC3); self._data(0x0B)
        self._cmd(0xC4); self._data(0x20)
        self._cmd(0xC6); self._data(0x0F)
        self._cmd(0xD0); self._data([0xA4, 0xA1])
        self._cmd(0xE0); self._data([0xD0,0x01,0x08,0x0F,0x11,0x2A,
                                      0x36,0x55,0x44,0x3A,0x0B,0x06,0x11,0x20])
        self._cmd(0xE1); self._data([0xD0,0x02,0x07,0x0A,0x0B,0x18,
                                      0x34,0x43,0x4A,0x2B,0x1B,0x1C,0x22,0x1F])
        self._cmd(0x21)   # inversion on
        self._cmd(0x13)   # normal display mode
        self._cmd(0x29); time.sleep(0.10)   # display on

    def _wait_te(self):
        # Poll until TE goes LOW (active scan), then HIGH (vblank start).
        # Writing during vblank and staying ahead of the scan line = zero tearing.
        v = self._gpio.get_value
        while v(self._te) == Value.ACTIVE:   # if already HIGH, wait for LOW first
            pass
        while v(self._te) == Value.INACTIVE: # wait for rising edge (vblank start)
            pass

    def display(self, image):
        """Push a 240×240 PIL image (any mode) to the display."""
        # Precompute before touching SPI — no gap between RAMWR and pixel data
        arr = np.frombuffer(image.convert('RGB').tobytes(), dtype=np.uint8)
        arr = arr.reshape(-1, 3).astype(np.uint16)
        rgb565 = ((arr[:,0] & 0xF8) << 8) | ((arr[:,1] & 0xFC) << 3) | (arr[:,2] >> 3)
        buf = ((rgb565 >> 8) | ((rgb565 & 0xFF) << 8)).tobytes()
        if self._te is not None:
            self._wait_te()
        cmd = 0x2C if not self._frame_started else 0x3C
        self._frame_started = True
        self._cmd(cmd)
        self._data(buf)
