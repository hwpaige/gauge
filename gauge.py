import pygame
import math
import os
from collections import deque
from datetime import datetime

# ── Config ────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 240, 240
FPS = 30

GAUGE_CENTER = (120, 120)
GAUGE_RADIUS = 118
ARC_WIDTH    = 13

# Left arc  — left side of circle, 105°→255°  (cold=bottom, hot=top)
LEFT_ARC_START  = 105
LEFT_ARC_END    = 255

# Right arc — right side of circle, 75°→-75°  (cold=bottom, hot=top)
RIGHT_ARC_START = 75
RIGHT_ARC_END   = -75

# Colors
BG_COLOR      = (5, 5, 10)
NORMAL_COLOR  = (0, 200, 120)
CAUTION_COLOR = (255, 180, 0)
DANGER_COLOR  = (220, 40, 40)
LABEL_COLOR   = (148, 148, 168)
ZONE_GREEN    = (8, 38, 20)
ZONE_AMBER    = (45, 33, 5)
ZONE_RED      = (48, 10, 10)

# Plot
PLOT_X1   = 55
PLOT_Y1   = 155
PLOT_X2   = 185
PLOT_Y2   = 210
PLOT_LEN  = 130          # samples (one per frame at 2-frame stride = ~8 s)

CYL1_LINE = (0,  210, 130)
CYL2_LINE = (80, 155, 255)

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
FONT_CONDENSED      = find_font(["D-DINCondensed.ttf"])
FONT_CONDENSED_BOLD = find_font(["D-DINCondensed-Bold.ttf"])

# ── Helpers ───────────────────────────────────────────────
def deg_to_rad(deg):
    return math.radians(deg)

def draw_arc(surface, color, center, radius, start_deg, end_deg, width):
    steps = max(180, int(abs(end_deg - start_deg) * 4))
    pts_outer, pts_inner = [], []
    for i in range(steps + 1):
        a = deg_to_rad(start_deg + (end_deg - start_deg) * i / steps)
        pts_outer.append((center[0] + radius           * math.cos(a),
                          center[1] + radius           * math.sin(a)))
        pts_inner.append((center[0] + (radius - width) * math.cos(a),
                          center[1] + (radius - width) * math.sin(a)))
    polygon = pts_outer + list(reversed(pts_inner))
    if len(polygon) > 2:
        pygame.draw.polygon(surface, color, polygon)

def _arc_color(pct):
    if pct < 0.6:    return NORMAL_COLOR
    elif pct < 0.85: return CAUTION_COLOR
    else:            return DANGER_COLOR

def _readout_pos(center, arc_start, arc_end, radius):
    x, y = center
    a = deg_to_rad((arc_start + arc_end) / 2)
    r = radius * 0.55
    return (int(x + r * math.cos(a)), int(y + r * math.sin(a)))

# ── Shape drawing (runs on supersampled surface) ──────────
def draw_gauge_shapes(surface, center, value, min_val, max_val,
                      arc_start, arc_end, radius, arc_width):
    x, y  = center
    pct   = max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))
    scale = radius / GAUGE_RADIUS

    p60 = arc_start + 0.60 * (arc_end - arc_start)
    p85 = arc_start + 0.85 * (arc_end - arc_start)
    draw_arc(surface, ZONE_GREEN, center, radius, arc_start, p60,     arc_width)
    draw_arc(surface, ZONE_AMBER, center, radius, p60,       p85,     arc_width)
    draw_arc(surface, ZONE_RED,   center, radius, p85,       arc_end, arc_width)

    if pct > 0:
        draw_arc(surface, _arc_color(pct), center, radius,
                 arc_start, arc_start + pct * (arc_end - arc_start), arc_width)

    outer_r = radius - arc_width - 2 * scale
    for i in range(11):
        ta      = deg_to_rad(arc_start + (i / 10) * (arc_end - arc_start))
        major   = (i % 5 == 0)
        inner_r = outer_r - (9 * scale if major else 4 * scale)
        color   = (170, 170, 185) if major else (60, 60, 75)
        lw      = max(1, round(2 * scale)) if major else max(1, round(scale))
        pygame.draw.line(surface, color,
                         (x + outer_r * math.cos(ta), y + outer_r * math.sin(ta)),
                         (x + inner_r * math.cos(ta), y + inner_r * math.sin(ta)),
                         lw)

