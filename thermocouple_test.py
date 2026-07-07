#!/usr/bin/env python3
"""Thermocouple HAT test tool  —  Raspberry Pi 4B + Raspberry Pi OS Lite

Live diagnostic for the 4-channel MAX31856 thermocouple HAT (the ordered board
in thermocouple_hat/). Configures each MAX31856 for K-type continuous
conversion, reads temperature + cold-junction + fault status, and shows all four
channels in a curses table (or a plain line printer for logging over SSH).

    cd /root/gauge && source venv/bin/activate
    python3 thermocouple_test.py                # live 4-channel table
    python3 thermocouple_test.py --plain        # one line per poll (SSH/log friendly)
    python3 thermocouple_test.py --once         # single read of all channels, exit
    python3 thermocouple_test.py --tc-type J    # different thermocouple type
    python3 thermocouple_test.py --sim          # fake data, no hardware (preview on a laptop)
    python3 thermocouple_test.py --selftest     # verify the register decoders, no hardware

────────────────────────────────────────────────────────────────────────────
HARDWARE  (from the ordered PCB netlist, thermocouple_hat/):

  4× MAX31856 share SPI0:  SCLK=GPIO11(pin23)  MOSI=GPIO10(pin19)  MISO=GPIO9(pin21)
  Per-channel chip-select (each its own GPIO — software CS):
        TC1 (U2, term J3) -> GPIO7  (pin 26)
        TC2 (U3, term J5) -> GPIO5  (pin 29)
        TC3 (U4, term J6) -> GPIO6  (pin 31)
        TC4 (U5, term J4) -> GPIO12 (pin 32)

  Only GPIO7 is a native SPI0 chip-select; the other three are plain GPIOs, so
  all four are driven as SOFTWARE chip-selects: we clock with hardware SPI0
  (spidev, no hardware CS) and toggle each CS GPIO with lgpio.

ONE-TIME PI SETUP (Raspberry Pi OS Lite / Bookworm):

  1. Enable SPI and free GPIO7 from the default 2-CS SPI (so we can drive it):
         sudo raspi-config  ->  Interface Options  ->  SPI  ->  Enable
     then in /boot/firmware/config.txt make sure you have:
         dtparam=spi=on
         dtoverlay=spi0-1cs          # SPI0 keeps only CE0 (GPIO8, the display); frees GPIO7
     and reboot.  (spidev0.0 must exist:  ls /dev/spidev0.0)

  2. Install the GPIO library used for software chip-select:
         sudo apt install -y python3-lgpio     # or:  pip install lgpio

The decode_max31856() / MAX31856 helpers are self-contained and drop straight
into gauge.py once the channels read correctly.
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

SPI_BUS, SPI_DEV = 0, 0          # /dev/spidev0.0 for clocking (CS handled in software)
SPI_MODE = 1                     # MAX31856 = SPI mode 1 (CPOL=0, CPHA=1)
DEFAULT_SPEED_HZ = 2_000_000     # MAX31856 max 5 MHz; 2 MHz is a safe default
DEFAULT_HZ = 4.0                 # poll rate; MAX31856 converts ~every 100 ms
DEFAULT_TC_TYPE = "K"
DEFAULT_FILTER = 60              # mains-rejection filter: 60 (US) or 50 (EU)

# Temperature range for the green/amber/red zone colouring (matches gauge.py).
RANGE_MIN, RANGE_MAX = 0, 300
CAUTION_FRAC, DANGER_FRAC = 0.60, 0.85

# ── MAX31856 registers ─────────────────────────────────────────────────────
REG_CR0, REG_CR1, REG_MASK = 0x00, 0x01, 0x02
REG_CJTH, REG_LTCBH, REG_SR = 0x0A, 0x0C, 0x0F
WRITE = 0x80                     # OR into address byte for a register write

TC_TYPES = {"B": 0x0, "E": 0x1, "J": 0x2, "K": 0x3,
            "N": 0x4, "R": 0x5, "S": 0x6, "T": 0x7}

# CR0 configuration bits
CR0_CMODE      = 0x80            # 1 = automatic continuous conversion
CR0_OCFAULT_01 = 0x10           # enable open-circuit fault detection (normal probes)
CR0_FILTER_50  = 0x01           # 0 = 60 Hz rejection, 1 = 50 Hz

# Fault Status Register (SR) bit meanings
SR_BITS = [
    (0x01, "OPEN"),      # thermocouple open circuit
    (0x02, "OV/UV"),     # over/under voltage on an input
    (0x04, "TC LOW"),    # TC temp below low threshold
    (0x08, "TC HIGH"),   # TC temp above high threshold
    (0x10, "CJ LOW"),    # cold junction below low threshold
    (0x20, "CJ HIGH"),   # cold junction above high threshold
    (0x40, "TC RANGE"),  # TC out of operating range
    (0x80, "CJ RANGE"),  # cold junction out of range
]


# ── Pure decoders (testable without hardware) ──────────────────────────────
def decode_temperature(b2, b1, b0):
    """LTCBH/LTCBM/LTCBL -> °C. 19-bit signed, value left-justified in 24 bits,
    resolution 2^-7 °C.  temp = signed24 / 4096."""
    raw = (b2 << 16) | (b1 << 8) | b0
    if raw & 0x800000:
        raw -= 0x1000000
    return raw / 4096.0


def decode_coldjunction(hi, lo):
    """CJTH/CJTL -> °C. 14-bit signed, value left-justified in 16 bits,
    resolution 2^-6 °C.  cj = signed16 / 256."""
    raw = (hi << 8) | lo
    if raw & 0x8000:
        raw -= 0x10000
    return raw / 256.0


def decode_max31856(regs):
    """Decode the 6-byte burst read starting at CJTH (0x0A):
       [CJTH, CJTL, LTCBH, LTCBM, LTCBL, SR]."""
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
    """0=normal 1=caution 2=danger over RANGE_MIN..RANGE_MAX (matches gauge.py)."""
    if RANGE_MAX <= RANGE_MIN:
        return 0
    pct = (temp_c - RANGE_MIN) / (RANGE_MAX - RANGE_MIN)
    return 0 if pct < CAUTION_FRAC else (1 if pct < DANGER_FRAC else 2)


# ── Software chip-select via lgpio ─────────────────────────────────────────
class ChipSelect:
    """Drives the four CS GPIOs, idle HIGH, asserted LOW around a transfer."""

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
                lgpio.gpio_claim_output(self.h, p, 1)   # start idle-high
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
    def __init__(self, channels, tc_type, filter_hz, speed_hz, sim=False, gpiochip=None):
        self.channels = channels
        self.tc_type = tc_type
        self.filter_hz = filter_hz
        self.speed_hz = speed_hz
        self.sim = sim
        self.spi = None
        self.cs = None
        self.fatal = None            # non-None => hardware unusable, message for the UI
        self._t0 = time.time()
        if sim:
            return
        self._open(gpiochip)

    def _open(self, gpiochip):
        try:
            import spidev
        except ImportError as e:
            self.fatal = f"spidev not installed ({e}) — pip install spidev"
            return
        try:
            self.spi = spidev.SpiDev()
            self.spi.open(SPI_BUS, SPI_DEV)
            self.spi.max_speed_hz = self.speed_hz
            self.spi.mode = SPI_MODE
            try:
                self.spi.no_cs = True    # we drive CS in software; don't toggle CE0/GPIO8
            except Exception:
                pass                     # harmless if unsupported (display isn't active)
        except Exception as e:            # noqa: BLE001
            self.fatal = (f"cannot open /dev/spidev{SPI_BUS}.{SPI_DEV}: {e}\n"
                          "  enable SPI: sudo raspi-config -> Interface Options -> SPI")
            return
        try:
            self.cs = ChipSelect([g for _, g, _ in self.channels], gpiochip)
        except Exception as e:            # noqa: BLE001
            self.fatal = str(e)
            return
        for label, gpio, _ in self.channels:
            self._configure(gpio)
        time.sleep(0.25)                 # let the first continuous conversion complete

    # -- low level SPI with software CS --
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

    def _configure(self, gpio):
        # CR1: averaging=1 sample, thermocouple type in low nibble
        self._write_reg(gpio, REG_CR1, TC_TYPES[self.tc_type])
        # CR0: continuous conversion + open-circuit detection + mains filter
        cr0 = CR0_CMODE | CR0_OCFAULT_01 | (CR0_FILTER_50 if self.filter_hz == 50 else 0)
        self._write_reg(gpio, REG_CR0, cr0)

    def read(self, label, gpio):
        if self.sim:
            return self._sim(label)
        try:
            regs = self._read_regs(gpio, REG_CJTH, 6)
            return decode_max31856(regs)
        except Exception as e:            # noqa: BLE001
            return {"unavailable": f"read error: {e}"}

    def verify(self, gpio):
        """Read CR1 back to confirm a chip is actually responding on this CS."""
        if self.sim:
            return True
        try:
            got = self._read_regs(gpio, REG_CR1, 1)[0]
            return got == TC_TYPES[self.tc_type]
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
                if "unavailable" in r:
                    parts.append(f"{label} {r['unavailable']}")
                else:
                    seg = f"{label} {fmt_temp(r['temp_c'], args.fahrenheit)} {status_text(r)}"
                    parts.append(seg)
            print(f"[{time.strftime('%H:%M:%S')}]  " + "   ".join(parts), flush=True)
            if args.once:
                return
            time.sleep(period)
    except KeyboardInterrupt:
        pass


# ── Curses TUI (row per channel) ───────────────────────────────────────────
def run_curses(reader, args):
    import curses

    def _main(scr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        C_TITLE, C_GREEN, C_HINT, C_ERR, C_AMBER, C_DIM = 1, 2, 3, 4, 5, 6
        curses.init_pair(C_TITLE, curses.COLOR_CYAN, -1)
        curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(C_HINT,  curses.COLOR_YELLOW, -1)
        curses.init_pair(C_ERR,   curses.COLOR_RED, -1)
        curses.init_pair(C_AMBER, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_DIM,   curses.COLOR_WHITE, -1)
        zc = {0: C_GREEN, 1: C_AMBER, 2: C_ERR}

        scr.timeout(int(1000 / args.hz))
        fahrenheit = args.fahrenheit
        stats = {label: Stat() for label, _, _ in reader.channels}

        def put(y, x, s, attr=0):
            h, w = scr.getmaxyx()
            if 0 <= y < h and 0 <= x < w:
                try:
                    scr.addstr(y, x, s[:max(0, w - x - 1)], attr)
                except curses.error:
                    pass

        def hline(y):
            h, w = scr.getmaxyx()
            if 0 <= y < h:
                try:
                    scr.hline(y, 0, curses.ACS_HLINE, w)
                except curses.error:
                    pass

        while True:
            scr.erase()
            h, w = scr.getmaxyx()
            unit = "°F" if fahrenheit else "°C"
            title = (f" THERMOCOUPLE TEST   MAX31856 ×{len(reader.channels)}   "
                     f"{args.tc_type}-type   SPI{SPI_BUS} @ {reader.speed_hz/1e6:.1f} MHz  "
                     f"sw-CS   {args.hz:g} Hz   {unit}")
            put(0, 0, title.ljust(max(0, w - 1)), curses.color_pair(C_TITLE) | curses.A_BOLD)
            if args.sim:
                put(0, max(0, w - 5), "SIM", curses.color_pair(C_HINT) | curses.A_BOLD)
            hline(1)

            if reader.fatal:
                put(3, 2, "HARDWARE UNAVAILABLE", curses.color_pair(C_ERR) | curses.A_BOLD)
                for i, line in enumerate(reader.fatal.split("\n")):
                    put(5 + i, 2, line, curses.color_pair(C_ERR))
                put(5 + len(reader.fatal.split("\n")) + 1, 2,
                    "Run with --sim to preview, or --selftest to check the decoders.",
                    curses.color_pair(C_DIM) | curses.A_DIM)
            else:
                # column header
                put(2, 2, f"{'CH':<4}{'CS':<9}{'TEMP':>9}  {'COLD-J':>8}  "
                          f"{'STATUS':<9}{'MIN':>8}{'MAX':>8}   RAW",
                    curses.color_pair(C_DIM) | curses.A_DIM)
                for i, (label, gpio, pin) in enumerate(reader.channels):
                    r = reader.read(label, gpio)
                    stats[label].update(r)
                    st = stats[label]
                    y = 3 + i
                    put(y, 2, f"{label:<4}", curses.color_pair(C_TITLE) | curses.A_BOLD)
                    put(y, 6, f"GPIO{gpio:<5}", curses.color_pair(C_DIM) | curses.A_DIM)
                    stxt = status_text(r)
                    if "unavailable" in r or r.get("no_data") or not r.get("ok", False):
                        put(y, 15, f"{'---':>9}", curses.color_pair(C_ERR))
                        put(y, 26, f"{'---':>8}", curses.color_pair(C_DIM) | curses.A_DIM)
                        put(y, 36, f"{stxt:<9}", curses.color_pair(C_ERR) | curses.A_BOLD)
                    else:
                        temp = c_to_f(r["temp_c"]) if fahrenheit else r["temp_c"]
                        cj = c_to_f(r["cj_c"]) if fahrenheit else r["cj_c"]
                        put(y, 15, f"{temp:8.1f}{'F' if fahrenheit else 'C'}",
                            curses.color_pair(zc[zone(r['temp_c'])]) | curses.A_BOLD)
                        put(y, 26, f"{cj:7.1f}{'F' if fahrenheit else 'C'}",
                            curses.color_pair(C_DIM))
                        put(y, 36, f"{stxt:<9}", curses.color_pair(C_GREEN) | curses.A_BOLD)
                    lo = "  --" if st.tmin is None else f"{(c_to_f(st.tmin) if fahrenheit else st.tmin):.0f}"
                    hi = "  --" if st.tmax is None else f"{(c_to_f(st.tmax) if fahrenheit else st.tmax):.0f}"
                    put(y, 45, f"{lo:>7}{hi:>8}", curses.color_pair(C_DIM) | curses.A_DIM)
                    if "raw" in r and "unavailable" not in r:
                        put(y, 61, f"0x{r['raw']:06X} sr{r.get('sr', 0):02X} n{st.n}/e{st.err}",
                            curses.color_pair(C_DIM) | curses.A_DIM)

            hint = " c/f units    r reset min/max    +/- rate    q quit "
            put(h - 1, 0, hint.ljust(max(0, w - 1)), curses.color_pair(C_HINT))
            scr.refresh()

            k = scr.getch()
            if k in (ord("q"), ord("Q"), 27):
                return
            elif k in (ord("c"), ord("C")):
                fahrenheit = False
            elif k in (ord("f"), ord("F")):
                fahrenheit = True
            elif k in (ord("r"), ord("R")):
                for s in stats.values():
                    s.reset()
            elif k in (ord("+"), ord("=")):
                args.hz = min(20.0, args.hz + 1)
                scr.timeout(int(1000 / args.hz))
            elif k in (ord("-"), ord("_")):
                args.hz = max(1.0, args.hz - 1)
                scr.timeout(int(1000 / args.hz))

    curses.wrapper(_main)


# ── Decoder self-test (no hardware) ────────────────────────────────────────
def run_selftest():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = abs(got - want) < 1e-6 if isinstance(want, float) else got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {name}: got {got!r} want {want!r}")

    # CJ 25.0 C -> 1600 counts << 2 = 0x1900 ; TC 25.0 C -> 3200 counts << 5 = 0x019000
    r = decode_max31856([0x19, 0x00, 0x01, 0x90, 0x00, 0x00])
    check("TC +25.00 C", round(r["temp_c"], 2), 25.0)
    check("CJ +25.00 C", round(r["cj_c"], 2), 25.0)
    check("ok (sr=0)", r["ok"], True)
    check("no faults", r["faults"], [])
    # TC -25.0 C -> -3200 << 5 = 0xFE7000
    r = decode_max31856([0x00, 0x00, 0xFE, 0x70, 0x00, 0x00])
    check("TC -25.00 C", round(r["temp_c"], 2), -25.0)
    # open-circuit fault (SR bit0)
    r = decode_max31856([0x19, 0x00, 0x00, 0x00, 0x00, 0x01])
    check("open oc", r["oc"], True)
    check("open not-ok", r["ok"], False)
    check("open status", status_text(r), "OPEN")
    # cold junction -10.0 C -> -2560 << 2 = 0xF600
    r = decode_max31856([0xF6, 0x00, 0x00, 0x00, 0x00, 0x00])
    check("CJ -10.00 C", round(r["cj_c"], 2), -10.0)
    # all 0xFF -> no data
    r = decode_max31856([0xFF] * 6)
    check("all-FF no_data", r["no_data"], True)

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


# ── main ───────────────────────────────────────────────────────────────────
def parse_args(argv):
    p = argparse.ArgumentParser(description="4-channel MAX31856 thermocouple HAT tester (Pi 4B)")
    p.add_argument("--tc-type", choices=list(TC_TYPES), default=DEFAULT_TC_TYPE,
                   help=f"thermocouple type (default: {DEFAULT_TC_TYPE})")
    p.add_argument("--filter", type=int, choices=(50, 60), default=DEFAULT_FILTER,
                   dest="filter_hz", help="mains-rejection filter Hz (default: 60)")
    p.add_argument("--speed", type=int, default=DEFAULT_SPEED_HZ, dest="speed_hz",
                   help=f"SPI clock Hz (default: {DEFAULT_SPEED_HZ})")
    p.add_argument("--hz", type=float, default=DEFAULT_HZ, help=f"poll rate (default: {DEFAULT_HZ})")
    p.add_argument("--gpiochip", type=int, default=None, help="lgpio gpiochip number (default: auto 0/4)")
    p.add_argument("-f", "--fahrenheit", action="store_true", help="show °F")
    p.add_argument("--plain", action="store_true", help="print lines instead of the TUI")
    p.add_argument("--once", action="store_true", help="single read of all channels then exit")
    p.add_argument("--sim", action="store_true", help="fake data, no hardware needed")
    p.add_argument("--selftest", action="store_true", help="verify the decoders and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.selftest:
        return run_selftest()

    reader = Reader(CHANNELS, args.tc_type, args.filter_hz, args.speed_hz,
                    sim=args.sim, gpiochip=args.gpiochip)

    if not args.sim and reader.fatal:
        sys.stderr.write("HARDWARE UNAVAILABLE:\n  " + reader.fatal.replace("\n", "\n  ") + "\n")
        sys.stderr.write("(Use --sim to preview the UI, or --selftest to check the decoders.)\n")
        if args.plain or args.once:
            reader.close()
            return 1

    # In plain/once mode, print a quick per-channel "is a chip responding?" probe.
    if (args.plain or args.once) and not args.sim and not reader.fatal:
        print("Probing channels (CR1 read-back):")
        for label, gpio, pin in CHANNELS:
            print(f"  {label} GPIO{gpio} (pin {pin}): "
                  f"{'responding' if reader.verify(gpio) else 'NO RESPONSE'}")
        print()

    try:
        if args.once or args.plain:
            run_plain(reader, args)
        else:
            run_curses(reader, args)
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
