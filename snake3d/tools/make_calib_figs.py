#!/usr/bin/env python3
"""Step-by-step figures for the finger-direction calibration, from one hand3d.html recording.

  python3 tools/make_calib_figs.py data/hand_calib/hand_samples_*.json --out docs/slides

  calib_step1.png  the recorded hands: one real frame per direction (2D keypoints, player view) + frame counts
  calib_step2.png  direction maps: classic 2D method vs our raw 3D direction (test samples)
  calib_step3.png  direction maps before / after the region-wise calibration + the gate tau chosen on training data
Same split and model as tools/calib_hand.py.
"""
import argparse, json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("file")
ap.add_argument("--out", default="docs/slides")
a = ap.parse_args()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

s2 = 0.5 ** 0.5
TARGET = {"forward (screen)": (0, 0, 1), "back (you)": (0, 0, -1), "left": (-1, 0, 0), "right": (1, 0, 0),
          "up": (0, 1, 0), "down": (0, -1, 0), "up-left": (-s2, s2, 0), "up-right": (s2, s2, 0),
          "down-left": (-s2, -s2, 0), "down-right": (s2, -s2, 0)}
SHORT = {"forward (screen)": "forward (screen)", "back (you)": "back (you)"}
FWD, BACK, SIDE = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#1d1f24", "#6b6f78", "#e3e3df"
group_col = lambda L: FWD if L.startswith("forward") else BACK if L.startswith("back") else SIDE
plt.rcParams.update({"font.size": 12, "font.family": "DejaVu Sans"})

S = [s for s in json.load(open(a.file))["samples"] if s["label"] in TARGET]
lab = np.array([s["label"] for s in S]); X = np.array([s["index"] for s in S], float)
t = np.array([s["t"] for s in S], float); img = np.array([s["image"] for s in S], float)
Y = np.array([TARGET[L] for L in lab], float)
d2 = img[:, 8] - img[:, 5]
F = np.stack([-d2[:, 0], -d2[:, 1], np.zeros(len(S))], 1)          # classic 2D method, player frame

unit = lambda v: v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)
ang = lambda u, v: np.degrees(np.arccos(np.clip((unit(u) * unit(v)).sum(-1), -1, 1)))
aff = lambda Z: np.c_[Z, np.ones(len(Z))]
lsq = lambda Z, T: np.linalg.lstsq(aff(Z), T, rcond=None)[0]

te = np.zeros(len(S), bool)
for L in TARGET:
    i = np.where(lab == L)[0]; i = i[np.argsort(t[i])]; te[i[int(len(i) * 0.6):]] = True
tr = ~te


def fit_region(Z, T, tau):
    g = np.abs(Z[:, 2]) > tau
    Wf = lsq(Z[g], T[g]) if g.sum() > 20 else lsq(Z, T)
    Ws = lsq(Z[~g], T[~g]) if (~g).sum() > 20 else lsq(Z, T)
    return lambda Q: unit(np.where((np.abs(Q[:, 2]) > tau)[:, None], aff(Q) @ Wf, aff(Q) @ Ws))


inner = np.zeros(len(S), bool)
for L in TARGET:
    i = np.where(tr & (lab == L))[0]; i = i[np.argsort(t[i])]; inner[i[int(len(i) * 0.6):]] = True
taus = np.arange(0.40, 0.951, 0.05)
cv = np.array([ang(fit_region(X[tr & ~inner], Y[tr & ~inner], tau)(X[inner]), Y[inner]).mean() for tau in taus])
tau = float(taus[int(np.argmin(cv))])
CAL = fit_region(X[tr], Y[tr], tau)(X)
print(f"tau = {tau:.2f}; test error: 2D {ang(F[te], Y[te]).mean():.1f}, raw 3D {ang(X[te], Y[te]).mean():.1f}, "
      f"calibrated {ang(CAL[te], Y[te]).mean():.1f}")


# ---------- direction map: centre = pointing at the screen, middle ring = sideways, rim = back at you ----------
def dmap(v):
    v = unit(v); r = np.degrees(np.arccos(np.clip(v[:, 2], -1, 1))); az = np.arctan2(v[:, 1], v[:, 0])
    return r * np.cos(az), r * np.sin(az)


def map_axes(ax, title):
    for rr, lw in [(45, 0.8), (90, 1.6), (135, 0.8), (180, 1.6)]:
        th = np.linspace(0, 2 * np.pi, 200); ax.plot(rr * np.cos(th), rr * np.sin(th), color=GRID if lw < 1 else "#c9c9c4", lw=lw, zorder=0)
    ax.plot([-180, 180], [0, 0], color=GRID, lw=0.8, zorder=0); ax.plot([0, 0], [-180, 180], color=GRID, lw=0.8, zorder=0)
    ax.text(0, -12, "screen", ha="center", fontsize=9, color=MUTED)
    ax.text(0, 94, "sideways", ha="center", fontsize=9, color=MUTED)
    ax.text(0, 184, "back (you)", ha="center", fontsize=9, color=MUTED)
    ax.set_xlim(-200, 200); ax.set_ylim(-195, 205); ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(title, fontsize=13, color=INK)


