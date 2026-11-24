#!/usr/bin/env python3
"""Figures for the slides on the lidar mapping part (dynamic point removal + RANSAC ground removal).

  python3 map/make_slide_figures.py [--bag ~/bags/nogt/pointlio_out/bag_0807_tagres] --out docs/slides

Writes:
  fig_persistence.png  how many of the 5 time slices each voxel is seen in, and the >= 3 threshold
  fig_dynamic.png      raw / kept / removed top views (the other drone's path is removed)
  fig_ransac.png       z histogram with the lowest dense slab, and a side view with the RANSAC floor inliers
  fig_ground.png       top view before / after removing the ground (and ceiling) by height above the plane
"""
import argparse, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))

ap = argparse.ArgumentParser()
ap.add_argument("--bag", default=str(Path.home() / "bags/nogt/pointlio_out/bag_0807_tagres"))
ap.add_argument("--t0", type=float, default=20.0)
ap.add_argument("--dur", type=float, default=20.0)
ap.add_argument("--out", default="docs/slides")
a = ap.parse_args()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.size": 14, "axes.titlesize": 15, "figure.dpi": 110})
GREY, RED, BLUE, GOLD = "#8a8f99", "#e04b4b", "#3d7be0", "#e0a400"
SLICES, MIN_SLICES, V = 5, 3, 0.10
lim = (-3.1, 8.1, -7.0, 4.2)          # the hall crop used for the level, in the bag's frame

# ---- frames from the Point-LIO bag ---------------------------------------------------------------
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore
pts, sl, traj = [], [], []
with AnyReader([Path(a.bag)], default_typestore=get_typestore(Stores.ROS2_HUMBLE)) as r:
    lo = r.start_time + int(a.t0 * 1e9); hi = lo + int(a.dur * 1e9)
    cons = [c for c in r.connections if c.topic in ("/cloud_registered", "/aft_mapped_to_init")]
    for c, t, raw in r.messages(connections=cons, start=lo, stop=hi):
        m = r.deserialize(raw, c.msgtype)
        if c.topic == "/aft_mapped_to_init":
            p = m.pose.pose.position; traj.append((p.x, p.y)); continue
        off = {f.name: f.offset for f in m.fields}
        buf = np.frombuffer(m.data, np.uint8).reshape(-1, m.point_step)
        xyz = np.stack([buf[:, off[k]:off[k] + 4].copy().view(np.float32)[:, 0] for k in "xyz"], 1)
        xyz = xyz[np.isfinite(xyz).all(1)]
        pts.append(xyz); sl.append(np.full(len(xyz), min(int((t - lo) / (hi - lo) * SLICES), SLICES - 1), np.int8))
P = np.concatenate(pts).astype(np.float64); S = np.concatenate(sl); traj = np.array(traj)
print(f"{len(pts)} scans, {len(P):,} points")

# ---- temporal persistence ------------------------------------------------------------------------
_, vid = np.unique(np.floor(P / V).astype(np.int64), axis=0, return_inverse=True); vid = vid.ravel()
seen = np.zeros((vid.max() + 1, SLICES), bool); seen[vid, S] = True
nseen_vox = seen.sum(1)
keep = nseen_vox[vid] >= MIN_SLICES

# ---- floor: lowest dense slab + deterministic RANSAC + least squares (as in export_level3d.py) ------
from floor import fit_floor
# fit on the same static scene the level is built from, so the slide numbers match the level
import open3d as o3d
Pk = np.asarray(o3d.io.read_point_cloud(str(Path(__file__).parent.parent / "data/scene_0807_20-40s.pcd")).points)
cx_, cy_, half_ = 2.5, -1.4, 5.6
crop = (np.abs(Pk[:, 0] - cx_) < half_ + 1) & (np.abs(Pk[:, 1] - cy_) < half_ + 1)
_unused = (Pk[:, 0] > lim[0] - 1) & (Pk[:, 0] < lim[1] + 1) & (Pk[:, 1] > lim[2] - 1) & (Pk[:, 1] < lim[3] + 1)
plane, info, dbg = fit_floor(Pk[crop], return_debug=True)
h_all = P @ plane[:3] + plane[3]
inb = (h_all > 0.05) & (h_all < 1.8) & (P[:, 0] > lim[0]) & (P[:, 0] < lim[1]) & (P[:, 1] > lim[2]) & (P[:, 1] < lim[3])

# ---- fig 1: persistence histogram -------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(8, 4.6))
cnt = np.bincount(nseen_vox, minlength=SLICES + 1)[1:]
cols = [RED if k < MIN_SLICES else BLUE for k in range(1, SLICES + 1)]
ax.bar(range(1, SLICES + 1), cnt, color=cols)
for k, c_ in zip(range(1, SLICES + 1), cnt):
    ax.text(k, c_, f"{c_:,}", ha="center", va="bottom", fontsize=12)
