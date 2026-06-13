#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  BPI M4 Zero CHT Gauge — Setup Script
#  Run as root: bash setup.sh
# ─────────────────────────────────────────────────────────────

set -e  # Exit on any error

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log()    { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()     { echo -e "${GREEN}[OK]${NC} $1"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     BPI M4 Zero CHT Gauge Setup          ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── 0. Check running as root ──────────────────────────────
if [ "$EUID" -ne 0 ]; then
  error "Please run as root: sudo bash setup.sh"
fi

# ── 1. Set hostname ───────────────────────────────────────
log "Setting hostname to bpi-temp-gauge..."
hostnamectl set-hostname bpi-temp-gauge
sed -i 's/127.0.1.1.*/127.0.1.1\tbpi-temp-gauge/' /etc/hosts
ok "Hostname set"

# ── 2. Enable SPI ─────────────────────────────────────────
log "Configuring SPI in /boot/armbianEnv.txt..."

ARMBIAN_ENV="/boot/armbianEnv.txt"

# Add spi-spidev to overlays if not already present
if grep -q "overlays=" "$ARMBIAN_ENV"; then
  if ! grep -q "spi-spidev" "$ARMBIAN_ENV"; then
    sed -i 's/^overlays=\(.*\)/overlays=\1 spi-spidev/' "$ARMBIAN_ENV"
    ok "Added spi-spidev to overlays"
  else
    ok "spi-spidev already in overlays"
  fi
else
  echo "overlays=spi-spidev" >> "$ARMBIAN_ENV"
  ok "Added overlays line with spi-spidev"
fi

# Add param_spidev_spi_bus if not present
if ! grep -q "param_spidev_spi_bus" "$ARMBIAN_ENV"; then
  echo "param_spidev_spi_bus=0" >> "$ARMBIAN_ENV"
  ok "Added param_spidev_spi_bus=0"
else
  ok "param_spidev_spi_bus already set"
fi

# ── 3. Install system packages ────────────────────────────
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
  avahi-daemon \
  git
ok "System packages installed"

# ── 4. Create project directory ───────────────────────────
log "Creating project directory at ~/gauge..."
mkdir -p /root/gauge/fonts
cd /root/gauge
ok "Directory ready"

# ── 5. Python virtual environment ─────────────────────────
log "Creating Python virtual environment..."
python3 -m venv /root/gauge/venv
ok "Virtual environment created"

# ── 6. Install Python packages ────────────────────────────
log "Installing Python packages (this may take a minute)..."
/root/gauge/venv/bin/pip install --quiet --upgrade pip
/root/gauge/venv/bin/pip install \
  st7789 \
  pillow \
  pygame \
  RPi.GPIO \
  spidev
ok "Python packages installed"

# ── 7. Download D-DIN font ────────────────────────────────
log "Downloading D-DIN font..."
cd /root/gauge/fonts

if wget -q "https://www.1001fonts.com/download/d-din.zip" -O d-din.zip; then
  unzip -q -o d-din.zip
  rm d-din.zip
  ok "D-DIN font downloaded and extracted"
  log "Font files found:"
  ls *.ttf 2>/dev/null || warn "No .ttf files found — check fonts directory manually"
else
  warn "Could not download D-DIN font. Download manually from https://www.1001fonts.com/d-din-font.html"
  warn "Place .ttf files in /root/gauge/fonts/"
fi

cd /root/gauge

# ── 8. Write gauge.py ─────────────────────────────────────
log "Writing gauge.py..."
cat > /root/gauge/gauge.py << 'GAUGE_PY'
import pygame
import math
import time

# ── Config ────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 240, 240
FPS = 30

# Gauge layout
LEFT_CENTER  = (60, 120)
RIGHT_CENTER = (180, 120)
GAUGE_RADIUS = 50
ARC_WIDTH    = 8

# Angle config
ARC_START = 135
ARC_END   = 405

# Colors
BG_COLOR      = (5, 5, 10)
TRACK_COLOR   = (40, 40, 50)
NORMAL_COLOR  = (0, 200, 120)
CAUTION_COLOR = (255, 180, 0)
DANGER_COLOR  = (220, 40, 40)
TEXT_COLOR    = (220, 220, 230)
LABEL_COLOR   = (120, 120, 140)
BEZEL_COLOR   = (60, 60, 75)

# ── Font paths ────────────────────────────────────────────
import os
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

def find_font(candidates, fallback):
    for name in candidates:
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            return path
    return fallback

FONT_BOLD      = find_font(["D-DIN-Bold.ttf", "D-DIN Alternate Bold.ttf"], None)
FONT_REGULAR   = find_font(["D-DIN.ttf", "D-DIN Alternate.ttf"], None)
FONT_CONDENSED = find_font(["D-DIN-Condensed.ttf", "D-DIN Condensed.ttf"], None)
FALLBACK_FONT  = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ── Helpers ───────────────────────────────────────────────
def deg_to_rad(deg):
    return math.radians(deg)

def draw_arc(surface, color, center, radius, start_deg, end_deg, width):
    steps = max(60, int(abs(end_deg - start_deg)))
    points_outer = []
    points_inner = []
    for i in range(steps + 1):
        angle = deg_to_rad(start_deg + (end_deg - start_deg) * i / steps)
        ox = center[0] + radius * math.cos(angle)
        oy = center[1] + radius * math.sin(angle)
        ix = center[0] + (radius - width) * math.cos(angle)
        iy = center[1] + (radius - width) * math.sin(angle)
        points_outer.append((ox, oy))
        points_inner.append((ix, iy))
    polygon = points_outer + list(reversed(points_inner))
    if len(polygon) > 2:
        pygame.draw.polygon(surface, color, polygon)

def draw_gauge(surface, center, value, min_val, max_val, label, font, small_font):
    x, y = center

    # Bezel
    pygame.draw.circle(surface, BEZEL_COLOR, center, GAUGE_RADIUS + 4, 2)

    # Track
    draw_arc(surface, TRACK_COLOR, center, GAUGE_RADIUS, ARC_START, ARC_END, ARC_WIDTH)

    # Value arc
    pct = max(0, min(1, (value - min_val) / (max_val - min_val)))
    filled_end = ARC_START + pct * (ARC_END - ARC_START)

    if pct < 0.6:
        arc_color = NORMAL_COLOR
    elif pct < 0.85:
        arc_color = CAUTION_COLOR
    else:
        arc_color = DANGER_COLOR

    if pct > 0:
        draw_arc(surface, arc_color, center, GAUGE_RADIUS, ARC_START, filled_end, ARC_WIDTH)

    # Tick marks
    for i in range(11):
        tick_pct   = i / 10
        tick_angle = deg_to_rad(ARC_START + tick_pct * (ARC_END - ARC_START))
        is_major   = (i % 5 == 0)
        outer_r    = GAUGE_RADIUS - ARC_WIDTH - 2
        inner_r    = outer_r - (6 if is_major else 3)
        tx1 = x + outer_r * math.cos(tick_angle)
        ty1 = y + outer_r * math.sin(tick_angle)
        tx2 = x + inner_r * math.cos(tick_angle)
        ty2 = y + inner_r * math.sin(tick_angle)
        tick_color = (180, 180, 190) if is_major else (80, 80, 95)
        pygame.draw.line(surface, tick_color, (tx1, ty1), (tx2, ty2), 2 if is_major else 1)

    # Digital readout
    value_surf = font.render(f"{int(value)}", True, arc_color)
    value_rect = value_surf.get_rect(center=(x, y - 5))
    surface.blit(value_surf, value_rect)

    # Unit
    unit_surf = small_font.render("°C", True, LABEL_COLOR)
    unit_rect = unit_surf.get_rect(center=(x, y + 14))
    surface.blit(unit_surf, unit_rect)

    # Label
    label_surf = small_font.render(label, True, LABEL_COLOR)
    label_rect = label_surf.get_rect(center=(x, y + GAUGE_RADIUS + 10))
    surface.blit(label_surf, label_rect)

def draw_divider(surface, title_font):
    pygame.draw.line(surface, (40, 40, 55), (120, 30), (120, 210), 1)
    title = title_font.render("C Y L I N D E R   H E A D   T E M P", True, (70, 70, 90))
    surface.blit(title, title.get_rect(center=(120, 12)))

# ── Main ──────────────────────────────────────────────────
def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("CHT Gauge")
    clock = pygame.time.Clock()

    font       = pygame.font.Font(FONT_BOLD      or FALLBACK_FONT, 22)
    small_font = pygame.font.Font(FONT_REGULAR   or FALLBACK_FONT, 10)
    title_font = pygame.font.Font(FONT_CONDENSED or FALLBACK_FONT, 9)

    # Simulated values — replace with real sensor reads later
    cht1 = 80.0
    cht2 = 145.0

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        cht1 = min(300, cht1 + 0.2)
        cht2 = max(0,   cht2 - 0.1)

        screen.fill(BG_COLOR)
        draw_divider(screen, title_font)
        draw_gauge(screen, LEFT_CENTER,  cht1, 0, 300, "CYL 1", font, small_font)
        draw_gauge(screen, RIGHT_CENTER, cht2, 0, 300, "CYL 2", font, small_font)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

if __name__ == "__main__":
    main()
GAUGE_PY
ok "gauge.py written"

# ── 9. Write run.sh ───────────────────────────────────────
log "Writing run.sh..."
cat > /root/gauge/run.sh << 'RUN_SH'
#!/bin/bash
cd /root/gauge
source venv/bin/activate
export SDL_VIDEODRIVER=fbcon
export SDL_FBDEV=/dev/fb0
python3 gauge.py
RUN_SH
chmod +x /root/gauge/run.sh
ok "run.sh written"

# ── 10. Write test_display.py ─────────────────────────────
log "Writing test_display.py..."
cat > /root/gauge/test_display.py << 'TEST_PY'
import st7789
from PIL import Image

display = st7789.ST7789(
    port=0,
    cs=st7789.BG_SPI_CS_FRONT,
    dc=9,
    backlight=19,
    rotation=0,
    width=240,
    height=240,
    offset_left=0,
    offset_top=0
)

img = Image.new('RGB', (240, 240), color=(255, 0, 0))
display.display(img)
print("If the display is red, it's working!")
TEST_PY
ok "test_display.py written"

# ── 11. Summary ───────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Setup Complete!                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Project location:${NC}  /root/gauge/"
echo -e "  ${CYAN}Run gauge:${NC}         /root/gauge/run.sh"
echo -e "  ${CYAN}Test display:${NC}      cd /root/gauge && source venv/bin/activate && python3 test_display.py"
echo -e "  ${CYAN}SSH hostname:${NC}      ssh root@bpi-temp-gauge.local"
echo ""
echo -e "  ${YELLOW}NOTE: A reboot is required for SPI to activate.${NC}"
echo ""

read -p "Reboot now? (y/n): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
  log "Rebooting..."
  reboot
else
  warn "Remember to reboot before using SPI: sudo reboot"
fi