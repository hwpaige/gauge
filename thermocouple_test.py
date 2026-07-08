#!/usr/bin/env python3
"""Thermocouple HAT test tool  —  Raspberry Pi 4B + Raspberry Pi OS Lite

Interactive tester for the 4-channel MAX31856 thermocouple HAT (the ordered
board in thermocouple_hat/). It configures each MAX31856 for continuous
conversion and lets you:

    • monitor all 4 channels at once (live table)
    • focus a single channel with a large readout (for probing one sensor)
    • run a one-shot probe of every channel (responding? open?)
    • set the thermocouple TYPE PER CHANNEL, plus units / poll rate / filter

    cd ~/gauge
    python3 thermocouple_test.py                  # interactive menu (arrow keys / hotkeys)
    python3 thermocouple_test.py --tc-type K,T,K,K # per-channel types (TC1..TC4)
    python3 thermocouple_test.py --tc-type T       # one type for all channels
    python3 thermocouple_test.py --plain           # non-interactive: one line per poll
    python3 thermocouple_test.py --once            # single read of all channels, exit
    python3 thermocouple_test.py --sim             # fake data, no hardware
    python3 thermocouple_test.py --selftest        # verify the register decoders, no hardware

Interactive hotkeys (work from anywhere):
    1-4 focus channel TCn    a all channels    p probe    s settings
    c/f units   +/- poll rate   r reset min/max   q / Esc back   (q on menu quits)
In the single-channel view:  ←/→ change channel   t change THIS channel's type

────────────────────────────────────────────────────────────────────────────
HARDWARE  (from the ordered PCB netlist, thermocouple_hat/):

  4× MAX31856 share SPI0:  SCLK=GPIO11(pin23)  MOSI=GPIO10(pin19)  MISO=GPIO9(pin21)
  Per-channel software chip-select (each its own GPIO):
        TC1 (U2, term J3, LEFT-most) -> GPIO7  (pin 26)
        TC2 (U3, term J4, RIGHT-most)-> GPIO5  (pin 29)
        TC3 (U4, term J5)            -> GPIO6  (pin 31)
        TC4 (U5, term J6)            -> GPIO12 (pin 32)
  Physical terminal order, left->right: TC1, TC3, TC4, TC2.
  Each MAX31856 is configured independently, so per-channel type just writes a
  different CR1 to each chip.

ONE-TIME PI SETUP (see setup_pi.sh):
  /boot/firmware/config.txt needs:  dtparam=spi=on  and  dtoverlay=spi0-1cs
  then reboot, and:  sudo apt install -y python3-spidev python3-lgpio
"""

import argparse
import math
import sys
import time

# ── Wiring (edit here if the board CS assignment ever changes) ─────────────
# (label, chip-select BCM GPIO, header pin for reference)
CHANNELS = [
    ("TC1", 7,  26),
    ("TC2", 5,  29),
    ("TC3", 6,  31),
    ("TC4", 12, 32),
]
TERM_POS = {"TC1": "left-most terminal", "TC3": "2nd from left",
            "TC4": "3rd from left", "TC2": "right-most terminal"}

SPI_BUS, SPI_DEV = 0, 0
SPI_MODE = 1                     # MAX31856 = SPI mode 1 (CPOL=0, CPHA=1)
DEFAULT_SPEED_HZ = 2_000_000
DEFAULT_HZ = 4.0
DEFAULT_TC_TYPE = "K"            # default type for every channel (override per-channel in the UI)
DEFAULT_FILTER = 60             # mains-rejection filter: 60 (US) or 50 (EU); global to all chips

RANGE_MIN, RANGE_MAX = 0, 300
CAUTION_FRAC, DANGER_FRAC = 0.60, 0.85

# ── MAX31856 registers ─────────────────────────────────────────────────────
REG_CR0, REG_CR1, REG_MASK = 0x00, 0x01, 0x02
REG_CJTH, REG_LTCBH, REG_SR = 0x0A, 0x0C, 0x0F
WRITE = 0x80

TC_TYPES = {"B": 0x0, "E": 0x1, "J": 0x2, "K": 0x3,
            "N": 0x4, "R": 0x5, "S": 0x6, "T": 0x7}
TC_ORDER = ["B", "E", "J", "K", "N", "R", "S", "T"]

CR0_CMODE      = 0x80
CR0_OCFAULT_01 = 0x10
CR0_FILTER_50  = 0x01

SR_BITS = [
    (0x01, "OPEN"), (0x02, "OV/UV"), (0x04, "TC LOW"), (0x08, "TC HIGH"),
    (0x10, "CJ LOW"), (0x20, "CJ HIGH"), (0x40, "TC RANGE"), (0x80, "CJ RANGE"),
]


