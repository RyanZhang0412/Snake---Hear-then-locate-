"""Floor plane for lidar scenes: lowest dense slab -> deterministic RANSAC -> least-squares refit."""
import numpy as np


def fit_floor(P, log=print, iters=1000, thresh=0.03, seed=0, return_debug=False):
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
    if return_debug:
        return plane, info, {"z": z, "zf": zf, "P": slab, "inlier_mask": inl}
    return plane, info
