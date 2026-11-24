#!/usr/bin/env python3
"""Static point cloud -> level for the continuous 3D snake (index.html).

Same object rules as the grid levels:
  floor  : RANSAC on the lowest dense slab + least squares (make_grid.fit_floor); the cloud is rotated so
           the floor is z = 0
  walls  : columns that also have points 1.8-3 m up (floor-to-ceiling structure), clusters longer than
           max_food_extent_m, and every other visible thing in the play volume that is not food
  food   : each DBSCAN cluster of object-band points is one object; touching it eats the whole object
Game frame: metres, x = right, y = forward (away from the fixed camera), z = up, origin at the arena
centre on the floor.

Writes <out>/level3d.json, points.bin (float32 xyz + uint8 label per point; 0 = scene, i = food object i),
voxels.bin (uint8 per voxel, x fastest: 0 free, 1 solid, 2+i = food object i) and preview.png.

  python3 map/export_level3d.py data/scene_0807_20-40s.pcd --center 2.5 -1.4 --side 11.2 --yaw-deg 90 --out levels/hall_0807
"""
import argparse, json, sys
from pathlib import Path
import numpy as np
import open3d as o3d
from scipy import ndimage
from scipy.spatial import cKDTree



def fit_floor(P, log, iters=1000, thresh=0.03, seed=0):
    """Lowest dense 5 cm z-bin -> RANSAC within +-0.3 m of it -> least-squares plane.
    Plain numpy RANSAC with a fixed seed, so the level is byte-for-byte reproducible
    (open3d's multi-threaded segment_plane is not, even when seeded)."""
    z = P[:, 2]
    h, e = np.histogram(z, bins=np.arange(z.min(), z.max() + 0.05, 0.05))
    first = np.flatnonzero(h > 0.01 * len(P))[0]
    win = (e[:-1] >= e[first]) & (e[:-1] < e[first] + 0.5)
    zf = e[np.argmax(np.where(win, h, 0))]
    slab = P[np.abs(z - zf) < 0.3]
    rng = np.random.default_rng(seed)
    S = slab[rng.choice(len(slab), min(len(slab), 50000), replace=False)]
    best, best_n = None, -1
    for _ in range(iters):
        a3 = S[rng.choice(len(S), 3, replace=False)]
        n = np.cross(a3[1] - a3[0], a3[2] - a3[0])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        cnt = int((np.abs((S - a3[0]) @ n) < thresh).sum())
        if cnt > best_n:
            best, best_n = (n, a3[0]), cnt
    n, p0 = best
    inl = np.abs((slab - p0) @ n) < thresh
    F = slab[inl]
    (a, b, c), *_ = np.linalg.lstsq(np.c_[F[:, :2], np.ones(len(F))], F[:, 2], rcond=None)
    plane = np.array([-a, -b, 1.0, -c]) / np.linalg.norm([a, b, 1.0])
    tilt = float(np.degrees(np.arccos(plane[2])))
    info = {"plane_abcd": plane.round(5).tolist(), "tilt_deg": round(tilt, 2),
            "inliers": int(inl.sum()), "inlier_ratio_of_slab": round(float(inl.mean()), 3)}
    log(f"floor: {plane[0]:.4f}x + {plane[1]:.4f}y + {plane[2]:.4f}z + {plane[3]:.4f} = 0, tilt {tilt:.2f} deg, "
        f"{int(inl.sum()):,} inliers ({info['inlier_ratio_of_slab']:.0%} of the slab)")
    return plane, info


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("pcd")
ap.add_argument("--center", type=float, nargs=2, required=True, metavar=("X", "Y"), help="arena centre in the cloud's frame")
ap.add_argument("--side", type=float, required=True, help="arena side length, m (square)")
ap.add_argument("--out", required=True)
ap.add_argument("--yaw-deg", type=float, default=0.0,
                help="rotate the whole scene about the vertical axis, counter-clockwise seen from above, deg")
