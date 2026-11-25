"""Statistical relative value: Engle–Granger cointegration on log prices (research output only).

Step 1: OLS  log(A) = a + b·log(B) + e      Step 2: ADF regression on the residual
        Δe_t = γ·e_{t-1} + φ·Δe_{t-1} + u_t   and compare the t-statistic of γ with the
Engle–Granger 5% critical value for two variables (≈ −3.34, MacKinnon 2010, large sample).
Half-life of mean reversion = −ln(2)/ln(1+γ). Two-leg positions are not paper-traded yet.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

EG_CRIT_5 = -3.34


def _ols(y: np.ndarray, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(1, len(y) - X.shape[1])
    sigma2 = resid @ resid / dof
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    return beta, resid, np.sqrt(np.diag(cov))


def engle_granger(a: np.ndarray, b: np.ndarray) -> dict:
    la, lb = np.log(a), np.log(b)
    beta, e, _ = _ols(la, np.column_stack([np.ones_like(lb), lb]))
    de = np.diff(e)
    y = de[1:]
    X = np.column_stack([e[1:-1], de[:-1]])
    g, _, se = _ols(y, X)
    t_stat = g[0] / se[0] if se[0] > 0 else 0.0
    half_life = -math.log(2) / math.log(1 + g[0]) if -1 < g[0] < 0 else None
    z = (e[-1] - e.mean()) / e.std() if e.std() > 0 else 0.0
    corr = float(np.corrcoef(np.diff(la), np.diff(lb))[0, 1])
    return {"hedge_ratio": float(beta[1]), "adf_t": float(t_stat), "cointegrated_5pct": bool(t_stat < EG_CRIT_5),
            "half_life_bars": half_life, "spread_z": float(z), "return_correlation": corr, "bars": int(a.size)}


def scan(closes: dict[str, np.ndarray], times: dict[str, np.ndarray]) -> list[dict]:
    out = []
    for x, y in itertools.combinations(sorted(closes), 2):
        common, ix, iy = np.intersect1d(times[x], times[y], return_indices=True)
        if common.size < 300:
            continue
        r = engle_granger(closes[x][ix], closes[y][iy])
        r.update(pair=f"{x} / {y}")
        if r["cointegrated_5pct"] and abs(r["spread_z"]) >= 2:
            r["research_note"] = (f"Spread is {r['spread_z']:+.2f}σ from its mean; a relative-value idea would "
                                  f"{'short' if r['spread_z'] > 0 else 'buy'} {x} against {y}. Not simulated (two legs).")
        out.append(r)
    out.sort(key=lambda r: r["adf_t"])
    return out
