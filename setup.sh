#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  BPI M4 Zero CHT Gauge — Bootstrap Setup Script
#  Run on a fresh Armbian flash as root: bash setup.sh
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

# ── CONFIGURE THESE ───────────────────────────────────────
GITHUB_USER="hwpaige"
GITHUB_REPO="gauge"
GITHUB_BRANCH="master"
HOSTNAME="moto"
INSTALL_DIR="/root/gauge"
# ─────────────────────────────────────────────────────────

REPO_URL="https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     BPI M4 Zero CHT Gauge Setup          ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 0. Must be root ───────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  error "Please run as root: bash setup.sh"
fi

# ── 1. Hostname ───────────────────────────────────────────
log "Setting hostname to ${HOSTNAME}..."
hostnamectl set-hostname "$HOSTNAME"
if grep -q "127.0.1.1" /etc/hosts; then
  sed -i "s/127.0.1.1.*/127.0.1.1\t${HOSTNAME}/" /etc/hosts
else
  echo -e "127.0.1.1\t${HOSTNAME}" >> /etc/hosts
fi
ok "Hostname set to ${HOSTNAME}"

# ── 2. SPI ────────────────────────────────────────────────
log "Configuring SPI in /boot/armbianEnv.txt..."
ARMBIAN_ENV="/boot/armbianEnv.txt"

if grep -q "overlays=" "$ARMBIAN_ENV"; then
  if ! grep -q "spi-spidev" "$ARMBIAN_ENV"; then
    sed -i 's/^overlays=\(.*\)/overlays=\1 spi-spidev/' "$ARMBIAN_ENV"
    ok "Added spi-spidev to overlays"
  else
    ok "spi-spidev already present"
  fi
else
  echo "overlays=spi-spidev" >> "$ARMBIAN_ENV"
  ok "Created overlays line"
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

log "Installing system dependencies..."
apt install -y \
  gcc \
  build-essential \
  python3-pip \
  python3-venv \
  python3-dev \
  python3-pil \
  python3-numpy \
  wget \
  unzip \
  git \
  avahi-daemon \
  libsdl2-dev \
  libsdl2-image-dev \
  libsdl2-ttf-dev
ok "System packages installed"

# ── 4. Clone repo ─────────────────────────────────────────
log "Cloning ${GITHUB_USER}/${GITHUB_REPO} from GitHub..."
if [ -d "$INSTALL_DIR/.git" ]; then
  warn "Repo already exists — pulling latest changes..."
  git -C "$INSTALL_DIR" pull
else
  git clone --branch "$GITHUB_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
ok "Repo cloned to $INSTALL_DIR"

# ── 5. Python venv ────────────────────────────────────────
log "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
ok "venv created"

# ── 6. Python packages ────────────────────────────────────
log "Installing Python packages (this may take a minute)..."
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
ok "Python packages installed"

# ── 7. Permissions ────────────────────────────────────────
chmod +x "$INSTALL_DIR/run.sh"
ok "run.sh marked executable"

# ── 8. Summary ────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Setup Complete!                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Project:${NC}       $INSTALL_DIR"
echo -e "  ${CYAN}Run gauge:${NC}     $INSTALL_DIR/run.sh"
echo -e "  ${CYAN}Test display:${NC}  cd $INSTALL_DIR && source venv/bin/activate && python3 test_display.py"
echo -e "  ${CYAN}SSH:${NC}           ssh root@${HOSTNAME}.local"
echo ""
echo -e "  ${YELLOW}NOTE: A reboot is required for SPI to activate.${NC}"
echo ""

read -rp "Reboot now? (y/n): " -n 1
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
  log "Rebooting..."
  reboot
else
  warn "Remember to reboot before testing: sudo reboot"
fi