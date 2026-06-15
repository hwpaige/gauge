#!/usr/bin/env python3
"""BPI M4 Zero interactive pin tester  —  arrow-key navigation
Run:  cd /root/gauge && source venv/bin/activate && python3 pin_test.py
"""
import curses, threading, time
import gpiod, spidev
from gpiod.line import Direction, Value as V

def _find_chip():
    for n in range(8):
        path = f'/dev/gpiochip{n}'
        try:
            with gpiod.Chip(path) as c:
                if '300b000' in c.get_info().label:
                    return path
        except OSError:
            break
    return '/dev/gpiochip1'

CHIP = _find_chip()

# label    phys   name              offset  notes                      wired?
PINS = [
    # SPI1 bus — these are kernel-owned after the new overlay loads on next boot
    ("SCLK",   23, "PH6/SPI1_CLK",  230, "SPI1 CLK  — kernel-owned", True ),
    ("MOSI",   19, "PH7/SPI1_MOSI", 231, "SPI1 MOSI — kernel-owned", True ),
    ("MISO",   21, "PH8/SPI1_MISO", 232, "SPI1 MISO — kernel-owned", True ),
    ("CS",     24, "PH5/SPI1_CS0",  229, "SPI1 CS0  — kernel-owned", True ),
    # Unknown — probe each with Hold HIGH and check pins 18 and 22 on header
    ("DC?",   "?", "PH2",           226, "PH2 — probe: is it pin 18?", False),
    ("DC?",   "?", "PH3",           227, "PH3 — probe: is it pin 18?", False),
    ("DC?",   "?", "PH4",           228, "PH4 — probe: is it pin 18?", False),
    ("?",     "?", "PI5",           261, "PI5 — probe: pin 18 or 22?", False),
    ("?",     "?", "PI6",           262, "PI6 — probe: pin 18 or 22?", False),
    ("?",     "?", "PI7",           263, "PI7 — probe: pin 18 or 22?", False),
    ("?",     "?", "PI8",           264, "PI8 — probe: pin 18 or 22?", False),
    ("?",     "?", "PI9",           265, "PI9 — probe: pin 18 or 22?", False),
    ("?",     "?", "PI10",          266, "PI10— probe: pin 18 or 22?", False),
    ("?",     "?", "PI11",          267, "PI11— probe: pin 18 or 22?", False),
    ("?",     "?", "PI12",          268, "PI12— probe: pin 18 or 22?", False),
    ("?",     "?", "PI13",          269, "PI13— probe: pin 18 or 22?", False),
    ("?",     "?", "PI14",          270, "PI14— probe: pin 18 or 22?", False),
    ("?",     "?", "PI15",          271, "PI15— probe: pin 18 or 22?", False),
    ("?",     "?", "PI16",          272, "PI16— probe: pin 18 or 22?", False),
]

FREQS   = [("1 Hz   — easy on a multimeter", 1),
           ("4 Hz",                           4),
           ("10 Hz",                          10)]

SPEEDS  = [("100 kHz  — reads ~1.65 V avg on multimeter", 100_000),
           ("1 MHz",                                        1_000_000),
           ("8 MHz",                                        8_000_000)]

ACTIONS = ["GPIO blink",
           "SPI bus signal",
           "Hold HIGH  (3.3 V)",
           "Hold LOW   (0 V)"]

# Offsets claimed by the SPI1 kernel driver after new overlay loads
KERNEL_OWNED = {229, 230, 231, 232}  # PH5=CS PH6=CLK PH7=MOSI PH8=MISO

# ── background workers ────────────────────────────────────────────────────

_stop  = threading.Event()
_error = ""

def _gpiod_errmsg(e, offset):
    if getattr(e, 'args', None) and e.args[0] == 22:
        if offset in KERNEL_OWNED:
            return f"offset {offset} is kernel-owned by SPI0 driver — GPIO/Hold unavailable, use SPI test"
        return f"EINVAL — pin may be claimed by another driver"
    return str(e)

def _blink(offset, hz):
    global _error
    half = 1.0 / hz / 2
    try:
        g = gpiod.request_lines(CHIP, consumer="pintest", config={
            offset: gpiod.LineSettings(direction=Direction.OUTPUT,
                                       output_value=V.INACTIVE)})
        while not _stop.is_set():
            g.set_value(offset, V.ACTIVE);   _stop.wait(half)
            g.set_value(offset, V.INACTIVE); _stop.wait(half)
        g.release()
    except Exception as e:
        _error = _gpiod_errmsg(e, offset)

