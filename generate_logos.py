"""Generate DarktableMCP logo options.

Run with:  python generate_logos.py [output_dir]

If no output dir is given, logos are written next to the script under
``./logos``.  Fonts are auto-detected across Windows / macOS / Linux.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SIZE = 512
BG    = (13, 13, 13)        # near-black
RED   = (224, 27, 36)       # Darktable red
RED2  = (180, 20, 28)       # darker red
WHITE = (255, 255, 255)
GREY  = (180, 180, 180)
DIM   = (60, 60, 60)
ORANGE= (230, 100, 30)


def _output_dir() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    return Path(__file__).parent / "logos"


def _find_bold_font(size: int) -> ImageFont.ImageFont:
    """Return a bold font available on the current OS, with sane fallbacks."""
    candidates: list[str] = []
    if os.name == "nt":
        candidates += [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
        ]
    elif sys.platform == "darwin":
        candidates += [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _find_regular_font(size: int) -> ImageFont.ImageFont:
    candidates: list[str] = []
    if os.name == "nt":
        candidates += [r"C:\Windows\Fonts\arial.ttf"]
    elif sys.platform == "darwin":
        candidates += [
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def new(bg=BG):
    img = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    return img, ImageDraw.Draw(img)


def save(img: Image.Image, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.png"
    bg = Image.new("RGB", (SIZE, SIZE), BG)
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img)
    bg.save(path)
    print(f"Saved {path}")
    return path


def circle_points(cx, cy, r, n=64):
    return [(cx + r * math.cos(2*math.pi*i/n),
             cy + r * math.sin(2*math.pi*i/n)) for i in range(n)]


def draw_ring(draw, cx, cy, r_out, r_in, color, n=256):
    for i in range(n):
        a0 = 2*math.pi*i/n
        a1 = 2*math.pi*(i+1)/n
        pts = [
            (cx + r_out*math.cos(a0), cy + r_out*math.sin(a0)),
            (cx + r_out*math.cos(a1), cy + r_out*math.sin(a1)),
            (cx + r_in *math.cos(a1), cy + r_in *math.sin(a1)),
            (cx + r_in *math.cos(a0), cy + r_in *math.sin(a0)),
        ]
        draw.polygon(pts, fill=color)


def draw_aperture_blade(draw, cx, cy, r, angle_deg, color):
    a = math.radians(angle_deg)
    spread = math.radians(28)
    tip_r  = r * 0.18
    pts = []
    for da in np.linspace(-spread, spread, 20):
        pts.append((cx + r * math.cos(a + da), cy + r * math.sin(a + da)))
    for da in np.linspace(spread, -spread, 20):
        pts.append((cx + tip_r * math.cos(a + math.pi + da),
                    cy + tip_r * math.sin(a + math.pi + da)))
    draw.polygon(pts, fill=color)


def logo1():
    img, draw = new()
    cx = cy = SIZE // 2

    for r in range(220, 200, -1):
        alpha = int(60 * (r - 200) / 20)
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=RED + (alpha,), width=1)

    draw_ring(draw, cx, cy, 215, 195, (30, 30, 30))
    draw_ring(draw, cx, cy, 195, 192, RED)

    for i in range(9):
        angle = i * 40
        draw_aperture_blade(draw, cx, cy, 185, angle, (20, 20, 20))
        draw_aperture_blade(draw, cx, cy, 170, angle + 20, (35, 8, 8))

    for r in range(90, 0, -1):
        t = r / 90
        col = (int(10 + t*20), int(10 + t*20), int(20 + t*40))
        draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=col)

    rng = np.random.default_rng(42)
    for _ in range(12):
        a = rng.uniform(0, 2*math.pi)
        r0 = 95
        r1 = 185 + rng.integers(0, 20)
        x0, y0 = cx + r0*math.cos(a), cy + r0*math.sin(a)
        mid_r = rng.uniform(130, 160)
        a_bend = a + rng.uniform(-0.3, 0.3)
        xm, ym = cx + mid_r*math.cos(a_bend), cy + mid_r*math.sin(a_bend)
        x1, y1 = cx + r1*math.cos(a_bend + rng.uniform(-0.1,0.1)), cy + r1*math.sin(a_bend + rng.uniform(-0.1,0.1))
        col = RED + (120,) if rng.random() > 0.5 else (80, 80, 80, 120)
        draw.line([(x0,y0),(xm,ym),(x1,y1)], fill=col, width=1)
        draw.ellipse([x1-3, y1-3, x1+3, y1+3], fill=RED+(180,))

    draw_ring(draw, cx, cy, 55, 50, RED)
    draw.ellipse([cx-49, cy-49, cx+49, cy+49], fill=BG)

    font = _find_bold_font(28)
    font_sm = _find_regular_font(13)
    draw.text((cx, cy-8), "MCP", font=font, fill=WHITE, anchor="mm")
    draw.text((cx, cy+18), "darktable", font=font_sm, fill=GREY, anchor="mm")

    for i in range(36):
        a = math.radians(i * 10)
        r_o = 192
        r_i = 196 if i % 3 == 0 else 194
        draw.line([(cx+r_o*math.cos(a), cy+r_o*math.sin(a)),
                   (cx+r_i*math.cos(a), cy+r_i*math.sin(a))],
                  fill=RED if i % 3 == 0 else DIM, width=1)

    return img


def logo2():
    img, draw = new()
    cx = cy = SIZE // 2

    arr = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
    y_idx, x_idx = np.ogrid[:SIZE, :SIZE]
    dist = np.sqrt((x_idx - cx)**2 + (y_idx - cy)**2)
    mask = dist < 220
    t = np.clip(1 - dist / 220, 0, 1)
    arr[mask, 0] = (22 * t[mask]).astype(np.uint8)
    arr[mask, 1] = (22 * t[mask]).astype(np.uint8)
    arr[mask, 2] = (35 * t[mask]).astype(np.uint8)
    arr[mask, 3] = 255
    base = Image.fromarray(arr, "RGBA")
    img = Image.alpha_composite(img, base)
    draw = ImageDraw.Draw(img)

    draw_ring(draw, cx, cy, 222, 218, RED)
    draw_ring(draw, cx, cy, 218, 216, (80, 10, 10))

    node_positions = []
    radii = [0, 70, 140]
    counts = [1, 6, 12]
    for r, n in zip(radii, counts):
        for i in range(n):
            a = 2*math.pi*i/n + (math.pi/6 if n==6 else math.pi/12)
            nx, ny = cx + r*math.cos(a), cy + r*math.sin(a)
            node_positions.append((nx, ny))

    rng = np.random.default_rng(7)
    all_pos = np.array([(p[0], p[1]) for p in node_positions])
    for i, (x1, y1) in enumerate(node_positions):
        dists = np.sqrt((all_pos[:,0]-x1)**2 + (all_pos[:,1]-y1)**2)
        close = np.where((dists < 120) & (dists > 1))[0]
        for j in close:
            x2, y2 = node_positions[j]
            alpha = rng.integers(60, 130)
            col = RED + (alpha,) if rng.random() > 0.4 else (100, 100, 100, alpha)
            draw.line([(x1,y1),(x2,y2)], fill=col, width=1)

    for i, (nx, ny) in enumerate(node_positions):
        r_node = 10 if i == 0 else (6 if i < 7 else 4)
        col = RED if i == 0 else (WHITE if i < 7 else GREY)
        draw.ellipse([nx-r_node, ny-r_node, nx+r_node, ny+r_node], fill=col)
        if i == 0:
            draw.ellipse([nx-4, ny-4, nx+4, ny+4], fill=BG)

    for i in range(18):
        a = math.radians(i * 20)
        r = 200
        nx, ny = cx + r*math.cos(a), cy + r*math.sin(a)
        draw.ellipse([nx-3, ny-3, nx+3, ny+3], fill=DIM)
        node_positions.append((nx, ny))

    return img


def logo3():
    img, draw = new()
    cx = cy = SIZE // 2

    ring_specs = [(210, 4, DIM), (195, 2, RED), (178, 8, (25,25,25)),
                  (165, 2, DIM), (140, 3, RED2), (100, 2, DIM)]
    for r, w, col in ring_specs:
        for dr in range(w):
            draw.ellipse([cx-(r-dr), cy-(r-dr), cx+(r-dr), cy+(r-dr)],
                         outline=col, width=1)

    for i in range(16):
        a = math.radians(i * 22.5)
        r0 = 108
        r1 = 185 + (15 if i % 2 == 0 else 0)
        w  = 2 if i % 2 == 0 else 1
        col = RED if i % 4 == 0 else DIM
        draw.line([(cx + r0*math.cos(a), cy + r0*math.sin(a)),
                   (cx + r1*math.cos(a), cy + r1*math.sin(a))],
                  fill=col, width=w)

    arr = np.array(img)
    y_idx, x_idx = np.ogrid[:SIZE, :SIZE]
    dist = np.sqrt((x_idx - cx)**2 + (y_idx - cy)**2)
    inner = dist < 98
    t = 1 - dist[inner] / 98
    arr[inner, 0] = np.clip(arr[inner, 0] + (t * 30).astype(int), 0, 255)
    arr[inner, 2] = np.clip(arr[inner, 2] + (t * 60).astype(int), 0, 255)
    img = Image.fromarray(arr.astype(np.uint8))
    draw = ImageDraw.Draw(img)

    for i in range(6):
        a = math.radians(i * 60)
        x1 = cx + 95 * math.cos(a)
        y1 = cy + 95 * math.sin(a)
        draw.line([(cx, cy), (x1, y1)], fill=RED + (160,), width=2)
        draw.ellipse([x1-5, y1-5, x1+5, y1+5], fill=RED)

    draw.ellipse([cx-8, cy-8, cx+8, cy+8], fill=WHITE)
    draw.ellipse([cx-3, cy-3, cx+3, cy+3], fill=BG)

    font_lg = _find_bold_font(34)
    font_sm = _find_regular_font(14)
    draw.rounded_rectangle([cx-90, cy+108, cx+90, cy+145], radius=8,
                            fill=(30, 30, 30))
    draw.text((cx, cy+120), "DarktableMCP", font=font_sm, fill=WHITE, anchor="mm")
    draw.rounded_rectangle([cx-90, cy+108, cx+90, cy+111], radius=0, fill=RED)

    return img


def logo4():
    img, draw = new()
    cx = cy = SIZE // 2

    draw.ellipse([cx-200, cy-200, cx+200, cy+200], fill=RED)
    draw.ellipse([cx-160, cy-160, cx+160, cy+160], fill=BG)

    for i in range(8):
        angle = i * 45
        draw_aperture_blade(draw, cx, cy, 152, angle, WHITE)
        draw_aperture_blade(draw, cx, cy, 145, angle + 22.5, (200, 200, 200))

    draw.ellipse([cx-55, cy-55, cx+55, cy+55], fill=BG)

    nodes = [(0,0), (28,-20), (-28,-20), (0,32), (36,18), (-36,18)]
    for i, (dx, dy) in enumerate(nodes):
        nx, ny = cx+dx, cy+dy
        for j, (dx2, dy2) in enumerate(nodes):
            if j > i:
                draw.line([(nx,ny),(cx+dx2,cy+dy2)],
                          fill=RED+(180,), width=1)
    for dx, dy in nodes:
        r = 6 if (dx, dy) == (0, 0) else 3
        draw.ellipse([cx+dx-r, cy+dy-r, cx+dx+r, cy+dy+r], fill=WHITE)

    for i in range(24):
        a = math.radians(i * 15)
        r_o = 198
        r_i = 194 if i % 3 != 0 else 190
        draw.line([(cx+r_o*math.cos(a), cy+r_o*math.sin(a)),
                   (cx+r_i*math.cos(a), cy+r_i*math.sin(a))],
                  fill=WHITE if i % 3 == 0 else (200,200,200), width=1)

    return img


def main() -> int:
    out_dir = _output_dir()
    paths: list[Path] = []
    for i, fn in enumerate([logo1, logo2, logo3, logo4], 1):
        img = fn()
        paths.append(save(img, out_dir, f"logo_option_{i}"))
    print(f"Done. {len(paths)} logos in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