# ── Pure decoders (type-independent — the chip linearises per its CR1) ──────
def decode_temperature(b2, b1, b0):
    raw = (b2 << 16) | (b1 << 8) | b0
    if raw & 0x800000:
        raw -= 0x1000000
    return raw / 4096.0


def decode_coldjunction(hi, lo):
    raw = (hi << 8) | lo
    if raw & 0x8000:
        raw -= 0x10000
    return raw / 256.0


def decode_max31856(regs):
    cjth, cjtl, ltcbh, ltcbm, ltcbl, sr = regs[:6]
    no_data = all(x == 0xFF for x in regs[:6]) or all(x == 0x00 for x in regs[:6])
    return {
        "temp_c": decode_temperature(ltcbh, ltcbm, ltcbl),
        "cj_c": decode_coldjunction(cjth, cjtl),
        "sr": sr,
        "oc": bool(sr & 0x01),
        "faults": [name for bit, name in SR_BITS if sr & bit],
        "raw": (ltcbh << 16) | (ltcbm << 8) | ltcbl,
        "no_data": no_data,
        "ok": (sr == 0) and not no_data,
    }


def status_text(r):
    if "unavailable" in r:
        return "N/C"
    if r.get("no_data"):
        return "NO DATA"
    if r["faults"]:
        return r["faults"][0]
    return "OK"


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


def zone(temp_c):
    if RANGE_MAX <= RANGE_MIN:
        return 0
    pct = (temp_c - RANGE_MIN) / (RANGE_MAX - RANGE_MIN)
    return 0 if pct < CAUTION_FRAC else (1 if pct < DANGER_FRAC else 2)


# ── Software chip-select via lgpio ─────────────────────────────────────────
class ChipSelect:
    def __init__(self, pins, gpiochip=None):
        import lgpio
        self.lg = lgpio
        self.pins = list(pins)
        chips = [gpiochip] if gpiochip is not None else [0, 4]
        last = None
        self.h = None
        for c in chips:
            try:
                self.h = lgpio.gpiochip_open(c)
                break
            except Exception as e:            # noqa: BLE001
                last = e
        if self.h is None:
            raise RuntimeError(f"could not open a gpiochip ({chips}): {last}")
        self.claimed = []
        for p in self.pins:
            try:
                lgpio.gpio_claim_output(self.h, p, 1)
                self.claimed.append(p)
            except Exception as e:            # noqa: BLE001
                busy = "GPIO_BUSY" in str(e) or "busy" in str(e).lower()
                hint = ("  (GPIO7 is claimed by the default 2-CS SPI — add "
                        "'dtoverlay=spi0-1cs' to /boot/firmware/config.txt and reboot)"
                        if p == 7 and busy else "")
                raise RuntimeError(f"cannot drive GPIO{p} as output: {e}{hint}")

    def low(self, pin):
        self.lg.gpio_write(self.h, pin, 0)

    def high(self, pin):
        self.lg.gpio_write(self.h, pin, 1)

    def close(self):
        try:
            for p in self.claimed:
                try:
                    self.lg.gpio_write(self.h, p, 1)
                    self.lg.gpio_free(self.h, p)
                except Exception:
                    pass
            self.lg.gpiochip_close(self.h)
        except Exception:
            pass