# ── Text drawing (runs on native-resolution surface) ──────
def draw_gauge_text(surface, center, value, min_val, max_val,
                    label, font, small_font, arc_start, arc_end):
    pct    = max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))
    rx, ry = _readout_pos(center, arc_start, arc_end, GAUGE_RADIUS)
    col    = _arc_color(pct)

    lbl  = small_font.render(label, True, LABEL_COLOR)
    val  = font.render(str(int(value)), True, col)
    unit = small_font.render("°C", True, LABEL_COLOR)

    surface.blit(lbl,  lbl.get_rect( center=(rx, ry - int(GAUGE_RADIUS * 0.19))))
    surface.blit(val,  val.get_rect( center=(rx, ry + int(GAUGE_RADIUS * 0.02))))
    surface.blit(unit, unit.get_rect(center=(rx, ry + int(GAUGE_RADIUS * 0.21))))

def draw_header(surface, font):
    t = font.render("C H T", True, (118, 118, 138))
    surface.blit(t, t.get_rect(center=(SCREEN_W // 2, SCREEN_H // 16)))

def draw_legend(surface, font, scale=1):
    y       = int((PLOT_Y2 - 5) * scale)
    bar_w   = max(8,  int(10 * scale))
    bar_h   = max(1,  int(2  * scale))
    gap     = max(2,  int(3  * scale))   # bar → label
    spacing = max(4,  int(5  * scale))   # item → item

    lbl1 = font.render("CYL 1", True, LABEL_COLOR)
    lbl2 = font.render("CYL 2", True, LABEL_COLOR)

    item1_w = bar_w + gap + lbl1.get_width()
    item2_w = bar_w + gap + lbl2.get_width()
    x       = SCREEN_W // 2 - (item1_w + spacing + item2_w) // 2

    pygame.draw.rect(surface, CYL1_LINE, (x,                    y - bar_h // 2, bar_w, bar_h))
    surface.blit(lbl1, lbl1.get_rect(midleft=(x + bar_w + gap, y)))

    x2 = x + item1_w + spacing
    pygame.draw.rect(surface, CYL2_LINE, (x2,                   y - bar_h // 2, bar_w, bar_h))
    surface.blit(lbl2, lbl2.get_rect(midleft=(x2 + bar_w + gap, y)))

def draw_time(surface, font):
    surf = font.render(datetime.now().strftime("%H:%M"), True, (255, 255, 255))
    surface.blit(surf, surf.get_rect(center=(SCREEN_W // 2, SCREEN_H - 16)))

def _thick_line(surface, color, pts, width):
    """Draw a filled-polygon ribbon along pts at the given pixel width."""
    if len(pts) < 2:
        return
    upper, lower = [], []
    for i, (x, y) in enumerate(pts):
        if i == 0:
            dx, dy = pts[1][0] - x, pts[1][1] - y
        elif i == len(pts) - 1:
            dx, dy = x - pts[-2][0], y - pts[-2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln, dx / ln
        hw = width / 2.0
        upper.append((x + nx * hw, y + ny * hw))
        lower.append((x - nx * hw, y - ny * hw))
    polygon = upper + lower[::-1]
    if len(polygon) > 2:
        pygame.draw.polygon(surface, color, polygon)

def _draw_dashed_hline(surface, color, x1, x2, y, dash, gap, thick=1):
    x = x1
    while x < x2:
        pygame.draw.line(surface, color, (x, y), (min(x + dash, x2), y), thick)
        x += dash + gap

def _zone_colors(v, min_val, max_val, normal_line, normal_glow):
    pct = max(0.0, min(1.0, (v - min_val) / (max_val - min_val)))
    if pct < 0.60:   return normal_line, normal_glow
    elif pct < 0.85: return CAUTION_COLOR, (70, 50, 0)
    else:            return DANGER_COLOR,  (70, 12, 12)

def _draw_zone_line(surface, hist, pts, min_val, max_val,
                    normal_line, normal_glow, lw, gw):
    """Draw hist as a thick line. Zone-color changes are prepared but disabled."""
    n = len(pts)
    if n < 2:
        return
    # Uncomment to re-enable zone-aware color changes (green→amber→red):
    # runs = []
    # for i, v in enumerate(hist):
    #     lc, gc = _zone_colors(v, min_val, max_val, normal_line, normal_glow)
    #     if not runs or runs[-1][0] != lc:
    #         runs.append([lc, gc, [i]])
    #     else:
    #         runs[-1][2].append(i)
    # for ri, (lc, gc, idxs) in enumerate(runs):
    #     if ri < len(runs) - 1:
    #         idxs = idxs + [runs[ri + 1][2][0]]
    #     run_pts = [pts[j] for j in idxs]
    #     if len(run_pts) >= 2:
    #         _thick_line(surface, gc, run_pts, gw)
    #         _thick_line(surface, lc, run_pts, lw)
    _thick_line(surface, normal_glow, pts, gw)
    _thick_line(surface, normal_line, pts, lw)


def draw_plot(surface, hist1, hist2, min_val=0, max_val=300, scale=1):
    if len(hist1) < 2:
        return
    x1 = int(PLOT_X1 * scale)
    y1 = int(PLOT_Y1 * scale)
    x2 = int(PLOT_X2 * scale)
    y2 = int(PLOT_Y2 * scale)
    pw, ph = x2 - x1, y2 - y1
    n = len(hist1)

    def make_pts(hist):
        return [
            (x1 + int(i * pw / max(n - 1, 1)),
             y2 - int(max(0.0, min(1.0, (v - min_val) / (max_val - min_val))) * ph))
            for i, v in enumerate(hist)
        ]

    # Faint dashed threshold lines
    dash  = max(3, int(4 * scale))
    gap   = max(2, int(3 * scale))
    thick = max(1, int(scale))
    _draw_dashed_hline(surface, (120, 88,  8), x1, x2, y2 - int(0.60 * ph), dash, gap, thick)
    _draw_dashed_hline(surface, (115, 22, 22), x1, x2, y2 - int(0.85 * ph), dash, gap, thick)

    pts1 = make_pts(hist1)
    pts2 = make_pts(hist2)
    lw   = max(2, round(2.5 * scale))
    gw   = lw * 2

    _draw_zone_line(surface, hist1, pts1, min_val, max_val,
                    CYL1_LINE, (0,  85,  50), lw, gw)
    _draw_zone_line(surface, hist2, pts2, min_val, max_val,
                    CYL2_LINE, (28, 62, 110), lw, gw)

# ── Sensor stub ───────────────────────────────────────────
def read_cht(channel):
    import time
    t = time.time()
    if channel == 0:
        return 150 + 80 * math.sin(t * 0.3)
    else:
        return 120 + 60 * math.sin(t * 0.2 + 1)

# ── Main ──────────────────────────────────────────────────
def main():
    from driver import ST7789
    import PIL.Image

    # No framebuffer needed — pygame renders offscreen, frames pushed via SPI
    os.environ.setdefault("SDL_VIDEODRIVER", "offscreen")
    pygame.init()
    screen = pygame.Surface((SCREEN_W, SCREEN_H))
    clock  = pygame.time.Clock()

    # Pin 18 = PI16 (272), Pin 22 = PC2 (66) — per WiringPi phyToGpio table for BPI M4 Zero
    disp = ST7789(dc=272, rst=66,
                  width=SCREEN_W, height=SCREEN_H,
                  speed_hz=64_000_000, y_off=40)

    font       = pygame.font.Font(FONT_BOLD,            26)
    small_font = pygame.font.Font(FONT_REGULAR,         12)
    title_font = pygame.font.Font(FONT_CONDENSED,       11)
    time_font  = pygame.font.Font(FONT_CONDENSED_BOLD,  14)

    SSAA  = 2
    ss    = pygame.Surface((SCREEN_W * SSAA, SCREEN_H * SSAA))
    gc_ss = (GAUGE_CENTER[0] * SSAA, GAUGE_CENTER[1] * SSAA)
    gr_ss = GAUGE_RADIUS * SSAA
    aw_ss = ARC_WIDTH    * SSAA

    hist1 = deque([150.0] * PLOT_LEN, maxlen=PLOT_LEN)
    hist2 = deque([150.0] * PLOT_LEN, maxlen=PLOT_LEN)
    frame = 0

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_q:
                running = False

        cht1 = read_cht(0)
        cht2 = read_cht(1)

        frame += 1
        if frame % 2 == 0:
            hist1.append(cht1)
            hist2.append(cht2)

        # Shapes + plot at 2× → smoothscale gives AA
        ss.fill(BG_COLOR)
        draw_gauge_shapes(ss, gc_ss, cht1, 0, 300, LEFT_ARC_START,  LEFT_ARC_END,  gr_ss, aw_ss)
        draw_gauge_shapes(ss, gc_ss, cht2, 0, 300, RIGHT_ARC_START, RIGHT_ARC_END, gr_ss, aw_ss)
        draw_plot(ss, hist1, hist2, scale=SSAA)
        screen.blit(pygame.transform.smoothscale(ss, (SCREEN_W, SCREEN_H)), (0, 0))

        # Text at native resolution
        draw_header(screen, title_font)
        draw_gauge_text(screen, GAUGE_CENTER, cht1, 0, 300, "CYL 1", font, small_font, LEFT_ARC_START,  LEFT_ARC_END)
        draw_gauge_text(screen, GAUGE_CENTER, cht2, 0, 300, "CYL 2", font, small_font, RIGHT_ARC_START, RIGHT_ARC_END)
        draw_legend(screen, title_font)
        draw_time(screen, time_font)

        disp.display(PIL.Image.frombytes(
            'RGB', (SCREEN_W, SCREEN_H),
            pygame.image.tostring(screen, 'RGB')))
        clock.tick(FPS)

    pygame.quit()

if __name__ == "__main__":
    main()
