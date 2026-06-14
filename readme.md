# CHT Gauge

A twin cylinder head temperature (CHT) gauge running on a Banana Pi M4 Zero, displayed on a 1.3" round SPI LCD. Designed to look like a dark-themed aircraft avionics instrument — two filled-arc gauges side by side, one per cylinder, with colour-coded temperature zones and a D-DIN digital readout.

---

## Hardware

| Component | Part |
|---|---|
| SBC | Banana Pi M4 Zero (Allwinner H618, quad-core A53) |
| Display | Pimoroni 1.3" Round SPI Colour LCD (ST7789, 240×240) — PIM570 |
| Thermocouple | MAX31855 or MAX6675 breakout (×2, one per cylinder) |
| OS | Armbian Debian Trixie Minimal |

---

## Wiring

### Display → BPI M4 Zero 40-pin header

| Display Pin | Signal | BPI Header Pin |
|---|---|---|
| VCC | 3.3V | Pin 1 |
| GND | GND | Pin 6 |
| CS | SPI0 CE0 | Pin 24 |
| SCK | SPI0 SCLK | Pin 23 |
| MOSI | SPI0 MOSI | Pin 19 |
| DC | GPIO 9 (BCM) | Pin 21 |
| BL | GPIO 19 (BCM) | Pin 35 |

> The BL (backlight) pin can be tied directly to 3.3V if software brightness control is not needed.

### Thermocouple (MAX31855 / MAX6675) — to be wired

Thermocouples share the SPI bus with the display using separate CS pins. Wiring and sensor code will be added in a future update.

---

## Project Structure

```
cht-gauge/
├── setup.sh          # One-shot bootstrap — run on fresh Armbian flash
├── gauge.py          # Main application
├── run.sh            # Launch script (sets SDL framebuffer env vars)
├── test_display.py   # Sanity check — fills screen red to verify SPI/wiring
├── .gitignore
└── fonts/
    └── README.md     # Fonts are downloaded by setup.sh, not committed to repo
```

---

## Fresh Install Instructions

### 1. Flash Armbian

Download **Armbian Debian Trixie Minimal** for BPI M4 Zero:
```
https://dl.armbian.com/bananapim4zero/Trixie_current_minimal
```

Flash with [Armbian Imager](https://imager.armbian.com) or Balena Etcher. Pre-configure WiFi credentials in the imager before flashing.

### 2. SSH in

Find the device IP via your router's DHCP client list, then:

```bash
ssh root@<ip-address>
# Default password: 1234 (you'll be prompted to change it)
```

### 3. Run setup

```bash
wget https://raw.githubusercontent.com/hwpaige/gauge/master/setup.sh
bash setup.sh
```

The script will:
- Set the hostname to `moto` (SSH via `root@moto.local` after reboot)
- Enable SPI in `/boot/armbianEnv.txt`
- Install all system and Python dependencies
- Clone this repo to `/root/gauge`
- Download the D-DIN font
- Create a Python virtual environment and install packages
- Prompt to reboot

After reboot, SSH back in with:

```bash
ssh root@moto.local
```

### 4. Test the display

```bash
cd /root/gauge
source venv/bin/activate
python3 test_display.py
```

The screen should fill solid red. If it does, SPI and wiring are good.

### 5. Run the gauge

```bash
/root/gauge/run.sh
```

---

## Application

`gauge.py` renders two CHT gauges using **pygame** directed at the Linux framebuffer (`/dev/fb0`) via SDL — no desktop environment required.

### Gauge design

- **Black background** with slight blue tint
- **270° filled arc** sweep per gauge
- **Colour zones:**
  - Green — 0–60% of range (normal)
  - Amber — 60–85% (caution)
  - Red — 85–100% (danger)
- **Tick marks** — major at 0%, 50%, 100%; minor every 10%
- **Digital readout** in the arc colour at the gauge centre
- **D-DIN font** throughout — industrial/avionics aesthetic
- **Centre divider** with "CYLINDER HEAD TEMP" header

### Temperature ranges

Min/max values are set per-gauge in `gauge.py`:

```python
draw_gauge(screen, LEFT_CENTER,  cht1, 0, 300, "CYL 1", font, small_font)
draw_gauge(screen, RIGHT_CENTER, cht2, 0, 300, "CYL 2", font, small_font)
```

Change `0` and `300` to match your engine's actual operating range.

### Sensor reads

The `read_cht(channel)` function in `gauge.py` currently returns simulated sine-wave values for testing. Replace it with real MAX31855/MAX6675 SPI reads when the thermocouple hardware is wired up.

---

## Dependencies

### System packages

Installed by `setup.sh`:

- `gcc`, `build-essential` — for compiling Python C extensions
- `python3-venv`, `python3-dev`, `python3-pip`
- `git`, `wget`, `unzip`
- `avahi-daemon` — enables `.local` mDNS hostname resolution
- `libsdl2-dev`, `libsdl2-image-dev`, `libsdl2-ttf-dev` — pygame display

### Python packages

Installed into `/root/gauge/venv`:

- `pygame` — rendering engine
- `st7789` — Pimoroni ST7789 display driver
- `pillow` — image handling (used by st7789)
- `spidev` — SPI bus access
- `RPi.GPIO` — GPIO control

---

## Roadmap

- [ ] Wire and integrate MAX31855 thermocouple reads
- [ ] Add configurable temperature range and alarm thresholds
- [ ] Auto-launch gauge on boot via systemd service
- [ ] Add EGT (exhaust gas temperature) display mode