#!/usr/bin/env python3
"""Principle figures for the audio (acoustic localization) slides. The math follows the team's
localizer (localize.py in Hear-then-Locate, main branch), re-implemented here and run on simulated signals:
two XVF3800 boards, 4 mics on a 66.6 mm square each, 6 cm apart; 16 kHz; 512-sample Hann frames;
GCC-PHAT in 450-3800 Hz with 8x oversampling; steered response power on a 3 deg azimuth/elevation grid.

  python3 tools/make_audio_figs.py docs/slides
writes audio_hw.png, audio_gcc.png, audio_srp.png
"""
import sys
from itertools import combinations
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle, FancyArrowPatch

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/slides"); out.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "axes.titlesize": 13.5, "axes.titleweight": "bold"})
ORANGE, DARK, GREY, LIGHT, BLUE = "#c8701d", "#2b2f38", "#8a8f99", "#f6f7f9", "#3d7be0"

# ---- array geometry (as in localize.py) -------------------------------------------------------------
RATE, C = 16000, 343.0
GAP, SIDE = 0.06, 0.0666
ang = np.deg2rad(np.arange(45, 360, 90))
xy = np.column_stack((np.cos(ang), np.sin(ang))) * SIDE / np.sqrt(2)
POS = np.vstack([np.c_[xy, np.full(4, +GAP / 2)], np.c_[xy, np.full(4, -GAP / 2)]])
PAIRS = np.array(list(combinations(range(8), 2)))
CROSS = (PAIRS[:, 0] < 4) & (PAIRS[:, 1] >= 4)
SAME = ~CROSS
FRAME, OVER = 512, 8
NFFT = FRAME * OVER
freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
BAND = (freqs >= 450) & (freqs <= 3800)


def unit(az, el):
    az, el = np.deg2rad(az), np.deg2rad(el)
    return np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])


def simulate(src_dir, n=4096, snr_db=12, seed=0, echo=None):
    """8-channel plane wave from src_dir: pink-ish noise source, fractional delays, sensor noise.
    echo = (direction, extra delay s, gain): one wall reflection of the same source."""
    rng = np.random.default_rng(seed)
    N = n + 2048
    spec = np.fft.rfft(rng.standard_normal(N))
    f = np.fft.rfftfreq(N, 1 / RATE)
    spec *= 1 / np.sqrt(np.maximum(f, 60))                 # coloured, speech-like tilt
    delays = -(POS @ src_dir) / C                          # earlier arrival for mics towards the source
    H = np.exp(-2j * np.pi * f[None, :] * delays[:, None])
    if echo is not None:
        e_dir, e_t, e_g = echo
        H = H + e_g * np.exp(-2j * np.pi * f[None, :] * (e_t - (POS @ e_dir) / C)[:, None])
    x = np.stack([np.fft.irfft(spec * H[k], N) for k in range(8)], 1)[1024:1024 + n]
    x /= x.std()
    x += rng.standard_normal(x.shape) * 10 ** (-snr_db / 20)
    return x


def gcc(x, phat=True):
    """Frame-averaged cross spectra -> band-limited, oversampled correlation per pair (lag-centred)."""
    frames = np.lib.stride_tricks.sliding_window_view(x.T, FRAME, axis=-1)[:, ::256]
    S = np.fft.rfft(frames * np.hanning(FRAME), axis=-1)
    i, j = PAIRS.T
    cross = S[i].conj() * S[j]
    if phat:
        cross = cross / np.maximum(np.abs(cross), 1e-12)
    cross = cross.mean(axis=1)
    cross[:, ~BAND] = 0
    r = np.fft.fftshift(np.fft.irfft(cross, n=NFFT, axis=-1), axes=-1)
    return r / np.abs(r).max(axis=1, keepdims=True)


AZ, EL = np.meshgrid(np.arange(0., 360., 3.), np.arange(-90., 91., 3.))
DIRS = unit(AZ.ravel(), EL.ravel())
TAU = (POS[PAIRS[:, 0]] - POS[PAIRS[:, 1]]) @ DIRS * (RATE / C)   # lag (samples) per pair and direction


def srp(r, mask):
    lags = TAU[mask] * OVER + NFFT // 2
    lo = np.floor(lags).astype(int); fr = lags - lo
    cc = r[mask]
    s = (1 - fr) * np.take_along_axis(cc, lo, 1) + fr * np.take_along_axis(cc, lo + 1, 1)
    return s.mean(0).reshape(AZ.shape)


# ======================= fig 1: hardware & geometry ==================================================
fig = plt.figure(figsize=(16, 5.6))
# top view
ax = fig.add_axes([0.02, 0.04, 0.25, 0.84]); ax.set_aspect("equal"); ax.axis("off")
ax.add_patch(FancyBboxPatch((-48, -48), 96, 96, boxstyle="round,pad=2,rounding_size=8", fc=LIGHT, ec=DARK, lw=1.6))
for k, (px, py) in enumerate(xy * 1000):
    ax.add_patch(Circle((px, py), 6.5, fc=ORANGE, ec="white", lw=1.5))
