"""Estimate up to four directions and received levels in a shared transient."""
import numpy as np

from localize import RATE
from sound_events import matching_impulse

MAX_PLAYERS = 4
MIN_SEPARATION = np.cos(np.deg2rad(34.))


def grids():
    azimuth, elevation = np.meshgrid(np.arange(0., 360., 10.), np.arange(-80., 81., 10.))
    azimuth = np.r_[azimuth.ravel(), 0., 0.]
    elevation = np.r_[elevation.ravel(), -90., 90.]
    az, el = np.deg2rad([azimuth, elevation])
    directions = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)]).T
    return azimuth, elevation, directions


class SpatialClaps:
    def __init__(self, positions):
        self.azimuth, self.elevation, self.directions = grids()
        self.taper = np.hanning(512)
        frequencies = np.fft.rfftfreq(len(self.taper), 1 / RATE)
        self.band = (frequencies >= 1000.) & (frequencies <= 6850.)
        self.omega = 2 * np.pi * frequencies[self.band]
        delay = self.directions @ positions.T / 343.
        self.atoms = np.exp(1j * delay[:, None, :] * self.omega[None, :, None]).astype(np.complex64)
        self.conjugates = self.atoms.conj()
        self.horizontal = np.equal(self.elevation, 0.)

    @staticmethod
    def fit(atoms, observed):
        """Fit each steering vector at every frequency and remove it from the mixture."""
        #  (candidate, frequency, channel) -> (frequency, channel, source)
        columns = np.moveaxis(atoms, 0, -1)
        transpose = columns.conj().swapaxes(1, 2)
        gram = transpose @ columns + .075 * np.eye(columns.shape[-1])
        amplitudes = np.linalg.solve(gram, (transpose @ observed[..., None]))[..., 0]
        predicted = (columns @ amplitudes[..., None])[..., 0]
        return amplitudes, observed - predicted

    def pick(self, observed, allowed, sectors=None):
        """Greedily explain spatially coherent energy, without reusing a direction."""
        original = float(np.vdot(observed, observed).real)
        if original <= 0.:
            return [], np.empty((len(observed), 0), dtype=complex)
        selected = []
        residual = observed
        leftover = original
        mask = allowed.copy()
        amplitudes = np.empty((len(observed), 0), dtype=complex)
        for _ in range(MAX_PLAYERS):
            if not mask.any() or leftover < .032 * original:
                break
            # Matched beam energy across a band, rather than the loudest single bin.
            projection = np.einsum("gfm,fm->gf", self.conjugates[:, :, sectors], residual)
            power = np.sum(np.abs(projection) ** 2, axis=1)
            power[~mask] = -1.
            candidate = int(np.argmax(power))
            fitted, remaining = self.fit(self.atoms[[*selected, candidate], :, sectors], observed)
            remaining_power = float(np.vdot(remaining, remaining).real)
            # A direction must explain a meaningful share of both the original and residual energy.
            if leftover - remaining_power < max(.046 * original, .28 * leftover):
                break
            selected.append(candidate)
            residual, leftover, amplitudes = remaining, remaining_power, fitted
            mask &= self.directions @ self.directions[candidate] < MIN_SEPARATION
        return selected, amplitudes

    def refine(self, selected, observed, allowed, sectors):
        # Greedy peaks can shift toward a competing source. Refit each angle
        # against all the others before comparing the separated amplitudes.
        for _ in range(3):
            changed = False
            for index, previous in enumerate(selected):
                others = selected[:index] + selected[index + 1:]
                valid = allowed & (self.directions @ self.directions[previous] > np.cos(np.deg2rad(19.)))
                separated = allowed.copy()
                for neighbour in others:
                    separated &= self.directions @ self.directions[neighbour] < MIN_SEPARATION
                valid &= separated
                if others:
                    # Project the proposals and observation away from the other sources.
                    # A thinned frequency grid keeps this search cheap; the full grid
                    # still determines the final residual and estimated level.
                    atoms = self.atoms[:, ::3, sectors]
                    conjugates = self.conjugates[:, ::3, sectors]
                    others_matrix = np.moveaxis(atoms[others], 0, -1)
                    adjoint = others_matrix.conj().swapaxes(1, 2)
                    gram = adjoint @ others_matrix + .075 * np.eye(len(others))
                    projector = others_matrix @ np.linalg.solve(gram, adjoint)
                    signal = observed[::3]
                    residual = signal - (projector @ signal[..., None])[..., 0]
                    raw = np.einsum("gfm,fm->gf", conjugates, residual)
                    projected = np.einsum("fmn,gfn->gfm", projector, atoms)
                    norm = atoms.shape[-1] - (conjugates * projected).real.sum(axis=2)
                    scores = np.sum(np.abs(raw) ** 2 / np.maximum(norm, .6), axis=1)
                    # A broadband overlap can hide a source entirely in the first pass.
                    # Reconsider several different regions as well as the local neighbours.
                    for _ in range(6):
                        if not separated.any():
                            break
                        guess = int(np.argmax(np.where(separated, scores, -1.)))
                        valid[guess] = True
                        separated &= self.directions @ self.directions[guess] < np.cos(np.deg2rad(20.))
                winner, best = previous, float("inf")
                for choice in np.flatnonzero(valid):
                    candidates = selected.copy()
                    candidates[index] = choice
                    _, residual = self.fit(self.atoms[candidates, :, sectors], observed)
                    error = float(np.vdot(residual, residual).real)
                    if error < best:
                        best, winner = error, int(choice)
                if winner != previous:
                    selected[index] = winner
                    changed = True
            if not changed:
                break
        return selected

    def direct_sources(self, selected, observed, sectors):
        if len(selected) < 2:
            return range(len(selected))
        fitted, _ = self.fit(self.atoms[selected, :, sectors], observed)
        spectra = np.zeros((len(selected), len(self.band)), dtype=complex)
        spectra[:, self.band] = fitted.T
        waveforms = np.fft.irfft(spectra, n=len(self.taper), axis=1)
        kept = []
        for index, waveform in enumerate(waveforms):
            echoes = []
            for earlier in kept:
                lag = matching_impulse(waveforms[earlier], waveform, RATE // 64)
                if lag is None:
                    continue
                if lag >= -(RATE // 1000):
                    break
                echoes.append(earlier)
            else:
                # When a wall reflection is louder, the beam search can find it
                # first. Retain the earlier copy when its lead exceeds 1 ms.
                kept = [earlier for earlier in kept if earlier not in echoes]
                kept.append(index)
        return kept

    def estimate(self, audio, phase, offset, noise=None):
        spectrum = np.fft.rfft(audio * self.taper[:, None], axis=0)[self.band]
        if noise is not None:
            # Frequency-wise whitening uses the ambient reference and retains original
            # units for the final per-direction level fit.
            raw_power = np.mean(np.abs(spectrum) ** 2, axis=1)
            ambient = np.median(noise[:, self.band], axis=0)
            weight = np.sqrt(np.maximum(0., 1. - 2.5 * ambient / np.maximum(raw_power, 1.)))
        else:
            weight = np.ones(len(spectrum))
        if np.count_nonzero(weight > .4) < 14:
            return []

        if offset is None:
            # Before vertical calibration, require the two independent planar fits
            # to agree before emitting a playable azimuth.
            upper, _ = self.pick(weight[:, None] * spectrum[:, :4], self.horizontal, slice(0, 4))
            lower, _ = self.pick(weight[:, None] * spectrum[:, 4:], self.horizontal, slice(4, 8))
            allowed = self.horizontal & np.array([
                any(self.directions[index] @ direction >= MIN_SEPARATION for index in upper)
                and any(self.directions[index] @ direction >= MIN_SEPARATION for index in lower)
                for direction in self.directions
            ])
            if not allowed.any():
                return []
            # Select directions on the clearer of the two corroborating boards.
            sectors = slice(0, 4) if np.linalg.norm(spectrum[:, :4]) >= np.linalg.norm(spectrum[:, 4:]) else slice(4, 8)
        else:
            spectrum[:, 4:] *= np.exp(1j * self.omega[:, None] * (offset - phase) / RATE)
            allowed = np.ones(len(self.directions), dtype=bool)
            sectors = slice(None)

        weighted = weight[:, None] * spectrum[:, sectors]
        selected, _ = self.pick(weighted, allowed, sectors)
        if not selected:
            return []
        selected = self.refine(selected, weighted, allowed, sectors)
        direct = self.direct_sources(selected, weighted, sectors)
        if offset is None:
            fits = [self.fit(self.atoms[selected, :, board], spectrum[:, board])[0]
                    for board in (slice(0, 4), slice(4, 8))]
            energy = np.mean([np.sum(np.abs(fit) ** 2, axis=0) for fit in fits], axis=0)
        else:
            fitted, _ = self.fit(self.atoms[selected], spectrum)
            energy = np.sum(np.abs(fitted) ** 2, axis=0)
        # Parseval with the taper's energy and int32 PCM scale removed.
        rms = np.sqrt(2 * energy / (len(self.taper) * np.sum(self.taper ** 2))) / (2 ** 31)
        return [{
            "azimuth": float(self.azimuth[selected[i]]),
            "elevation": None if offset is None else float(self.elevation[selected[i]]),
            "rms": float(rms[i]),
        } for i in direct if np.isfinite(rms[i]) and rms[i] > 0.]
