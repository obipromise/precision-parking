"""
Precision Parking - Python edition
A driving-school parking simulator, by Obi Promise (CSC 131).

Top-down "drone view" game written with pygame-ce. Runs on desktop
(python main.py) and in a phone browser when packaged with pygbag.

Code map
  1. Setup, helpers, smooth (anti-aliased) drawing
  2. Sprites: cars, trees, steering wheel, light glows
  3. Collision geometry (oriented boxes + Separating Axis Theorem)
  4. Level builder and the 8 lessons
  5. Baking a level into one background image
  6. Computer driver (ghost car on a Catmull-Rom spline)
  7. Sound (generated in code, no audio files)
  8. Game class: physics, input, HUD, menus, main loop
"""
import asyncio
import array
import json
import math
import os
import random
import sys

import pygame as pg
from pygame import gfxdraw

# ------------------------------------------------------------------ 1. setup
WEB = sys.platform == "emscripten"
W, H = 1280, 720
PX = 30                     # pixels per metre on screen
PI, R2 = math.pi, math.pi / 2
HERE = os.path.dirname(os.path.abspath(__file__))

pg.mixer.pre_init(22050, -16, 1, 512)
pg.init()
screen = pg.display.set_mode((W, H)) if WEB else pg.display.set_mode((W, H), pg.SCALED | pg.RESIZABLE)
pg.display.set_caption("Precision Parking")
clock = pg.time.Clock()

def font(size):
    return pg.font.Font(None, size)
F = {k: font(v) for k, v in dict(xs=20, s=24, m=30, l=40, xl=64, xxl=110).items()}

INK = (238, 241, 244)
MUTED = (147, 160, 171)
SIGN = (31, 99, 214)
SIGN_HI = (74, 138, 242)
PAINT = (242, 194, 48)
GO = (56, 208, 124)
STOP = (255, 90, 82)
PANEL = (15, 20, 26)


def clamp(v, a, b):
    return a if v < a else b if v > b else v


def wrap(a):
    return (a + PI) % (2 * PI) - PI


def shade(c, f):
    return tuple(clamp(int(v * f), 0, 255) for v in c[:3])


def poly(surf, color, pts):
    """Filled polygon with anti-aliased edges."""
    ip = [(int(round(x)), int(round(y))) for x, y in pts]
    if len(ip) < 3:
        return
    gfxdraw.filled_polygon(surf, ip, color)
    gfxdraw.aapolygon(surf, ip, color)


def circle(surf, color, x, y, r):
    x, y, r = int(round(x)), int(round(y)), max(1, int(round(r)))
    gfxdraw.filled_circle(surf, x, y, r, color)
    gfxdraw.aacircle(surf, x, y, r, color)


def superellipse(cx, cy, a, b, n=3.5, taper=0.0, steps=64):
    """Rounded-rectangle-like curve: n=2 is an ellipse, bigger n is boxier."""
    pts = []
    for i in range(steps):
        t = 2 * PI * i / steps
        c, s = math.cos(t), math.sin(t)
        x = a * math.copysign(abs(c) ** (2 / n), c)
        y = b * math.copysign(abs(s) ** (2 / n), s)
        if taper and y > 0:
            x *= 1 - taper * (y / b) ** 2
        pts.append((cx + x, cy - y))
    return pts


def rounded_rect_local(hx, hz, r, n=5):
    r = min(r, hx, hz)
    pts = []
    for (sx, sz, a0) in ((1, 1, 0), (-1, 1, R2), (-1, -1, PI), (1, -1, 3 * R2)):
        cx, cz = sx * (hx - r), sz * (hz - r)
        for k in range(n + 1):
            a = a0 + k * R2 / n
            pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    return pts