ap.add_argument("--voxel", type=float, default=0.10, help="collision voxel size, m")
ap.add_argument("--play-h", type=float, default=2.0, help="the snake flies from the floor up to this height, m")
ap.add_argument("--band", type=float, nargs=2, default=[0.05, 1.5], help="object band above the floor, m")
ap.add_argument("--wall-band", type=float, nargs=2, default=[1.8, 3.0], help="only floor-to-ceiling structure reaches this")
ap.add_argument("--view-h", type=float, default=3.0, help="display points up to this height, m")
ap.add_argument("--view-voxel", type=float, default=0.03)
ap.add_argument("--eps", type=float, default=0.12, help="DBSCAN radius, m")
ap.add_argument("--min-cluster-pts", type=int, default=30)
ap.add_argument("--max-food-extent", type=float, default=2.0)
ap.add_argument("--min-top", type=float, default=0.25, help="an object must reach this height, m (drops floor speckle)")
ap.add_argument("--solid-min-pts", type=int, default=2, help="a voxel with this many non-food points is solid")
ap.add_argument("--food-pad", type=int, default=3, help="grow each object's edible zone by this many voxels (never into walls)")
a = ap.parse_args()
o3d.utility.random.seed(0)
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
log = lambda *s: print(*s, flush=True)

# ---- load, floor, rotate so the floor is z = 0, centre the arena ------------------------------
P = np.asarray(o3d.io.read_point_cloud(a.pcd).points, dtype=np.float64)
cx, cy, half = a.center[0], a.center[1], a.side / 2
near = (np.abs(P[:, 0] - cx) < half + 1) & (np.abs(P[:, 1] - cy) < half + 1)
plane, floor_info = fit_floor(P[near], log)
n = plane[:3]
v = np.cross(n, [0, 0, 1.0]); s, c = np.linalg.norm(v), n[2]
K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
Rm = np.eye(3) + K + K @ K * ((1 - c) / s ** 2) if s > 1e-9 else np.eye(3)
h = P @ n + plane[3]                                   # height above the floor plane
Q = (P - [cx, cy, 0]) @ Rm.T
Q[:, 2] = h
if a.yaw_deg:                                          # turn the scene for the fixed camera
    t = np.radians(a.yaw_deg); c_, s_ = np.cos(t), np.sin(t)
    Q[:, :2] = Q[:, :2] @ np.array([[c_, s_], [-s_, c_]])   # row vectors: (x, y) -> (x c - y s, x s + y c)
inside = (np.abs(Q[:, 0]) < half) & (np.abs(Q[:, 1]) < half)
Q = Q[inside & (Q[:, 2] > -0.05) & (Q[:, 2] < a.view_h)]
log(f"{len(Q):,} points in the {a.side} m arena up to {a.view_h} m")

# ---- structure vs objects (2D, at voxel resolution) ---------------------------------------------
vx = a.voxel
nx = ny = int(np.ceil(a.side / vx)); nz = int(np.ceil(a.play_h / vx))
ij = lambda X: (np.clip(((X[:, 0] + half) / vx).astype(int), 0, nx - 1), np.clip(((X[:, 1] + half) / vx).astype(int), 0, ny - 1))
B = Q[(Q[:, 2] > a.band[0]) & (Q[:, 2] < a.band[1])]
T = Q[(Q[:, 2] > a.wall_band[0]) & (Q[:, 2] < a.wall_band[1])]
occB = np.zeros((nx, ny), int); np.add.at(occB, ij(B), 1)
occT = np.zeros((nx, ny), int); np.add.at(occT, ij(T), 1)
structure = ndimage.binary_dilation((occB >= 3) & (occT >= 3), iterations=1)
bi, bj = ij(B)
Bo = B[~structure[bi, bj]]
cid = np.asarray(o3d.geometry.PointCloud(o3d.utility.Vector3dVector(Bo)).cluster_dbscan(eps=a.eps, min_points=5))
objects, obj_pts, n_long, n_flat = [], [], 0, 0
for k in sorted(range(cid.max() + 1), key=lambda k: -(cid == k).sum()):
    m = cid == k
    if m.sum() < a.min_cluster_pts:
        continue
    ext = float(np.ptp(Bo[m, :2], axis=0).max())
    if ext > a.max_food_extent:
        n_long += 1
        continue
    if Bo[m, 2].max() < a.min_top:      # flat speckle on the floor, not an object
        n_flat += 1
        continue
    obj_pts.append(Bo[m])
