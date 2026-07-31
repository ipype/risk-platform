"""Special functions the engine needs, implemented against NumPy alone.

SciPy would supply every one of these. It is deliberately not a dependency: the same call
was made in ``services/quant_validation.py``, the platform's numerical surface is small
enough to own, and a wheel that ships its own BLAS is a large thing to pull into an image
for four functions. What is here is standard published numerics, not invention:

* :func:`norm_ppf` — Wichura's AS241 (``PPND16``), accurate to about 1e-16 across the
  whole line. The cheap Beasley-Springer-Moro variants lose digits past roughly 1e-7,
  which is exactly where a 10,000-iteration Latin hypercube puts its outermost stratum.
* :func:`betainc` — the regularised incomplete beta by modified Lentz continued fraction,
  with the standard symmetry swap so the fraction is always evaluated in its convergent
  half.
* :func:`beta_ppf` — Newton on :func:`betainc`, bracketed by bisection so a bad step can
  never leave the interval.

All three are vectorised over their variable argument. ``a`` and ``b`` are scalars, which
is the only shape the engine needs — a Beta-PERT has one pair of shape parameters and many
sample points, never the reverse.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

__all__ = ["norm_ppf", "betainc", "beta_ppf", "beta_pdf"]

# --------------------------------------------------------------------------------------
# normal quantile — Wichura (1988), Algorithm AS 241, PPND16
# --------------------------------------------------------------------------------------

_A = (
    3.3871328727963666080e0,
    1.3314166789178437745e2,
    1.9715909503065514427e3,
    1.3731693765509461125e4,
    4.5921953931549871457e4,
    6.7265770927008700853e4,
    3.3430575583588128105e4,
    2.5090809287301226727e3,
)
_B = (
    1.0,
    4.2313330701600911252e1,
    6.8718700749205790830e2,
    5.3941960214247511077e3,
    2.1213794301586595867e4,
    3.9307895800092710610e4,
    2.8729085735721942674e4,
    5.2264952788528545610e3,
)
_C = (
    1.42343711074968357734e0,
    4.63033784615654529590e0,
    5.76949722146069140550e0,
    3.64784832476320460504e0,
    1.27045825245236838258e0,
    2.41780725177450611770e-1,
    2.27238449892691845833e-2,
    7.74545014278341407640e-4,
)
_D = (
    1.0,
    2.05319162663775882187e0,
    1.67638483018380384940e0,
    6.89767334985100004550e-1,
    1.48103976427480074590e-1,
    1.51986665636164571966e-2,
    5.47593808499534494600e-4,
    1.05075007164441684324e-9,
)
_E = (
    6.65790464350110377720e0,
    5.46378491116411436990e0,
    1.78482653991729133580e0,
    2.96560571828504891230e-1,
    2.65321895265761230930e-2,
    1.24266094738807843860e-3,
    2.71155556874348757815e-5,
    2.01033439929228813265e-7,
)
_F = (
    1.0,
    5.99832206555887937690e-1,
    1.36929880922735805310e-1,
    1.48753612908506148525e-2,
    7.86869131145613259100e-4,
    1.84631831751005468180e-5,
    1.42151175831644588870e-7,
    2.04426310338993978564e-15,
)


def _poly(coeffs: tuple[float, ...], r: NDArray[np.float64]) -> NDArray[np.float64]:
    """Horner evaluation. Coefficients are given lowest order first."""
    out = np.full_like(r, coeffs[-1])
    for c in reversed(coeffs[:-1]):
        out = out * r + c
    return out


def norm_ppf(p: NDArray[np.float64] | float) -> NDArray[np.float64]:
    """Inverse standard normal CDF.

    ``p`` outside ``(0, 1)`` returns ``-inf`` / ``+inf`` at the ends and ``nan`` beyond
    them, matching the convention every other quantile function in this package uses.
    """
    pa = np.asarray(p, dtype=np.float64)
    out = np.empty(pa.shape, dtype=np.float64)

    q = pa - 0.5
    central = np.abs(q) <= 0.425

    # -- central region ---------------------------------------------------------
    if central.any():
        qc = q[central]
        r = 0.180625 - qc * qc
        out[central] = qc * _poly(_A, r) / _poly(_B, r)

    # -- tails ------------------------------------------------------------------
    tail = ~central & (pa > 0.0) & (pa < 1.0)
    if tail.any():
        qt = q[tail]
        pt = pa[tail]
        r = np.where(qt < 0.0, pt, 1.0 - pt)
        r = np.sqrt(-np.log(r))

        near = r <= 5.0
        val = np.empty_like(r)
        rn = r[near] - 1.6
        val[near] = _poly(_C, rn) / _poly(_D, rn)
        rf = r[~near] - 5.0
        val[~near] = _poly(_E, rf) / _poly(_F, rf)

        out[tail] = np.where(qt < 0.0, -val, val)

    out[pa <= 0.0] = -np.inf
    out[pa >= 1.0] = np.inf
    out[np.isnan(pa)] = np.nan
    out[(pa < 0.0) | (pa > 1.0)] = np.nan
    return out


# --------------------------------------------------------------------------------------
# regularised incomplete beta
# --------------------------------------------------------------------------------------

_CF_MAX_ITER = 300
_CF_EPS = 3.0e-16
_TINY = 1.0e-300


def _betacf(a: float, b: float, x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Continued fraction for the incomplete beta, by modified Lentz.

    Vectorised over ``x``. Every element runs the same iteration count; the loop exits
    when the largest remaining correction is below tolerance, so convergence is governed
    by the worst element rather than averaged away.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0

    c = np.ones_like(x)
    d = 1.0 - qab * x / qap
    d = np.where(np.abs(d) < _TINY, _TINY, d)
    d = 1.0 / d
    h = d.copy()

    for m in range(1, _CF_MAX_ITER + 1):
        m2 = 2 * m

        # even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = np.where(np.abs(d) < _TINY, _TINY, d)
        c = 1.0 + aa / c
        c = np.where(np.abs(c) < _TINY, _TINY, c)
        d = 1.0 / d
        h *= d * c

        # odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = np.where(np.abs(d) < _TINY, _TINY, d)
        c = 1.0 + aa / c
        c = np.where(np.abs(c) < _TINY, _TINY, c)
        d = 1.0 / d
        delta = d * c
        h *= delta

        if np.all(np.abs(delta - 1.0) <= _CF_EPS):
            break

    return h


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def betainc(a: float, b: float, x: NDArray[np.float64] | float) -> NDArray[np.float64]:
    """Regularised incomplete beta ``I_x(a, b)``, vectorised over ``x``.

    The continued fraction converges quickly only for ``x`` below the distribution's
    centre of mass, so anything above it is evaluated as ``1 - I_{1-x}(b, a)``.
    """
    if a <= 0.0 or b <= 0.0:
        raise ValueError("beta shape parameters must be positive")

    xa = np.asarray(x, dtype=np.float64)
    out = np.zeros(xa.shape, dtype=np.float64)

    interior = (xa > 0.0) & (xa < 1.0)
    out[xa >= 1.0] = 1.0
    if not interior.any():
        return out

    xi = xa[interior]
    swap = xi >= (a + 1.0) / (a + b + 2.0)

    res = np.empty_like(xi)

    if (~swap).any():
        xs = xi[~swap]
        front = np.exp(a * np.log(xs) + b * np.log1p(-xs) - _log_beta(a, b))
        res[~swap] = front * _betacf(a, b, xs) / a

    if swap.any():
        xs = 1.0 - xi[swap]
        front = np.exp(b * np.log(xs) + a * np.log1p(-xs) - _log_beta(b, a))
        res[swap] = 1.0 - front * _betacf(b, a, xs) / b

    out[interior] = np.clip(res, 0.0, 1.0)
    return out


def beta_pdf(a: float, b: float, x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Beta density on ``(0, 1)``, zero outside. Used as the Newton derivative."""
    xa = np.asarray(x, dtype=np.float64)
    out = np.zeros(xa.shape, dtype=np.float64)
    inner = (xa > 0.0) & (xa < 1.0)
    if inner.any():
        xi = xa[inner]
        out[inner] = np.exp(
            (a - 1.0) * np.log(xi) + (b - 1.0) * np.log1p(-xi) - _log_beta(a, b)
        )
    return out


