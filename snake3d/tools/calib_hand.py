#!/usr/bin/env python3
"""Fit a depth / direction correction for the finger direction from recorded hand samples, and plot
the error per direction for: the classic 2D method, our raw 3D direction, and the calibrated 3D direction.

Record in hand3d.html: hold each direction and its key (1-6) for ~5 s, then Export JSON.
  python3 tools/calib_hand.py hand_samples_*.json --out docs/slides/cam_calib.png

Model: d' = normalize(W d + b), fitted by least squares. 'region' uses two (W, b): one for directions towards /
away from the screen (|d_z| > tau on the measured direction), one for sideways; tau is chosen on training data.
Split: with 2+ files, the last file is the test set; with one file, the last 40% of each direction (in time).
"""
import argparse, json, sys
from pathlib import Path
import numpy as np

TARGET = {"forward (screen)": (0, 0, 1), "back (you)": (0, 0, -1), "left": (-1, 0, 0),
          "right": (1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0)}
_s = 0.5 ** 0.5
TARGET.update({"up-left": (-_s, _s, 0), "up-right": (_s, _s, 0), "down-left": (-_s, -_s, 0), "down-right": (_s, -_s, 0)})
SHORT = {"forward (screen)": "forward\n(screen)", "back (you)": "back\n(you)", "up-left": "up-\nleft",
         "up-right": "up-\nright", "down-left": "down-\nleft", "down-right": "down-\nright"}

ap = argparse.ArgumentParser()
ap.add_argument("files", nargs="+")
ap.add_argument("--out", default="docs/slides/cam_calib.png")
ap.add_argument("--min-straight", type=float, default=0.0, help="drop samples with a bent finger")
ap.add_argument("--test-frac", type=float, default=0.4)
a = ap.parse_args()


def unit(v):
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-9)


def ang(u, v):
    return np.degrees(np.arccos(np.clip((unit(u) * unit(v)).sum(-1), -1, 1)))


def load(path):
    S = [s for s in json.load(open(path))["samples"] if s["label"] in TARGET and s["straightness"] >= a.min_straight]
    lab = np.array([s["label"] for s in S])
    raw = np.array([s["index"] for s in S], float)
    img = np.array([s["image"] for s in S], float)          # raw (unmirrored) image coords, x right, y down
    d2 = img[:, 8] - img[:, 5]
    flat = np.stack([-d2[:, 0], -d2[:, 1], np.zeros(len(S))], 1)   # 2D method in the player frame: no depth
    t = np.array([s["t"] for s in S], float)
    return lab, raw, flat, t


sets = [load(f) for f in a.files]
if len(sets) >= 2:
    tr = [np.concatenate(x) for x in zip(*sets[:-1])]
    te = list(sets[-1])
else:
    lab, raw, flat, t = sets[0]
    m = np.zeros(len(lab), bool)
    for L in np.unique(lab):
        idx = np.where(lab == L)[0]
        idx = idx[np.argsort(t[idx])]
        m[idx[int(len(idx) * (1 - a.test_frac)):]] = True
    tr = [x[~m] for x in (lab, raw, flat, t)]
    te = [x[m] for x in (lab, raw, flat, t)]

Ytr = np.array([TARGET[L] for L in tr[0]], float)
Yte = np.array([TARGET[L] for L in te[0]], float)
aff = lambda X: np.c_[X, np.ones(len(X))]


def lsq(X, Y):                                                     # 4x3: rows 0-2 = W^T, row 3 = b
    return np.linalg.lstsq(aff(X), Y, rcond=None)[0]


def fit_region(X, Y, tau):
    """Two corrections, picked by the MEASURED direction (never the label): |d_z| > tau = towards / away from
    the screen, else sideways."""
    g = np.abs(X[:, 2]) > tau
    Wf = lsq(X[g], Y[g]) if g.sum() > 20 else lsq(X, Y)
    Ws = lsq(X[~g], Y[~g]) if (~g).sum() > 20 else lsq(X, Y)
    return lambda Z: unit(np.where((np.abs(Z[:, 2]) > tau)[:, None], aff(Z) @ Wf, aff(Z) @ Ws)), (Wf, Ws)


# the gate tau is chosen on the training set only (inner time split), then refit on all training samples
inner = np.zeros(len(tr[0]), bool)
for L in np.unique(tr[0]):
    idx = np.where(tr[0] == L)[0]
    idx = idx[np.argsort(tr[3][idx])]
    inner[idx[int(len(idx) * 0.6):]] = True