log(f"structure columns {int(structure.sum())}, long walls {n_long}, flat floor speckle dropped {n_flat}, food objects {len(obj_pts)}")

# ---- per-point labels and voxels ------------------------------------------------------------------
lab = np.zeros(len(Q), np.uint8)
if obj_pts:
    tree = cKDTree(np.concatenate([p[:, :2] for p in obj_pts]))
    olab = np.concatenate([np.full(len(p), i + 1) for i, p in enumerate(obj_pts)])
    d, j = tree.query(Q[:, :2], distance_upper_bound=0.10)
    qi, qj = ij(Q)
    hit = np.isfinite(d) & (Q[:, 2] > a.band[0]) & (Q[:, 2] < a.wall_band[0]) & ~structure[qi, qj]
    lab[hit] = olab[j[hit]]
vox = np.zeros((nz, ny, nx), np.uint8)             # index [z, y, x] so the file is x-fastest
inplay = (Q[:, 2] > a.band[0]) & (Q[:, 2] < a.play_h)
qi, qj = ij(Q)
qk = np.clip((Q[:, 2] / vx).astype(int), 0, nz - 1)
solid_cnt = np.zeros((nz, ny, nx), int)
np.add.at(solid_cnt, (qk[inplay & (lab == 0)], qj[inplay & (lab == 0)], qi[inplay & (lab == 0)]), 1)
vox[solid_cnt >= a.solid_min_pts] = 1
for s2 in np.argwhere(structure):                  # floor-to-ceiling: the whole column
    vox[:, s2[1], s2[0]] = 1
for i in range(len(obj_pts)):
    m = inplay & (lab == i + 1)
    f = np.zeros_like(vox, bool); f[qk[m], qj[m], qi[m]] = True
    f = ndimage.binary_dilation(f, iterations=a.food_pad) & (vox == 0)   # easier to hit, never over walls
    vox[f] = 2 + i
vox[:, [0, -1], :] = 1; vox[:, :, [0, -1]] = 1     # arena border

# ---- start, reachability ------------------------------------------------------------------------
free = vox == 0
zs = min(nz - 2, int(0.6 / vx))
open3 = ndimage.binary_erosion(free, iterations=2)
cand = np.argwhere(open3[zs])
if not len(cand):
    sys.exit("no free start position")
sy, sx = cand[np.argmin(((cand - [ny / 2, nx / 2]) ** 2).sum(1))]
start = [(sx + 0.5) * vx - half, (sy + 0.5) * vx - half, (zs + 0.5) * vx]
reach, _ = ndimage.label(vox != 1)                  # 6-connected through free and food voxels
rs = reach[zs, sy, sx]
for i, p in enumerate(obj_pts):
    vs = np.argwhere(vox == 2 + i)
    reachable = bool(len(vs)) and bool((reach[tuple(vs.T)] == rs).any())
    objects.append({"id": i + 1, "points": int((lab == i + 1).sum()), "voxels": int(len(vs)), "reachable": reachable,
                    "center": np.round(p.mean(0), 3).tolist(), "top_m": round(float(p[:, 2].max()), 2),
                    "extent_m": round(float(np.ptp(p[:, :2], axis=0).max()), 2)})
bad = [o["id"] for o in objects if not o["reachable"]]
if bad:
    log(f"unreachable objects (kept, but not counted for the win): {bad}")