def beta_ppf(
    a: float, b: float, u: NDArray[np.float64] | float, *, tol: float = 1e-12
) -> NDArray[np.float64]:
    """Inverse of :func:`betainc` in ``x``, vectorised over ``u``.

    Newton against a maintained bisection bracket. Newton alone is fast but will step out
    of ``(0, 1)`` when the density is small near an endpoint, which is precisely the
    region a Latin hypercube samples on every run; the bracket makes that step
    recoverable instead of fatal.

    The seed is a moment-matched normal, which lands close for the ``a, b >= 1`` shapes a
    Beta-PERT produces and cuts the iteration count sharply against starting from the
    mean. It is only a seed: correctness rests on the bracket, not on the guess.
    """
    if a <= 0.0 or b <= 0.0:
        raise ValueError("beta shape parameters must be positive")

    ua = np.asarray(u, dtype=np.float64)
    out = np.empty(ua.shape, dtype=np.float64)

    interior = (ua > 0.0) & (ua < 1.0)
    out[ua <= 0.0] = 0.0
    out[ua >= 1.0] = 1.0
    if not interior.any():
        return out

    ui = ua[interior]

    # Moment-matched normal seed, clamped well inside the open interval.
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) * (a + b) * (a + b + 1.0)))
    x = np.clip(mean + sd * norm_ppf(ui), 1e-12, 1.0 - 1e-12)

    lo = np.zeros_like(x)
    hi = np.ones_like(x)

    # Newton is quadratic once it is near, so the unconverged set collapses within a few
    # passes. Carrying the converged elements through the continued fraction anyway costs
    # more than the whole rest of the sampler: every element pays for the slowest one,
    # because the fraction runs until its worst element is within tolerance.
    active = np.arange(x.size)

    for _ in range(100):
        xa = x[active]
        fx = betainc(a, b, xa) - ui[active]

        neg = fx < 0.0
        lo[active] = np.where(neg, xa, lo[active])
        hi[active] = np.where(fx > 0.0, xa, hi[active])

        unconverged = np.abs(fx) >= tol
        if not unconverged.any():
            break

        active = active[unconverged]
        xa = xa[unconverged]
        fx = fx[unconverged]

        d = beta_pdf(a, b, xa)
        nxt = xa - np.where(d > 1e-300, fx / d, 0.0)
        lo_a = lo[active]
        hi_a = hi[active]
        # Fall back to bisection whenever Newton leaves the bracket or stalls.
        bad = ~np.isfinite(nxt) | (nxt <= lo_a) | (nxt >= hi_a)
        x[active] = np.where(bad, 0.5 * (lo_a + hi_a), nxt)

    out[interior] = x
    return out
