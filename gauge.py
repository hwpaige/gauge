import pygame
import math
import os

# ── Config ────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 240, 240
FPS = 30

# Gauge layout — one each side of the display
LEFT_CENTER  = (60, 120)
RIGHT_CENTER = (180, 120)
GAUGE_RADIUS = 50
ARC_WIDTH    = 8

# Sweep angle config (degrees, clockwise from right)
ARC_START = 135   # bottom-left
ARC_END   = 405   # bottom-right (270° sweep)

# Colors
BG_COLOR      = (5, 5, 10)
TRACK_COLOR   = (40, 40, 50)
NORMAL_COLOR  = (0, 200, 120)
CAUTION_COLOR = (255, 180, 0)
DANGER_COLOR  = (220, 40, 40)
LABEL_COLOR   = (120, 120, 140)
BEZEL_COLOR   = (60, 60, 75)

# ── Fonts ─────────────────────────────────────────────────
FONT_DIR      = os.path.join(os.path.dirname(__file__), "fonts")
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def find_font(candidates):
    for name in candidates:
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path):
            return path
    return FALLBACK_FONT

FONT_BOLD      = find_font(["D-DIN-Bold.ttf"])
FONT_REGULAR   = find_font(["D-DIN.ttf"])
FONT_CONDENSED = find_font(["D-DINCondensed.ttf"])

# ── Helpers ───────────────────────────────────────────────
def deg_to_rad(deg):
    return math.radians(deg)

def draw_arc(surface, color, center, radius, start_deg, end_deg, width):
    """Draw a smooth thick arc using a filled polygon."""
    steps = max(60, int(abs(end_deg - start_deg)))
    points_outer = []
    points_inner = []
    for i in range(steps + 1):
        angle   = deg_to_rad(start_deg + (end_deg - start_deg) * i / steps)
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
    """Draw a single filled-arc CHT gauge."""
    x, y = center

    # Bezel ring
    pygame.draw.circle(surface, BEZEL_COLOR, center, GAUGE_RADIUS + 4, 2)

    # Background track
    draw_arc(surface, TRACK_COLOR, center, GAUGE_RADIUS, ARC_START, ARC_END, ARC_WIDTH)

    # Value arc
    pct        = max(0, min(1, (value - min_val) / (max_val - min_val)))
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

    # Unit label
    unit_surf = small_font.render("\u00b0C", True, LABEL_COLOR)
    unit_rect = unit_surf.get_rect(center=(x, y + 14))
    surface.blit(unit_surf, unit_rect)

    # Cylinder label
    label_surf = small_font.render(label, True, LABEL_COLOR)
    label_rect = label_surf.get_rect(center=(x, y + GAUGE_RADIUS + 10))
    surface.blit(label_surf, label_rect)

def draw_divider(surface, title_font):
    """Centre divider line and header title."""
    pygame.draw.line(surface, (40, 40, 55), (120, 30), (120, 210), 1)
    title = title_font.render("C Y L I N D E R   H E A D   T E M P", True, (70, 70, 90))
    surface.blit(title, title.get_rect(center=(120, 12)))

# ── Sensor read (stub — replace with real MAX31855 reads) ─
def read_cht(channel):
    """
    Stub function — returns simulated temperature.
    Replace with actual MAX31855 / MAX6675 SPI reads.
    channel: 0 = CYL1, 1 = CYL2
    """
    import time
    t = time.time()
    if channel == 0:
        return 150 + 80 * math.sin(t * 0.3)
    else:
        return 120 + 60 * math.sin(t * 0.2 + 1)

# ── Main ──────────────────────────────────────────────────
def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("CHT Gauge")
    clock = pygame.time.Clock()

    font       = pygame.font.Font(FONT_BOLD,      22)
    small_font = pygame.font.Font(FONT_REGULAR,   10)
    title_font = pygame.font.Font(FONT_CONDENSED,  9)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_q:
                running = False

        cht1 = read_cht(0)
        cht2 = read_cht(1)

        screen.fill(BG_COLOR)
        draw_divider(screen, title_font)
        draw_gauge(screen, LEFT_CENTER,  cht1, 0, 300, "CYL 1", font, small_font)
        draw_gauge(screen, RIGHT_CENTER, cht2, 0, 300, "CYL 2", font, small_font)
        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

if __name__ == "__main__":
    main()