ax.axvline(MIN_SLICES - 0.5, color="k", ls="--", lw=1.5)
ax.text(MIN_SLICES - 0.45, cnt.max() * 0.92, " keep: seen in >= 3 of 5 slices", fontsize=13)
ax.set_xlabel("number of 4 s slices a 10 cm voxel is occupied in"); ax.set_ylabel("voxels")
ax.set_title(f"Temporal persistence over 20 s: {keep.mean():.0%} of points kept")
ax.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.savefig(out / "fig_persistence.png"); plt.close()

# ---- fig 2: dynamic removal top views -----------------------------------------------------------------
fig, axs = plt.subplots(1, 3, figsize=(18, 6.4))
def panel(ax, m, title, red=None):
    ax.scatter(P[m, 0], P[m, 1], s=0.15, c="0.25", alpha=0.35, lw=0)
    if red is not None: ax.scatter(P[red, 0], P[red, 1], s=0.5, c=RED, alpha=0.5, lw=0)
    ax.plot(traj[:, 0], traj[:, 1], color=BLUE, lw=1.8)
    ax.set_xlim(lim[:2]); ax.set_ylim(lim[2:]); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.set_title(title)
panel(axs[0], inb, "raw: 20 s accumulated")
panel(axs[1], inb & keep, "kept (static map)")
panel(axs[2], inb & keep, "removed in red", red=inb & ~keep)
axs[2].annotate("other drone's\nflight path", xy=(4.5, 1.8), xytext=(1.0, 3.2), fontsize=13, color=RED,
                arrowprops=dict(arrowstyle="->", color=RED))
plt.tight_layout(); plt.savefig(out / "fig_dynamic.png"); plt.close()
print(f"object band in the crop: {inb.sum():,} points, removed {(inb & ~keep).sum():,} ({(inb & ~keep).sum() / inb.sum():.1%})")

# ---- fig 3: RANSAC floor ----------------------------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17, 5.6), gridspec_kw={"width_ratios": [1, 1.6]})
z = dbg["z"]
ax1.hist(z, bins=np.arange(z.min(), min(z.max(), z.min() + 6), 0.05), color=GREY)
ax1.axvspan(dbg["zf"] - 0.3, dbg["zf"] + 0.3, color=GOLD, alpha=0.3, label="search slab (±0.3 m)")
ax1.axvline(dbg["zf"], color=GOLD, lw=2, label="lowest dense 5 cm bin")
ax1.set_xlabel("z [m]"); ax1.set_ylabel("points"); ax1.set_yscale("log")
ax1.set_title("1. find the floor height"); ax1.legend(fontsize=11, loc="upper right")
sub = np.random.default_rng(1).choice(len(dbg["P"]), min(len(dbg["P"]), 120000), replace=False)
Ps, inl = dbg["P"][sub], dbg["inlier_mask"][sub]
ax2.scatter(Ps[~inl, 0], Ps[~inl, 2], s=0.2, c=GREY, alpha=0.4, lw=0, label="other points")
ax2.scatter(Ps[inl, 0], Ps[inl, 2], s=0.3, c=BLUE, alpha=0.6, lw=0, label="RANSAC floor inliers")
xs = np.linspace(Ps[:, 0].min(), Ps[:, 0].max(), 10); ym = np.median(Ps[:, 1])
ax2.plot(xs, -(plane[0] * xs + plane[1] * ym + plane[3]) / plane[2], color=RED, lw=2, label="least-squares plane")
ax2.set_ylim(dbg["zf"] - 0.45, dbg["zf"] + 0.45)
ax2.set_xlabel("x [m]"); ax2.set_ylabel("z [m]")
ax2.set_title(f"2. RANSAC in the slab, 3. least-squares refit: tilt {info['tilt_deg']}°, {info['inliers']:,} inliers")
ax2.legend(fontsize=11, loc="upper right", markerscale=12)
plt.tight_layout(); plt.savefig(out / "fig_ransac.png"); plt.close()

# ---- fig 4: ground removal top views ------------------------------------------------------------------
inc = keep & (P[:, 0] > lim[0]) & (P[:, 0] < lim[1]) & (P[:, 1] > lim[2]) & (P[:, 1] < lim[3])
fig, axs = plt.subplots(1, 2, figsize=(14, 6.6))
for ax, m, t_ in [(axs[0], inc & (h_all < 3.0), "before: floor dominates the top view"),
                  (axs[1], inc & (h_all > 0.05) & (h_all < 1.5), "after: 0.05-1.5 m above the plane")]:
    H, xe, ye = np.histogram2d(P[m, 0], P[m, 1], bins=[np.arange(lim[0], lim[1], 0.05), np.arange(lim[2], lim[3], 0.05)])
    ax.imshow(np.log1p(H.T), origin="lower", extent=[lim[0], lim[1], lim[2], lim[3]], cmap="Greys")
    ax.set_title(t_); ax.set_xticks([]); ax.set_yticks([])
plt.tight_layout(); plt.savefig(out / "fig_ground.png"); plt.close()
print("figures ->", out)