ax.add_patch(Rectangle((-9, -56), 18, 7, fc=GREY, ec="none")); ax.text(0, -64, "USB-C (front, azimuth 0°)", ha="center", fontsize=11, color=DARK)
ax.annotate("", xy=(xy[0, 0] * 1000, -38), xytext=(xy[1, 0] * 1000, -38), arrowprops=dict(arrowstyle="<->", color=DARK, lw=1.2))
ax.text(0, -34, "66.6 mm", ha="center", fontsize=11, color=DARK)
ax.set_xlim(-70, 70); ax.set_ylim(-72, 62)
fig.text(0.145, 0.95, "Top view: 4 mics per XVF3800 board", ha="center", fontsize=13.5, weight="bold")
# side view
ax = fig.add_axes([0.27, 0.04, 0.32, 0.84]); ax.set_aspect("equal"); ax.axis("off")
for zc, name in ((30, "board A (upper)"), (-30, "board B (lower)")):
    ax.add_patch(FancyBboxPatch((-55, zc - 4), 110, 8, boxstyle="round,pad=1,rounding_size=3", fc=LIGHT, ec=DARK, lw=1.5))
    for px in (-33, 33):
        ax.add_patch(Circle((px, zc), 5, fc=ORANGE, ec="white", lw=1.2))
    ax.text(62, zc, name, va="center", fontsize=11, color=DARK)
ax.annotate("", xy=(-70, 30), xytext=(-70, -30), arrowprops=dict(arrowstyle="<->", color=DARK, lw=1.2))
ax.text(-74, 0, "6 cm", ha="right", va="center", fontsize=11, color=DARK)
ax.plot([-33, 33], [30, 30], color=BLUE, lw=2.2); ax.text(0, 40, "in-board pair: azimuth", ha="center", color=BLUE, fontsize=10.5)
ax.plot([33, 33], [30, -30], color=ORANGE, lw=2.2, ls="--"); ax.text(38, -8, "cross-board pair:\nelevation", color=ORANGE, fontsize=10.5)
ax.set_xlim(-100, 140); ax.set_ylim(-55, 58)
fig.text(0.43, 0.95, "Side view: 8 mics → 28 pairs", ha="center", fontsize=13.5, weight="bold")
ax.text(20, -50, "12 in-board pairs + 16 cross-board pairs", ha="center", fontsize=11, color=DARK)
# signal chain
ax = fig.add_axes([0.61, 0.04, 0.38, 0.84]); ax.axis("off"); ax.set_xlim(0, 100); ax.set_ylim(0, 100)
def blk(x, y, w, h, t, s, accent=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=2.5",
                                fc="#fbf2e8" if accent else LIGHT, ec=ORANGE if accent else DARK, lw=1.5))
    ax.text(x + w / 2, y + h * 0.64, t, ha="center", va="center", fontsize=12, weight="bold", color=DARK)
    ax.text(x + w / 2, y + h * 0.28, s, ha="center", va="center", fontsize=10, color="#4a4f5a")
blk(2, 66, 30, 22, "2 × XVF3800", "raw mic PCM", True)
blk(38, 66, 26, 22, "ESP32", "2 × I²S receivers", True)
blk(70, 66, 28, 22, "PC", "UDP :54322", True)
for x0, x1, lab in ((32.6, 37.4, ""), (64.6, 69.4, "")):
    ax.add_patch(FancyArrowPatch((x0, 77), (x1, 77), arrowstyle="-|>", mutation_scale=16, color=DARK, lw=1.6))
ax.text(35, 93, "shared BCLK / LRCK\n(upper board is I²S master)", ha="center", fontsize=9.5, color="#4a4f5a")
ax.text(67, 93, "Wi-Fi", ha="center", fontsize=10, color="#4a4f5a")
blk(10, 22, 80, 28, "Per window", "16 kHz · 4096 samples (256 ms), hop 128 ms\nonly windows complete on both boards")
ax.add_patch(FancyArrowPatch((84, 65), (72, 51), arrowstyle="-|>", mutation_scale=16, color=DARK, lw=1.6))
fig.text(0.80, 0.95, "Capture chain", ha="center", fontsize=13.5, weight="bold")
plt.savefig(out / "audio_hw.png", dpi=160, facecolor="white", bbox_inches="tight", pad_inches=0.15)
plt.close()

# ======================= fig 2: GCC-PHAT ============================================================
src = unit(60, 25)
ECHO = (unit(200, -10), 0.0025, 0.6)
x = simulate(src, seed=3, echo=ECHO)
pi, pj = 0, 2                                          # a diagonal in-board pair (largest baseline)
k = int(np.flatnonzero((PAIRS[:, 0] == pi) & (PAIRS[:, 1] == pj))[0])
tau_true = (POS[pi] - POS[pj]) @ src / C
r_phat, r_plain = gcc(x, True)[k], gcc(x, False)[k]
lag_ms = (np.arange(NFFT) - NFFT // 2) / (RATE * OVER) * 1000
fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw={"width_ratios": [1, 1.5]})
a1.set_aspect("equal"); a1.axis("off")
m1, m2 = np.array([-1.0, 0]), np.array([1.0, 0])
for m, lab in ((m1, "mic i"), (m2, "mic j")):
    a1.add_patch(Circle(m, 0.12, fc=ORANGE, ec="white", lw=1.5)); a1.text(m[0], -0.32, lab, ha="center", fontsize=12)
