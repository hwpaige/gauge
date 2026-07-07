#!/usr/bin/env bash
#
# setup_pi.sh — Raspberry Pi 4B + Raspberry Pi OS Lite setup for the
#               4-channel MAX31856 thermocouple HAT test app.
#
# Fastest path (no Mac needed) — bootstrap straight from GitHub:
#
#     wget https://raw.githubusercontent.com/hwpaige/gauge/master/setup_pi.sh
#     bash setup_pi.sh
#
# It clones/updates the repo to ~/gauge and runs from there. If you instead run
# it from inside an existing checkout, it uses that checkout in place.
#
# What it does (idempotent — safe to re-run):
#   1. gets the repo (git clone/pull from GitHub, or uses the local checkout)
#   2. installs python3-spidev + python3-lgpio
#   3. enables SPI0 and frees GPIO7 for TC1's software chip-select
#      (dtparam=spi=on  +  dtoverlay=spi0-1cs)
#   4. reboots if the boot config changed (just re-run this script afterwards)
#   5. runs the decoder self-test, probes the channels, launches the live UI
#
# Wiring recap (thermocouple_hat/ netlist): 4× MAX31856 share SPI0 (SCLK GPIO11 /
# MOSI GPIO10 / MISO GPIO9); chip-selects TC1=GPIO7, TC2=GPIO5, TC3=GPIO6,
# TC4=GPIO12.  The LEFT-most screw terminal is TC1.
#
set -euo pipefail

REPO_URL="https://github.com/hwpaige/gauge.git"
BRANCH="master"

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
RUN_USER="$(id -un)"
INSTALL_DIR="${HOME:-/home/$RUN_USER}/gauge"

echo "${BLD}MAX31856 thermocouple HAT — Raspberry Pi setup${NC}"

# sudo helper (script runs as the normal user; elevates only for apt/config)
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || { err "run as your normal user with sudo available"; exit 1; }
  SUDO="sudo"
fi

# must be an actual Raspberry Pi
if [ -f /boot/firmware/config.txt ]; then CONFIG=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then CONFIG=/boot/config.txt
else
  err "No Raspberry Pi boot config (/boot/firmware/config.txt or /boot/config.txt)."
  err "Run this on the Raspberry Pi, not your dev machine."
  exit 1
fi

# ── obtain the repo ─────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/thermocouple_test.py" ]; then
  REPO_DIR="$SCRIPT_DIR"
  ok "Using local checkout: $REPO_DIR"
else
  log "Fetching the repo from GitHub…"
  if ! command -v git >/dev/null 2>&1; then
    $SUDO apt-get update -qq || warn "apt update failed (continuing)"
    $SUDO apt-get install -y git >/dev/null 2>&1 || { err "could not install git"; exit 1; }
  fi
  if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only || warn "git pull failed — using existing checkout"
  elif [ -e "$INSTALL_DIR" ]; then
    err "$INSTALL_DIR exists but is not a git checkout — move/remove it and re-run."
    exit 1
  else
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
  REPO_DIR="$INSTALL_DIR"
  ok "Repo ready: $REPO_DIR"
fi
APP="$REPO_DIR/thermocouple_test.py"
[ -f "$APP" ] || { err "thermocouple_test.py missing in $REPO_DIR"; exit 1; }
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
    || { err "could not import spidev + lgpio — install manually:"; \
         err "  sudo apt install python3-spidev python3-lgpio"; exit 1; }
  ok "spidev + lgpio ready"
fi

# let this user reach /dev/spidev* and /dev/gpiochip*
if ! { id -nG "$RUN_USER" | grep -qw spi && id -nG "$RUN_USER" | grep -qw gpio; }; then
  $SUDO usermod -aG spi,gpio "$RUN_USER" 2>/dev/null || true
  warn "Added $RUN_USER to spi,gpio groups — the reboot below makes it effective"
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
  warn "After it reboots, reconnect and run this again (or:  bash $REPO_DIR/setup_pi.sh)"
  if [ "$AUTO" -eq 1 ]; then log "Rebooting now…"; $SUDO reboot; exit 0; fi
  read -rp "Reboot now? [Y/n] " a || a=Y
  case "${a:-Y}" in
    [Nn]*) echo "OK — 'sudo reboot' yourself, then re-run this script."; exit 0 ;;
    *)     log "Rebooting…"; $SUDO reboot; exit 0 ;;
  esac
elif ! spi_ready; then
  warn "SPI0 configured but not active yet"
  warn "  /dev/spidev0.0: $([ -e /dev/spidev0.0 ] && echo present || echo MISSING)"
  warn "  /dev/spidev0.1: $([ -e /dev/spidev0.1 ] && echo 'present (GPIO7 still claimed!)' || echo absent)"
  warn "If you edited the config this run, reboot once: sudo reboot"
else
  ok "SPI0 active: /dev/spidev0.0 present, GPIO7 free"
fi

# ── verify + run ───────────────────────────────────────────────────────────
echo
log "Decoder self-test (no hardware needed)…"
python3 "$APP" --selftest || { err "self-test failed — stop here"; exit 1; }

echo
log "Probing all four channels…"
echo "   (one probe in the LEFT terminal = TC1, so expect:"
echo "    TC1 → a real temperature,  TC2/TC3/TC4 → OPEN)"
echo
python3 "$APP" --plain --once || warn "probe returned an error (see above)"

echo
if [ "$AUTO" -eq 1 ]; then ok "Setup done.  Live UI:  python3 $APP"; exit 0; fi
read -rp "Launch the interactive monitor now? [Y/n] " a || a=Y
case "${a:-Y}" in
  [Nn]*) echo "Run it anytime with:  python3 $APP" ;;
  *)     exec python3 "$APP" ;;
esac
