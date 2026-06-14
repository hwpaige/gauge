"""
dev_preview.py — Windows desktop preview of the CHT gauge UI.

Simulates the 240×240 round ST7789 display at 2× scale with a circular bezel.
Imports gauge.py directly — any change to gauge.py is reflected on next run.

Run:  python dev_preview.py
Keys: Q / Esc   — quit
      Up / Down — scrub CYL 1 temp (manual mode)
      W / S     — scrub CYL 2 temp (manual mode)
      Space     — toggle simulated / manual mode
"""

import pygame
import sys
import os
from collections import deque

sys.path.insert(0, os.path.dirname(__file__))
import gauge as g

# ── Preview config ────────────────────────────────────────
SCALE    = 2
SSAA     = 2

DISPLAY_PX  = g.SCREEN_W * SCALE
SS_PX       = DISPLAY_PX * SSAA
BEZEL_R     = DISPLAY_PX // 2
BEZEL_PAD   = 10            # margin around the gauge circle
WIN_W       = DISPLAY_PX + 2 * BEZEL_PAD
WIN_H       = WIN_W + 22    # extra strip at bottom for HUD text
CX          = WIN_W // 2    # gauge centre in window coords
CY          = WIN_W // 2
GAUGE_BX    = CX - BEZEL_R  # where to blit the 480×480 gauge surface
GAUGE_BY    = CY - BEZEL_R


# ── Helpers ───────────────────────────────────────────────
def make_round_mask(size):
    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    mask.fill((0, 0, 0, 0))
    pygame.draw.circle(mask, (255, 255, 255, 255), (size // 2, size // 2), size // 2)
    return mask

def apply_round_clip(surface, mask):
    result = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    result.blit(surface, (0, 0))
    result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return result

def draw_bezel(window, cx, cy):
    pygame.draw.circle(window, (180, 30, 30), (cx, cy), BEZEL_R + 1, 2)

def draw_hud(window, font, mode, cht1, cht2):
    line = (f"{'MANUAL' if mode == 'manual' else 'SIM '}  "
            f"CYL1:{cht1:.0f}°  CYL2:{cht2:.0f}°  "
            f"↑↓ CYL1  W/S CYL2  Space: mode  Q: quit")
    window.blit(font.render(line, True, (60, 60, 78)), (6, WIN_W + 4))

# ── Main ──────────────────────────────────────────────────
def main():
    pygame.init()
    window = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("CHT Gauge — Dev Preview")
    clock = pygame.time.Clock()

    font       = pygame.font.Font(g.FONT_BOLD,            26 * SCALE)
    small_font = pygame.font.Font(g.FONT_REGULAR,         12 * SCALE)
    title_font = pygame.font.Font(g.FONT_CONDENSED,       11 * SCALE)
    time_font  = pygame.font.Font(g.FONT_CONDENSED_BOLD,  14 * SCALE)
    hud_font   = pygame.font.SysFont("consolas", 11)

    display_surf = pygame.Surface((DISPLAY_PX, DISPLAY_PX))
    ss_surf      = pygame.Surface((SS_PX, SS_PX))
    round_mask   = make_round_mask(DISPLAY_PX)

    gc_display = (g.GAUGE_CENTER[0] * SCALE,        g.GAUGE_CENTER[1] * SCALE)
    gc_ss      = (g.GAUGE_CENTER[0] * SCALE * SSAA, g.GAUGE_CENTER[1] * SCALE * SSAA)
    gr_display = g.GAUGE_RADIUS * SCALE
    gr_ss      = g.GAUGE_RADIUS * SCALE * SSAA
    aw_ss      = g.ARC_WIDTH    * SCALE * SSAA

    hist1 = deque([150.0] * g.PLOT_LEN, maxlen=g.PLOT_LEN)
    hist2 = deque([150.0] * g.PLOT_LEN, maxlen=g.PLOT_LEN)
    frame = 0

    mode = "sim"
    cht1 = 150.0
    cht2 = 120.0
    STEP = 10.0

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                if event.key == pygame.K_SPACE:
                    mode = "manual" if mode == "sim" else "sim"
                if mode == "manual":
                    if event.key == pygame.K_UP:    cht1 = min(300, cht1 + STEP)
                    if event.key == pygame.K_DOWN:  cht1 = max(0,   cht1 - STEP)
                    if event.key == pygame.K_w:     cht2 = min(300, cht2 + STEP)
                    if event.key == pygame.K_s:     cht2 = max(0,   cht2 - STEP)

        if mode == "sim":
            cht1 = g.read_cht(0)
            cht2 = g.read_cht(1)

        frame += 1
        if frame % 2 == 0:
            hist1.append(cht1)
            hist2.append(cht2)

        # ── 1. Shapes + plot at SS_PX, smoothscale to DISPLAY_PX ─
        ss_surf.fill(g.BG_COLOR)
        g.draw_gauge_shapes(ss_surf, gc_ss, cht1, 0, 300,
                            g.LEFT_ARC_START,  g.LEFT_ARC_END,  gr_ss, aw_ss)
        g.draw_gauge_shapes(ss_surf, gc_ss, cht2, 0, 300,
                            g.RIGHT_ARC_START, g.RIGHT_ARC_END, gr_ss, aw_ss)
        g.draw_plot(ss_surf, hist1, hist2, scale=SCALE * SSAA)
        display_surf.blit(
            pygame.transform.smoothscale(ss_surf, (DISPLAY_PX, DISPLAY_PX)), (0, 0))

        # ── 2. Text at DISPLAY_PX (crisp) ────────────────────────
        g.GAUGE_RADIUS = gr_display
        g.GAUGE_CENTER = gc_display
        g.SCREEN_W     = DISPLAY_PX
        g.SCREEN_H     = DISPLAY_PX

        g.draw_header(display_surf, title_font)
        g.draw_gauge_text(display_surf, gc_display, cht1, 0, 300,
                          "CYL 1", font, small_font, g.LEFT_ARC_START,  g.LEFT_ARC_END)
        g.draw_gauge_text(display_surf, gc_display, cht2, 0, 300,
                          "CYL 2", font, small_font, g.RIGHT_ARC_START, g.RIGHT_ARC_END)
        g.draw_legend(display_surf, title_font, scale=SCALE)
        g.draw_time(display_surf, time_font)

        g.GAUGE_RADIUS = gr_display // SCALE
        g.GAUGE_CENTER = (gc_display[0] // SCALE, gc_display[1] // SCALE)
        g.SCREEN_W     = DISPLAY_PX // SCALE
        g.SCREEN_H     = DISPLAY_PX // SCALE

        # ── 3. Round clip + composite onto window ─────────────────
        clipped = apply_round_clip(display_surf, round_mask)
        window.fill((10, 10, 14))
        draw_bezel(window, CX, CY)
        window.blit(clipped, (GAUGE_BX, GAUGE_BY))
        draw_hud(window, hud_font, mode, cht1, cht2)

        pygame.display.flip()
        clock.tick(g.FPS)

    pygame.quit()

if __name__ == "__main__":
    main()