th = np.deg2rad(40); u = np.array([np.cos(th), np.sin(th)])
for s_ in np.linspace(-1.2, 1.9, 6):                    # wavefronts perpendicular to u
    c0 = u * s_ + np.array([0, 0])
    p = np.array([-u[1], u[0]])
    a1.plot(*np.c_[c0 - 1.6 * p, c0 + 1.6 * p], color=GREY, lw=1, alpha=0.6)
a1.add_patch(FancyArrowPatch(u * 2.2, u * 1.0, arrowstyle="-|>", mutation_scale=20, color=BLUE, lw=2))
a1.text(*(u * 2.35), "sound from\ndirection u", color=BLUE, fontsize=12)
foot = m1 + ((m2 - m1) @ u) * u
a1.plot(*np.c_[m2, foot], color=ORANGE, lw=2.2); a1.plot(*np.c_[m1, foot], color=DARK, lw=1, ls=":")
a1.text(*(0.5 * (m2 + foot) + np.array([0.12, -0.05])), "(pᵢ − pⱼ)·u", color=ORANGE, fontsize=12)
a1.text(-0.2, -0.75, "τᵢⱼ(u) = (pᵢ − pⱼ)·u / c", fontsize=15, color=DARK, ha="center", style="italic")
a1.set_xlim(-1.8, 2.6); a1.set_ylim(-1.0, 2.6)
a1.set_title("Time difference of arrival for one mic pair")
a2.plot(lag_ms, r_plain, color=GREY, lw=1.6, label="plain cross-correlation")
a2.plot(lag_ms, r_phat, color=ORANGE, lw=2.2, label="GCC-PHAT (whitened)")
a2.axvline(tau_true * 1000, color=BLUE, ls="--", lw=1.4, label=f"true τ = {tau_true * 1e3:+.3f} ms")
lim = np.linalg.norm(POS[pi] - POS[pj]) / C * 1000 * 1.6
a2.set_xlim(-lim, lim); a2.set_ylim(-0.6, 1.08)
a2.set_xlabel("lag τ [ms]"); a2.set_ylabel("normalised correlation")
a2.legend(loc="lower right", fontsize=10.5, frameon=False)
a2.set_title("R(τ) = IFFT{ Xᵢ(f) Xⱼ*(f) / |Xᵢ(f) Xⱼ*(f)| },  450–3800 Hz, 8× oversampled", fontsize=12.5)
a2.spines[["top", "right"]].set_visible(False)
a2.text(0.02, 0.96, "simulated coloured-noise source + one wall reflection (0.6×, +2.5 ms), 12 dB SNR", transform=a2.transAxes, fontsize=10, color=GREY, va="top")
plt.tight_layout(); plt.savefig(out / "audio_gcc.png", dpi=160, facecolor="white"); plt.close()

# ======================= fig 3: SRP maps before / after calibration =====================================
r = gcc(x, True)
maps = [("12 in-board pairs (before calibration)", srp(r, SAME)), ("all 28 pairs (after calibration)", srp(r, np.ones(len(PAIRS), bool)))]
fig, axs = plt.subplots(1, 2, figsize=(15, 5.0))
for ax, (t, m) in zip(axs, maps):
    im = ax.imshow(m, origin="lower", extent=[0, 360, -90, 90], aspect="auto", cmap="magma", vmin=m.min(), vmax=m.max())
    iy, ix = np.unravel_index(np.argmax(m), m.shape)
    ax.plot(60, 25, marker="o", ms=13, mfc="none", mec="#5fd3ff", mew=2.2, label="true source (60°, 25°)")
    ax.plot(AZ[iy, ix], EL[iy, ix], marker="x", ms=11, mew=2.6, color="white", label=f"best: ({AZ[iy, ix]:.0f}°, {EL[iy, ix]:.0f}°)")
    ax.set_xlabel("azimuth [°]"); ax.set_ylabel("elevation [°]"); ax.set_title(t)
    ax.set_xticks(range(0, 361, 90)); ax.set_yticks(range(-90, 91, 45))
    ax.legend(loc="lower right", fontsize=10, framealpha=0.85)
    plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02).set_label("mean GCC at predicted lags")
axs[0].annotate("mirror peak:\nflat arrays cannot\ntell up from down", xy=(60, -25), xytext=(150, -62), color="white", fontsize=11,
                arrowprops=dict(arrowstyle="->", color="white"))
plt.tight_layout(); plt.savefig(out / "audio_srp.png", dpi=160, facecolor="white"); plt.close()
print("->", out)
