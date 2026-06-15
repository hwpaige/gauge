# Banana Pi CHT Gauge - Conversation Changes Summary

**Date**: June 2026 (conversation spanned ~June 14-15)  
**Project**: `gauge` - Twin cylinder head temperature (CHT) gauge on Banana Pi M4 Zero with 1.3" round ST7789 SPI display.  
**Goal of this conversation**: Fix the app after the kernel took over the display as the primary framebuffer.

## Background & Original Problem

- After a reboot, the Banana Pi was using the round display as the **primary display** via the kernel framebuffer driver (`fb_st7789v` or equivalent).
- This produced "super smooth" output with no flickering (the kernel driver handled SPI/panel timing well).
- The existing `gauge.py` (and `driver.py`) used a **userspace ST7789 driver** (`gpiod.request_lines` for DC/RST + `spidev`).
- This caused: `OSError: [Errno 16] Device or resource busy` because the kernel had already claimed the GPIO pins and SPI bus.
- The local workspace had **uncommitted changes** switching to kernel `/dev/fb0` + `pygame` + `fbcon`, but the Pi's `git pull` saw an old version (and later hit an untracked `pin_test.py` conflict).

The root cause: The project had evolved to rely on the **kernel owning the display** for smoothness, but the code and deployment weren't fully updated for that reality.

## Major Architectural Shift

**From**: Userspace pixel pushing (Pygame offscreen → custom ST7789 class → SPI via `spidev` + GPIO via `gpiod`).

**To**: Kernel framebuffer direct write.
- Pygame renders offscreen only (using `SDL_VIDEODRIVER=dummy`).
- At the end of each frame, convert the 240×280 surface to raw **RGB565** bytes.
- Write directly to `/dev/fb0` (via `mmap` for efficiency).
- This lets the **kernel driver** handle all the panel timing, SPI, and refresh — matching the smooth behavior the user observed with the console.

This also eliminated the GPIO "busy" conflict.

## Key Code Changes (gauge.py)

- Removed reliance on `driver.py` / `ST7789` class and `gpiod`/`spidev` for the main app.
- Added `os.environ` setup early (before `import pygame`).
- New helper: `_surface_to_fb565(surface)` — converts Pygame surface to RGB565 bytes.
  - Prefers `numpy` for speed (when available).
  - Pure-Python fallback.
  - Includes **byte swap** to match the format the original userspace driver (and kernel fb) expected:
    ```python
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    rgb565 = ((rgb565 >> 8) | ((rgb565 & 0xFF) << 8))  # byte swap
    ```
- Switched to offscreen `full_fb` surface (240×280) + blitting the 240×240 gauge at `y=FB_Y_OFF=40`.
- Direct write path:
  - `open('/dev/fb0', 'r+b')` + `mmap.mmap(...)`
  - `fb_map[:] = buf; fb_map.flush()`