def hull(points):
    pts = sorted(set((round(x, 2), round(y, 2)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


PANEL_CACHE, TEXT_CACHE = {}, {}


def panel(surf, rect, alpha=215, radius=14, color=PANEL, border=True):
    r = pg.Rect(rect)
    key = (r.size, alpha, radius, color, border)
    s = PANEL_CACHE.get(key)
    if s is None:
        if len(PANEL_CACHE) > 200:
            PANEL_CACHE.clear()
        s = pg.Surface(r.size, pg.SRCALPHA)
        pg.draw.rect(s, (*color, alpha), s.get_rect(), border_radius=radius)
        if border:
            pg.draw.rect(s, (255, 255, 255, 28), s.get_rect(), 1, border_radius=radius)
        PANEL_CACHE[key] = s
    surf.blit(s, r.topleft)


def text(surf, s, f, color, pos, anchor="topleft"):
    key = (str(s), f, color)
    img = TEXT_CACHE.get(key)
    if img is None:
        if len(TEXT_CACHE) > 600:
            TEXT_CACHE.clear()
        img = TEXT_CACHE[key] = F[f].render(str(s), True, color)
    r = img.get_rect(**{anchor: pos})
    surf.blit(img, r)
    return r


# ------------------------------------------------------------------ 2. sprites
SS = 3  # supersampling factor for sprites: draw big, shrink smoothly


CAR_KINDS = {
    #         body a, b, n, taper | glass cy, a, b, n | roof cy, a, b, n
    "sedan": ((0.885, 2.175, 3.0, 0.09), (-0.2, 0.79, 1.22, 3.0), (-0.33, 0.68, 0.74, 3.4)),
    "suv":   ((0.93, 2.2, 3.8, 0.05), (-0.38, 0.85, 1.5, 3.8), (-0.48, 0.77, 1.12, 4.2)),
    "hatch": ((0.87, 2.08, 3.2, 0.1), (-0.48, 0.79, 1.3, 3.2), (-0.62, 0.67, 0.86, 3.6)),
}


def make_car(color, plate=None, ghost=False, scale=PX, kind="sedan", front_tires=True):
    """Top-down car drawn from smooth superellipse curves at 3x size, then shrunk
    for clean anti-aliased edges. Layered shading gives the body a rounded look."""
    K = scale * SS
    w, l = int(2.3 * K), int(4.7 * K)
    s = pg.Surface((w, l), pg.SRCALPHA)
    cx, cy = w / 2, l / 2
    (ba, bb, bn, bt), (gy, ga, gb, gn), (ry, ra, rb, rn) = CAR_KINDS[kind]

    def se(x, y, a, b, n, col, taper=0.0, surf=s):
        poly(surf, col, superellipse(cx + x * K, cy - y * K, a * K, b * K, n, taper))

    def layer(alpha, fn):
        t = pg.Surface((w, l), pg.SRCALPHA)
        fn(t)
        t.set_alpha(alpha)
        s.blit(t, (0, 0))

    def P(x, y):
        return cx + x * K, cy - y * K
    body, light = color, shade(color, 1.3)
    for sx in (-1, 1):
        for sy in ((-1, 1) if front_tires else (-1,)):
            se(sx * 0.81, sy * 1.325, 0.14, 0.34, 4, (18, 18, 20))                     # tyres
    se(0, 0, ba + 0.05, bb + 0.05, bn, shade(color, 0.45), bt)                      # body edge
    se(0, 0, ba, bb, bn, body, bt)                                                  # paint
    layer(110, lambda t: se(0, 0.05, ba - 0.2, bb - 0.28, bn, (*shade(color, 1.16), 255), bt, t))  # rounded volume
    layer(80, lambda t: se(-0.3, 0.3, 0.22, bb - 0.6, 2.2, (*light, 255), 0, t))   # gloss streak
    pg.draw.aaline(s, shade(color, 0.82), P(0, bb - 0.2), P(0, gy + gb + 0.15))     # hood centre crease
    se(0, gy, ga, gb, gn, (22, 28, 37), 0.12)                                       # glasshouse
    layer(60, lambda t: se(-0.22, gy + gb - 0.32, ga * 0.5, 0.14, 2.2, (200, 220, 240, 255), 0, t))  # windscreen glint
    layer(40, lambda t: se(0.25, gy - gb + 0.25, ga * 0.4, 0.1, 2.2, (200, 220, 240, 255), 0, t))   # rear glass glint
    se(0, ry, ra, rb, rn, shade(color, 1.06))                                       # roof
    layer(70, lambda t: se(-0.12, ry + 0.1, ra * 0.55, rb * 0.7, 2.4, (*light, 255), 0, t))  # roof sheen
    pw = max(2, int(0.07 * K))
    for sx in (-1, 1):
        pg.draw.line(s, body, P(sx * ra * 0.92, ry + rb * 0.85), P(sx * ga * 0.96, gy + gb * 0.62), pw)   # A-pillars
        pg.draw.line(s, body, P(sx * ra * 0.92, ry - rb * 0.85), P(sx * ga * 0.96, gy - gb * 0.68), pw)   # C-pillars
        pg.draw.line(s, body, P(sx * ra, ry + 0.05), P(sx * ga, ry + 0.05), pw)                          # B-pillars
        pg.draw.line(s, (12, 12, 14), P(sx * 0.05, gy + gb - 0.2), P(sx * 0.55, gy + gb - 0.12), max(1, pw // 2))  # wipers
        se(sx * (ba + 0.06), 0.55, 0.1, 0.07, 2.4, shade(color, 0.6))               # mirrors
        se(sx * 0.55, bb - 0.2, 0.22, 0.085, 3, (170, 175, 180))                    # headlight chrome
        se(sx * 0.55, bb - 0.2, 0.18, 0.06, 3, (250, 247, 228))                     # headlight lens
        se(sx * 0.6, -bb + 0.09, 0.24, 0.07, 3, (110, 12, 12))                      # tail light
        se(sx * 0.6, -bb + 0.1, 0.17, 0.04, 3, (200, 30, 26))
    if kind == "suv":
        for sx in (-1, 1):
            pg.draw.line(s, (40, 42, 46), P(sx * ra * 0.8, ry + rb * 0.7), P(sx * ra * 0.8, ry - rb * 0.7), max(2, pw // 2))  # roof rails
    if plate:
        pr = pg.Rect(0, 0, int(0.56 * K), int(0.15 * K))
        pr.center = (int(cx), int(cy + (bb - 0.18) * K))
        pg.draw.rect(s, (240, 240, 232), pr, border_radius=int(0.03 * K))
        t = pg.font.Font(None, int(0.17 * K)).render(plate, True, (30, 40, 70))
        s.blit(t, t.get_rect(center=pr.center))
    out = pg.transform.smoothscale(s, (w // SS, l // SS))
    if ghost:
        out.fill((90, 170, 255, 255), special_flags=pg.BLEND_RGBA_MULT)
        out.set_alpha(120)
    return out


def make_shadow(scale=PX):
    """Soft blurred car shadow: draw tiny, then scale up (cheap blur)."""
    small = pg.Surface((24, 44), pg.SRCALPHA)
    pg.draw.rect(small, (0, 0, 0, 120), (3, 3, 18, 38), border_radius=7)
    return pg.transform.smoothscale(small, (int(2.4 * scale), int(4.9 * scale)))


def make_glow(radius, color=(255, 255, 255)):
    g = pg.Surface((radius * 2, radius * 2), pg.SRCALPHA)
    for i in range(radius, 0, -2):
        a = int(255 * (1 - i / radius) ** 1.6)
        pg.draw.circle(g, (*color, a), (radius, radius), i)
    return g


def make_tree(rng, size, scale=PX):
    K = scale * SS
    r = 1.5 * size
    d = int(2 * (r + 0.6) * K)
    s = pg.Surface((d, d), pg.SRCALPHA)
    c = d / 2
    base = rng.choice([(56, 98, 48), (64, 106, 52), (50, 90, 56)])
    blobs = [(0, 0, r * 0.8)] + [(math.cos(a) * r * 0.45, math.sin(a) * r * 0.45, r * (0.45 + rng.random() * 0.15))
                                 for a in [rng.random() * 6.28 + k * 1.25 for k in range(5)]]
    for x, y, br in blobs:
        circle(s, shade(base, 0.75), c + x * K, c + y * K, br * K)
    for x, y, br in blobs:
        circle(s, base, c + x * K - 0.12 * K, c + y * K - 0.12 * K, br * K * 0.88)
    for x, y, br in blobs[1:]:
        circle(s, shade(base, 1.28), c + x * K - 0.3 * K, c + y * K - 0.3 * K, br * K * 0.38)
    return pg.transform.smoothscale(s, (d // SS, d // SS))


TREE_CACHE = {}


def tree_sprite(rng, size):
    key = (round(size, 1), rng.randrange(4))
    if key not in TREE_CACHE:
        TREE_CACHE[key] = make_tree(random.Random(hash(key)), key[0])
    return TREE_CACHE[key]


def make_vignette():
    v = pg.Surface((64, 36), pg.SRCALPHA)
    for y in range(36):
        for x in range(64):
            d = math.hypot((x - 31.5) / 32, (y - 17.5) / 18)
            v.set_at((x, y), (0, 0, 0, int(clamp((d - 0.55) * 170, 0, 110))))
    return pg.transform.smoothscale(v, (W, H))


def make_wheel(size):
    K = 3
    d = size * K
    s = pg.Surface((d, d), pg.SRCALPHA)
    c = d / 2
    circle(s, (16, 18, 21), c, c, d * 0.48)
    circle(s, (44, 50, 56), c, c, d * 0.44)
    circle(s, (0, 0, 0, 0), c, c, d * 0.36)
    gfxdraw.filled_circle(s, int(c), int(c), int(d * 0.36), (0, 0, 0, 0))
    pg.draw.circle(s, (0, 0, 0, 0), (int(c), int(c)), int(d * 0.36))
    for ang in (PI, 0, R2):
        x2, y2 = c + math.cos(ang) * d * 0.4, c + math.sin(ang) * d * 0.4
        pg.draw.line(s, (34, 39, 44), (c, c), (x2, y2), int(d * 0.09))
    circle(s, (27, 31, 36), c, c, d * 0.15)
    pg.draw.arc(s, PAINT, (c - d * 0.46, c - d * 0.46, d * 0.92, d * 0.92), R2 - 0.18, R2 + 0.18, int(d * 0.06))
    t = pg.font.Font(None, int(d * 0.22)).render("P", True, SIGN_HI)
    s.blit(t, t.get_rect(center=(c, c)))
    return pg.transform.smoothscale(s, (size, size))


# ------------------------------------------------------------------ 3. collision
class OBB:
    """Oriented box on the ground plane. Local z = the box's 'forward'."""
    __slots__ = ("x", "z", "hx", "hz", "a", "c", "s", "r")

    def __init__(self, x, z, hx, hz, a=0.0):
        self.hx, self.hz = hx, hz
        self.r = math.hypot(hx, hz)
        self.set(x, z, a)

    def set(self, x, z, a):
        self.x, self.z, self.a = x, z, a
        self.c, self.s = math.cos(a), math.sin(a)

    def to_world(self, lx, lz):
        return self.x + lx * self.c + lz * self.s, self.z - lx * self.s + lz * self.c

    def local(self, px, pz):
        dx, dz = px - self.x, pz - self.z
        return dx * self.c - dz * self.s, dx * self.s + dz * self.c

    def dist(self, px, pz):
        lx, lz = self.local(px, pz)
        return math.hypot(max(abs(lx) - self.hx, 0), max(abs(lz) - self.hz, 0))


def sat(A, B):
    """Separating Axis Theorem. Returns (depth, nx, nz) with n pointing A->B, or None."""
    dx, dz = B.x - A.x, B.z - A.z
    if dx * dx + dz * dz > (A.r + B.r) ** 2:
        return None
    best, bn = 1e9, (0, 0)
    for ax, az in ((A.c, -A.s), (A.s, A.c), (B.c, -B.s), (B.s, B.c)):
        ra = A.hx * abs(A.c * ax - A.s * az) + A.hz * abs(A.s * ax + A.c * az)
        rb = B.hx * abs(B.c * ax - B.s * az) + B.hz * abs(B.s * ax + B.c * az)
        d = dx * ax + dz * az
        o = ra + rb - abs(d)
        if o <= 0:
            return None
        if o < best:
            best, bn = o, ((ax, az) if d >= 0 else (-ax, -az))
    return best, bn[0], bn[1]


# ------------------------------------------------------------------ 4. levels
CAR_COLORS = [(233, 233, 230), (34, 35, 38), (141, 147, 154), (59, 68, 82), (122, 31, 36),
              (42, 74, 122), (191, 182, 163), (47, 93, 80), (154, 163, 171), (90, 46, 42)]
COL = dict(barrier=(190, 188, 180), wall=(128, 130, 134), concrete=(176, 175, 168), house=(214, 206, 192),
           pillar=(140, 142, 146), bin=(47, 93, 58), fence=(138, 106, 74), red=(179, 34, 28), pole=(93, 100, 107))


class Builder:
    def __init__(self, seed):
        self.rand = random.Random(seed)
        self.obbs, self.cones, self.items, self.lamps = [], [], [], []

    def box(self, x, z, sx, sz, a=0.0, kind="wall", h=1.0, collide=True):
        self.items.append(("box", x, z, sx / 2, sz / 2, a, kind, h))
        if collide:
            self.obbs.append(OBB(x, z, sx / 2, sz / 2, a))

    def seg(self, x1, z1, x2, z2, t, kind, h=1.0, collide=True):
        dx, dz = x2 - x1, z2 - z1
        self.box((x1 + x2) / 2, (z1 + z2) / 2, t, math.hypot(dx, dz), math.atan2(dx, dz), kind, h, collide)

    def paint(self, x1, z1, x2, z2, w=0.12, col=(241, 241, 234)):
        self.items.append(("line", x1, z1, x2, z2, w, col))

    def dashed(self, x1, z1, x2, z2, dash=3, gap=3, w=0.14, col=PAINT):
        L = math.hypot(x2 - x1, z2 - z1)
        ux, uz = (x2 - x1) / L, (z2 - z1) / L
        d = 0
        while d < L:
            e = min(L, d + dash)
            self.paint(x1 + ux * d, z1 + uz * d, x1 + ux * e, z1 + uz * e, w, col)
            d += dash + gap

    @staticmethod
    def tf(x, z, a):
        c, s = math.cos(a), math.sin(a)
        return lambda lx, lz: (x + lx * c + lz * s, z - lx * s + lz * c)

    def bay(self, x, z, a, w, l):
        P, hw, hl = self.tf(x, z, a), w / 2, l / 2
        for sx in (-1, 1):
            self.paint(*P(sx * hw, -hl), *P(sx * hw, hl))
        self.paint(*P(-hw, hl), *P(hw, hl))
        self.items.append(("stop", *P(0, hl - 0.45), a, min(0.85, hw - 0.3)))
        if self.rand.random() < 0.6:
            self.items.append(("stain", *P((self.rand.random() - 0.5) * 0.8, (self.rand.random() - 0.3) * l * 0.5), 0.3 + self.rand.random() * 0.45))

    def arrow(self, x, z, a, col=(241, 241, 234)):
        P = self.tf(x, z, a)
        self.paint(*P(0, -1.4), *P(0, 0.9), 0.2, col)
        for sx in (-1, 1):
            self.paint(*P(0, 1.4), *P(sx * 0.6, 0.6), 0.2, col)

    def car(self, x, z, a, color=None):
        self.items.append(("car", x, z, a, color or self.rand.choice(CAR_COLORS), self.rand.choice(("sedan", "sedan", "suv", "hatch"))))
        self.obbs.append(OBB(x, z, 0.935, 2.22, a))

    def cone(self, x, z):
        self.cones.append(dict(x=x, z=z, vx=0.0, vz=0.0, hit=False, tip=0.0, dir=0.0))

    def tree(self, x, z, s=1.0):
        self.items.append(("tree", x, z, s))
        self.obbs.append(OBB(x, z, 0.2 * s, 0.2 * s))

    def lamp(self, x, z):
        self.items.append(("lamp", x, z))
        self.obbs.append(OBB(x, z, 0.12, 0.12))
        self.lamps.append((x, z))

    def psign(self, x, z):
        self.items.append(("psign", x, z))
        self.obbs.append(OBB(x, z, 0.08, 0.08))

    def building(self, x, z, sx, sz, h, sign=None):
        self.items.append(("building", x, z, sx / 2, sz / 2, min(h, 18), sign))
        self.obbs.append(OBB(x, z, sx / 2, sz / 2))

    def patch(self, x, z, sx, sz, kind):
        self.items.append(("patch", x, z, sx / 2, sz / 2, kind))

    def bounds(self, x1, z1, x2, z2):
        for a, b, c, d in ((x1, z1, x2, z1), (x1, z2, x2, z2), (x1, z1, x1, z2), (x2, z1, x2, z2)):
            self.seg(a, b, c, d, 0.45, "barrier", 0.8)

    def surround(self, x1, z1, x2, z2, sign=True):
        r = self.rand
        mx, mz, wx, wz = (x1 + x2) / 2, (z1 + z2) / 2, x2 - x1, z2 - z1
        self.patch(mx, z2 + 15.5, wx + 62, 30, "grass")
        self.patch(mx, z1 - 15.5, wx + 62, 30, "grass")
        self.patch(x1 - 15.5, mz, 30, wz + 1, "grass")
        self.patch(x2 + 15.5, mz, 30, wz + 1, "grass")
        x = x1 + 2
        while x < x2:
            if r.random() < 0.75:
                self.tree(x + r.random() * 2, z1 - 3 - r.random() * 2, 0.9 + r.random() * 0.4)
            if r.random() < 0.75:
                self.tree(x + r.random() * 2, z2 + 3 + r.random() * 2, 0.9 + r.random() * 0.4)
            x += 7
        z = z1 + 2
        while z < z2:
            if r.random() < 0.7:
                self.tree(x1 - 3 - r.random() * 2, z + r.random() * 2, 0.9 + r.random() * 0.4)
            if r.random() < 0.7:
                self.tree(x2 + 3 + r.random() * 2, z + r.random() * 2, 0.9 + r.random() * 0.4)
            z += 7
        for (ax, az, bx, bz) in ((x1, z1 - 1.3, x2, z1 - 1.3), (x1, z2 + 1.3, x2, z2 + 1.3), (x1 - 1.3, z1, x1 - 1.3, z2), (x2 + 1.3, z1, x2 + 1.3, z2)):
            n = int(math.hypot(bx - ax, bz - az) / 1.3)
            for j in range(n + 1):
                if r.random() < 0.88:
                    self.items.append(("bush", ax + (bx - ax) * j / n, az + (bz - az) * j / n, 0.42 + r.random() * 0.12))
        for _ in range(2):
            self.items.append(("manhole", x1 + 3 + r.random() * (x2 - x1 - 6), z1 + 2 + r.random() * 4))
        for i in range(4):
            w, d = 12 + r.random() * 8, 9 + r.random() * 4
            bx = x1 + (x2 - x1) * (i + 0.5) / 4
            bz = z2 + 9 + d / 2 if i % 2 else z1 - 9 - d / 2
            self.building(bx, bz, w, d, 8 + r.random() * 12, "PROMISE PLAZA" if (sign and i == 1) else None)

    def street(self, x1, x2, near, far):
        r = self.rand
        self.patch((x1 + x2) / 2, (near + far) / 2, x2 - x1 + 40, 70, "grass")
        self.patch((x1 + x2) / 2, (near + far) / 2, x2 - x1 + 40, near - far, "asphalt")
        self.box((x1 + x2) / 2, near + 1.5, x2 - x1 + 40, 3, 0, "concrete", 0.15)
        self.box((x1 + x2) / 2, far - 1.5, x2 - x1 + 40, 3, 0, "concrete", 0.15)
        self.seg(x1, far, x1, near, 0.45, "barrier", 0.8)
        self.seg(x2, far, x2, near, 0.45, "barrier", 0.8)
        self.paint(x1 - 20, near - 2.2, x2 + 20, near - 2.2)
        self.dashed(x1 - 20, (near + far) / 2 - 0.8, x2 + 20, (near + far) / 2 - 0.8)
        x = x1 + 4
        while x < x2:
            self.tree(x + r.random() * 2, near + 2.3, 0.75 + r.random() * 0.3)
            x += 9
        self.items.append(("manhole", x1 + 10 + r.random() * 8, (near + far) / 2 - 2.5))
        x = x1 + 8
        while x < x2:
            self.lamp(x, far - 0.6)
            x += 14
        for i in range(5):
            cx = x1 + (x2 - x1) * (i + 0.5) / 5
            self.building(cx, near + 10, 7.5, 8, 8 + r.random() * 10, "PROMISE PLAZA" if i == 2 else None)
            self.building(cx + 2, far - 10, 7.5, 8, 8 + r.random() * 10)


def row_bays(B, xs, z, into, w, l, occ):
    for x in xs:
        B.bay(x, z, into, w, l)
    for x in occ:
        B.car(x + (B.rand.random() - 0.5) * 0.25, z + (B.rand.random() - 0.5) * 0.35,
              into + (0 if B.rand.random() < 0.5 else PI) + (B.rand.random() - 0.5) * 0.06)


XS7 = [-7.8, -5.2, -2.6, 0, 2.6, 5.2, 7.8]


def lv_first(B):
    B.surround(-13, -15, 13, 10)
    B.bounds(-13, -15, 13, 10)
    row_bays(B, XS7, 6, 0, 2.6, 5.2, [-7.8, -5.2, 5.2, 7.8])
    for z in (-6, -2):
        B.cone(-2.3, z)
        B.cone(2.3, z)
    B.psign(-11.8, -13.6)
    B.lamp(-12.2, 0)
    B.lamp(12.2, 0)


def lv_between(B):
    B.surround(-13, -15, 13, 10)
    B.bounds(-13, -15, 13, 10)
    row_bays(B, XS7, 6, 0, 2.6, 5.2, [-7.8, -5.2, 0, 5.2])
    for z in (-13, -9, -5):
        B.cone(-11.2, z)
    for x, z in ((8, -9), (9, -8.4), (10, -9)):
        B.cone(x, z)
    B.psign(11.8, -13.6)
    B.lamp(-12.2, -4)
    B.lamp(12.2, -4)


def lv_angled(B):
    B.surround(-17, -5, 17, 10)
    B.bounds(-17, -5, 17, 10)
    sp = 2.6 / math.cos(PI / 4)
    for k in range(-3, 4):
        x = k * sp
        B.bay(x, 5, PI / 4, 2.6, 5.2)
        if k in (-3, -1, 1, 2):
            B.car(x + (B.rand.random() - 0.5) * 0.2, 5 + (B.rand.random() - 0.5) * 0.2, PI / 4)
    B.arrow(-8, 0, R2)
    B.arrow(8, 0, R2)
    B.lamp(0, -4.5)
    B.psign(-16, -4.2)


def lv_back(B):
    B.surround(-13, -12.5, 13, 8.5)
    B.bounds(-13, -12.5, 13, 8.5)
    row_bays(B, XS7, 4, 0, 2.6, 5.2, [-7.8, -2.6, 2.6, 5.2])
    row_bays(B, XS7, -8.6, PI, 2.6, 5.2, [-7.8, -5.2, 0, 5.2, 7.8])
    B.lamp(-12.4, 6.5)
    B.lamp(12.4, -11)


def lv_parallel(B):
    B.street(-22, 22, 3, -5.5)
    for x in (-14, -8, 2.6, 9, 15):
        B.car(x + (B.rand.random() - 0.5) * 0.3, 1.9 + (B.rand.random() - 0.5) * 0.1, R2)
    B.items.append(("hydrant", -2.7, 3.6))


def lv_garage(B):
    B.patch(0, 4, 60, 50, "grass")
    B.patch(0, -4.75, 60, 8.5, "asphalt")
    B.box(0, -10.5, 60, 3, 0, "concrete", 0.15)
    B.seg(-20, -9, -20, -0.5, 0.45, "barrier", 0.8)
    B.seg(20, -9, 20, -0.5, 0.45, "barrier", 0.8)
    B.dashed(-30, -4.8, 30, -4.8)
    B.patch(0, 2.6, 3.4, 6.2, "concrete")
    B.patch(0, 9, 3.1, 6.6, "deck")
    B.seg(-1.55, 5.7, -1.55, 12.3, 0.2, "wall", 2.5)
    B.seg(1.55, 5.7, 1.55, 12.3, 0.2, "wall", 2.5)
    B.seg(-1.65, 12.3, 1.65, 12.3, 0.2, "wall", 2.5)
    B.box(6.85, 10.9, 10.3, 10.4, 0, "house", 5.5)
    B.box(-9, 11.5, 10, 9, 0, "house", 5.5)
    B.seg(-20, 16.5, 20, 16.5, 0.12, "fence", 1.2)
    B.seg(-20, -0.5, -20, 16.5, 0.12, "fence", 1.2)
    B.seg(20, -0.5, 20, 16.5, 0.12, "fence", 1.2)
    B.box(2.35, 4.6, 0.6, 0.6, 0, "bin", 1.0)
    B.box(-2.4, 4.8, 0.6, 0.6, 0, "bin", 1.0)
    B.box(-3.2, -0.2, 0.3, 0.45, 0, "red", 1.2)
    B.cone(1.9, 2.4)
    B.cone(-1.9, 2.4)
    for x in (-15, -7, 7, 15):
        B.tree(x, 1.5, 1)
    B.car(-14, -8.0, R2)
    B.car(12, -8.0, -R2)
    for x in (-12, 4):
        B.lamp(x, -9.6)
    for i in range(4):
        B.building(-15 + i * 10, -18, 8, 7, 7 + B.rand.random() * 5, "PROMISE PLAZA" if i == 2 else None)


def lv_deck(B):
    B.patch(0, 0.6, 28, 13.6, "deck")
    xs = [-8.7, -5.8, -2.9, 0, 2.9, 5.8, 8.7]
    row_bays(B, xs, 4.2, 0, 2.9, 5.4, [-8.7, -5.8, 0, 5.8])
    for a, b, c, d in ((-14, 7.4, 14, 7.4), (-14, -6.2, 14, -6.2), (-14, -6.2, -14, 7.4), (14, -6.2, 14, 7.4)):
        B.seg(a, b, c, d, 0.3, "wall", 2.8)
    for x in (-7.25, -4.35, -1.45, 1.45, 4.35, 7.25):
        B.box(x, 1.75, 0.5, 0.5, 0, "pillar", 2.8)
    for x in (-9, -3, 3, 9):
        B.box(x, -5.75, 0.5, 0.5, 0, "pillar", 2.8)
    B.arrow(-6, -2.2, R2, PAINT)
    B.arrow(6, -2.2, R2, PAINT)
    B.dashed(-13.5, -0.6, 13.5, -0.6, 1, 1, 0.1)
    B.lamps += [(-10, 6.6), (-3.5, 6.6), (3.5, 6.6), (10, 6.6), (-5, -2), (5, -2)]


def lv_tight(B):
    B.street(-22, 22, 3, -5.5)
    for x in (-13.5, -7.5, 2.5, 8.5, 14.5):
        B.car(x + (B.rand.random() - 0.5) * 0.2, 1.9 + (B.rand.random() - 0.5) * 0.1, R2)
    B.items.append(("hydrant", -1.2, 3.6))
    for x in (10.5, 11.3, 12.1):
        B.cone(x, -4.6)


LEVELS = [
    dict(name="First Spot", env="day", start=(0, -11, 0), build=lv_first, kind="fwd",
         text="Drive between the cones and stop inside the glowing bay. Keep it slow and straight.",
         spot=(0, 6, 2.6, 5.2, 0), ghost=[(1, [(0, -11), (0, -3), (0, 6)])]),
    dict(name="Between the Lines", env="day", start=(-8, -11, 0), build=lv_between, kind="fwd",
         text="The free bay sits between two parked cars. Swing wide first, then come in straight.",
         spot=(2.6, 6, 2.6, 5.2, 0),
         ghost=[(1, [(-8, -11), (-8, -6), (-5.5, -2), (-1, 0.6), (2, 2.4), (2.6, 4), (2.6, 6)])]),
    dict(name="Angled Row", env="day", start=(-13, 0, R2), build=lv_angled, kind="ang",
         text="One-way aisle with 45 degree bays. Turn in early and line up with the arrow.",
         spot=(0, 5, 2.6, 5.2, PI / 4),
         ghost=[(1, [(-13, 0), (-7, 0), (-4.2, 0.8), (-2.12, 2.88), (-1.06, 3.94), (0, 5)])]),
    dict(name="Back It In", env="day", start=(-10.5, -2.6, R2), build=lv_back, kind="rev",
         text="Reverse into the bay so the nose points out. Pull past it, steer away, then back in.",
         spot=(0, 4, 2.6, 5.2, PI),
         ghost=[(1, [(-10.5, -2.6), (-2, -2.6), (2.2, -2.6), (4.6, -3.4)]),
                (-1, [(4.6, -3.4), (2.7, -1.7), (1.0, 0.3), (0.2, 2.2), (0, 3.2), (0, 4)])]),
    dict(name="Parallel Street", env="day", start=(-18, -1.4, R2), build=lv_parallel, kind="par",
         text="Pull up beside the front car, reverse in at an angle, then straighten next to the curb.",
         spot=(-2.7, 1.9, 2.2, 6.0, R2),
         ghost=[(1, [(-18, -1.4), (-6, -1.4), (1.5, -0.7), (5.4, -0.5)]),
                (-1, [(5.4, -0.5), (2.4, -0.4), (0.2, 0.2), (-1.5, 1.2), (-2.6, 1.75), (-3.4, 1.9)]),
                (1, [(-3.4, 1.9), (-3.0, 1.9), (-2.7, 1.9)])]),
    dict(name="The Garage", env="day", start=(-12, -3, R2), build=lv_garage, kind="rev",
         text="Reverse into the home garage. Narrow door, real walls. Watch the rear sensor.",
         spot=(0, 9.0, 2.7, 5.4, PI),
         ghost=[(1, [(-12, -3), (-3, -3), (2.5, -3.6), (4.8, -4.8)]),
                (-1, [(4.8, -4.8), (2.9, -3), (1.1, -0.6), (0.2, 2.0), (0, 5), (0, 9)])]),
    dict(name="Pillar Deck", env="deck", start=(-11, -2.2, R2), build=lv_deck, kind="rev",
         text="Underground deck. Reverse between two pillars with about 25 cm to spare on each side.",
         spot=(2.9, 4.2, 2.7, 5.3, PI),
         ghost=[(1, [(-11, -2.2), (0, -2.2), (4.6, -2.4), (7.4, -3.6)]),
                (-1, [(7.4, -3.6), (5.4, -2.0), (3.7, -0.3), (3.0, 1.5), (2.9, 3), (2.9, 4.2)])]),
    dict(name="Tight Squeeze", env="dusk", start=(-18, -1.4, R2), build=lv_tight, kind="par",
         text="Parallel park at dusk with only 1.2 m to spare. Small corrections, lots of patience.",
         spot=(-2.5, 1.9, 2.2, 5.4, R2),
         ghost=[(1, [(-18, -1.4), (-6, -1.4), (1.5, -0.7), (5.1, -0.5)]),
                (-1, [(5.1, -0.5), (2.2, -0.4), (0.2, 0.3), (-1.4, 1.3), (-2.4, 1.8), (-3.0, 1.9)]),
                (1, [(-3.0, 1.9), (-2.75, 1.9), (-2.5, 1.9)])]),
]
KIND = dict(fwd="Nose in", ang="Nose in, 45 degrees", rev="Reverse in, nose out", par="Parallel, face traffic")
ENV = dict(day=dict(night=None, ground=(62, 65, 70)),
           dusk=dict(night=(28, 22, 58, 120), ground=(62, 65, 70)),
           deck=dict(night=(8, 10, 18, 150), ground=(104, 106, 108)))


# ------------------------------------------------------------------ 5. baking
def noise_tile(base, amp, size=256, dots=9000, seed=1):
    r = random.Random(seed)
    t = pg.Surface((size, size))
    t.fill(base)
    for _ in range(dots):
        n = int((r.random() - 0.5) * amp)
        t.set_at((r.randrange(size), r.randrange(size)), tuple(clamp(c + n, 0, 255) for c in base))
    for _ in range(14):
        s = pg.Surface((size, size), pg.SRCALPHA)
        pg.draw.circle(s, (0, 0, 0, r.randrange(6, 16)), (r.randrange(size), r.randrange(size)), r.randrange(20, 60))
        t.blit(s, (0, 0))
    return t


TILES = {}


def tile(kind):
    if kind not in TILES:
        TILES[kind] = {"asphalt": lambda: noise_tile((62, 65, 70), 26),
                       "deck": lambda: noise_tile((104, 106, 108), 16, seed=2),
                       "grass": lambda: noise_tile((74, 112, 56), 34, dots=14000, seed=3),
                       "concrete": lambda: noise_tile((176, 175, 168), 14, seed=4)}[kind]()
    return TILES[kind]


class Level:
    def __init__(self, idx):
        self.idx = idx
        self.d = d = LEVELS[idx]
        B = Builder(idx * 977 + 13)
        d["build"](B)
        self.obbs, self.lamps = B.obbs, B.lamps
        self.cones = B.cones
        self.cones0 = [dict(c) for c in B.cones]
        sx, sz, sw, sl, sa = d["spot"]
        self.spot = OBB(sx, sz, sw / 2, sl / 2, sa)
        self.env = ENV[d["env"]]
        self.ghost = build_path(d["ghost"])
        self.par = math.ceil(path_time(self.ghost, DIFF["medium"][0]) + 4)
        self.bake(B.items)
        self.spot_imgs = {k: self.spot_img(c) for k, c in (("idle", (61, 220, 132)), ("in", (255, 197, 61)), ("good", (255, 255, 255)))}

    # world (x, z) -> pixel inside the baked image. Screen x is mirrored so the
    # layout matches the 3D edition exactly.
    def bp(self, x, z):
        return (self.X1 - x) * PX, (self.Z1 - z) * PX

    def bake(self, items):
        xs, zs = [], []
        for it in items:
            if it[0] in ("box", "building"):
                r = math.hypot(it[3], it[4]) + (it[7] * 0.5 if it[0] == "box" else it[5] * 0.5)
                xs += [it[1] - r, it[1] + r]
                zs += [it[2] - r, it[2] + r]
            elif it[0] == "line":
                xs += [it[1], it[3]]
                zs += [it[2], it[4]]
            elif it[0] in ("car", "tree", "lamp"):
                xs += [it[1] - 3, it[1] + 3]
                zs += [it[2] - 3, it[2] + 3]
        x0, x1 = max(min(xs) - 2, -40), min(max(xs) + 2, 40)
        z0, z1 = max(min(zs) - 2, -34), min(max(zs) + 2, 34)
        self.X1, self.Z1 = x1, z1
        w, h = int((x1 - x0) * PX), int((z1 - z0) * PX)
        bg = pg.Surface((w, h))
        T = tile("deck" if self.d["env"] == "deck" else "asphalt")
        for x in range(0, w, T.get_width()):
            for y in range(0, h, T.get_height()):
                bg.blit(T, (x, y))
        rng = random.Random(self.idx)

        def wpoly(surf, col, pts):
            poly(surf, col, [self.bp(x, z) for x, z in pts])

        def rect_pts(x, z, hx, hz, a, r=0.0):
            o = OBB(x, z, hx, hz, a)
            loc = rounded_rect_local(hx, hz, r) if r else [(hx, hz), (-hx, hz), (-hx, -hz), (hx, -hz)]
            return [o.to_world(lx, lz) for lx, lz in loc]

        # ground patches (textured)
        for it in items:
            if it[0] == "patch":
                _, x, z, hx, hz, kind = it
                px0, py0 = self.bp(x + hx, z + hz)
                r = pg.Rect(int(px0), int(py0), int(2 * hx * PX), int(2 * hz * PX))
                t = tile(kind)
                sub = pg.Surface(r.size)
                for xx in range(0, r.w, t.get_width()):
                    for yy in range(0, r.h, t.get_height()):
                        sub.blit(t, (xx, yy))
                bg.blit(sub, r.topleft)
        # painted lines
        for it in items:
            if it[0] == "line":
                _, x1_, z1_, x2_, z2_, wd, col = it
                L = math.hypot(x2_ - x1_, z2_ - z1_) or 1
                nx, nz = -(z2_ - z1_) / L * wd / 2, (x2_ - x1_) / L * wd / 2
                wpoly(bg, col, [(x1_ + nx, z1_ + nz), (x2_ + nx, z2_ + nz), (x2_ - nx, z2_ - nz), (x1_ - nx, z1_ - nz)])
        for it in items:
            if it[0] == "stain":
                rr = int(it[3] * PX)
                st = pg.Surface((rr * 2, int(rr * 1.4)), pg.SRCALPHA)
                pg.draw.ellipse(st, (20, 20, 18, 55), st.get_rect())
                pg.draw.ellipse(st, (20, 20, 18, 40), st.get_rect().inflate(-rr // 2, -rr // 3))
                p = self.bp(it[1], it[2])
                bg.blit(st, st.get_rect(center=(int(p[0]), int(p[1]))))
            elif it[0] == "manhole":
                p = self.bp(it[1], it[2])
                circle(bg, (48, 50, 53), p[0], p[1], 0.4 * PX)
                circle(bg, (78, 80, 84), p[0], p[1], 0.34 * PX)
                for j in (-2, -1, 0, 1, 2):
                    pg.draw.line(bg, (58, 60, 63), (p[0] - 0.28 * PX, p[1] + j * 0.12 * PX), (p[0] + 0.28 * PX, p[1] + j * 0.12 * PX), 2)
            elif it[0] == "stop":
                _, x, z, a, hw = it
                pts = rect_pts(x, z, hw, 0.1, a, 0.09)
                wpoly(bg, (40, 40, 40), [(px_ - 0.06, pz_ - 0.05) for px_, pz_ in pts])
                wpoly(bg, (128, 128, 124), pts)
                wpoly(bg, (186, 184, 178), rect_pts(x, z, hw - 0.04, 0.06, a, 0.06))
        # shadows: everything tall throws a soft shadow toward the bottom-right
        sh = pg.Surface((w, h), pg.SRCALPHA)
        SX, SZ = -0.42, -0.3

        def drop(pts, height):
            pts2 = pts + [(x + SX * height, z + SZ * height) for x, z in pts]
            wpoly(sh, (0, 0, 0, 255), hull(pts2))
        for it in items:
            k = it[0]
            if k == "box" and it[6] != "concrete":
                drop(rect_pts(it[1], it[2], it[3], it[4], it[5]), it[7])
            elif k == "building":
                drop(rect_pts(it[1], it[2], it[3], it[4], 0), it[5])
            elif k == "car":
                drop(rect_pts(it[1], it[2], 0.9, 2.15, it[3], 0.5), 1.0)
            elif k == "bush":
                circle(sh, (0, 0, 0, 255), *self.bp(it[1] + SX * 0.6, it[2] + SZ * 0.6), 1.5 * it[3] * PX)
            elif k == "tree":
                cx, cz = it[1] + SX * 3.5, it[2] + SZ * 3.5
                circle(sh, (0, 0, 0, 255), *self.bp(cx, cz), 1.4 * it[3] * PX)
                drop(rect_pts(it[1], it[2], 0.12, 0.12, 0), 3)
            elif k in ("lamp", "psign"):
                drop(rect_pts(it[1], it[2], 0.08, 0.08, 0), 5 if k == "lamp" else 2.3)
        sh = pg.transform.smoothscale(pg.transform.smoothscale(sh, (w // 4, h // 4)), (w, h))
        sh.set_alpha(95)
        bg.blit(sh, (0, 0))
        # solid objects
        car_cache = {}
        for it in items:
            k = it[0]
            if k == "box":
                _, x, z, hx, hz, a, kind, ht = it
                col = COL.get(kind, (128, 130, 134))
                if kind == "concrete":
                    wpoly(bg, col, rect_pts(x, z, hx, hz, a))
                    T2 = tile("concrete")
                    px0, py0 = self.bp(x + hx, z + hz)
                    r = pg.Rect(int(px0), int(py0), int(2 * hx * PX), int(2 * hz * PX))
                    sub = pg.Surface(r.size)
                    for xx in range(0, r.w, 256):
                        sub.blit(T2, (xx, 0))
                    bg.blit(sub, r.topleft)
                    for j in range(int(2 * hx / 1.5)):                        # expansion joints
                        jx = x - hx + j * 1.5
                        pg.draw.aaline(bg, shade(col, 0.85), self.bp(jx, z - hz), self.bp(jx, z + hz))
                    edge = z - hz if z > 0 else z + hz
                    pg.draw.line(bg, shade(col, 1.12), self.bp(x - hx, edge), self.bp(x + hx, edge), 4)
                    continue
                if kind == "house":
                    wpoly(bg, (92, 76, 68), rect_pts(x, z, hx, hz, a, 0.2))
                    wpoly(bg, (112, 92, 82), rect_pts(x, z + hz / 2, hx - 0.15, hz / 2 - 0.15, a, 0.1))
                    pg.draw.aaline(bg, (70, 58, 52), self.bp(x - hx, z), self.bp(x + hx, z))
                    continue
                rr = min(hx, hz) * 0.6 if kind in ("barrier", "bin", "pillar", "red") else 0.03
                wpoly(bg, shade(col, 0.72), rect_pts(x, z, hx, hz, a, rr))
                wpoly(bg, col, rect_pts(x, z, max(hx - 0.06, 0.02), max(hz - 0.06, 0.02), a, rr * 0.8))
                if kind == "pillar":
                    wpoly(bg, PAINT, rect_pts(x, z, hx + 0.03, hz + 0.03, a, 0.05))
                    wpoly(bg, col, rect_pts(x, z, hx - 0.06, hz - 0.06, a, 0.04))
            elif k == "building":
                _, x, z, hx, hz, ht, sign = it
                tint = rng.choice([(178, 180, 184), (160, 156, 150), (186, 176, 164), (140, 148, 156)])
                wpoly(bg, shade(tint, 0.7), rect_pts(x, z, hx, hz, 0, 0.3))
                wpoly(bg, tint, rect_pts(x, z, hx - 0.4, hz - 0.4, 0, 0.2))
                for _ in range(3):                                              # rooftop AC units
                    ux, uz = x + (rng.random() - 0.5) * hx, z + (rng.random() - 0.5) * hz
                    wpoly(bg, shade(tint, 0.6), rect_pts(ux + 0.15, uz - 0.15, 0.7, 0.5, 0, 0.1))
                    wpoly(bg, shade(tint, 1.15), rect_pts(ux, uz, 0.7, 0.5, 0, 0.1))
                    circle(bg, shade(tint, 0.8), *self.bp(ux, uz), 0.3 * PX)
                if sign:
                    t = F["m"].render(sign, True, (245, 245, 240))
                    if t.get_width() < hx * 2 * PX - 20:
                        p = self.bp(x, z)
                        bg.blit(t, t.get_rect(center=(int(p[0]), int(p[1]))))
            elif k == "car":
                _, x, z, a, col, kind = it
                if (col, kind) not in car_cache:
                    car_cache[(col, kind)] = make_car(col, kind=kind)
                img = pg.transform.rotozoom(car_cache[(col, kind)], math.degrees(a), 1)
                p = self.bp(x, z)
                bg.blit(img, img.get_rect(center=(int(p[0]), int(p[1]))))
            elif k == "hydrant":
                p = self.bp(it[1], it[2])
                circle(bg, (120, 20, 16), p[0], p[1], 0.2 * PX)
                circle(bg, COL["red"], p[0] - 1, p[1] - 1, 0.15 * PX)
        for it in items:
            k = it[0]
            if k in ("tree", "bush"):
                img = tree_sprite(rng, it[3])
                p = self.bp(it[1], it[2])
                bg.blit(img, img.get_rect(center=(int(p[0]), int(p[1]))))
            elif k == "lamp":
                p = self.bp(it[1], it[2])
                circle(bg, (60, 66, 72), p[0], p[1], 0.16 * PX)
                circle(bg, (235, 232, 215), p[0], p[1], 0.1 * PX)
            elif k == "psign":
                p = self.bp(it[1], it[2])
                r = pg.Rect(0, 0, int(0.7 * PX), int(0.7 * PX))
                r.center = (int(p[0]), int(p[1]))
                pg.draw.rect(bg, (255, 255, 255), r, border_radius=4)
                pg.draw.rect(bg, SIGN, r.inflate(-4, -4), border_radius=3)
                bg.blit(F["s"].render("P", True, (255, 255, 255)), F["s"].render("P", True, (0, 0, 0)).get_rect(center=r.center))
        self.bg = bg

    def spot_img(self, col):
        s = self.spot
        K = 2
        w, l = int((2 * s.hx + 0.4) * PX * K), int((2 * s.hz + 0.4) * PX * K)
        img = pg.Surface((w, l), pg.SRCALPHA)
        cx, cy = w / 2, l / 2
        loc = rounded_rect_local(s.hx, s.hz, 0.25)
        poly(img, (*col, 50), [(cx + x * PX * K, cy - z * PX * K) for x, z in loc])
        pts = [(cx + x * PX * K, cy - z * PX * K) for x, z in loc]
        pg.draw.lines(img, (*col, 255), True, pts, 5)
        for a, b in (((0, -0.9), (0, 0.9)), ((0, 1.25), (-0.55, 0.6)), ((0, 1.25), (0.55, 0.6))):
            pg.draw.line(img, (*col, 255), (cx + a[0] * PX * K, cy - a[1] * PX * K), (cx + b[0] * PX * K, cy - b[1] * PX * K), 8)
        img = pg.transform.smoothscale(img, (w // K, l // K))
        # mirrored screen: rotate by +heading (see Game.car_screen)
        return pg.transform.rotozoom(img, math.degrees(s.a), 1)


# ------------------------------------------------------------------ 6. ghost
DIFF = dict(easy=(1.5, 78, "Easy"), medium=(2.3, 88, "Medium"), hard=(3.2, 97, "Hard"))
PAUSE = 0.7


def catmull(p, step=0.3):
    out, n = [], len(p)
    for i in range(n - 1):
        p0, p1, p2, p3 = p[max(0, i - 1)], p[i], p[i + 1], p[min(n - 1, i + 2)]
        k = max(2, math.ceil(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / step))
        for j in range(k):
            t = j / k
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3)
                             for a, b, c, d in zip(p0, p1, p2, p3)))
    out.append(tuple(p[-1]))
    return out


def build_path(segs):
    res = []
    for d, pts in segs:
        dense = catmull(pts)
        cum = [0.0]
        for i in range(1, len(dense)):
            cum.append(cum[-1] + math.dist(dense[i], dense[i - 1]))
        res.append((d, dense, cum))
    return res


def path_time(G, speed):
    return sum(c[-1] * 1.3 / speed for _, _, c in G) + PAUSE * (len(G) - 1)


def path_pose(G, speed, t):
    for i, (d, pts, cum) in enumerate(G):
        dur = cum[-1] * 1.3 / speed
        if t <= dur:
            return sample(d, pts, cum, (0.5 - 0.5 * math.cos(PI * t / dur)) * cum[-1]) + (False,)
        t -= dur
        if i < len(G) - 1:
            if t <= PAUSE:
                return sample(d, pts, cum, cum[-1]) + (False,)
            t -= PAUSE
    d, pts, cum = G[-1]
    return sample(d, pts, cum, cum[-1]) + (True,)


def sample(d, pts, cum, dist):
    i = 1
    while i < len(cum) - 1 and cum[i] < dist:
        i += 1
    a, b = pts[i - 1], pts[i]
    f = clamp((dist - cum[i - 1]) / ((cum[i] - cum[i - 1]) or 1), 0, 1)
    h = math.atan2(b[0] - a[0], b[1] - a[1]) + (PI if d < 0 else 0)
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, h


# ------------------------------------------------------------------ 7. sound
RATE = 22050
SND = {}


def make_sound(parts, vol=0.4):
    buf = array.array("h")
    for freq, dur, kind in parts:
        n = int(RATE * dur)
        for i in range(n):
            t = i / RATE
            env = (1 - i / n) ** 2
            if kind == "noise":
                v = random.uniform(-1, 1) * env
            elif kind == "square":
                v = (1 if math.sin(2 * PI * freq * t) > 0 else -1) * env * 0.5
            elif kind == "hum":
                v = (((t * freq) % 1) * 2 - 1) * 0.6 + math.sin(2 * PI * freq * 0.5 * t) * 0.4
            else:
                v = math.sin(2 * PI * freq * t) * min(1, i / 60) * env
            buf.append(int(clamp(v * vol, -1, 1) * 32767))
    return pg.mixer.Sound(buffer=buf.tobytes())


def init_sounds():
    try:
        if not pg.mixer.get_init():
            pg.mixer.init(RATE, -16, 1, 512)
        SND["beep"] = make_sound([(1180, 0.06, "sine")], 0.25)
        SND["click"] = make_sound([(1800, 0.025, "square")], 0.2)
        SND["thud"] = make_sound([(0, 0.25, "noise"), ], 0.6)
        SND["chime"] = make_sound([(660, 0.12, "sine"), (880, 0.12, "sine"), (1320, 0.35, "sine")], 0.35)
        SND["fail"] = make_sound([(220, 0.3, "square"), (150, 0.45, "square")], 0.3)
        SND["cone"] = make_sound([(520, 0.12, "sine")], 0.3)
        SND["engine"] = make_sound([(46, 1.0, "hum")], 0.18)
    except Exception:
        SND.clear()


# ------------------------------------------------------------------ 8. game
CAR = dict(hw=0.925, hl=2.2, wb=2.65, rear=1.325, steer=0.6, accF=2.4, accR=1.7, vF=7.0, vR=2.8, brake=7.5, roll=1.2)
SAVE = os.path.join(HERE, "records.json")


def load_save():
    try:
        with open(SAVE) as f:
            return json.load(f)
    except Exception:
        return {"records": {}, "unlocked": 0, "wins": {"easy": 0, "medium": 0, "hard": 0}, "sound": True, "guides": True}


def score(t, par, acc, hits, cones):
    tf = clamp(1.3 - 0.6 * t / par, 0, 1)
    parts = dict(base=300, acc=round(5 * acc), time=round(400 * tf), hits=-100 * hits, cones=-40 * cones)
    total = max(0, sum(parts.values()))
    stars = 3 if total >= 1050 and hits == 0 and cones == 0 else 2 if total >= 850 else 1
    return parts, total, stars


class Game:
    def __init__(self):
        self.save = load_save()
        self.state = "title"
        self.mode = "career"
        self.diff = "medium"
        self.li = 0
        self.lv = None
        self.buttons = []
        self.pointers = {}
        self.keys = set()
        self.toast_t, self.toast_msg = 0, ""
        self.player_img = make_car((200, 52, 42), plate="OBI P", front_tires=False)
        self.vignette = make_vignette()
        self.dark = pg.Surface((W, H), pg.SRCALPHA)
        self.glow_cache = {}
        self.dim_cache = {}
        self.title_grad = None
        self.ghost_img = make_car((200, 200, 200), ghost=True)
        self.show_img = make_car((200, 52, 42), plate="OBI P", scale=78)
        self.shadow = make_shadow()
        self.big_shadow = make_shadow(78)
        self.glow = make_glow(64, (0, 0, 0))   # alpha-only: cuts holes in the night overlay
        self.red_glow = make_glow(24, (255, 40, 30))
        self.white_glow = make_glow(20, (255, 255, 240))
        self.wheel_img = make_wheel(170)
        self.wheel_deg = 0.0
        self.sounds_ready = False
        self.t_anim = 0.0
        self.pvp = None
        self.cam = [0.0, 0.0]
        self.load(0, attract=True)

    # ---------------------------------------------------------- level control
    def load(self, i, attract=False):
        self.li = i
        self.lv = Level(i)
        self.lv.cones = [dict(c) for c in self.lv.cones0]
        d = self.lv.d
        x, z, h = (self.lv.spot.x, self.lv.spot.z, self.lv.spot.a) if attract else d["start"]
        self.car = OBB(x, z, CAR["hw"], CAR["hl"], h)
        self.v = self.steer = self.thr = self.brk = 0.0
        self.kb_steer = 0.0
        self.wheel_deg = 0.0
        self.gas = self.brake_on = False
        self.gear = "P"
        self.hits = self.cone_hits = 0
        self.damage = self.cool = 0.0
        self.t = self.hold = self.beep_t = 0.0
        self.started = self.running = self.done = False
        self.ghost_t = 0.0
        self.ghost_pose = None
        self.ghost_told = False
        self.cam = [x, z]

    def toast(self, msg):
        self.toast_msg, self.toast_t = msg, 1.6

    def play(self, name, loops=0):
        if self.save.get("sound", True) and name in SND:
            SND[name].play(loops)

    def shift(self, g):
        if not self.running or g == self.gear:
            return
        if abs(self.v) > 0.6:
            self.toast("Stop before changing gear")
            return
        self.gear = g
        self.play("click")
        if g == "P":
            st = self.spot_state()
            if st["inside"] and st["aligned"] and not st["wrong"] and self.started:
                self.finish(True)
        elif not self.started and self.mode != "vs":
            self.started = True

    # ---------------------------------------------------------- physics
    def physics(self, dt):
        c = self.car
        target = -self.steer_in * CAR["steer"]
        rate = 3.2 if any(p[0] == "wheel" for p in self.pointers.values()) else 1.9
        self.steer += clamp(target - self.steer, -rate * dt, rate * dt)
        d = 1 if self.gear == "D" else -1 if self.gear == "R" else 0
        a = 0.0
        if d and self.thr > 0:
            vmax = CAR["vF"] if d > 0 else CAR["vR"]
            a = d * self.thr * (CAR["accF"] if d > 0 else CAR["accR"]) * (1 - clamp(self.v * d / vmax, 0, 1))
        dec = CAR["roll"] * (0.15 if self.thr > 0 else 1) + self.brk * CAR["brake"] + 0.04 * self.v * self.v
        if self.gear == "P":
            dec += 14
        v = self.v + a * dt
        if v:
            s = math.copysign(1, v)
            v -= s * dec * dt
            if math.copysign(1, v) != s:
                v = 0.0
        self.v = v
        # kinematic bicycle model, pivoting on the rear axle like a real car
        fx, fz = math.sin(c.a), math.cos(c.a)
        rx, rz = c.x - fx * CAR["rear"] + fx * v * dt, c.z - fz * CAR["rear"] + fz * v * dt
        h = wrap(c.a + v * math.tan(self.steer) / CAR["wb"] * dt)
        c.set(rx + math.sin(h) * CAR["rear"], rz + math.cos(h) * CAR["rear"], h)
        for o in self.lv.obbs:
            r = sat(c, o)
            if not r:
                continue
            depth, nx, nz = r
            c.set(c.x - nx * depth, c.z - nz * depth, c.a)
            vn = (math.sin(c.a) * nx + math.cos(c.a) * nz) * self.v
            if vn > 0:
                if vn > 0.22 and self.cool <= 0:
                    self.hits += 1
                    self.cool = 0.6
                    self.damage = min(100, self.damage + 5 + vn * 10)
                    self.shake = min(1, 0.3 + vn / 3)
                    self.play("thud")
                    self.toast("Collision -100")
                self.v *= -0.18

    def cones_step(self, dt):
        c = self.car
        for k in self.lv.cones:
            lx, lz = c.local(k["x"], k["z"])
            lx, lz = clamp(lx, -c.hx, c.hx), clamp(lz, -c.hz, c.hz)
            wx, wz = c.to_world(lx, lz)
            ex, ez = k["x"] - wx, k["z"] - wz
            d = math.hypot(ex, ez)
            if d < 0.22:
                nx, nz = (ex / d, ez / d) if d > 1e-4 else (math.sin(c.a), math.cos(c.a))
                k["x"], k["z"] = wx + nx * 0.23, wz + nz * 0.23
                sp = abs(self.v) * 1.3 + 0.6
                k["vx"], k["vz"] = nx * sp, nz * sp
                if not k["hit"]:
                    k["hit"] = True
                    k["dir"] = math.atan2(nx, nz)
                    self.cone_hits += 1
                    self.play("cone")
                    self.toast("Cone -40")
            k["x"] += k["vx"] * dt
            k["z"] += k["vz"] * dt
            f = math.exp(-3.5 * dt)
            k["vx"] *= f
            k["vz"] *= f
            if k["hit"]:
                k["tip"] = min(1.0, k["tip"] + dt * 5)

    def sensors(self):
        c, f, r = self.car, 9.0, 9.0
        for side in (1, -1):
            for lx in (-0.85, -0.3, 0.3, 0.85):
                wx, wz = c.to_world(lx, side * (CAR["hl"] + 0.03))
                d = 9.0
                for o in self.lv.obbs:
                    if abs(o.x - wx) < o.r + 2.5 and abs(o.z - wz) < o.r + 2.5:
                        d = min(d, o.dist(wx, wz))
                for k in self.lv.cones:
                    d = min(d, math.hypot(k["x"] - wx, k["z"] - wz) - 0.22)
                if side > 0:
                    f = min(f, d)
                else:
                    r = min(r, d)
        return f, r

    def spot_state(self):
        c, s = self.car, self.lv.spot
        inside = True
        for sx in (-1, 1):
            for sz in (-1, 1):
                lx, lz = s.local(*c.to_world(sx * CAR["hw"], sz * CAR["hl"]))
                if abs(lx) > s.hx or abs(lz) > s.hz:
                    inside = False
        e1, e2 = abs(wrap(c.a - s.a)), abs(wrap(c.a - s.a - PI))
        lx, lz = s.local(c.x, c.z)
        ang = math.degrees(e1)
        acc = clamp(100 - ang * 2.5 - abs(lx) * 60 - abs(lz) * (6 if self.lv.d["kind"] == "par" else 10), 0, 100)
        return dict(inside=inside, wrong=e2 < e1 and e2 < 0.35, aligned=ang < 10, ang=ang, lx=lx, acc=acc)

    # ---------------------------------------------------------- update
    def update_play(self, dt):
        k = self.keys
        L, R = pg.K_LEFT in k or pg.K_a in k, pg.K_RIGHT in k or pg.K_d in k
        U, D = pg.K_UP in k or pg.K_w in k, pg.K_DOWN in k or pg.K_s in k
        kt = (1 if R else 0) - (1 if L else 0)
        kr = 1.6 if kt else 2.4
        self.kb_steer += clamp(kt - self.kb_steer, -kr * dt, kr * dt)
        wheel_held = any(p[0] == "wheel" for p in self.pointers.values())
        if not wheel_held and self.wheel_deg:
            back = 260 * min(1, abs(self.v) / 2.5) * dt
            self.wheel_deg = 0 if abs(self.wheel_deg) <= back else self.wheel_deg - math.copysign(back, self.wheel_deg)
        self.steer_in = clamp(self.kb_steer + self.wheel_deg / 450, -1, 1)
        self.gas_v = min(1, getattr(self, "gas_v", 0) + dt * 2.2) if self.gas else 0
        self.brk_v = min(1, getattr(self, "brk_v", 0) + dt * 4) if self.brake_on else 0
        thr, brk = self.gas_v, self.brk_v
        still = abs(self.v) < 0.2
        if U and not D:
            if self.gear != "D":
                if still:
                    self.shift("D")
                else:
                    brk = 1
            else:
                thr = 1
        if D and not U:
            if self.gear != "R":
                if still:
                    self.shift("R")
                else:
                    brk = 1
            else:
                thr = 1
        if pg.K_SPACE in k:
            brk = 1
        if thr > 0 and not self.started and self.gear != "P" and self.mode != "vs":
            self.started = True
        self.thr, self.brk = thr, brk
        if self.cool > 0:
            self.cool -= dt
        n = max(1, math.ceil(dt * 120))
        for _ in range(n):
            self.physics(dt / n)
        self.cones_step(dt)
        if self.started:
            self.t += dt
        if self.mode == "vs" and self.started:
            self.ghost_t += dt
            x, z, h, done = path_pose(self.lv.ghost, DIFF[self.diff][0], self.ghost_t)
            gh = self.ghost_pose[2] if self.ghost_pose else h
            self.ghost_pose = (x, z, gh + wrap(h - gh) * min(1, dt * 9))
            if done and not self.ghost_told:
                self.ghost_told = True
                self.toast("Computer parked: %.1f s" % self.ghost_total)
        st = self.spot_state()
        self.hint, self.hint_prog = "", None
        if st["inside"] and self.started:
            kind = self.lv.d["kind"]
            if st["wrong"]:
                self.hint = "Wrong way: reverse in, nose out" if kind == "rev" else "Wrong way: face the traffic" if kind == "par" else "Wrong way: this bay is nose-in"
            elif not st["aligned"]:
                self.hint = "Straighten up"
            elif abs(self.v) > 0.05:
                self.hint, self.hold = "Stop inside the lines", 0
            else:
                self.hold += dt
                self.hint, self.hint_prog = "Hold still or shift to P", self.hold / 1.2
                if self.hold >= 1.2:
                    self.finish(True)
        else:
            self.hold = 0
        self.spot_look = "good" if st["inside"] and st["aligned"] and not st["wrong"] else "in" if st["inside"] else "idle"
        self.sens = self.sensors()
        sd = self.sens[1] if self.gear == "R" else self.sens[0] if self.gear == "D" else 9
        if sd < 1.6 and self.running:
            self.beep_t -= dt
            if self.beep_t <= 0:
                self.play("beep")
                self.beep_t = 0.085 if sd < 0.35 else 0.12 + (sd - 0.35) * 0.42
        else:
            self.beep_t = 0
        if self.running and self.damage >= 100:
            self.finish(False, "Car wrecked. Too many collisions.")
        elif self.running and self.t > self.lv.par * 3:
            self.finish(False, "Time's up.")

    def finish(self, ok, reason=""):
        if self.done:
            return
        self.done, self.running = True, False
        self.thr = 0
        self.hint, self.hint_prog = "", None
        self.gas = self.brake_on = False
        st = self.spot_state()
        self.result = dict(ok=ok, reason=reason, st=st, t=self.t, hits=self.hits, cones=self.cone_hits)
        if ok:
            self.result["parts"], self.result["total"], self.result["stars"] = score(self.t, self.lv.par, st["acc"], self.hits, self.cone_hits)
            self.play("chime")
        else:
            self.result.update(parts=None, total=0, stars=0)
            self.play("fail")
        r = self.result
        sv = self.save
        rec = sv["records"].get(str(self.li))
        r["best"] = False
        if ok and self.mode != "pvp" and (not rec or r["total"] > rec["score"]):
            sv["records"][str(self.li)] = dict(score=r["total"], stars=r["stars"], time=round(self.t, 1))
            r["best"] = True
        if ok and self.mode == "career":
            sv["unlocked"] = max(sv["unlocked"], self.li + 1)
        if self.mode == "vs":
            sp, acc, label = DIFF[self.diff]
            _, gtot, _ = score(self.ghost_total, self.lv.par, acc, 0, 0)
            r["ghost"] = (gtot, self.ghost_total, acc, label)
            r["win"] = ok and r["total"] > gtot
            if r["win"]:
                sv["wins"][self.diff] += 1
        if self.mode == "pvp":
            self.pvp["scores"][self.pvp["turn"]].append(r["total"])
        self.write_save()
        self.result_delay = 0.9 if ok else 0.6

    def write_save(self):
        try:
            with open(SAVE, "w") as f:
                json.dump(self.save, f)
        except Exception:
            pass

    def start_run(self):
        self.load(self.li)
        self.ghost_total = path_time(self.lv.ghost, DIFF[self.diff][0])
        if self.mode == "vs":
            x, z, h, _ = path_pose(self.lv.ghost, DIFF[self.diff][0], 0)
            self.ghost_pose = (x, z, h)
        self.state = "brief"

    def go(self):
        self.state = "play"
        self.shake = 0
        self.hint, self.hint_prog, self.spot_look, self.sens = "", None, "idle", (9, 9)
        if self.mode == "vs":
            self.countdown = 2.4
        else:
            self.running = True
            self.toast("Shift to D, then press GAS  (or press Up)")

    # ---------------------------------------------------------- input
    def pointer_down(self, pid, pos):
        if not self.sounds_ready:
            init_sounds()
            self.sounds_ready = True
        if self.state == "play" and self.running:
            for name, rect in self.controls().items():
                if name == "wheel":
                    if math.dist(pos, rect.center) < rect.w / 2 + 20:
                        self.pointers[pid] = ["wheel", math.atan2(pos[1] - rect.centery, pos[0] - rect.centerx)]
                        return
                elif rect.collidepoint(pos):
                    if name in ("P", "R", "D"):
                        self.shift(name)
                    elif name == "gas":
                        self.gas = True
                        self.pointers[pid] = ["gas"]
                        if self.gear == "P":
                            self.toast("Shift to D or R")
                    elif name == "brake":
                        self.brake_on = True
                        self.pointers[pid] = ["brake"]
                    return
        for rect, fn in self.buttons:
            if rect.collidepoint(pos):
                self.play("click")
                fn()
                return

    def pointer_move(self, pid, pos):
        p = self.pointers.get(pid)
        if p and p[0] == "wheel":
            r = self.controls()["wheel"]
            a = math.atan2(pos[1] - r.centery, pos[0] - r.centerx)
            self.wheel_deg = clamp(self.wheel_deg + math.degrees(wrap(a - p[1])), -450, 450)
            p[1] = a

    def pointer_up(self, pid):
        p = self.pointers.pop(pid, None)
        if p and p[0] == "gas":
            self.gas = False
        if p and p[0] == "brake":
            self.brake_on = False

    def key_down(self, key):
        self.keys.add(key)
        if not self.sounds_ready:
            init_sounds()
            self.sounds_ready = True
        if self.state == "play":
            if key == pg.K_p:
                self.shift("P")
            elif key == pg.K_r:
                self.shift("D" if self.gear == "R" else "R")
            elif key == pg.K_ESCAPE and self.running:
                self.running = False
                self.state = "pause"
        elif self.state == "pause" and key == pg.K_ESCAPE:
            self.resume()
        elif self.state == "brief" and key in (pg.K_RETURN, pg.K_SPACE):
            self.go()

    def resume(self):
        self.state = "play"
        self.running = True

    def controls(self):
        wr = pg.Rect(0, 0, 170, 170)
        wr.bottomleft = (24, H - 20)
        c = dict(wheel=wr)
        x = W - 24
        g = pg.Rect(0, 0, 78, 150); g.bottomright = (x, H - 20); c["gas"] = g
        b = pg.Rect(0, 0, 112, 104); b.bottomright = (g.left - 14, H - 20); c["brake"] = b
        for i, gname in enumerate("DRP"):
            r = pg.Rect(0, 0, 60, 48)
            r.bottomright = (b.left - 16, H - 20 - i * 56)
            c[gname] = r
        return c

    # ---------------------------------------------------------- drawing
    def to_screen(self, x, z):
        return W / 2 - (x - self.cam[0]) * PX, H / 2 - (z - self.cam[1]) * PX

    def draw_world(self, dt):
        lv = self.lv
        screen.fill(lv.env["ground"] if lv.d["env"] == "deck" else (74, 112, 56))
        ox, oy = self.to_screen(lv.X1, lv.Z1)
        sx = sy = 0
        if getattr(self, "shake", 0) > 0:
            sx, sy = random.uniform(-1, 1) * self.shake * 8, random.uniform(-1, 1) * self.shake * 8
            self.shake = max(0, self.shake - dt * 2.5)
        screen.blit(lv.bg, (round(ox + sx), round(oy + sy)))
        off = (sx, sy)
        # target spot (pulsing)
        if self.state != "title":
            img = lv.spot_imgs[getattr(self, "spot_look", "idle")]
            p = self.to_screen(lv.spot.x, lv.spot.z)
            pulse = 200 + int(55 * math.sin(self.t_anim * 4))
            img.set_alpha(pulse)
            screen.blit(img, img.get_rect(center=(p[0] + off[0], p[1] + off[1])))
        # cones
        for k in lv.cones:
            p = self.to_screen(k["x"], k["z"])
            p = (p[0] + off[0], p[1] + off[1])
            if k["tip"] > 0.3:
                ang = -k["dir"]
                dx, dy = -math.sin(k["dir"]) * 0.45 * PX, -math.cos(k["dir"]) * 0.45 * PX
                pg.draw.line(screen, (255, 100, 24), p, (p[0] + dx, p[1] + dy), 9)
                circle(screen, (230, 80, 20), p[0], p[1], 0.2 * PX)
            else:
                circle(screen, (40, 40, 40), p[0] + 3, p[1] + 3, 0.24 * PX)
                circle(screen, (255, 100, 24), p[0], p[1], 0.22 * PX)
                circle(screen, (245, 245, 245), p[0], p[1], 0.13 * PX)
                circle(screen, (255, 120, 40), p[0], p[1], 0.07 * PX)
        # ghost
        if self.mode == "vs" and self.ghost_pose and self.state not in ("title",):
            x, z, h = self.ghost_pose
            img = pg.transform.rotozoom(self.ghost_img, math.degrees(h), 1)
            p = self.to_screen(x, z)
            screen.blit(img, img.get_rect(center=(p[0] + off[0], p[1] + off[1])))
        # guide lines: predicted path of the bumper corners
        c = self.car
        d = 1 if self.gear == "D" else -1 if self.gear == "R" else 0
        if self.state == "play" and self.running and d and self.save.get("guides", True) and abs(self.v) < 4:
            h = c.a
            rx, rz = c.x - math.sin(h) * CAR["rear"], c.z - math.cos(h) * CAR["rear"]
            lz = (CAR["hl"] if d > 0 else -CAR["hl"]) + CAR["rear"]
            k = math.tan(self.steer) / CAR["wb"]
            left, right = [], []
            for i in range(26):
                cs, sn = math.cos(h), math.sin(h)
                left.append(self.to_screen(rx + lz * sn - CAR["hw"] * cs, rz + lz * cs + CAR["hw"] * sn))
                right.append(self.to_screen(rx + lz * sn + CAR["hw"] * cs, rz + lz * cs - CAR["hw"] * sn))
                ds = 0.22 * d
                rx += math.sin(h) * ds
                rz += math.cos(h) * ds
                h += ds * k
            pg.draw.aalines(screen, (255, 210, 74), False, left)
            pg.draw.aalines(screen, (255, 210, 74), False, right)
        # player car + shadow
        p = self.to_screen(c.x, c.z)
        p = (p[0] + off[0], p[1] + off[1])
        deg = math.degrees(c.a)
        sh = pg.transform.rotozoom(self.shadow, deg, 1)
        screen.blit(sh, sh.get_rect(center=(p[0] + 0.35 * PX, p[1] + 0.3 * PX)))
        for sx_ in (-1, 1):   # front tyres follow the steering angle
            o = OBB(*c.to_world(sx_ * 0.81, 1.325), 0.12, 0.33, c.a + self.steer)
            poly(screen, (18, 18, 20), [(q[0] + off[0], q[1] + off[1]) for q in
                                        (self.to_screen(*o.to_world(lx, lz)) for lx, lz in rounded_rect_local(0.12, 0.33, 0.1, 3))])
        img = pg.transform.rotozoom(self.player_img, deg, 1)
        screen.blit(img, img.get_rect(center=p))
        braking = self.brk > 0.05 or (self.state == "play" and self.started and self.gear != "P" and abs(self.v) < 0.01)
        night = lv.env["night"]
        if braking or self.gear == "R" or night:
            for sx_ in (-1, 1):
                tx, tz = c.to_world(sx_ * 0.63, -2.12)
                q = self.to_screen(tx, tz)
                if braking:
                    screen.blit(self.red_glow, self.red_glow.get_rect(center=q), special_flags=pg.BLEND_ADD)
                if self.gear == "R":
                    rx_, rz_ = c.to_world(sx_ * 0.28, -2.15)
                    screen.blit(self.white_glow, self.white_glow.get_rect(center=self.to_screen(rx_, rz_)), special_flags=pg.BLEND_ADD)
        # night lighting: darken, then cut holes for lamps and headlights
        if night:
            dark = self.dark
            dark.fill(night)
            lights = [(x, z, 7.5) for x, z in lv.lamps]
            for sx_ in (-1, 1):
                for dist, rad in ((2.6, 1.6), (4.4, 2.6), (6.6, 3.4)):
                    lights.append((*c.to_world(sx_ * 0.55, dist), rad))
            for x, z, rad in lights:
                q = self.to_screen(x, z)
                rr = int(rad * PX)
                if -rr < q[0] < W + rr and -rr < q[1] < H + rr:
                    g = self.glow_cache.get(rr)
                    if g is None:
                        g = self.glow_cache[rr] = pg.transform.smoothscale(self.glow, (rr * 2, rr * 2))
                    dark.blit(g, g.get_rect(center=q), special_flags=pg.BLEND_RGBA_SUB)
            screen.blit(dark, (0, 0))

    def draw_hud(self):
        st_time = "%.1f" % self.t
        panel(screen, (16, 14, 250, 92))
        text(screen, "%d  %s" % (self.li + 1, self.lv.d["name"].upper()), "xs", MUTED, (30, 24))
        r = text(screen, st_time, "l", INK, (30, 44))
        text(screen, "s   par %d" % self.lv.par, "s", MUTED, (r.right + 6, 54))
        text(screen, "DAMAGE", "xs", MUTED, (30, 82))
        pg.draw.rect(screen, (60, 64, 70), (106, 86, 110, 7), border_radius=4)
        pg.draw.rect(screen, STOP, (106, 86, int(110 * self.damage / 100), 7), border_radius=4)
        # gear + speed
        panel(screen, (16, 114, 170, 56))
        pg.draw.rect(screen, (0, 0, 0), (24, 120, 44, 44), border_radius=9)
        text(screen, self.gear, "l", PAINT, (46, 142), "center")
        text(screen, "%d km/h" % round(abs(self.v) * 3.6), "m", INK, (80, 130))
        # sensors
        panel(screen, (W / 2 - 60, 14, 120, 92))
        f, r_ = self.sens
        for side, d, y0 in ((1, f, 30), (-1, r_, 90)):
            n = 3 if d < 0.4 else 2 if d < 0.9 else 1 if d < 1.6 else 0
            col = STOP if n == 3 else PAINT if n == 2 else GO
            for i in range(3):
                w = 30 + i * 10
                y = y0 - side * i * 7
                pg.draw.rect(screen, col if i < n else (60, 64, 70), (W / 2 - w / 2, y, w, 4), border_radius=2)
        pg.draw.rect(screen, (220, 224, 228), (W / 2 - 11, 44, 22, 36), 2, border_radius=6)
        text(screen, "SENSORS", "xs", MUTED, (W / 2, 100), "midbottom")
        # buttons top right
        self.buttons.append((pg.Rect(W - 70, 14, 54, 54), self.pause))
        panel(screen, (W - 70, 14, 54, 54), radius=12)
        pg.draw.rect(screen, INK, (W - 51, 30, 5, 22))
        pg.draw.rect(screen, INK, (W - 40, 30, 5, 22))
        snd_r = pg.Rect(W - 132, 14, 54, 54)
        self.buttons.append((snd_r, self.toggle_sound))
        panel(screen, snd_r, radius=12)
        text(screen, "SND" if self.save.get("sound", True) else "OFF", "s", INK if self.save.get("sound", True) else MUTED, snd_r.center, "center")
        if self.mode == "vs":
            panel(screen, (W - 330, 80, 314, 40), radius=10)
            circle(screen, (88, 180, 255), W - 310, 100, 6)
            msg = "Computer parked  %.1f s" % self.ghost_total if self.ghost_told else "Computer driving" if self.started else "Computer ready"
            text(screen, msg, "s", INK, (W - 294, 100), "midleft")
        if self.hint:
            img = F["l"].render(self.hint.upper(), True, INK)
            r = img.get_rect(center=(W / 2, H * 0.36))
            panel(screen, r.inflate(36, 30 if self.hint_prog is not None else 20), radius=12)
            screen.blit(img, r.move(0, -4 if self.hint_prog is not None else 0))
            if self.hint_prog is not None:
                pg.draw.rect(screen, (60, 64, 70), (r.left, r.bottom + 2, r.w, 4))
                pg.draw.rect(screen, GO, (r.left, r.bottom + 2, int(r.w * min(1, self.hint_prog)), 4))
        if self.toast_t > 0:
            img = F["m"].render(self.toast_msg.upper(), True, (255, 255, 255))
            r = img.get_rect(center=(W / 2, H * 0.24))
            info = not self.toast_msg.startswith(("Collision", "Cone"))
            panel(screen, r.inflate(28, 18), alpha=235, radius=10, color=(31, 99, 214) if info else (190, 40, 34), border=False)
            screen.blit(img, r)
        # touch controls
        c = self.controls()
        wr = c["wheel"]
        img = pg.transform.rotozoom(self.wheel_img, -self.wheel_deg, 1)
        screen.blit(img, img.get_rect(center=wr.center))
        for name in ("brake", "gas"):
            r = c[name]
            on = self.brake_on if name == "brake" else self.gas
            rr = r.move(0, 2) if on else r
            pg.draw.rect(screen, (24, 28, 33), rr, border_radius=12)
            for y in range(rr.top + 8, rr.bottom - 26, 12):
                pg.draw.rect(screen, (42, 47, 53), (rr.left + 8, y, rr.w - 16, 6), border_radius=3)
            pg.draw.rect(screen, PAINT if on else (80, 86, 92), rr, 2, border_radius=12)
            text(screen, name.upper(), "s", PAINT if on else INK, (rr.centerx, rr.bottom - 12), "midbottom")
        for gname in "PRD":
            r = c[gname]
            on = self.gear == gname
            pg.draw.rect(screen, PAINT if on else (20, 24, 29), r, border_radius=10)
            pg.draw.rect(screen, (80, 86, 92), r, 1, border_radius=10)
            text(screen, gname, "l", (26, 20, 5) if on else MUTED, r.center, "center")
        if not WEB:
            text(screen, "Keys: Up/Down drive   Left/Right steer   Space brake   P park   Esc pause", "xs", MUTED, (W / 2, H - 12), "midbottom")

    def toggle_sound(self):
        self.save["sound"] = not self.save.get("sound", True)
        self.write_save()
        if not self.save["sound"] and SND:
            pg.mixer.stop()

    def pause(self):
        if self.running:
            self.running = False
            self.state = "pause"

    # menus -------------------------------------------------------------
    def button(self, rect, label, fn, primary=False, sub=None, size="m", enabled=True):
        r = pg.Rect(rect)
        col = SIGN if primary else (36, 42, 50)
        pg.draw.rect(screen, col if enabled else (28, 32, 38), r, border_radius=10)
        pg.draw.rect(screen, SIGN_HI if primary else (70, 78, 88), r, 1, border_radius=10)
        if sub:
            text(screen, label.upper(), size, INK if enabled else MUTED, (r.left + 18, r.centery), "midleft")
            text(screen, sub, "xs", (220, 230, 250) if primary else MUTED, (r.right - 16, r.centery), "midright")
        else:
            text(screen, label.upper(), size, INK if enabled else MUTED, r.center, "center")
        if enabled:
            self.buttons.append((r, fn))
        return r

    def dim(self, a=150):
        s = self.dim_cache.get(a)
        if s is None:
            s = self.dim_cache[a] = pg.Surface((W, H), pg.SRCALPHA)
            s.fill((8, 11, 14, a))
        screen.blit(s, (0, 0))

    def stars(self, n, pos, size="l"):
        x, y = pos
        for i in range(3):
            draw_star(screen, x + i * 46, y, 18, PAINT if i < n else (60, 64, 70))

    def to_menu(self):
        self.mode = "career" if self.mode == "pvp" else self.mode
        self.load(self.li, attract=True)
        self.state = "title"

    def draw_title(self):
        self.dim(90)
        if self.title_grad is None:
            self.title_grad = pg.Surface((W, H), pg.SRCALPHA)
            for x in range(0, 700, 4):
                pg.draw.rect(self.title_grad, (10, 13, 17, int(235 * (1 - x / 700) ** 1.2)), (x, 0, 4, H))
        screen.blit(self.title_grad, (0, 0))
        # showroom car, plate reads OBI P
        ang = 18 + math.sin(self.t_anim * 0.6) * 10
        img = pg.transform.rotozoom(self.show_img, ang, 1)
        sh = pg.transform.rotozoom(self.big_shadow, ang, 1)
        screen.blit(sh, sh.get_rect(center=(W - 300 + 16, H / 2 + 22)))
        screen.blit(img, img.get_rect(center=(W - 300, H / 2)))
        ps = pg.Rect(70, 70, 92, 92)
        pg.draw.rect(screen, (255, 255, 255), ps, border_radius=16)
        pg.draw.rect(screen, SIGN, ps.inflate(-8, -8), border_radius=13)
        text(screen, "P", "xxl", (255, 255, 255), (ps.centerx + 2, ps.centery + 6), "center")
        text(screen, "PRECISION", "xl", INK, (180, 70))
        text(screen, "PARKING", "xl", INK, (180, 114))
        text(screen, "A DRIVING-SCHOOL PARKING SIMULATOR", "xs", MUTED, (182, 162))
        unlocked_stars = sum(r["stars"] for r in self.save["records"].values())
        y = 214
        self.button((70, y, 420, 64), "Career", lambda: self.open_levels("career"), True, "%d / 24 stars" % unlocked_stars, "l"); y += 76
        self.button((70, y, 420, 64), "Vs Computer", lambda: self.open_levels("vs"), False, "Beat the ghost car", "l"); y += 76
        self.button((70, y, 420, 64), "2 Players", lambda: setattr(self, "state", "pvpsetup"), False, "Pass the phone", "l"); y += 84
        self.button((70, y, 130, 46), "Records", lambda: setattr(self, "state", "records"), size="s")
        self.button((215, y, 130, 46), "How to play", lambda: setattr(self, "state", "help"), size="s")
        self.button((360, y, 130, 46), "Sound: " + ("on" if self.save.get("sound", True) else "off"), self.toggle_sound, size="s"); y += 56
        self.button((70, y, 205, 46), "Guides: " + ("on" if self.save.get("guides", True) else "off"), self.toggle_guides, size="s")
        text(screen, "a game by Obi Promise  -  CSC 131", "xs", MUTED, (70, H - 34))

    def toggle_guides(self):
        self.save["guides"] = not self.save.get("guides", True)
        self.write_save()

    def open_levels(self, mode):
        self.mode = mode
        self.state = "levels"

    def draw_levels(self):
        self.dim(170)
        text(screen, "VS COMPUTER" if self.mode == "vs" else "CAREER", "s", MUTED, (80, 48))
        text(screen, "PICK A LESSON TO RACE" if self.mode == "vs" else "CHOOSE A LESSON", "xl", INK, (80, 70))
        self.button((W - 200, 56, 120, 46), "Back", self.to_menu, size="s")
        y0 = 150
        if self.mode == "vs":
            text(screen, "COMPUTER DIFFICULTY", "xs", MUTED, (80, 146))
            for i, dk in enumerate(("easy", "medium", "hard")):
                self.button((80 + i * 150, 166, 140, 44), DIFF[dk][2], lambda dk=dk: setattr(self, "diff", dk), dk == self.diff, size="s")
            y0 = 236
        unlocked = self.save["unlocked"]
        cw, ch = (W - 160 - 3 * 20) / 4, 150
        for i, L in enumerate(LEVELS):
            x, y = 80 + (i % 4) * (cw + 20), y0 + (i // 4) * (ch + 20)
            locked = self.mode == "career" and i > unlocked
            r = pg.Rect(x, y, cw, ch)
            pg.draw.rect(screen, (30, 36, 43) if not locked else (22, 26, 31), r, border_radius=12)
            pg.draw.rect(screen, (70, 78, 88), r, 1, border_radius=12)
            pg.draw.lines(screen, (200, 200, 195), False, [(x + 14, y), (x + 14, y + 8), (x + cw - 14, y + 8), (x + cw - 14, y)], 3)
            text(screen, "%02d" % (i + 1), "l", PAINT if not locked else MUTED, (x + 16, y + 20))
            text(screen, L["name"].upper(), "m", INK if not locked else MUTED, (x + 16, y + 56))
            text(screen, "Locked" if locked else KIND[L["kind"]], "xs", MUTED, (x + 16, y + 84))
            rec = self.save["records"].get(str(i))
            for k in range(3):
                draw_star(screen, x + 28 + k * 30, y + 124, 11, PAINT if rec and k < rec["stars"] else (60, 64, 70))
            if not locked:
                self.buttons.append((r, lambda i=i: self.pick(i)))

    def pick(self, i):
        self.li = i
        self.start_run()

    def draw_brief(self):
        self.dim(130)
        r = pg.Rect(0, 0, 640, 330)
        r.center = (W / 2, H / 2)
        panel(screen, r, 235)
        L = self.lv.d
        eyebrow = ("%s  -  ROUND %d" % (self.pvp["names"][self.pvp["turn"]].upper(), self.pvp["round"] + 1)) if self.mode == "pvp" else "LESSON %d OF %d" % (self.li + 1, len(LEVELS))
        text(screen, eyebrow, "xs", MUTED, (r.left + 28, r.top + 26))
        text(screen, L["name"].upper(), "xl", INK, (r.left + 28, r.top + 48))
        x = r.left + 28
        for label, col in ((KIND[L["kind"]], SIGN), ("Par %d s" % self.lv.par, (120, 96, 20))) + ((("Computer: " + DIFF[self.diff][2], (60, 66, 74)),) if self.mode == "vs" else ()):
            img = F["xs"].render(label.upper(), True, INK)
            tr = pg.Rect(x, r.top + 116, img.get_width() + 24, 30)
            pg.draw.rect(screen, col, tr, border_radius=15)
            screen.blit(img, img.get_rect(center=tr.center))
            x = tr.right + 10
        msg = L["text"] + (" The blue ghost car is the computer. Beat its score." if self.mode == "vs" else "")
        for j, line in enumerate(wrap_text(msg, "s", r.w - 56)):
            text(screen, line, "s", (201, 209, 216), (r.left + 28, r.top + 166 + j * 26))
        self.button((r.left + 28, r.bottom - 76, 280, 52), "Back", self.to_menu if self.mode == "pvp" else (lambda: self.open_levels(self.mode)))
        self.button((r.right - 308, r.bottom - 76, 280, 52), "Start", self.go, True)

    def draw_result(self):
        self.dim(140)
        res = self.result
        r = pg.Rect(0, 0, 600, 520)
        r.center = (W / 2, H / 2)
        panel(screen, r, 238)
        who = ("  -  " + self.pvp["names"][self.pvp["turn"]].upper()) if self.mode == "pvp" else ""
        text(screen, "LESSON %d  -  %s%s" % (self.li + 1, self.lv.d["name"].upper(), who), "xs", MUTED, (r.left + 28, r.top + 24))
        title = ("PERFECT PARK" if res["stars"] == 3 else "NICELY PARKED" if res["stars"] == 2 else "PARKED") if res["ok"] else "NOT PARKED"
        text(screen, title, "xl", INK, (r.left + 28, r.top + 44))
        for k in range(3):
            draw_star(screen, r.left + 48 + k * 48, r.top + 132, 20, PAINT if k < res["stars"] else (60, 64, 70))
        sr = text(screen, str(res["total"]), "xl", INK, (r.right - 150, r.top + 106))
        text(screen, "points", "s", MUTED, (sr.right + 6, sr.bottom - 26))
        y = r.top + 176
        rows = []
        if res["ok"]:
            p, st = res["parts"], res["st"]
            rows.append(("Parked", "+%d" % p["base"], False))
            rows.append(("Accuracy %d%%  (%.1f deg, %d cm off centre)" % (st["acc"], st["ang"], abs(st["lx"]) * 100), "+%d" % p["acc"], False))
            rows.append(("Time %.1f s  (par %d s)" % (res["t"], self.lv.par), "+%d" % p["time"], False))
            if res["hits"]:
                rows.append(("Collisions x %d" % res["hits"], str(p["hits"]), True))
            if res["cones"]:
                rows.append(("Cones x %d" % res["cones"], str(p["cones"]), True))
            if res["best"]:
                rows.append(("New personal best", "", False))
        else:
            rows.append((res["reason"] or "Run ended", "0", True))
        for a, b, neg in rows:
            text(screen, a, "s", MUTED, (r.left + 28, y))
            text(screen, b, "s", STOP if neg else INK, (r.right - 28, y), "topright")
            y += 28
        if self.mode == "vs":
            gtot, gt, acc, label = res["ghost"]
            box = pg.Rect(r.left + 28, y + 6, r.w - 56, 70)
            panel(screen, box, 120, 10, (40, 46, 54))
            text(screen, "YOU BEAT THE COMPUTER" if res["win"] else "COMPUTER WINS", "m", GO if res["win"] else STOP, (box.left + 14, box.top + 10))
            text(screen, "Computer (%s): %d pts  -  %.1f s  -  %d%% accuracy" % (label, gtot, gt, acc), "xs", MUTED, (box.left + 14, box.top + 44))
        if self.mode == "pvp" and self.pvp["turn"] == 1:
            a, b = self.pvp["scores"][0][-1], self.pvp["scores"][1][-1]
            n = self.pvp["names"]
            box = pg.Rect(r.left + 28, y + 6, r.w - 56, 70)
            panel(screen, box, 120, 10, (40, 46, 54))
            text(screen, "ROUND TIED" if a == b else "%s TAKES THE ROUND" % n[0 if a > b else 1].upper(), "m", GO, (box.left + 14, box.top + 10))
            text(screen, "%s %d  -  %s %d" % (n[0], a, n[1], b), "xs", MUTED, (box.left + 14, box.top + 44))
        by = r.bottom - 76
        if self.mode == "pvp":
            self.button((r.left + 28, by, r.w - 56, 52), "Continue", self.pvp_next, True)
        else:
            bw = (r.w - 56 - 20) / 3
            self.button((r.left + 28, by, bw, 52), "Menu", self.to_menu)
            self.button((r.left + 38 + bw, by, bw, 52), "Retry", self.start_run, not res["ok"] or self.mode == "vs")
            if self.mode == "career" and res["ok"] and self.li < len(LEVELS) - 1:
                self.button((r.left + 48 + 2 * bw, by, bw, 52), "Next", lambda: self.pick(self.li + 1), True)
            else:
                self.button((r.left + 48 + 2 * bw, by, bw, 52), "Lessons", lambda: self.open_levels(self.mode))

    def draw_pause(self):
        self.dim(150)
        r = pg.Rect(0, 0, 400, 300)
        r.center = (W / 2, H / 2)
        panel(screen, r, 238)
        text(screen, "PAUSED", "xl", INK, (r.centerx, r.top + 30), "midtop")
        self.button((r.left + 30, r.top + 100, r.w - 60, 50), "Resume", self.resume, True)
        self.button((r.left + 30, r.top + 160, r.w - 60, 50), "Restart", self.start_run)
        self.button((r.left + 30, r.top + 220, r.w - 60, 50), "Quit to menu", self.to_menu)

    def draw_pvpsetup(self):
        self.dim(170)
        r = pg.Rect(0, 0, 560, 360)
        r.center = (W / 2, H / 2)
        panel(screen, r, 238)
        text(screen, "PASS AND PLAY", "xs", MUTED, (r.left + 28, r.top + 26))
        text(screen, "2 PLAYERS", "xl", INK, (r.left + 28, r.top + 46))
        for j, line in enumerate(wrap_text("Both players park the same spots on this device. Highest total after all rounds wins.", "s", r.w - 56)):
            text(screen, line, "s", (201, 209, 216), (r.left + 28, r.top + 112 + j * 26))
        rounds = getattr(self, "pvp_rounds", 3)
        text(screen, "ROUNDS", "s", MUTED, (r.left + 28, r.top + 196))
        for i, n in enumerate((3, 5)):
            self.button((r.left + 140 + i * 110, r.top + 184, 96, 44), str(n), lambda n=n: setattr(self, "pvp_rounds", n), n == rounds, size="s")
        self.button((r.left + 28, r.bottom - 80, 200, 52), "Back", self.to_menu)
        self.button((r.right - 288, r.bottom - 80, 260, 52), "Start match", self.pvp_start, True)

    def pvp_start(self):
        rounds = getattr(self, "pvp_rounds", 3)
        lv = random.sample(range(len(LEVELS)), rounds)
        self.mode = "pvp"
        self.pvp = dict(names=["Player 1", "Player 2"], levels=sorted(lv), round=0, turn=0, scores=[[], []])
        self.state = "pass"

    def pvp_next(self):
        p = self.pvp
        p["turn"] += 1
        if p["turn"] > 1:
            p["turn"] = 0
            p["round"] += 1
        self.state = "final" if p["round"] >= len(p["levels"]) else "pass"

    def draw_pass(self):
        self.dim(170)
        p = self.pvp
        r = pg.Rect(0, 0, 520, 320)
        r.center = (W / 2, H / 2)
        panel(screen, r, 238)
        li = p["levels"][p["round"]]
        text(screen, "ROUND %d OF %d  -  %s" % (p["round"] + 1, len(p["levels"]), LEVELS[li]["name"].upper()), "xs", MUTED, (r.centerx, r.top + 28), "midtop")
        text(screen, "PASS THE PHONE TO", "l", INK, (r.centerx, r.top + 66), "midtop")
        text(screen, p["names"][p["turn"]].upper(), "xl", PAINT, (r.centerx, r.top + 112), "midtop")
        tot = [sum(s) for s in p["scores"]]
        text(screen, "%s %d  -  %s %d" % (p["names"][0], tot[0], p["names"][1], tot[1]), "s", MUTED, (r.centerx, r.top + 184), "midtop")

        def ready():
            self.li = li
            self.start_run()
        self.button((r.left + 30, r.bottom - 80, r.w - 60, 52), "I'm ready", ready, True)

    def draw_final(self):
        self.dim(170)
        p = self.pvp
        tot = [sum(s) for s in p["scores"]]
        r = pg.Rect(0, 0, 600, 200 + 34 * len(p["levels"]))
        r.center = (W / 2, H / 2)
        panel(screen, r, 238)
        text(screen, "MATCH OVER", "xs", MUTED, (r.centerx, r.top + 24), "midtop")
        text(screen, "IT'S A TIE" if tot[0] == tot[1] else "%s WINS" % p["names"][0 if tot[0] > tot[1] else 1].upper(), "xl", PAINT, (r.centerx, r.top + 46), "midtop")
        y = r.top + 112
        for j, li in enumerate(p["levels"]):
            text(screen, LEVELS[li]["name"], "s", MUTED, (r.left + 40, y))
            text(screen, str(p["scores"][0][j]), "s", INK, (r.left + 380, y), "topright")
            text(screen, str(p["scores"][1][j]), "s", INK, (r.right - 40, y), "topright")
            y += 30
        text(screen, "TOTAL  %s %d  -  %s %d" % (p["names"][0], tot[0], p["names"][1], tot[1]), "s", INK, (r.centerx, y + 4), "midtop")
        self.button((r.left + 30, r.bottom - 72, 250, 50), "Menu", self.to_menu)
        self.button((r.right - 280, r.bottom - 72, 250, 50), "Rematch", self.rematch, True)

    def rematch(self):
        p = self.pvp
        self.pvp = dict(names=p["names"], levels=p["levels"], round=0, turn=0, scores=[[], []])
        self.state = "pass"

    def draw_records(self):
        self.dim(180)
        text(screen, "RECORDS", "xl", INK, (80, 60))
        self.button((W - 200, 56, 120, 46), "Back", self.to_menu, size="s")
        y = 140
        for col, x in (("LESSON", 80), ("BEST", 560), ("STARS", 700), ("TIME", 860)):
            text(screen, col, "xs", MUTED, (x, y))
        y += 30
        for i, L in enumerate(LEVELS):
            rec = self.save["records"].get(str(i))
            text(screen, "%d  %s" % (i + 1, L["name"]), "s", INK, (80, y))
            text(screen, str(rec["score"]) if rec else "-", "s", INK, (560, y))
            for k in range(3):
                draw_star(screen, 710 + k * 26, y + 9, 9, PAINT if rec and k < rec["stars"] else (60, 64, 70))
            text(screen, ("%.1f s" % rec["time"]) if rec else "-", "s", INK, (860, y))
            y += 36
        w = self.save["wins"]
        text(screen, "Wins vs computer:  Easy %d   Medium %d   Hard %d" % (w["easy"], w["medium"], w["hard"]), "s", MUTED, (80, y + 16))

    def draw_help(self):
        self.dim(180)
        text(screen, "HOW TO PLAY", "xl", INK, (80, 60))
        self.button((W - 200, 56, 120, 46), "Back", self.to_menu, size="s")
        lines = [
            "Goal: stop fully inside the glowing bay, lined up with its arrow, then hold still or shift to P.",
            "Phone: turn the steering wheel with your thumb, press GAS and BRAKE, tap P / R / D for gears.",
            "The wheel re-centres by itself as the car rolls, like a real car.",
            "Keyboard: Up drives forward, Down reverses (gear changes automatically when stopped),",
            "Left/Right steer, Space brakes, P parks, R flips the gear, Esc pauses.",
            "Scoring: 300 for parking, up to 500 for accuracy, up to 400 for speed.",
            "Each collision costs 100 and each cone 40. Too much damage wrecks the car.",
            "Yellow guide lines show where your bumper corners will go. The beeper speeds up near obstacles.",
        ]
        for j, line in enumerate(lines):
            text(screen, line, "s", (201, 209, 216), (80, 140 + j * 34))

    # ---------------------------------------------------------- frame
    def frame(self, dt):
        self.t_anim += dt
        self.toast_t = max(0, self.toast_t - dt)
        self.buttons = []
        if self.state == "play":
            if self.mode == "vs" and getattr(self, "countdown", 0) > 0:
                self.countdown -= dt
                if self.countdown <= 0:
                    self.running = self.started = True
            if self.running:
                self.update_play(dt)
            if self.done:
                self.result_delay -= dt
                if self.result_delay <= 0:
                    self.state = "result"
            self.cam_follow(dt)
        elif self.state == "title":
            sp = self.lv.spot
            self.cam[0] = sp.x + math.sin(self.t_anim * 0.12) * 10 - 6
            self.cam[1] = sp.z - 4 + math.cos(self.t_anim * 0.09) * 5
        else:
            self.cam_follow(dt)
        self.draw_world(dt)
        screen.blit(self.vignette, (0, 0))
        if SND.get("engine"):
            on = self.state == "play" and self.running and self.save.get("sound", True)
            if on and not getattr(self, "engine_on", False):
                SND["engine"].play(-1)
                self.engine_on = True
            elif not on and getattr(self, "engine_on", False):
                SND["engine"].stop()
                self.engine_on = False
            if on:
                SND["engine"].set_volume(0.35 + 0.65 * self.thr)
        if self.state in ("play", "pause", "result"):
            self.draw_hud()
        if self.state == "play" and self.mode == "vs" and getattr(self, "countdown", 0) > 0:
            n = math.ceil(self.countdown / 0.8)
            text(screen, str(n), "xxl", INK, (W / 2, H / 2), "center")
        getattr(self, "draw_" + self.state, lambda: None)()

    def cam_follow(self, dt):
        c = self.car
        tx, tz = c.x + math.sin(c.a) * self.v * 0.5, c.z + math.cos(c.a) * self.v * 0.5
        k = 1 - math.exp(-dt * 4)
        self.cam[0] += (tx - self.cam[0]) * k
        self.cam[1] += (tz - self.cam[1]) * k


def draw_star(surf, x, y, r, col):
    pts = []
    for i in range(10):
        a = -R2 + i * PI / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((x + math.cos(a) * rr, y + math.sin(a) * rr))
    poly(surf, col, pts)


def wrap_text(s, f, width):
    words, lines, cur = s.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if F[f].size(t)[0] > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


async def main():
    game = Game()
    while True:
        for e in pg.event.get():
            if e.type == pg.QUIT:
                pg.quit()
                return
            if e.type == pg.KEYDOWN:
                game.key_down(e.key)
            elif e.type == pg.KEYUP:
                game.keys.discard(e.key)
            elif e.type == pg.MOUSEBUTTONDOWN and not getattr(e, "touch", False) and e.button == 1:
                game.pointer_down("m", e.pos)
            elif e.type == pg.MOUSEMOTION and not getattr(e, "touch", False):
                game.pointer_move("m", e.pos)
            elif e.type == pg.MOUSEBUTTONUP and not getattr(e, "touch", False) and e.button == 1:
                game.pointer_up("m")
            elif e.type == pg.FINGERDOWN:
                game.pointer_down(e.finger_id, (e.x * W, e.y * H))
            elif e.type == pg.FINGERMOTION:
                game.pointer_move(e.finger_id, (e.x * W, e.y * H))
            elif e.type == pg.FINGERUP:
                game.pointer_up(e.finger_id)
            elif e.type == pg.WINDOWFOCUSLOST:
                game.keys.clear()
                game.pause()
        game.frame(min(clock.tick(60) / 1000, 0.05))
        pg.display.flip()
        await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())