# ---- write ----------------------------------------------------------------------------------------
Qd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(Q))
_, _, idx = Qd.voxel_down_sample_and_trace(a.view_voxel, Qd.get_min_bound(), Qd.get_max_bound())
keep = np.array([ix[0] for ix in idx]) if len(idx) else np.arange(len(Q))
with open(out / "points.bin", "wb") as f:
    f.write(Q[keep].astype(np.float32).tobytes()); f.write(lab[keep].tobytes())
(out / "voxels.bin").write_bytes(vox.tobytes())
meta = {"name": Path(a.pcd).stem, "source": Path(a.pcd).name, "frame": "metres; x right, y forward, z up; origin = arena centre on the floor",
        "arena": {"half_x": half, "half_y": half, "height": a.play_h},
        "voxel": {"size": vx, "nx": nx, "ny": ny, "nz": nz, "origin": [-half, -half, 0.0], "order": "x fastest, then y, then z",
                  "values": "0 free, 1 solid, 2+i food object i (id i+1)"},
        "points": {"file": "points.bin", "n": int(len(keep)), "layout": "float32 n*3 xyz, then uint8 n labels (0 scene, i food object id)"},
        "start": [round(x, 3) for x in start], "floor": floor_info, "objects": objects,
        "params": {k: v for k, v in vars(a).items() if k not in ("pcd", "out")}}
json.dump(meta, open(out / "level3d.json", "w"), indent=1)
log(f"voxels {nx}x{ny}x{nz} @ {vx} m: solid {int((vox == 1).sum())}, food {int((vox >= 2).sum())}; "
    f"start {meta['start']}; display points {len(keep):,} -> {out}")

# ---- preview: top view + side view with numbered objects ----------------------------------------
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7), gridspec_kw={"width_ratios": [1, 1.3]})
bg = Q[(Q[:, 2] > 0.05) & (Q[:, 2] < a.play_h)]
ax1.scatter(bg[:, 0], bg[:, 1], s=0.2, c="0.6", lw=0)
solid2 = (vox == 1).any(0)
ax1.imshow(np.ma.masked_equal(solid2.astype(int), 0), extent=[-half, half, -half, half], origin="lower", cmap="Reds", alpha=0.35, vmin=0, vmax=1.5)
cmap = plt.get_cmap("tab20")
for i, p in enumerate(obj_pts):
    ax1.scatter(p[:, 0], p[:, 1], s=1, color=cmap(i % 20), lw=0)
    o = objects[i]
    ax1.annotate(str(o["id"]), o["center"][:2], fontsize=10, weight="bold", ha="center", va="center",
                 bbox=dict(boxstyle="circle", fc="gold" if o["reachable"] else "0.7", ec="k"))
ax1.plot(*start[:2], "g^", ms=12, mec="k")
ax1.set_aspect("equal"); ax1.set_xlim(-half, half); ax1.set_ylim(-half, half)
ax1.set_title(f"top view: {len(objects)} food objects (numbered), solid voxels red, start green\nforward = up the page, as seen from the fixed camera")
for i, p in enumerate(obj_pts):
    ax2.scatter(p[:, 0], p[:, 2], s=1, color=cmap(i % 20), lw=0)
    ax2.text(objects[i]["center"][0], objects[i]["top_m"] + 0.05, str(objects[i]["id"]), ha="center", fontsize=9, weight="bold")
ax2.scatter(bg[:, 0], bg[:, 2], s=0.1, c="0.75", lw=0, zorder=0)
ax2.axhline(a.play_h, color="r", ls="--", lw=1); ax2.set_ylim(-0.1, a.view_h)
ax2.set_title("side view (x vs height): food objects coloured, red dashed = top of the play volume")
ax2.set_xlabel("x [m]"); ax2.set_ylabel("height [m]")
plt.tight_layout(); plt.savefig(out / "preview.png", dpi=90)
