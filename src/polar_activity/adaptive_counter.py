"""Class-supported short/long return cycles. No labels or expected totals are inputs."""

from dataclasses import replace

import numpy as np

from .counter import (
    CounterConfig,
    _chains,
    _correlation,
    _cycle_candidates,
    _group_bouts,
    _principal,
    _smooth,
)
from .experiments.signal import validate_block


def short_return_bouts(t, acc, gyro):
    """Search several time scales, requiring two observed returns and both IMUs.

    The caller supplies a class-supported interval with bounded context. Class
    evidence is separate from motion evidence; counting does not read that class.
    A longer seed cannot displace all shorter seeds before their cycles are tested.
    """
    validate_block(t, acc, gyro)
    rate = 1 / float(np.median(np.diff(t)))
    config = replace(CounterConfig(), grid_hz=rate, min_cycles=2, max_period_s=5)
    width = max(3, round(config.smoothing_s * rate) // 2 * 2 + 1)
    acc, gyro = _smooth(acc, width), _smooth(gyro, width)
    seeds, candidates = [], []
    for seconds in (4, 6, 8, 12):
        size = round(seconds * rate)
        if size > len(t):
            continue
        starts = list(range(0, len(t) - size + 1, max(1, round(0.48 * rate))))
        if starts[-1] != len(t) - size:
            starts.append(len(t) - size)
        for first in starts:
            stop = first + size
            g, ga, fraction = _principal(gyro[first:stop])
            a, aa, _ = _principal(acc[first:stop])
            if np.std(g) < 15 or np.std(a) < 40 or fraction < 0.65:
                continue
            # Two periods of support can seed a short set; the old size//3 cap
            # required three periods before any complete-cycle check occurred.
            lower = max(2, round(0.48 * rate))
            upper = min(round(5 * rate), size // 2)
            lags = np.arange(lower - 1, upper + 2)
            correlations = np.array([_correlation(g[:-lag], g[lag:]) for lag in lags])
            peaks = (
                np.flatnonzero(
                    (correlations[1:-1] > correlations[:-2])
                    & (correlations[1:-1] >= correlations[2:])
                )
                + 1
            )
            passing = [
                p
                for p in peaks
                if correlations[p] >= 0.8 and _correlation(a[: -lags[p]], a[lags[p] :]) >= 0.7
            ]
            if not passing:
                continue
            # Prefer a supported fundamental over its near-equally-correlated harmonic.
            strongest = max(correlations[p] for p in passing)
            best = min(
                (p for p in passing if correlations[p] >= strongest - 0.05), key=lambda p: lags[p]
            )
            lag = int(lags[best])
            seed = {
                "first": first,
                "stop": stop,
                "period_s": lag / rate,
                "acc_axis": aa,
                "gyro_axis": ga,
                "gyro_periodicity": float(correlations[best]),
                "acc_periodicity": _correlation(a[:-lag], a[lag:]),
            }
            records, detail = _cycle_candidates(t, acc, gyro, seed, config)
            chains = _chains(t, records, seed, config)
            seeds.append(
                {
                    "start_time_s": float(t[first]),
                    "end_time_s": float(t[stop - 1]),
                    "window_s": seconds,
                    "period_s": seed["period_s"],
                    "raw_return_candidates": len(records),
                    "qualifying_chains": len(chains),
                }
            )
            for chain in chains:
                candidates.append(
                    {
                        "start_time_s": float(t[chain[0]["first"]]),
                        "end_time_s": float(t[chain[-1]["last"]]),
                        "period_s": seed["period_s"],
                        "shape_similarity": float(
                            np.mean(
                                [(r["acc_similarity"] + r["gyro_similarity"]) / 2 for r in chain]
                            )
                        ),
                        "acc_axis": aa.tolist(),
                        "gyro_axis": ga.tolist(),
                        **detail,
                        "seed_start_time_s": float(t[first]),
                        "seed_window_s": seconds,
                        "cycles": [
                            {
                                "start_time_s": float(t[r["first"]]),
                                "end_time_s": float(t[r["last"]]),
                                "duration_s": r["duration_s"],
                                "return_observed": True,
                                "acc_similarity": r["acc_similarity"],
                                "gyro_similarity": r["gyro_similarity"],
                            }
                            for r in chain
                        ],
                    }
                )
    selected = []
    for candidate in sorted(
        candidates,
        key=lambda b: (
            -(b["end_time_s"] - b["start_time_s"]) * b["shape_similarity"],
            b["start_time_s"],
            b["seed_window_s"],
        ),
    ):
        if any(
            min(candidate["end_time_s"], s["end_time_s"])
            > max(candidate["start_time_s"], s["start_time_s"])
            for s in selected
        ):
            continue
        selected.append(candidate)
    bouts = _group_bouts(t, acc, gyro, sorted(selected, key=lambda b: b["start_time_s"]), config)
    return bouts, {
        "seeds": seeds,
        "candidate_chains": candidates,
        "policy": "At least two physical returns; opposite phase coverage selected once",
    }
