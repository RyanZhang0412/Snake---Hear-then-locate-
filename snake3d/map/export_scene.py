#!/usr/bin/env python3
"""Accumulate Point-LIO /cloud_registered over a time window into a single PCD scene."""
import argparse, numpy as np, open3d as o3d
from pathlib import Path
from rosbags.highlevel import AnyReader
from rosbags.typesys import Stores, get_typestore

ap = argparse.ArgumentParser()
ap.add_argument("bag", help="Point-LIO output bag dir (*_tagres)")
ap.add_argument("--t0", type=float, default=20.0, help="window start, s from bag start")
ap.add_argument("--dur", type=float, default=20.0)
ap.add_argument("--voxel", type=float, default=0.02)
ap.add_argument("--slices", type=int, default=5, help="time slices for the static (persistence) filter")
ap.add_argument("--min-slices", type=int, default=3, help="keep a voxel only if seen in >= this many slices; 0 disables")
ap.add_argument("--static-voxel", type=float, default=0.10)
ap.add_argument("--out", required=True)
a = ap.parse_args()

ts = get_typestore(Stores.ROS2_HUMBLE)
pts, sl, poses = [], [], []
with AnyReader([Path(a.bag)], default_typestore=ts) as r:
    start = r.start_time
    lo, hi = start + int(a.t0 * 1e9), start + int((a.t0 + a.dur) * 1e9)
    cons = [c for c in r.connections if c.topic in ("/cloud_registered", "/aft_mapped_to_init")]
    for c, t, raw in r.messages(connections=cons, start=lo, stop=hi):
        m = r.deserialize(raw, c.msgtype)
        if c.topic == "/aft_mapped_to_init":
            p = m.pose.pose.position; poses.append((p.x, p.y, p.z))
            continue
        off = {f.name: f.offset for f in m.fields}
        buf = np.frombuffer(m.data, np.uint8).reshape(-1, m.point_step)
        xyz = np.stack([buf[:, off[k]:off[k] + 4].copy().view(np.float32)[:, 0] for k in "xyz"], 1)
        xyz = xyz[np.isfinite(xyz).all(1)]
        pts.append(xyz)
        sl.append(np.full(len(xyz), min(int((t - lo) / (hi - lo) * a.slices), a.slices - 1), np.int8))
P = np.concatenate(pts).astype(np.float64)
n_raw = len(P)
if a.min_slices > 0:
    # Static filter: a voxel occupied in only a few time slices is something that moved (e.g. the other drone).
    S = np.concatenate(sl)
    key = np.floor(P / a.static_voxel).astype(np.int64)
    _, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.ravel()
    seen = np.zeros((vid.max() + 1, a.slices), bool)
    seen[vid, S] = True
    keep = seen.sum(1)[vid] >= a.min_slices
    P = P[keep]
    print(f"static filter: kept {keep.mean():.1%} of points (voxel seen in >= {a.min_slices}/{a.slices} slices)")
pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P)).voxel_down_sample(a.voxel)
o3d.io.write_point_cloud(a.out, pc)
np.save(Path(a.out).with_suffix(".traj.npy"), np.array(poses))
print(f"{len(pts)} scans, {n_raw} raw pts -> {len(pc.points)} after {a.voxel} m voxel -> {a.out}")
print("trajectory start/end:", np.round(poses[0], 2), np.round(poses[-1], 2))
