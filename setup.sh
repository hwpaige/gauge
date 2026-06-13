#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  BPI M4 Zero CHT Gauge — Bootstrap Setup Script
#  Run as root: bash setup.sh
# ─────────────────────────────────────────────────────────────

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── UPDATE THIS ───────────────────────────────────────────
GITHUB_USER="your-username"
GITHUB_REPO="cht-gauge"
GITHUB_BRANCH="main"
# ─────────────────────────────────────────────────────────

REPO_URL="https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"
INSTALL_DIR="/root/gauge"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     BPI M4 Zero CHT Gauge Setup          ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 0. Check root ─────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  error "Please run as root: sudo bash setup.sh"
fi

# ── 1. Hostname ───────────────────────────────────────────
log "Setting hostname to bpi-temp-gauge..."
hostnamectl set-hostname bpi-temp-gauge
if grep -q "127.0.1.1" /etc/hosts; then
  sed -i 's/127.0.1.1.*/127.0.1.1\tbpi-temp-gauge/' /etc/hosts
else
  echo "127.0.1.1	bpi-temp-gauge" >> /etc/hosts
fi
ok "Hostname set"

# ── 2. Enable SPI ─────────────────────────────────────────
log "Configuring SPI..."
ARMBIAN_ENV="/boot/armbianEnv.txt"

if grep -q "overlays=" "$ARMBIAN_ENV"; then
  if ! grep -q "spi-spidev" "$ARMBIAN_ENV"; then
    sed -i 's/^overlays=\(.*\)/overlays=\1 spi-spidev/' "$ARMBIAN_ENV"
    ok "Added spi-spidev to overlays"
  else
    ok "spi-spidev already in overlays"
  fi
else
  echo "overlays=spi-spidev" >> "$ARMBIAN_ENV"
  ok "Added overlays line"
fi

if ! grep -q "param_spidev_spi_bus" "$ARMBIAN_ENV"; then
  echo "param_spidev_spi_bus=0" >> "$ARMBIAN_ENV"
  ok "Added param_spidev_spi_bus=0"
else
  ok "param_spidev_spi_bus already set"
fi

# ── 3. System packages ────────────────────────────────────
log "Updating package list..."
apt update -qq

log "Installing system