def scatter_map(ax, V, mask, title, err=None):
    map_axes(ax, title)
    rng = np.random.default_rng(0)
    for L in TARGET:
        k = np.where(mask & (lab == L))[0]
        k = rng.choice(k, min(len(k), 120), replace=False)
        x, y = dmap(V[k]); ax.scatter(x, y, s=7, color=group_col(L), alpha=0.45, lw=0, zorder=2)
    for L, v in TARGET.items():
        x, y = dmap(np.array([v], float))
        if L.startswith("back"):
            th = np.linspace(0, 2 * np.pi, 200); ax.plot(180 * np.cos(th), 180 * np.sin(th), color=INK, lw=1.2, ls="--", zorder=3)
        else:
            ax.scatter(x, y, marker="x", s=60, color=INK, lw=2, zorder=4)
    if err is not None:
        ax.text(0, -192, f"mean error {err:.0f}°", ha="center", fontsize=12, color=INK, fontweight="bold")


def legend(fig):
    from matplotlib.lines import Line2D
    h = [Line2D([], [], marker="o", ls="", color=c, label=n) for c, n in [(FWD, "pointing at the screen"), (BACK, "pointing back at you"), (SIDE, "sideways (8 directions)")]]
    h.append(Line2D([], [], marker="x", ls="", color=INK, mew=2, label="true direction"))
    fig.legend(handles=h, loc="lower center", ncol=4, frameon=False, fontsize=11)


# ---------- step 1: the recorded hands ----------
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
order = list(TARGET)
fig, axs = plt.subplots(2, 5, figsize=(14, 5.6))
for ax, L in zip(axs.ravel(), order):
    k = np.where(lab == L)[0]
    med = unit(np.median(X[k], 0)[None])[0]
    j = k[np.argmax(X[k] @ med)]                       # the frame closest to this direction's typical reading
    p = img[j].copy(); p[:, 0] = 1 - p[:, 0]; p[:, 1] = -p[:, 1]   # mirror like a selfie, y up
    c = p[0] + (p.max(0) - p.min(0)) * 0 + (p.max(0) + p.min(0)) / 2 - p[0]
    span = max(np.ptp(p[:, 0]), np.ptp(p[:, 1])) * 0.62 + 1e-3
    for a_, b_ in BONES:
        ax.plot(p[[a_, b_], 0], p[[a_, b_], 1], color="#9aa0ab", lw=1.6, zorder=1)
    ax.plot(p[5:9, 0], p[5:9, 1], color=group_col(L), lw=3.2, zorder=2)
    ax.scatter(p[5:9, 0], p[5:9, 1], s=22, color=group_col(L), zorder=3)
    ax.scatter(p[:, 0], p[:, 1], s=6, color="#6b6f78", zorder=2)
    ax.set_xlim(c[0] - span, c[0] + span); ax.set_ylim(c[1] - span, c[1] + span); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values(): sp.set_color(GRID)
    ax.set_title(f"{SHORT.get(L, L)}\n{len(k)} frames", fontsize=11.5, color=INK)
fig.suptitle(f"Recorded in our bench: {len(S):,} frames, 10 directions (one real frame each, keypoints as the camera sees them)",
             fontsize=13, color=INK, x=0.01, ha="left")
plt.tight_layout(rect=(0, 0, 1, 0.95)); plt.savefig(out / "calib_step1.png", dpi=150, facecolor="white"); plt.close()

# ---------- step 2: 2D method vs our 3D direction ----------
fig, axs = plt.subplots(1, 2, figsize=(11, 5.6))
scatter_map(axs[0], F, te, "Classic 2D: tip − knuckle in the image", ang(F[te], Y[te]).mean())
scatter_map(axs[1], X, te, "Ours: 3D keypoints + PCA line fit", ang(X[te], Y[te]).mean())
legend(fig); plt.tight_layout(rect=(0, 0.07, 1, 0.95)); plt.savefig(out / "calib_step2.png", dpi=150, facecolor="white"); plt.close()

# ---------- step 3: calibration ----------
fig = plt.figure(figsize=(15, 5.4))
gs = fig.add_gridspec(1, 3, width_ratios=[1, 1, 0.9])
ax1, ax2, ax3 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1]), fig.add_subplot(gs[2])
scatter_map(ax1, X, te, "Before: raw 3D", ang(X[te], Y[te]).mean())
scatter_map(ax2, CAL, te, "After: region-wise calibration", ang(CAL[te], Y[te]).mean())
ax3.plot(taus, cv, color=FWD, lw=2, marker="o", ms=5)
ax3.scatter([tau], [cv.min()], s=90, color=FWD, zorder=5, edgecolor="white", lw=1.5)
ax3.annotate(f"τ = {tau:.2f}", (tau, cv.min()), xytext=(tau + 0.06, cv.min() + (cv.max() - cv.min()) * 0.35), fontsize=12,
             color=INK, arrowprops=dict(arrowstyle="-", color=MUTED))
ax3.set_xlabel("switch point τ on |d_z|", color=MUTED); ax3.set_ylabel("validation error (°)", color=MUTED)
ax3.set_title("Choosing τ on training data only", fontsize=13, color=INK)
ax3.grid(color=GRID); ax3.spines[["top", "right"]].set_visible(False)
legend(fig); plt.tight_layout(rect=(0, 0.07, 1, 0.95)); plt.savefig(out / "calib_step3.png", dpi=150, facecolor="white"); plt.close()
print("->", out / "calib_step1.png", out / "calib_step2.png", out / "calib_step3.png")
