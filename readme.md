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

**Important**: The kernel must provide `/dev/fb0` for the ST7789 (see "Kernel framebuffer driver" below). The Python app no longer drives the display directly.

---

## Wiring

### Display → BPI M4 Zero 40-pin header

The kernel framebuffer overlay (not the Python app) owns the pins. Example mapping used by current overlays/driver code (confirm against your .dts):

| Display Pin | Signal          | BPI Header Pin | Notes (Allwinner)     |
|-------------|-----------------|----------------|-----------------------|
| VCC         | 3.3V            | Pin 1          |                       |
| GND         | GND             | Pin 6          |                       |
| CS          | SPI1 CS0 (PH5)  | Pin 24         |                       |
| SCK         | SPI1 SCLK (PH6) | Pin 23         |                       |
| MOSI        | SPI1 MOSI (PH7) | Pin 19         |                       |
| DC          | PI16            | Pin 18         | (offset 272)          |
| RST         | PC2             | Pin 22         | (offset 66)           |
| (BL)        | (backlight)     | (Pin 35)       | Tie to 3.3V (Pin 1) if not software-controlled |

> Backlight: tie BL directly to 3.3V for always-on (recommended for gauge). The kernel panel driver typically handles reset/DC in its DT.

### Thermocouple (MAX31855 / MAX6675) — to be wired

Thermocouples share the SPI bus with the display using separate CS pins. Wiring and sensor code will be added in a future update.

---

## Project Structure

```
cht-gauge/
├── setup.sh          # One-shot bootstrap — run on fresh Armbian flash
├── gauge.py          # Main application (uses kernel /dev/fb0)
├── run.sh            # Launch script
├── test_display.py   # Legacy userspace test (may fail once kernel owns display)
├── pin_test.py       # Dev tool for probing GPIOs/SPI (kernel pins will be busy)
├── driver.py         # Legacy userspace ST7789 driver (kept for reference)
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

Once the kernel framebuffer driver is active (see below), the display is the system primary framebuffer. A basic test that the fb is working:

```bash
cat /dev/urandom > /dev/fb0   # noise on screen; Ctrl-C to stop
```

Or (inside venv):

```bash
cd /root/gauge
source venv/bin/activate
python3 -c "
import pygame, os
os.environ['SDL_VIDEODRIVER']='fbcon'; os.environ['SDL_FBDEV']='/dev/fb0'
pygame.init()
s = pygame.display.set_mode((240,280))
s.fill((255,0,0)); pygame.display.flip()
input('red fb - press enter'); pygame.quit()
"
```

(If the legacy `test_display.py` using the Python `st7789` package fails with EBUSY, that is expected once the kernel driver owns the pins — it is no longer needed.)

See "Kernel framebuffer driver" section for how the display becomes `/dev/fb0`.

### 5. Run the gauge

```bash
/root/gauge/run.sh
```

(Or manually: `cd /root/gauge && source venv/bin/activate && python3 gauge.py`)

**Important:** Because the kernel is now driving the round display as the primary framebuffer/console, you must run the gauge while on the **actual Linux console** that the display is attached to (not over SSH in most cases).

- Easiest for testing: Plug a USB keyboard into the Banana Pi. The round display should be showing the login prompt. Login directly there as root and run the command on that console.
- SSH sessions often produce `pygame.error: fbcon not available` because the SDL fbcon driver needs a real virtual terminal context.
- For production: create a systemd service that launches it on boot.

See the on-screen error message in the new gauge.py for more details and a quick kernel-fb test command that *does* work over SSH.

---

## Application

`gauge.py` renders two CHT gauges using **pygame** directed at the Linux framebuffer (`/dev/fb0`) via SDL fbcon — no desktop environment required.

The kernel (via a DT overlay for the ST7789 panel, typically exposing `fb_st7789v` or a tinydrm/fbdev panel) must own the SPI + DC/RST GPIOs and register the display as `/dev/fb0` (configured as 240×280 logical fb with the round 240×240 visible area starting at row 40). This gives rock-solid vsync/tearing-free updates ("super smooth with no flickering").

The old userspace Python ST7789 + gpiod + spidev driver path has been removed from the main app (it conflicts with "Device or resource busy" once the kernel claims the pins).

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

### Python packages (runtime)

Installed into `/root/gauge/venv`:

- `pygame` — rendering engine (fbcon targeting kernel `/dev/fb0`)

`st7789`, `pillow`, `spidev`, `gpiod`, `numpy` are also pulled in for the legacy test scripts only.

---

## Kernel framebuffer driver (required)

After the initial `setup.sh` (which only enables spidev), you must make the ST7789 the kernel's primary framebuffer (`/dev/fb0`) so that:

- The kernel owns DC, RST, and the SPI pins (your app will no longer get "Device or resource busy").
- Updates are smooth / tear-free (as you observed after the reboot that made the round display primary).

Typical steps (edit as root):

1. Create a device-tree overlay (example name `st7789-fb.dts`) in `/boot/overlay-user/` (or `/boot/dtb/overlay/` depending on Armbian version). A minimal overlay declares the spi bus + panel compatible (sitronix,st7789v or fbtft), with the correct GPIOs for your board (DC=PI16/272 on pin 18, RST=PC2/66 on pin 22, SPI1 on PH5-8), width/height, and any rotation/x/y offsets the round visible area needs (the gauge code assumes a 240×280 fb with visible content at y=40).

2. Compile it:
   ```
   armbian-add-overlay /boot/overlay-user/st7789-fb.dts
   ```
   (or `dtc -@ -I dts -O dtb -o /boot/dtb/allwinner/overlay/st7789-fb.dtbo ...` and add to user_overlays).

3. Edit `/boot/armbianEnv.txt`:
   ```
   overlays=... spi-spidev   # or remove spidev if the panel driver claims the bus exclusively
   user_overlays=st7789-fb
   # extraargs or param_ lines as required by your overlay (bus num, speed, etc.)
   ```

4. Reboot.

5. Verify:
   ```
   ls -l /dev/fb0
   fbset   # or cat /sys/class/graphics/fb0/virtual_size  (expect something like 240x280)
   ```

Once `/dev/fb0` is the round display, `python3 gauge.py` (or `run.sh`) will use it directly and be super smooth.

If you have a working overlay .dts that matches the Pimoroni 1.3" + BPI M4 Zero pinout (SPI1, offsets 272/66, y_off=40), contribute it!

The gauge code hard-codes the 240×280 / y=40 layout that the kernel driver must expose for the round 240×240 visible panel.

---

## Roadmap

- [ ] Wire and integrate MAX31855 thermocouple reads
- [ ] Add configurable temperature range and alarm thresholds
- [ ] Auto-launch gauge on boot via systemd service
- [ ] Add EGT (exhaust gas temperature) display mode