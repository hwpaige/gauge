#!/usr/bin/env bash
#
# setup_pi.sh — Raspberry Pi 4B + Raspberry Pi OS Lite setup for the
#               4-channel MAX31856 thermocouple HAT test app.
#
# Run it ON THE PI, from inside the copied repo directory:
#
#     chmod +x setup_pi.sh
#     ./setup_pi.sh            # add -y to skip the reboot / launch prompts
#
# What it does (idempotent — safe to re-run):
#   1. installs python3-spidev + python3-lgpio
#   2. enables SPI0 and frees GPIO7 for TC1's software chip-select
#      (dtparam=spi=on  +  dtoverlay=spi0-1cs)
#   3. reboots if the boot config changed (just re-run this script afterwards)
#   4. runs the decoder self-test, probes the channels, then launches the
#      live monitor
#
# Wiring recap (from thermocouple_hat/ netlist): 4× MAX31856 share SPI0
# (SCLK GPIO11 / MOSI GPIO10 / MISO GPIO9); chip-selects TC1=GPIO7, TC2=GPIO5,
# TC3=GPIO6, TC4=GPIO12.  The LEFT-most screw terminal is TC1.
#
set -euo pipefail

# ── pretty output ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; BLD=$'\e[1m'; NC=$'\e[0m'
else RED=; GRN=; YLW=; CYN=; BLD=; NC=; fi
log()  { echo "${CYN}▸ $*${NC}"; }
ok()   { echo "${GRN}✓ $*${NC}"; }
warn() { echo "${YLW}! $*${NC}"; }
err()  { echo "${RED}✗ $*${NC}" >&2; }

AUTO=0
case "${1:-}" in -y|--yes) AUTO=1 ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$SCRIPT_DIR/thermocouple_test.py"
RUN_USER="$(id -un)"

echo "${BLD}MAX31856 thermocouple HAT — Raspberry Pi setup${NC}"

# ── sanity ──────────────────────────────────────────────────────────────────
if [ ! -f "$APP" ]; then
  err "thermocouple_test.py not found next to this script:"
  err "  $SCRIPT_DIR"
  err "Copy the whole repo onto the Pi and run setup_pi.sh from inside it, e.g.:"
  err "  scp -r <this-repo> ${RUN_USER}@raspberrypi.local:~/gauge"
  exit 1
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || { err "run as root, or install sudo"; exit 1; }
  SUDO="sudo"
fi

# check we're actually on a Pi / have a Pi boot config
if [ -f /boot/firmware/config.txt ]; then CONFIG=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then CONFIG=/boot/config.txt
else
  err "No Raspberry Pi boot config found (/boot/firmware/config.txt or /boot/config.txt)."
  err "This script is meant to run on the Raspberry Pi, not your dev machine."
  exit 1
fi
log "Boot config: $CONFIG"

# ── packages ────────────────────────────────────────────────────────────────
if python3 -c 'import spidev, lgpio' 2>/dev/null; then
  ok "python3 spidev + lgpio already installed"
else
  log "Installing python3-spidev + python3-lgpio…"
  $SUDO apt-get update -qq || warn "apt update failed (continuing)"
  $SUDO apt-get install -y python3-spidev python3-lgpio >/dev/null 2>&1 \
    || warn "apt install reported an issue — will verify by import"
  if ! python3 -c 'import spidev, lgpio' 2>/dev/null; then
    warn "Falling back to pip…"
    $SUDO pip3 install --break-system-packages spidev lgpio >/dev/null 2>&1 || true
  fi
  python3 -c 'import spidev, lgpio' 2>/dev/null \
    || { err "could not import spidev + lgpio — install them manually:"; \
         err "  sudo apt install python3-spidev python3-lgpio"; exit 1; }
  ok "spidev + lgpio ready"
fi

# make sure this user can reach /dev/spidev* and /dev/gpiochip*
if ! { id -nG "$RUN_USER" | grep -qw spi && id -nG "$RUN_USER" | grep -qw gpio; }; then
  $SUDO usermod -aG spi,gpio "$RUN_USER" 2>/dev/null || true
  warn "Added $RUN_USER to the spi,gpio groups — reboot (below) makes it effective"
fi

# ── boot config: enable SPI0 with a single CS so GPIO7 is free ─────────────
CHANGED=0
ADD=()
grep -qE '^[[:space:]]*dtparam=spi=on'     "$CONFIG" || ADD+=("dtparam=spi=on")
grep -qE '^[[:space:]]*dtoverlay=spi0-1cs' "$CONFIG" || ADD+=("dtoverlay=spi0-1cs")
if [ "${#ADD[@]}" -gt 0 ]; then
  log "Enabling SPI0 (single CS → GPIO7 free for TC1)…"
  { echo ""; echo "# thermocouple HAT (added by setup_pi.sh)"; echo "[all]";
    printf '%s\n' "${ADD[@]}"; } | $SUDO tee -a "$CONFIG" >/dev/null
  for l in "${ADD[@]}"; do ok "added to $CONFIG:  $l"; done
  CHANGED=1
else
  ok "SPI0 already configured (dtparam=spi=on + dtoverlay=spi0-1cs)"
fi

# ── reboot if the config changed ───────────────────────────────────────────
spi_ready() { [ -e /dev/spidev0.0 ] && [ ! -e /dev/spidev0.1 ]; }

if [ "$CHANGED" -eq 1 ]; then
  echo
  warn "A REBOOT is required for the SPI0 / GPIO7 change to take effect."
  warn "After it reboots, reconnect and just run this script again:"
  warn "  $SCRIPT_DIR/setup_pi.sh"
  if [ "$AUTO" -eq 1 ]; then
    log "Rebooting now…"; $SUDO reboot; exit 0
  fi
  read -rp "Reboot now? [Y/n] " a || a=Y
  case "${a:-Y}" in
    [Nn]*) echo "OK — reboot yourself with 'sudo reboot', then re-run this script."; exit 0 ;;
    *)     log "Rebooting…"; $SUDO reboot; exit 0 ;;
  esac
elif ! spi_ready; then
  warn "SPI0 is configured but not active yet"
  warn "  /dev/spidev0.0: $([ -e /dev/spidev0.0 ] && echo present || echo MISSING)"
  warn "  /dev/spidev0.1: $([ -e /dev/spidev0.1 ] && echo 'present (GPIO7 still claimed!)' || echo absent)"
  warn "If you edited the config in this run, reboot once: sudo reboot"
else
  ok "SPI0 active: /dev/spidev0.0 present, GPIO7 free"
fi

# ── verify + run ───────────────────────────────────────────────────────────
echo
log "Decoder self-test (no hardware needed)…"
python3 "$APP" --selftest || { err "self-test failed — decoders are wrong, stop here"; exit 1; }

echo
log "Probing all four channels…"
echo "   (you have one probe in the LEFT terminal = TC1, so expect:"
echo "    TC1 → a real temperature,  TC2/TC3/TC4 → OPEN)"
echo
python3 "$APP" --plain --once || warn "probe returned an error (see above)"

echo
if [ "$AUTO" -eq 1 ]; then
  ok "Setup done. Live monitor:  python3 $APP"
  exit 0
fi
read -rp "Launch the live monitor now? [Y/n] " a || a=Y
case "${a:-Y}" in
  [Nn]*) echo "Run it anytime with:  python3 $APP" ;;
  *)     exec python3 "$APP" ;;
esac
