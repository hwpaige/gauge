import spidev, time, gpiod, numpy as np
from gpiod.line import Direction, Value as GpioValue

_CHUNK = 4096


def _find_gpiochip(label: str) -> str:
    """Return the /dev/gpiochipN path whose label contains `label`."""
    for n in range(8):
        path = f'/dev/gpiochip{n}'
        try:
            with gpiod.Chip(path) as chip:
                if label in chip.get_info().label:
                    return path
        except OSError:
            break
    raise RuntimeError(f"No gpiochip found with label containing {label!r}")


# Allwinner H618 main GPIO controller (300b000.pinctrl, 288 lines)
_GPIOCHIP = _find_gpiochip('300b000')


class ST7789:
    # BPI M4 Zero pin mapping:
    #   dc=231  → PH7, physical pin 18
    #   rst=232 → PH8, physical pin 22
    #   cs_gpio=67 → PC3, physical pin 24 (software CS — hardware CS unclaimed by kernel)
    def __init__(self, port=0, cs=0, dc=231, rst=232, cs_gpio=67,
                 width=240, height=240, spi_speed_hz=16_000_000):
        self._w, self._h = width, height
        self._dc, self._rst, self._cs_gpio = dc, rst, cs_gpio

        self._gpio = gpiod.request_lines(_GPIOCHIP, consumer='st7789', config={
            dc:       gpiod.LineSettings(direction=Direction.OUTPUT, output_value=GpioValue.ACTIVE),
            rst:      gpiod.LineSettings(direction=Direction.OUTPUT, output_value=GpioValue.ACTIVE),
            cs_gpio:  gpiod.LineSettings(direction=Direction.OUTPUT, output_value=GpioValue.ACTIVE),
        })

        self._spi = spidev.SpiDev()
        self._spi.open(port, cs)
        self._spi.max_speed_hz = spi_speed_hz
        self._spi.mode = 0

        self._reset()
        self._init()

    def _set(self, pin, value):
        self._gpio.set_value(pin, GpioValue.ACTIVE if value else GpioValue.INACTIVE)

    def _cs(self, active):
        # CS is active-LOW: INACTIVE=LOW=selected, ACTIVE=HIGH=deselected
        self._gpio.set_value(self._cs_gpio,
                             GpioValue.INACTIVE if active else GpioValue.ACTIVE)

    def _cmd(self, cmd):
        self._set(self._dc, 0)
        self._cs(True); self._spi.writebytes([cmd]); self._cs(False)

    def _data(self, data):
        self._set(self._dc, 1)
        self._cs(True)
        if isinstance(data, int):
            self._spi.writebytes([data])
        else:
            b = data if isinstance(data, (bytes, bytearray)) else bytes(data)
            for i in range(0, len(b), _CHUNK):
                self._spi.writebytes2(b[i:i+_CHUNK])
        self._cs(False)

    def _reset(self):
        self._set(self._rst, 1); time.sleep(0.05)
        self._set(self._rst, 0); time.sleep(0.05)
        self._set(self._rst, 1); time.sleep(0.15)

    def _init(self):
        self._cmd(0x01); time.sleep(0.15)
        self._cmd(0x11); time.sleep(0.12)
        self._cmd(0x36); self._data(0x00)
        self._cmd(0x3A); self._data(0x05)
        self._cmd(0xB2); self._data([0x0C, 0x0C, 0x00, 0x33, 0x33])
        self._cmd(0xB7); self._data(0x35)
        self._cmd(0xBB); self._data(0x28)
        self._cmd(0xC0); self._data(0x0C)
        self._cmd(0xC2); self._data(0x01)
        self._cmd(0xC3); self._data(0x0B)
        self._cmd(0xC4); self._data(0x20)
        self._cmd(0xC6); self._data(0x0F)
        self._cmd(0xD0); self._data([0xA4, 0xA1])
        self._cmd(0xE0); self._data([0xD0, 0x01, 0x08, 0x0F, 0x11, 0x2A,
                                     0x36, 0x55, 0x44, 0x3A, 0x0B, 0x06, 0x11, 0x20])
        self._cmd(0xE1); self._data([0xD0, 0x02, 0x07, 0x0A, 0x0B, 0x18,
                                     0x34, 0x43, 0x4A, 0x2B, 0x1B, 0x1C, 0x22, 0x1F])
        self._cmd(0x21)
        self._cmd(0x13)
        self._cmd(0x29); time.sleep(0.10)

    def display(self, image):
        """Push a PIL RGB image to the display."""
        self._cmd(0x2A); self._data(bytes([0x00, 0x00, 0x00, self._w - 1]))
        self._cmd(0x2B); self._data(bytes([0x00, 0x00, 0x00, self._h - 1]))
        self._cmd(0x2C)
        arr = np.frombuffer(image.convert('RGB').tobytes(), dtype=np.uint8)
        arr = arr.reshape(-1, 3).astype(np.uint16)
        rgb565 = ((arr[:, 0] & 0xF8) << 8) | ((arr[:, 1] & 0xFC) << 3) | (arr[:, 2] >> 3)
        rgb565 = ((rgb565 >> 8) | ((rgb565 & 0xFF) << 8))
        self._data(rgb565.tobytes())