- Added `FBIO_WAITFORVSYNC` ioctl (best-effort) after writes to improve timing and reduce tearing (to better match the kernel console's smoothness).
- **Startup reliability fixes**:
  - Immediate **solid red test pattern** (for 1.5s) on startup for visibility debugging.
  - Explicit first real gauge frame written before the main loop.
  - Prints to journal for diagnostics.
- Debug prints: `SDL_VIDEODRIVER`, framebuffer checks, "First real gauge frame written", etc.
- Graceful cleanup of mmap/fd on exit.
- Event loop (Q to quit) preserved for testing.

## Systemd Service (new file: gauge.service)

- Created `gauge.service` for auto-launch on boot.
- Uses `Environment=SDL_VIDEODRIVER=dummy` + direct `/dev/fb0` write.
- `ExecStartPre` steps:
  - `chvt 1`
  - Disable cursor blink
  - Stop interfering gettys (`getty@tty1`, serial gettys, etc.)
- `Restart=always`, `StandardOutput=journal` / `StandardError=journal` (prevents error text from being painted onto the framebuffer as "terminal" garbage).
- TTY-related options retained for the console context.
- Installation instructions added to README.

High CPU time seen in early journals was from long crash/restart loops on the old fbcon code.

## Documentation & Supporting Files

- **readme.md**:
  - Major rewrite of "Kernel framebuffer driver (required)" section.
  - Updated wiring table (correct BPI M4 Zero pins: SPI1 on PH5–PH8, DC=PI16/pin18, RST=PC2/pin22).
  - New "Auto-start via systemd" subsection with concrete commands.
  - Updated install/test/run instructions (emphasize kernel fb, SSH vs local console).
  - Explained why userspace ST7789 + gpiod/pillow/etc. are now legacy.
- **setup.sh**: Added warnings about needing the kernel ST7789/fb overlay (in addition to spi-spidev). Updated final notes and test instructions.
- **run.sh**: Updated comment to reflect direct fb path.
- **requirements.txt**: Clarified that only `pygame` is runtime-required; others are for legacy tools.
- **test_display.py**: Marked as legacy (will fail with EBUSY once kernel owns pins).
- **pin_test.py**: Committed (was previously untracked in some clones); contains kernel-owned pin warnings.
- Added `CONVERSATION_CHANGES_SUMMARY.md` (this file) for future reference.

## Debugging Journey (Key Failures & Fixes)

1. **EBUSY on gpiod** → Realized kernel now owns display → switched to fb path + pushed commits.
2. **Untracked `pin_test.py` blocking pull** → User `mv` + `git pull`.
3. **`fbcon not available`** (over SSH) → Explained console vs. SSH context. Created systemd service as long-term solution (no keyboard required).
4. **"Only showing in red"** → RGB565 byte order was wrong. Matched original `driver.py` conversion (byte swap).
5. **Blank screen after mmap/vsync** → Vsync ioctl could block before first write. Added:
   - Immediate red test pattern.
   - Explicit first real frame write + prints.
   - Moved vsync *after* write.
6. **High CPU in journal** → Artifact of long restart loop on broken code; fixed by stable direct-write path.

User observation that helped: "When the kernel was the display output, the CLI was super smooth including the blinking cursor (no rapid refresh)." This drove the mmap + vsync improvements to emulate kernel fbcon timing.

## Current Recommended Workflow (as of end of conversation)

1. Kernel DT overlay must expose the round ST7789 as `/dev/fb0` (240×280 logical, visible at y=40).
2. `git pull` on Pi.
3. Install/restart service:
   ```bash
   sudo cp gauge.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now gauge.service
   # Optional: stop getty
   sudo systemctl stop getty@tty1.service
   sudo systemctl disable getty@tty1.service
   ```
4. Journal for logs: `journalctl -u gauge.service -f`
5. Temporary clean console: `sudo systemctl stop gauge.service && sudo chvt 1`
6. Quick hardware test (works over SSH): `cat /dev/urandom > /dev/fb0` (Ctrl-C after 1-2s). Should show noise if `/dev/fb0` is the round panel.

## Files Changed / Added

- `gauge.py` (major rewrite)
- `gauge.service` (new)
- `readme.md`
- `setup.sh`
- `run.sh`
- `requirements.txt`
- `test_display.py`
- `pin_test.py` (committed)
- `CONVERSATION_CHANGES_SUMMARY.md` (this file)

## Lessons / Future Reference

- When the kernel owns the display as primary fb, **never** use userspace GPIO/SPI drivers for it.
- `fbcon` in SDL2 is fragile on embedded Armbian (especially over SSH or with minimal builds). Direct `/dev/fb0` + mmap is more reliable.
- Match pixel format (RGB565 + byte order) to what the original working driver used.
- Always guarantee at least one visible frame on startup for debugging.
- Use journal logging + test patterns (solid red) when the display can go completely black.
- For maximum smoothness (matching kernel fbcon), combine mmap + `FBIO_WAITFORVSYNC` (or TE pin polling if wired).
- Service + pre-commands to take over the console are essential for "set and forget" embedded gauges.

This summary captures the evolution from a conflicting userspace driver to a robust kernel-fb direct-write solution. Use it when revisiting deployment, debugging color/tearing/blank issues, or extending the project (e.g., adding thermocouple sensors or improving vsync with the TE pin).

---

*Generated at the end of the troubleshooting conversation for future reference.*