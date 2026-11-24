#!/usr/bin/env python3
"""Slide figure: MediaPipe's 21 metric 3D hand keypoints and the index-finger direction fit.
   python3 tools/make_finger_fit_fig.py hand_world.json docs/slides/fig_finger_fit.png
hand_world.json = {"world": [[x,y,z]*21] in the player frame (x right, y up, z towards the screen), "v": smoothed dir}"""
import json, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open(sys.argv[1])); out = sys.argv[2]
W = np.array(d["world"]) * 100                      # cm
BONES = [(0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),(5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),(13,17),(17,18),(18,19),(19,20),(0,17)]
J = W[[5, 6, 7, 8]]
c = J.mean(0); U, S, Vt = np.linalg.svd(J - c); v = Vt[0]
if v @ (J[-1] - J[0]) < 0: v = -v
straight = S[0] ** 2 / (S ** 2).sum()
yaw = np.degrees(np.arctan2(v[0], v[2])); pitch = np.degrees(np.arctan2(v[1], np.hypot(v[0], v[2])))
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 13})
fig = plt.figure(figsize=(8.0, 6.4))
ax = fig.add_subplot(111, projection="3d")
P = lambda p: (p[0], p[2], p[1])                      # plot axes: x right, y = towards screen, z = up
for a, b in BONES:
    ax.plot(*zip(P(W[a]), P(W[b])), color="#9aa0ab", lw=2)
ax.scatter(*zip(*[P(p) for i, p in enumerate(W) if i not in (5, 6, 7, 8)]), s=18, color="#5b606b", depthshade=False)
ax.scatter(*zip(*[P(p) for p in J]), s=60, color="#037d54", depthshade=False, label="index joints 5–8")
side = np.cross(v, [0, 0, 1.0]); side = side / (np.linalg.norm(side) + 1e-9)
for i, p in zip((5, 6, 7, 8), J):                     # labels beside the joints, away from the line
    ax.text(*P(p + side * 1.3 + np.array([0, -0.9, 0])), str(i), color="#037d54", fontsize=12, weight="bold")
l0, l1 = J[0] - v * 1.2, J[-1] + v * 0.6
ax.plot(*zip(P(l0), P(l1)), color="#e0a400", lw=1.6, ls="--")          # fitted line through the joints
a0, a1 = J[-1] + v * 0.6, J[-1] + v * 6.5
ax.quiver(*P(a0), *P(a1 - a0), color="#e0a400", lw=3.5, arrow_length_ratio=0.22)
ax.text(*P(a1 + v * 0.9), "d", color="#b07f00", fontsize=18, weight="bold", style="italic")
ax.set_xlabel("x  (right) [cm]", labelpad=6); ax.set_ylabel("z  (towards screen) [cm]", labelpad=6); ax.set_zlabel("y  (up) [cm]", labelpad=4)
ax.view_init(elev=16, azim=-58)
from matplotlib.ticker import MaxNLocator
for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
    axis.set_major_locator(MaxNLocator(4))
rng = np.ptp(np.vstack([W, a1[None]]), axis=0).max() / 2; mid = np.vstack([W, a1[None]]).mean(0)
ax.set_xlim(mid[0] - rng, mid[0] + rng); ax.set_ylim(mid[2] - rng, mid[2] + rng); ax.set_zlim(mid[1] - rng, mid[1] + rng)
for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
    axis.pane.set_facecolor((0.97, 0.97, 0.98, 1)); axis.pane.set_edgecolor("#dde1e6")
ax.set_title(f"d = 1st principal axis of joints 5–8\nyaw {yaw:.0f}°, pitch {pitch:.0f}°, straightness {straight:.2f}", fontsize=13.5, pad=2)
ax.legend(loc="upper left", fontsize=11, frameon=False)
ax.set_box_aspect(None, zoom=0.86)
plt.savefig(out, dpi=200, facecolor="white", bbox_inches="tight", pad_inches=0.35)
print("->", out, "d =", v.round(3), f"yaw {yaw:.1f} pitch {pitch:.1f}")
