#!/usr/bin/env python3
"""System overview figure for the slides: map pipeline, input modes, game engine.
   python3 tools/make_overview.py docs/slides/fig_overview.png"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

out = sys.argv[1] if len(sys.argv) > 1 else "docs/slides/fig_overview.png"
fig = plt.figure(figsize=(20, 11.25))           # 16:9
ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 100); ax.set_ylim(0, 56.25); ax.axis("off")

C = {"map": "#3d7be0", "cam": "#2fa36b", "aud": "#d9822b", "key": "#7a7f8c", "mp": "#a0a4ad", "game": "#8e44ad"}
LIGHT = {k: matplotlib.colors.to_rgba(v, 0.12) for k, v in C.items()}


def box(x, y, w, h, title, body="", key="map", dashed=False, tsize=12.5, bsize=10.5, top=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=0.8",
                                fc=LIGHT[key], ec=C[key], lw=2, ls="--" if dashed else "-"))
    if top:
        ax.text(x + w / 2, y + h - 1.0, title, ha="center", va="top", fontsize=tsize, weight="bold", color="#1d1f24")
        if body:
            ax.text(x + w / 2, y + h - 3.2, body, ha="center", va="top", fontsize=bsize, color="#3a3d45", linespacing=1.35)
        return
    nb = body.count("\n") + 1 if body else 0
    ty = y + h / 2 + (0.95 * nb if body else 0)
    ax.text(x + w / 2, ty, title, ha="center", va="center", fontsize=tsize, weight="bold", color="#1d1f24")
    if body:
        ax.text(x + w / 2, ty - 1.4, body, ha="center", va="top", fontsize=bsize, color="#3a3d45", linespacing=1.35)


def arrow(x0, y0, x1, y1, key="map", text=None, dashed=False, rad=0.0):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=18, lw=2,
                                 color=C[key], ls="--" if dashed else "-", connectionstyle=f"arc3,rad={rad}"))
    if text:
        ax.text((x0 + x1) / 2, (y0 + y1) / 2 + 0.7, text, ha="center", va="bottom", fontsize=9.5, color=C[key], style="italic")


def lane_label(y, h, name, who, key):
    ax.add_patch(FancyBboxPatch((0.8, y), 9.4, h, boxstyle="round,pad=0.2,rounding_size=0.8", fc=C[key], ec="none"))
    ax.text(5.5, y + h / 2 + 0.9, name, ha="center", va="center", fontsize=14, weight="bold", color="white")
    ax.text(5.5, y + h / 2 - 1.3, who, ha="center", va="center", fontsize=10.5, color="white")


ax.text(50, 54.6, "LiDAR Snake 3D: system overview", ha="center", va="center", fontsize=22, weight="bold")

# ---- map pipeline (offline) -----------------------------------------------------------------------
y0, h0 = 44.0, 8.0
lane_label(y0, h0, "MAP", "offline, Python", "map")
steps = [("Mid-360 rosbag", "drone flight, Point-LIO\nworld-frame scans"),
         ("Accumulate 20 s", "400 scans\n1.52 M points"),
         ("Dynamic removal", "voxel occupied in >= 3\nof 5 time slices (79% kept)"),
         ("RANSAC floor", "lowest dense slab +\nleast-squares refit, 1.97°"),
         ("Walls vs objects", "tall columns = walls\nDBSCAN clusters = food"),
         ("3D level", "0.1 m voxels, labelled\npoints, 15 food objects")]
xs = [11.5 + i * 14.6 for i in range(6)]
for i, (t, b) in enumerate(steps):
    box(xs[i], y0, 12.8, h0, t, b, "map")
    if i:
        arrow(xs[i - 1] + 13.05, y0 + h0 / 2, xs[i] - 0.25, y0 + h0 / 2, "map")

# ---- game engine (right) ---------------------------------------------------------------------------
gx, gy, gw, gh = 77.0, 4.0, 22.0, 36.5
box(gx, gy, gw, gh, "GAME  (browser, index.html)",
    "snake3d.js engine, every frame:\n\n"
    "1. turn towards the target direction\n   (rate limit ~125°/s)\n"
    "2. move at the current speed\n"
    "3. voxel collision: walls stop,\n   the snake slides along them\n"
    "4. eat: touch an object (+5, +0.3 m)\n   or a random pellet (+1, +0.1 m)\n"
    "5. own body hit = game over\n\n"
    "WebGL view, fixed camera:\n"
    "real point cloud, food glows,\nfloor shadow + height stalk",
    "game", tsize=14, bsize=11.5, top=True)
arrow(xs[5] + 6.4, y0 - 0.25, gx + gw / 2 + 2, gy + gh + 0.3, "map")
ax.text(xs[5] + 6.6, (y0 + gy + gh) / 2 + 0.2, "level: voxels + points", ha="left", va="center", fontsize=9.5,
        color=C["map"], style="italic")
ax.text(gx + 1.0, 27.8 - 26.0 + 0.4, "", fontsize=1)

# ---- input modes ------------------------------------------------------------------------------------
def lane(y, h, key, name, who, chain, out_text, dashed=False):
    lane_label(y, h, name, who, key)
    n = len(chain); w = (55.5 - 1.2 * (n - 1)) / n
    xx = 11.5
    for i, (t, b) in enumerate(chain):
        box(xx, y, w, h, t, b, key, dashed=dashed, tsize=12, bsize=10)
        if i:
            arrow(xx - 1.15, y + h / 2, xx - 0.05, y + h / 2, key, dashed=dashed)
        xx += w + 1.2
    ya = y + h / 2
    arrow(xx - 1.0, ya, gx - 0.3, ya, key, dashed=dashed)
    ax.text((xx - 1.0 + gx) / 2, ya + 0.8, out_text, ha="center", va="bottom", fontsize=9.5, color=C[key],
            style="italic", linespacing=1.2)

lane(32.0, 8.5, "cam", "CAMERA", "our part", [
    ("Webcam", "laptop RGB camera\n(IR camera skipped)"),
    ("MediaPipe Hands", "21 hand keypoints,\nmetric 3D, in browser"),
    ("Finger direction", "line fit through the 4\nindex-finger joints"),
    ("One Euro filter", "adaptive low-pass +\ndepth-bias correction"),
], "3D direction\nspeed = slider")

lane(20.5, 9.0, "aud", "AUDIO", "Ryan's part", [
    ("2 x XVF3800 + ESP32", "8 mics, boards 6 cm apart\n2 x I2S -> Wi-Fi UDP"),
    ("GCC-PHAT", "28 mic pairs, 450-3800 Hz\nroom-noise weighting"),
    ("Sphere search", "3° grid over azimuth /\nelevation (SRP)"),
    ("Gate + tracker", "above background; 3 of 5\nwindows agree within 17°"),
], "WebSocket:\nazimuth, elevation,\nloudness -> speed")
ax.text(39.25, 19.0, "+ clap detection: onset + YAMNet classifier", ha="center", fontsize=10.5, color=C["aud"], style="italic")

lane(11.0, 5.6, "key", "KEYBOARD", "testing", [
    ("Arrows / WASD + E / Q", "keys held together give diagonals"),
], "direction\nspeed = + / -")

lane(4.0, 5.6, "mp", "MULTIPLAYER", "planned", [
    ("Several snakes in one world", "engine already keeps snakes by id"),
], "per-player\ndirection", dashed=True)

plt.savefig(out, dpi=110, facecolor="white")
print("->", out)