def _hold(offset, high):
    global _error
    try:
        g = gpiod.request_lines(CHIP, consumer="pintest", config={
            offset: gpiod.LineSettings(direction=Direction.OUTPUT,
                output_value=V.ACTIVE if high else V.INACTIVE)})
        _stop.wait()
        g.release()
    except Exception as e:
        _error = _gpiod_errmsg(e, offset)

def _spi(hz):
    global _error
    try:
        sp = spidev.SpiDev(); sp.open(1, 0)
        sp.max_speed_hz = hz; sp.mode = 0
        payload = bytes([0xAA, 0x55, 0xFF, 0x00] * 16)
        while not _stop.is_set():
            sp.writebytes2(payload); time.sleep(0.005)
        sp.close()
    except Exception as e:
        _error = str(e)

def launch(fn, *args):
    global _error; _error = ""
    _stop.clear()
    t = threading.Thread(target=fn, args=args, daemon=True); t.start()
    return t

def stopit(t):
    _stop.set()
    if t: t.join(timeout=2)

# ── curses UI ─────────────────────────────────────────────────────────────

class App:
    C_TITLE = 1;  C_SEL = 2;  C_GREEN = 3;  C_HINT = 4;  C_ERR = 5

    def __init__(self, scr):
        self.scr = scr
        curses.curs_set(0)
        curses.start_color(); curses.use_default_colors()
        curses.init_pair(self.C_TITLE, curses.COLOR_CYAN,   -1)
        curses.init_pair(self.C_SEL,   curses.COLOR_BLACK,  curses.COLOR_WHITE)
        curses.init_pair(self.C_GREEN, curses.COLOR_GREEN,  -1)
        curses.init_pair(self.C_HINT,  curses.COLOR_YELLOW, -1)
        curses.init_pair(self.C_ERR,   curses.COLOR_RED,    -1)

        self.state  = "main"
        self.sel    = dict(main=0, action=0, freq=0, speed=0)
        self.worker = None
        self.runlbl = ""

    # ── drawing primitives ────────────────────────────────────────

    def hw(self):
        return self.scr.getmaxyx()

    def put(self, y, x, s, attr=0):
        try:
            h, w = self.hw()
            if 0 <= y < h and 0 <= x < w:
                self.scr.addstr(y, x, s[:w - x], attr)
        except curses.error:
            pass

    def row(self, y, attr):
        try:
            h, w = self.hw()
            if 0 <= y < h:
                self.scr.addstr(y, 0, " " * (w - 1), attr)
        except curses.error:
            pass

    def hline(self, y):
        try:
            self.scr.hline(y, 0, curses.ACS_HLINE, self.hw()[1])
        except curses.error:
            pass

    def header(self, title, sub=""):
        self.scr.erase()
        bold_cyan = curses.color_pair(self.C_TITLE) | curses.A_BOLD
        self.row(0, bold_cyan)
        self.put(0, 2, title, bold_cyan)
        if sub:
            self.put(1, 2, sub)
        self.hline(2)

    def footer(self, hint):
        h = self.hw()[0]
        self.row(h - 1, curses.color_pair(self.C_HINT))
        self.put(h - 1, 2, hint, curses.color_pair(self.C_HINT))

    def item(self, y, idx, sel, text, normal_attr=0):
        if idx == sel:
            sel_attr = curses.color_pair(self.C_SEL) | curses.A_BOLD
            self.row(y, sel_attr)
            self.put(y, 2, "▶ " + text, sel_attr)
        else:
            self.put(y, 4, text, normal_attr)

    # ── screens ───────────────────────────────────────────────────

    def screen_main(self):
        self.header("BPI M4 Zero Pin Tester",
                    "COM/GND → pin 6    probe → any header pin")
        self.put(4, 2, f"  {'Signal':<7}  {'Pin':>4}  {'RPi name':<22} BPI",
                 curses.A_DIM)
        self.hline(5)
        for i, (sig, phys, rpi, off, notes, wired) in enumerate(PINS):
            tag  = " ◀" if wired else ""
            attr = curses.color_pair(self.C_GREEN) if wired else curses.A_DIM
            text = f"{sig:<7}   {str(phys):>4}   {rpi:<22} {notes}{tag}"
            self.item(6 + i, i, self.sel["main"], text, attr)
        self.footer("↑ ↓  move    Enter  select    Q  quit")
        self.scr.refresh()

    def screen_action(self):
        sig, phys, rpi, off, notes, _ = PINS[self.sel["main"]]
        self.header(f"  {sig}  —  pin {phys}  —  {rpi}",
                    f"  BPI offset {off}  ({notes})")
        if off in KERNEL_OWNED:
            self.put(3, 2,
                     "  ⚠  kernel-owned by SPI0 — GPIO blink & Hold will fail; use SPI test",
                     curses.color_pair(self.C_ERR))
        for i, act in enumerate(ACTIONS):
            self.item(5 + i, i, self.sel["action"], act)
        self.footer("↑ ↓  move    Enter  select    Esc  back")
        self.scr.refresh()

    def screen_freq(self):
        sig, phys, _, off, _, _ = PINS[self.sel["main"]]
        self.header(f"  GPIO blink — {sig}",
                    f"  offset {off}  →  pin {phys}")
        for i, (label, _) in enumerate(FREQS):
            self.item(4 + i, i, self.sel["freq"], label)
        self.footer("↑ ↓  move    Enter  select    Esc  back")
        self.scr.refresh()

    def screen_speed(self):
        self.header("  SPI bus signal",
                    "  PC0 = CLK  (pin 23)    PC2 = MOSI  (pin 19)    PH5 = HW-CS  (pin 24)")
        for i, (label, _) in enumerate(SPEEDS):
            self.item(4 + i, i, self.sel["speed"], label)
        self.footer("↑ ↓  move    Enter  select    Esc  back")
        self.scr.refresh()

    def screen_running(self):
        self.header("  ◉  Running", f"  {self.runlbl}")
        self.put(4, 4, "Signal active — press any key to stop",
                 curses.color_pair(self.C_GREEN) | curses.A_BOLD)
        if _error:
            self.put(6, 4, f"Error: {_error}",
                     curses.color_pair(self.C_ERR) | curses.A_BOLD)
        self.footer("Any key  →  stop")
        self.scr.refresh()

    # ── main loop ─────────────────────────────────────────────────

    def run(self):
        self.scr.timeout(200)
        screens = dict(main=self.screen_main, action=self.screen_action,
                       freq=self.screen_freq, speed=self.screen_speed,
                       running=self.screen_running)
        sizes   = dict(main=len(PINS), action=len(ACTIONS),
                       freq=len(FREQS), speed=len(SPEEDS))

        while True:
            screens[self.state]()
            k = self.scr.getch()

            # ── running: any key stops ────────────────────────────
            if self.state == "running":
                if k != -1:
                    stopit(self.worker); self.worker = None
                    self.state = "action"
                continue

            # ── navigation ────────────────────────────────────────
            if k in (curses.KEY_UP, ord('k')):
                self.sel[self.state] = max(0, self.sel[self.state] - 1)

            elif k in (curses.KEY_DOWN, ord('j')):
                n = sizes.get(self.state, 1)
                self.sel[self.state] = min(n - 1, self.sel[self.state] + 1)

            elif k in (10, 13, curses.KEY_ENTER):
                self._enter()

            elif k in (27, ord('q'), ord('Q'), curses.KEY_BACKSPACE):
                backs = dict(action="main", freq="action", speed="action")
                if self.state == "main":
                    return  # quit
                self.state = backs.get(self.state, "main")

    def _enter(self):
        s = self.state

        if s == "main":
            self.sel["action"] = 0
            self.state = "action"

        elif s == "action":
            a   = self.sel["action"]
            off = PINS[self.sel["main"]][3]
            sig = PINS[self.sel["main"]][0]
            if a == 0:
                self.sel["freq"] = 0; self.state = "freq"
            elif a == 1:
                self.sel["speed"] = 0; self.state = "speed"
            else:
                high = (a == 2)
                self.runlbl = f"Hold {'HIGH' if high else 'LOW'}  —  {sig}  (offset {off})"
                self.worker = launch(_hold, off, high)
                self.state  = "running"

        elif s == "freq":
            off   = PINS[self.sel["main"]][3]
            sig   = PINS[self.sel["main"]][0]
            label, hz = FREQS[self.sel["freq"]]
            self.runlbl = f"Blink {label}  —  {sig}  (offset {off})"
            self.worker = launch(_blink, off, hz)
            self.state  = "running"

        elif s == "speed":
            label, hz = SPEEDS[self.sel["speed"]]
            self.runlbl = f"SPI {label}  —  PC0/PC2/PH5"
            self.worker = launch(_spi, hz)
            self.state  = "running"


def main(scr):
    App(scr).run()

if __name__ == "__main__":
    curses.wrapper(main)