# ── Reader: spidev bus + software CS + per-channel MAX31856 config/read ────
class Reader:
    def __init__(self, channels, tc_types, filter_hz, speed_hz, sim=False, gpiochip=None):
        self.channels = channels
        self.tc_types = dict(tc_types)        # {label: 'K'/'T'/...}  — one per channel
        self.filter_hz = filter_hz            # global (mains frequency)
        self.speed_hz = speed_hz
        self.sim = sim
        self.spi = None
        self.cs = None
        self.fatal = None
        self._t0 = time.time()
        self._gpio_of = {label: gpio for label, gpio, _ in channels}
        if sim:
            return
        self._open(gpiochip)

    def _open(self, gpiochip):
        try:
            import spidev
        except ImportError as e:
            self.fatal = f"spidev not installed ({e}) — sudo apt install python3-spidev"
            return
        try:
            self.spi = spidev.SpiDev()
            self.spi.open(SPI_BUS, SPI_DEV)
            self.spi.max_speed_hz = self.speed_hz
            self.spi.mode = SPI_MODE
            try:
                self.spi.no_cs = True
            except Exception:
                pass
        except Exception as e:            # noqa: BLE001
            self.fatal = (f"cannot open /dev/spidev{SPI_BUS}.{SPI_DEV}: {e}\n"
                          "  enable SPI: sudo raspi-config -> Interface Options -> SPI")
            return
        try:
            self.cs = ChipSelect([g for _, g, _ in self.channels], gpiochip)
        except Exception as e:            # noqa: BLE001
            self.fatal = str(e)
            return
        self.reconfigure_all()

    def _xfer(self, gpio, data):
        self.cs.low(gpio)
        try:
            return self.spi.xfer2(list(data))
        finally:
            self.cs.high(gpio)

    def _write_reg(self, gpio, addr, value):
        self._xfer(gpio, [(addr & 0x7F) | WRITE, value & 0xFF])

    def _read_regs(self, gpio, addr, n):
        resp = self._xfer(gpio, [addr & 0x7F] + [0x00] * n)
        return resp[1:]

    def _configure(self, label, gpio):
        # CR1: averaging=1 sample, THIS channel's thermocouple type in the low nibble
        self._write_reg(gpio, REG_CR1, TC_TYPES[self.tc_types[label]])
        cr0 = CR0_CMODE | CR0_OCFAULT_01 | (CR0_FILTER_50 if self.filter_hz == 50 else 0)
        self._write_reg(gpio, REG_CR0, cr0)

    def reconfigure_all(self):
        if self.sim or self.fatal or self.spi is None:
            return
        for label, gpio, _ in self.channels:
            self._configure(label, gpio)
        time.sleep(0.25)

    def set_type(self, label, tc_type):
        """Change one channel's thermocouple type and re-write just that chip."""
        self.tc_types[label] = tc_type
        if self.sim or self.fatal or self.spi is None:
            return
        self._configure(label, self._gpio_of[label])
        time.sleep(0.2)

    def set_filter(self, filter_hz):
        """Mains filter is global — re-write CR0 on every chip."""
        self.filter_hz = filter_hz
        self.reconfigure_all()

    def read(self, label, gpio):
        if self.sim:
            return self._sim(label)
        try:
            return decode_max31856(self._read_regs(gpio, REG_CJTH, 6))
        except Exception as e:            # noqa: BLE001
            return {"unavailable": f"read error: {e}"}

    def verify(self, label, gpio):
        """Read CR1 back to confirm the chip is responding and set to the right type."""
        if self.sim:
            return True
        try:
            return self._read_regs(gpio, REG_CR1, 1)[0] == TC_TYPES[self.tc_types[label]]
        except Exception:
            return False

    def _sim(self, label):
        t = time.time() - self._t0
        base = {"TC1": 150, "TC2": 120, "TC3": 200, "TC4": 90}.get(label, 130)
        amp = {"TC1": 80, "TC2": 60, "TC3": 40, "TC4": 100}.get(label, 50)
        c = base + amp * math.sin(t * 0.3 + len(label))
        return {"temp_c": c, "cj_c": 24.5, "sr": 0, "oc": False,
                "faults": [], "raw": 0, "no_data": False, "ok": True}

    def close(self):
        if self.cs:
            self.cs.close()
        if self.spi:
            try:
                self.spi.close()
            except Exception:
                pass


# ── Per-channel running stats ──────────────────────────────────────────────
class Stat:
    __slots__ = ("n", "err", "tmin", "tmax")

    def __init__(self):
        self.n = self.err = 0
        self.tmin = self.tmax = None

    def update(self, r):
        if "unavailable" in r or not r.get("ok"):
            self.err += 1
            return
        self.n += 1
        c = r["temp_c"]
        self.tmin = c if self.tmin is None else min(self.tmin, c)
        self.tmax = c if self.tmax is None else max(self.tmax, c)

    def reset(self):
        self.n = self.err = 0
        self.tmin = self.tmax = None


def fmt_temp(c, fahrenheit):
    return f"{c_to_f(c):6.1f}F" if fahrenheit else f"{c:6.1f}C"


# ── Plain (non-curses) output ──────────────────────────────────────────────
def run_plain(reader, args):
    period = 1.0 / args.hz
    try:
        while True:
            parts = []
            for label, gpio, _ in reader.channels:
                r = reader.read(label, gpio)
                t = reader.tc_types[label]
                if "unavailable" in r:
                    parts.append(f"{label}({t}) {r['unavailable']}")
                else:
                    parts.append(f"{label}({t}) {fmt_temp(r['temp_c'], args.fahrenheit)} {status_text(r)}")
            print(f"[{time.strftime('%H:%M:%S')}]  " + "   ".join(parts), flush=True)
            if args.once:
                return
            time.sleep(period)
    except KeyboardInterrupt:
        pass


# ── Big block digits for the single-channel readout ────────────────────────
_BIG = {
    "0": ("███", "█ █", "█ █", "█ █", "███"),
    "1": (" █ ", "██ ", " █ ", " █ ", "███"),
    "2": ("███", "  █", "███", "█  ", "███"),
    "3": ("███", "  █", "███", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"),
    "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"),
    "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"),
    "9": ("███", "█ █", "███", "  █", "███"),
    "-": ("   ", "   ", "███", "   ", "   "),
    ".": ("   ", "   ", "   ", "   ", " ■ "),
    " ": ("   ", "   ", "   ", "   ", "   "),
}