taus = np.arange(0.40, 0.951, 0.05)
cv = [ang(fit_region(tr[1][~inner], Ytr[~inner], tau)[0](tr[1][inner]), Ytr[inner]).mean() for tau in taus]
tau = float(taus[int(np.argmin(cv))])
region, (Wf, Ws) = fit_region(tr[1], Ytr, tau)
Wb = lsq(tr[1], Ytr)
glob = unit(aff(te[1]) @ Wb)
reg = region(te[1])

order = [L for L in TARGET if L in set(te[0])]
res = {}
for L in order + ["all"]:
    k = te[0] == L if L != "all" else np.ones(len(te[0]), bool)
    res[L] = {"n_test": int(k.sum()), "2d": float(ang(te[2][k], Yte[k]).mean()),
              "raw3d": float(ang(te[1][k], Yte[k]).mean()), "global3d": float(ang(glob[k], Yte[k]).mean()),
              "region3d": float(ang(reg[k], Yte[k]).mean())}

print(f"train {len(tr[0])} samples, test {len(te[0])} samples, gate tau = {tau:.2f} (chosen on training data)")
print(f"{'direction':18s} {'n':>5s} {'2D':>7s} {'3D raw':>7s} {'global':>7s} {'region':>7s}   (mean angle error, deg)")
for L, r in res.items():
    print(f"{L:18s} {r['n_test']:5d} {r['2d']:7.1f} {r['raw3d']:7.1f} {r['global3d']:7.1f} {r['region3d']:7.1f}")

# stricter check: fit on the 6 axis directions only, test on the 4 diagonals that were never used in fitting
diag = ["up-left", "up-right", "down-left", "down-right"]
lab_all = np.concatenate([tr[0], te[0]]); X_all = np.concatenate([tr[1], te[1]]); F_all = np.concatenate([tr[2], te[2]])
Y_all = np.array([TARGET[L] for L in lab_all], float)
ax6 = ~np.isin(lab_all, diag)
unseen = None
if (~ax6).any() and ax6.any():
    reg6, _ = fit_region(X_all[ax6], Y_all[ax6], tau)
    k = ~ax6
    unseen = {"n": int(k.sum()), "2d": float(ang(F_all[k], Y_all[k]).mean()), "raw3d": float(ang(X_all[k], Y_all[k]).mean()),
              "global3d": float(ang(unit(aff(X_all[k]) @ lsq(X_all[ax6], Y_all[ax6])), Y_all[k]).mean()),
              "region3d": float(ang(reg6(X_all[k]), Y_all[k]).mean())}
    print(f"unseen diagonals ({unseen['n']}): 2D {unseen['2d']:.1f}  raw {unseen['raw3d']:.1f}  "
          f"global {unseen['global3d']:.1f}  region {unseen['region3d']:.1f}")

out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
json.dump({"results": res, "unseen_diagonals": unseen, "tau": tau, "W_global": Wb.tolist(),
           "W_front_back": Wf.tolist(), "W_sideways": Ws.tolist(), "files": a.files},
          open(out.with_suffix(".json"), "w"), indent=1)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams.update({"font.size": 13, "font.family": "DejaVu Sans"})
INK, MUTED = "#1d1f24", "#6b6f78"
series = [("2d", "Classic 2D", "#eb6834"), ("region3d", "Ours: 3D + calibration", "#2a78d6")]
cats = order + ["all"]
x = np.arange(len(cats)); w = 0.36
fig, ax = plt.subplots(figsize=(12, 5.2))
for i, (key, name, col) in enumerate(series):
    v = [res[c][key] for c in cats]
    bars = ax.bar(x + (i - 0.5) * w, v, w - 0.04, color=col, label=name, zorder=3)
    for b_, val in zip(bars, v):
        ax.text(b_.get_x() + b_.get_width() / 2, val + 1, f"{val:.0f}", ha="center", va="bottom", fontsize=10.5, color=INK)
ax.set_xticks(x, [SHORT.get(c, c) if c != "all" else "all\ndirections" for c in cats], color=INK)
ax.set_ylabel("mean angle error on test samples (°)", color=MUTED)
ax.grid(axis="y", color="#e6e6e3", zorder=0)
ax.spines[["top", "right", "left"]].set_visible(False)
ax.tick_params(axis="y", colors=MUTED, length=0)
ax.legend(frameon=False, loc="upper right", ncol=2)
ax.set_ylim(0, max(100, max(res[c]["2d"] for c in cats) + 12))
ax.set_title(f"Finger direction error per pointing direction ({res['all']['n_test']} test samples)", color=INK, loc="left")
plt.tight_layout()
plt.savefig(out, dpi=150, facecolor="white")
print("->", out)