def draw_big(put, y, x, s, attr):
    for i, ch in enumerate(s):
        g = _BIG.get(ch, _BIG[" "])
        for row in range(5):
            put(y + row, x + i * 4, g[row], attr)


# ── Interactive curses app ─────────────────────────────────────────────────
class App:
    def __init__(self, scr, reader, args):
        import curses
        self.curses = curses
        self.scr = scr
        self.reader = reader
        self.hz = args.hz
        self.fahrenheit = args.fahrenheit
        self.running = True
        self.state = "menu"
        self.menu_sel = 0
        self.pick_sel = 0
        self.set_sel = 0
        self.single_idx = 0
        self.readings = {label: {} for label, _, _ in reader.channels}
        self.stats = {label: Stat() for label, _, _ in reader.channels}
        self.probe_rows = []

        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        self.CT, self.CSEL, self.CG, self.CH, self.CE, self.CA, self.CD = range(1, 8)
        curses.init_pair(self.CT,   curses.COLOR_CYAN, -1)
        curses.init_pair(self.CSEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(self.CG,   curses.COLOR_GREEN, -1)
        curses.init_pair(self.CH,   curses.COLOR_YELLOW, -1)
        curses.init_pair(self.CE,   curses.COLOR_RED, -1)
        curses.init_pair(self.CA,   curses.COLOR_YELLOW, -1)
        curses.init_pair(self.CD,   curses.COLOR_WHITE, -1)
        self.zc = {0: self.CG, 1: self.CA, 2: self.CE}

    # -- drawing primitives --
    def cp(self, n):
        return self.curses.color_pair(n)

    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and 0 <= x < w:
            try:
                self.scr.addstr(y, x, s[:max(0, w - x - 1)], attr)
            except self.curses.error:
                pass

    def hline(self, y):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h:
            try:
                self.scr.hline(y, 0, self.curses.ACS_HLINE, w)
            except self.curses.error:
                pass

    def header(self, title, sub=""):
        _, w = self.scr.getmaxyx()
        self.put(0, 0, (" " + title).ljust(max(0, w - 1)), self.cp(self.CT) | self.curses.A_BOLD)
        if self.reader.sim:
            self.put(0, max(0, w - 5), "SIM", self.cp(self.CH) | self.curses.A_BOLD)
        if sub:
            self.put(1, 2, sub, self.cp(self.CD) | self.curses.A_DIM)
        self.hline(2)

    def footer(self, hint):
        h, w = self.scr.getmaxyx()
        self.put(h - 1, 0, (" " + hint).ljust(max(0, w - 1)), self.cp(self.CH))

    def status_attr(self, r):
        st = status_text(r)
        return st, (self.cp(self.CG) | self.curses.A_BOLD if st == "OK"
                    else self.cp(self.CE) | self.curses.A_BOLD)

    def unit(self):
        return "°F" if self.fahrenheit else "°C"

    def conv(self, c):
        return c_to_f(c) if self.fahrenheit else c

    def types_summary(self):
        return "/".join(self.reader.tc_types[l] for l, _, _ in self.reader.channels)

    def poll(self):
        for label, gpio, _ in self.reader.channels:
            r = self.reader.read(label, gpio)
            self.readings[label] = r
            self.stats[label].update(r)

    # -- screens --
    def render(self):
        self.scr.erase()
        if self.state in ("all", "single"):
            self.poll()
        {"menu": self.s_menu, "all": self.s_all, "pick": self.s_pick,
         "single": self.s_single, "probe": self.s_probe,
         "settings": self.s_settings}[self.state]()
        self.scr.refresh()

    def _fatal_banner(self, y=4):
        if not self.reader.fatal:
            return False
        self.put(y, 2, "HARDWARE UNAVAILABLE", self.cp(self.CE) | self.curses.A_BOLD)
        for i, line in enumerate(self.reader.fatal.split("\n")):
            self.put(y + 2 + i, 2, line, self.cp(self.CE))
        self.put(y + 3 + len(self.reader.fatal.split("\n")), 2,
                 "Fix the wiring/config, or relaunch with --sim to preview.",
                 self.cp(self.CD) | self.curses.A_DIM)
        return True

    def s_menu(self):
        self.header("THERMOCOUPLE HAT TEST   —   MAX31856 ×4",
                    f"types {self.types_summary()} · {self.reader.filter_hz} Hz filter · "
                    f"{self.hz:g} Hz poll · {self.unit()}"
                    + ("   [SPI OK]" if not self.reader.fatal else "   [NO HARDWARE — see below]"))
        items = ["Monitor all 4 channels",
                 "Focus a single channel",
                 "Quick probe (one-shot)",
                 "Settings  (per-channel type / units / rate / filter)",
                 "Quit"]
        for i, it in enumerate(items):
            y = 4 + i
            if i == self.menu_sel:
                _, w = self.scr.getmaxyx()
                self.put(y, 1, (" ▶ " + it).ljust(max(0, w - 2)), self.cp(self.CSEL) | self.curses.A_BOLD)
            else:
                self.put(y, 4, it, self.cp(self.CD))
        if self.reader.fatal:
            self._fatal_banner(4 + len(items) + 1)
        self.footer("↑↓ move   Enter select   1-4 focus TCn   a all   p probe   s settings   q quit")

    # all-channels table column x-positions
    _X = dict(lbl=2, ty=6, cs=9, temp=17, cj=28, st=38, mn=49, mx=56, raw=64)

    def s_all(self):
        X = self._X
        self.header(f"ALL CHANNELS   —   MAX31856 ×4 · {self.hz:g} Hz · {self.unit()}")
        if self._fatal_banner():
            self.footer("q / Esc  back to menu")
            return
        dim = self.cp(self.CD) | self.curses.A_DIM
        for name, x in (("CH", X["lbl"]), ("TY", X["ty"]), ("CS", X["cs"]),
                        ("TEMP", X["temp"] + 2), ("COLD-J", X["cj"]), ("STATUS", X["st"]),
                        ("MIN", X["mn"]), ("MAX", X["mx"]), ("RAW", X["raw"])):
            self.put(3, x, name, dim)
        for i, (label, gpio, pin) in enumerate(self.reader.channels):
            r = self.readings.get(label, {})
            st = self.stats[label]
            y = 4 + i
            self.put(y, X["lbl"], f"{label}", self.cp(self.CT) | self.curses.A_BOLD)
            self.put(y, X["ty"], self.reader.tc_types[label], self.cp(self.CH) | self.curses.A_BOLD)
            self.put(y, X["cs"], f"GPIO{gpio}", dim)
            stxt = status_text(r) if r else "…"
            if not r or "unavailable" in r or r.get("no_data") or not r.get("ok", False):
                self.put(y, X["temp"], f"{'---':>7}", self.cp(self.CE))
                self.put(y, X["cj"], f"{'---':>6}", dim)
                self.put(y, X["st"], f"{stxt:<9}", self.cp(self.CE) | self.curses.A_BOLD)
            else:
                self.put(y, X["temp"], f"{self.conv(r['temp_c']):6.1f}{'F' if self.fahrenheit else 'C'}",
                         self.cp(self.zc[zone(r['temp_c'])]) | self.curses.A_BOLD)
                self.put(y, X["cj"], f"{self.conv(r['cj_c']):5.1f}{'F' if self.fahrenheit else 'C'}",
                         self.cp(self.CD))
                self.put(y, X["st"], f"{stxt:<9}", self.cp(self.CG) | self.curses.A_BOLD)
            lo = "  --" if st.tmin is None else f"{self.conv(st.tmin):.0f}"
            hi = "  --" if st.tmax is None else f"{self.conv(st.tmax):.0f}"
            self.put(y, X["mn"], f"{lo:>5}", dim)
            self.put(y, X["mx"], f"{hi:>5}", dim)
            if r and "unavailable" not in r:
                self.put(y, X["raw"], f"0x{r.get('raw', 0):06X} sr{r.get('sr', 0):02X}", dim)
        self.footer("1-4 focus   c/f units   +/- rate   r reset   p probe   s settings   q back")

    def s_pick(self):
        self.header("FOCUS A SINGLE CHANNEL", "pick a channel to watch up close")
        for i, (label, gpio, pin) in enumerate(self.reader.channels):
            y = 4 + i
            txt = (f"{label}  [{self.reader.tc_types[label]}]  GPIO{gpio} (pin {pin})  "
                   f"{TERM_POS.get(label, '')}")
            if i == self.pick_sel:
                _, w = self.scr.getmaxyx()
                self.put(y, 1, (" ▶ " + txt).ljust(max(0, w - 2)), self.cp(self.CSEL) | self.curses.A_BOLD)
            else:
                self.put(y, 4, txt, self.cp(self.CD))
        self.footer("↑↓ move   Enter select   q back")

    def s_single(self):
        label, gpio, pin = self.reader.channels[self.single_idx]
        self.header(f"{label}   —   GPIO{gpio} (pin {pin})   —   {TERM_POS.get(label, '')}",
                    f"{self.reader.tc_types[label]}-type · {self.hz:g} Hz poll")
        if self._fatal_banner():
            self.footer("←→ channel   q back")
            return
        r = self.readings.get(label, {})
        st = self.stats[label]
        ok = r and "unavailable" not in r and not r.get("no_data") and r.get("ok", False)
        if ok:
            txt = f"{self.conv(r['temp_c']):.1f}"
            draw_big(self.put, 4, 4, txt, self.cp(self.zc[zone(r['temp_c'])]) | self.curses.A_BOLD)
            self.put(5, 4 + len(txt) * 4 + 1, self.unit(), self.cp(self.CD) | self.curses.A_BOLD)
            _, w = self.scr.getmaxyx()
            barw = max(10, min(48, w - 8))
            frac = max(0.0, min(1.0, (r['temp_c'] - RANGE_MIN) / (RANGE_MAX - RANGE_MIN)))
            fill = int(frac * barw)
            self.put(10, 4, "[" + "█" * fill + "·" * (barw - fill) + "]",
                     self.cp(self.zc[zone(r['temp_c'])]))
        else:
            draw_big(self.put, 4, 4, "---", self.cp(self.CE) | self.curses.A_BOLD)
        stxt, sattr = self.status_attr(r) if r else ("…", self.cp(self.CD))
        self.put(12, 4, f"type          : {self.reader.tc_types[label]}   (press t to change)",
                 self.cp(self.CH))
        self.put(13, 4, f"cold junction : {self.conv(r['cj_c']):6.1f} {self.unit()}"
                 if ok else "cold junction :   ---", self.cp(self.CD))
        self.put(14, 4, "status        : ", self.cp(self.CD))
        self.put(14, 20, stxt, sattr)
        lo = "--" if st.tmin is None else f"{self.conv(st.tmin):.1f}"
        hi = "--" if st.tmax is None else f"{self.conv(st.tmax):.1f}"
        self.put(15, 4, f"min / max     : {lo} / {hi} {self.unit()}", self.cp(self.CD))
        faults = ", ".join(r.get("faults", [])) or "none"
        self.put(16, 4, f"faults        : {faults}",
                 self.cp(self.CE if r.get("faults") else self.CD))
        self.put(17, 4, f"raw / SR      : 0x{r.get('raw', 0):06X} / 0x{r.get('sr', 0):02X}"
                 f"   n={st.n} err={st.err}" if r else "",
                 self.cp(self.CD) | self.curses.A_DIM)
        self.footer("←→ channel   t change type   c/f units   r reset   +/- rate   a all   q back")

    def enter_probe(self):
        self.probe_rows = []
        for label, gpio, pin in self.reader.channels:
            responding = self.reader.verify(label, gpio)
            r = self.reader.read(label, gpio)
            self.probe_rows.append((label, gpio, pin, responding, r))
        self.state = "probe"

    def s_probe(self):
        self.header("QUICK PROBE   —   one-shot check of every channel")
        if self._fatal_banner():
            self.footer("q back   any other key re-probe")
            return
        self.put(3, 2, f"{'CH':<4}{'TY':<4}{'CS':<9}{'CHIP':<14}{'READING':>10}  STATUS",
                 self.cp(self.CD) | self.curses.A_DIM)
        for i, (label, gpio, pin, responding, r) in enumerate(self.probe_rows):
            y = 4 + i
            self.put(y, 2, f"{label:<4}", self.cp(self.CT) | self.curses.A_BOLD)
            self.put(y, 6, f"{self.reader.tc_types[label]:<4}", self.cp(self.CH) | self.curses.A_BOLD)
            self.put(y, 10, f"GPIO{gpio:<5}", self.cp(self.CD) | self.curses.A_DIM)
            chip = "responding" if responding else "NO RESPONSE"
            self.put(y, 19, f"{chip:<14}",
                     self.cp(self.CG if responding else self.CE) | self.curses.A_BOLD)
            stxt, sattr = self.status_attr(r)
            rd = f"{self.conv(r['temp_c']):8.1f}{'F' if self.fahrenheit else 'C'}" if r.get("ok") else "     ---"
            self.put(y, 33, f"{rd:>10}", self.cp(self.CD))
            self.put(y, 45, stxt, sattr)
        self.put(4 + len(self.probe_rows) + 1, 2,
                 "‘responding’ = the MAX31856 answered on SPI.  OPEN = no thermocouple wired.",
                 self.cp(self.CD) | self.curses.A_DIM)
        self.footer("r re-probe   1-4 focus   a all   q back")

    def _settings_rows(self):
        rows = []
        for label, _, _ in self.reader.channels:
            rows.append((f"{label} type", self.reader.tc_types[label], "◀▶ B E J K N R S T", "type", label))
        rows.append(("Units", "Fahrenheit" if self.fahrenheit else "Celsius", "◀▶ toggle", "units", None))
        rows.append(("Poll rate", f"{self.hz:g} Hz", "◀▶ 1-20 Hz", "poll", None))
        rows.append(("Mains filter", f"{self.reader.filter_hz} Hz", "◀▶ 50 / 60 Hz", "filter", None))
        return rows

    def s_settings(self):
        self.header("SETTINGS", "thermocouple type is per-channel; units/rate/filter are global")
        rows = self._settings_rows()
        for i, (name, val, hint, kind, key) in enumerate(rows):
            y = 3 + i
            sel = (i == self.set_sel)
            attr = self.cp(self.CSEL) | self.curses.A_BOLD if sel else self.cp(self.CD)
            self.put(y, 2, f"{'▶ ' if sel else '  '}{name:<16}", attr)
            valc = self.CH if kind == "type" else self.CT
            self.put(y, 22, f"[ {val:^10} ]", self.cp(valc) | self.curses.A_BOLD)
            if sel:
                self.put(y, 38, hint, self.cp(self.CD) | self.curses.A_DIM)
        self.put(3 + len(rows) + 1, 2,
                 "type / filter changes re-write the chip register(s) immediately.",
                 self.cp(self.CD) | self.curses.A_DIM)
        self.footer("↑↓ row   ◀▶ change value   q back")

    # -- input --
    def back(self):
        if self.state == "menu":
            self.running = False
        else:
            self.state = "menu"

    def _cycle_type(self, label, delta):
        cur = self.reader.tc_types[label]
        self.reader.set_type(label, TC_ORDER[(TC_ORDER.index(cur) + delta) % len(TC_ORDER)])

    def adjust_setting(self, delta):
        rows = self._settings_rows()
        name, val, hint, kind, key = rows[self.set_sel]
        if kind == "type":
            self._cycle_type(key, delta)
        elif kind == "units":
            self.fahrenheit = not self.fahrenheit
        elif kind == "poll":
            self.hz = min(20.0, max(1.0, self.hz + delta))
        elif kind == "filter":
            self.reader.set_filter(50 if self.reader.filter_hz == 60 else 60)

    def handle(self, k):
        c = self.curses
        if k == -1:
            return
        if k in (ord('q'), ord('Q'), 27):
            self.back(); return
        if k in (ord('a'), ord('A')):
            self.state = "all"; return
        if k in (ord('p'), ord('P')):
            self.enter_probe(); return
        if k in (ord('c'), ord('C')):
            self.fahrenheit = False; return
        if k in (ord('f'), ord('F')):
            self.fahrenheit = True; return
        if k in (ord('+'), ord('=')):
            self.hz = min(20.0, self.hz + 1); return
        if k in (ord('-'), ord('_')):
            self.hz = max(1.0, self.hz - 1); return
        if k in (ord('r'), ord('R')):
            if self.state == "probe":
                self.enter_probe()
            else:
                for s in self.stats.values():
                    s.reset()
            return
        if ord('1') <= k <= ord('4'):
            self.single_idx = k - ord('1'); self.state = "single"; return
        if k in (ord('s'), ord('S')) and self.state != "settings":
            self.state = "settings"; self.set_sel = 0; return

        if self.state == "menu":
            if k in (c.KEY_UP, ord('k')):
                self.menu_sel = max(0, self.menu_sel - 1)
            elif k in (c.KEY_DOWN, ord('j')):
                self.menu_sel = min(4, self.menu_sel + 1)
            elif k in (10, 13, c.KEY_ENTER):
                self._menu_activate()
        elif self.state == "pick":
            if k in (c.KEY_UP, ord('k')):
                self.pick_sel = max(0, self.pick_sel - 1)
            elif k in (c.KEY_DOWN, ord('j')):
                self.pick_sel = min(len(self.reader.channels) - 1, self.pick_sel + 1)
            elif k in (10, 13, c.KEY_ENTER):
                self.single_idx = self.pick_sel; self.state = "single"
        elif self.state == "single":
            if k in (c.KEY_LEFT, ord('['), ord('h')):
                self.single_idx = (self.single_idx - 1) % len(self.reader.channels)
            elif k in (c.KEY_RIGHT, ord(']'), ord('l')):
                self.single_idx = (self.single_idx + 1) % len(self.reader.channels)
            elif k in (ord('t'), ord('T')):
                self._cycle_type(self.reader.channels[self.single_idx][0], 1)
        elif self.state == "settings":
            n = len(self._settings_rows())
            if k in (c.KEY_UP, ord('k')):
                self.set_sel = max(0, self.set_sel - 1)
            elif k in (c.KEY_DOWN, ord('j')):
                self.set_sel = min(n - 1, self.set_sel + 1)
            elif k in (c.KEY_LEFT, ord('h')):
                self.adjust_setting(-1)
            elif k in (c.KEY_RIGHT, ord('l'), 10, 13, c.KEY_ENTER):
                self.adjust_setting(+1)

    def _menu_activate(self):
        target = ["all", "pick", "probe", "settings", "quit"][self.menu_sel]
        if target == "quit":
            self.running = False
        elif target == "probe":
            self.enter_probe()
        else:
            self.state = target
            if target == "settings":
                self.set_sel = 0

    def run(self):
        while self.running:
            self.scr.timeout(int(1000 / self.hz) if self.state in ("all", "single") else 120)
            self.render()
            self.handle(self.scr.getch())


def run_interactive(reader, args):
    import curses
    curses.wrapper(lambda scr: App(scr, reader, args).run())


# ── Decoder self-test (no hardware) ────────────────────────────────────────
def run_selftest():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = abs(got - want) < 1e-6 if isinstance(want, float) else got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {name}: got {got!r} want {want!r}")

    r = decode_max31856([0x19, 0x00, 0x01, 0x90, 0x00, 0x00])
    check("TC +25.00 C", round(r["temp_c"], 2), 25.0)
    check("CJ +25.00 C", round(r["cj_c"], 2), 25.0)
    check("ok (sr=0)", r["ok"], True)
    check("no faults", r["faults"], [])
    r = decode_max31856([0x00, 0x00, 0xFE, 0x70, 0x00, 0x00])
    check("TC -25.00 C", round(r["temp_c"], 2), -25.0)
    r = decode_max31856([0x19, 0x00, 0x00, 0x00, 0x00, 0x01])
    check("open oc", r["oc"], True)
    check("open not-ok", r["ok"], False)
    check("open status", status_text(r), "OPEN")
    r = decode_max31856([0xF6, 0x00, 0x00, 0x00, 0x00, 0x00])
    check("CJ -10.00 C", round(r["cj_c"], 2), -10.0)
    r = decode_max31856([0xFF] * 6)
    check("all-FF no_data", r["no_data"], True)

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


# ── main ───────────────────────────────────────────────────────────────────
def parse_types(spec, channels):
    """'K' -> all K;  'K,T,K,K' -> per channel. Returns {label: TYPE}."""
    parts = [p.strip().upper() for p in str(spec).split(",")]
    if len(parts) == 1:
        parts = parts * len(channels)
    if len(parts) != len(channels):
        raise ValueError(f"give 1 type or {len(channels)} comma-separated, got {len(parts)}")
    for p in parts:
        if p not in TC_TYPES:
            raise ValueError(f"unknown type {p!r}; choose from {' '.join(TC_ORDER)}")
    return {ch[0]: p for ch, p in zip(channels, parts)}


def parse_args(argv):
    p = argparse.ArgumentParser(description="Interactive 4-channel MAX31856 thermocouple HAT tester")
    p.add_argument("--tc-type", default=DEFAULT_TC_TYPE,
                   help="thermocouple type: one value for all channels (e.g. K), or "
                        "comma-separated per channel TC1..TC4 (e.g. K,T,K,K). Types: B E J K N R S T")
    p.add_argument("--filter", type=int, choices=(50, 60), default=DEFAULT_FILTER,
                   dest="filter_hz", help="mains-rejection filter Hz (default: 60)")
    p.add_argument("--speed", type=int, default=DEFAULT_SPEED_HZ, dest="speed_hz",
                   help=f"SPI clock Hz (default: {DEFAULT_SPEED_HZ})")
    p.add_argument("--hz", type=float, default=DEFAULT_HZ, help=f"poll rate (default: {DEFAULT_HZ})")
    p.add_argument("--gpiochip", type=int, default=None, help="lgpio gpiochip number (default: auto 0/4)")
    p.add_argument("-f", "--fahrenheit", action="store_true", help="show °F")
    p.add_argument("--plain", action="store_true", help="print lines instead of the interactive UI")
    p.add_argument("--once", action="store_true", help="single read of all channels then exit")
    p.add_argument("--sim", action="store_true", help="fake data, no hardware needed")
    p.add_argument("--selftest", action="store_true", help="verify the decoders and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.selftest:
        return run_selftest()

    try:
        tc_types = parse_types(args.tc_type, CHANNELS)
    except ValueError as e:
        sys.stderr.write(f"--tc-type: {e}\n")
        return 2

    reader = Reader(CHANNELS, tc_types, args.filter_hz, args.speed_hz,
                    sim=args.sim, gpiochip=args.gpiochip)

    if not args.sim and reader.fatal:
        sys.stderr.write("HARDWARE UNAVAILABLE:\n  " + reader.fatal.replace("\n", "\n  ") + "\n")
        sys.stderr.write("(Use --sim to preview the UI, or --selftest to check the decoders.)\n")
        if args.plain or args.once:
            reader.close()
            return 1

    if (args.plain or args.once) and not args.sim and not reader.fatal:
        print("Probing channels (CR1 read-back):")
        for label, gpio, pin in CHANNELS:
            print(f"  {label} {reader.tc_types[label]}-type GPIO{gpio} (pin {pin}): "
                  f"{'responding' if reader.verify(label, gpio) else 'NO RESPONSE'}")
        print()

    try:
        if args.once or args.plain:
            run_plain(reader, args)
        else:
            run_interactive(reader, args)
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
