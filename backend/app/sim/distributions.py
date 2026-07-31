"""Sampling shapes, as inverse CDFs.

Every shape is sampled by inverse transform rather than by a bespoke generator. That is
not a stylistic preference: Latin hypercube stratification and Iman-Conover reordering
both act on the *uniforms*, and only a monotone transform carries a stratified, rank-
correlated uniform through to a stratified, rank-correlated sample. A shape with its own
generator would silently opt out of both.

A :class:`DistributionSpec` carries **absolute support**. Recovering true bounds from
elicited percentiles is ``quant_validation.absolute_bounds``' job and it stays there —
this package must not depend on ``services``, and duplicating the solver would give the
platform two answers to the same question. Callers build a spec from a
``quant_validation.Moments`` via :func:`spec_from_moments`, which reads the attributes
structurally and imports nothing.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.sim.special import beta_ppf

__all__ = ["DistributionSpec", "PointMass", "spec_from_moments"]

DistKind = Literal["pert", "triangular", "uniform", "cumulative", "discrete", "point"]


class PointMass(BaseModel):
    """One node of a ``cumulative`` curve or one outcome of a ``discrete`` shape."""

    model_config = ConfigDict(frozen=True)

    x: float
    p: float = Field(ge=0.0, le=1.0)


class DistributionSpec(BaseModel):
    """A sampling shape with its support already resolved to absolute values.

    ``trigen`` is not a kind here. It is a triangular whose bounds were recovered from
    percentiles, and once they are recovered the two are the same shape — keeping the
    distinction past that point would only invite a second, divergent recovery.
    """

    model_config = ConfigDict(frozen=True)

    kind: DistKind
    lo: float = 0.0
    hi: float = 0.0
    ml: float | None = None
    #: Beta shape parameters for ``pert``. Supplied rather than recomputed so a spec and
    #: the moments it was built from cannot disagree about lambda.
    alpha: float | None = None
    beta: float | None = None
    points: tuple[PointMass, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> DistributionSpec:
        k = self.kind
        if k == "point":
            return self
        if k in ("pert", "triangular", "uniform"):
            if not math.isfinite(self.lo) or not math.isfinite(self.hi):
                raise ValueError(f"{k}: bounds must be finite")
            if self.hi < self.lo:
                raise ValueError(f"{k}: hi ({self.hi}) is below lo ({self.lo})")
            if k in ("pert", "triangular"):
                if self.ml is None:
                    raise ValueError(f"{k}: needs a most likely value")
                if not self.lo <= self.ml <= self.hi:
                    raise ValueError(f"{k}: most likely value is outside the support")
            if k == "pert" and self.hi > self.lo:
                if self.alpha is None or self.beta is None:
                    raise ValueError("pert: needs alpha and beta")
                if self.alpha <= 0 or self.beta <= 0:
                    raise ValueError("pert: alpha and beta must be positive")
            return self

        if not self.points:
            raise ValueError(f"{k}: needs at least one point")
        xs = [p.x for p in self.points]
        ps = [p.p for p in self.points]
        if k == "cumulative":
            if len(self.points) < 2:
                raise ValueError("cumulative: needs at least two points")
            if any(b < a for a, b in zip(xs, xs[1:])):
                raise ValueError("cumulative: x values must be non-decreasing")
            if any(b < a for a, b in zip(ps, ps[1:])):
                raise ValueError("cumulative: probabilities must be non-decreasing")
            if abs(ps[0]) > 1e-9 or abs(ps[-1] - 1.0) > 1e-9:
                raise ValueError("cumulative: curve must run from p=0 to p=1")
        else:
            total = sum(ps)
            if total <= 0:
                raise ValueError("discrete: masses must sum to something positive")
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"discrete: masses sum to {total}, not 1")
        return self

    # -- support ----------------------------------------------------------------

    @property
    def support(self) -> tuple[float, float]:
        if self.kind in ("cumulative", "discrete"):
            xs = [p.x for p in self.points]
            return min(xs), max(xs)
        return self.lo, self.hi

    @property
    def is_degenerate(self) -> bool:
        lo, hi = self.support
        return hi <= lo

    # -- sampling ---------------------------------------------------------------

    def ppf(self, u: NDArray[np.float64]) -> NDArray[np.float64]:
        """Inverse CDF, vectorised. ``u`` must lie in ``(0, 1)``."""
        k = self.kind
        if k == "point":
            return np.full(u.shape, self.lo, dtype=np.float64)

        if k == "uniform":
            return self.lo + u * (self.hi - self.lo)

        if k == "triangular":
            return self._triangular_ppf(u)

        if k == "pert":
            if self.hi <= self.lo:
                return np.full(u.shape, self.lo, dtype=np.float64)
            assert self.alpha is not None and self.beta is not None
            return self.lo + (self.hi - self.lo) * beta_ppf(self.alpha, self.beta, u)

        if k == "cumulative":
            xs = np.array([p.x for p in self.points], dtype=np.float64)
            ps = np.array([p.p for p in self.points], dtype=np.float64)
            # np.interp needs a strictly increasing x-argument; a flat run in the curve
            # is a point mass, and nudging the duplicate keeps interp picking the upper
            # node so the mass lands on the value the analyst drew it at.
            ps = np.maximum.accumulate(ps)
            bump = np.arange(ps.size, dtype=np.float64) * 1e-15
            return np.interp(u, ps + bump, xs)

        # discrete
        xs = np.array([p.x for p in self.points], dtype=np.float64)
        ps = np.array([p.p for p in self.points], dtype=np.float64)
        cum = np.cumsum(ps / ps.sum())
        cum[-1] = 1.0
        idx = np.searchsorted(cum, u, side="left")
        return xs[np.clip(idx, 0, xs.size - 1)]

    def _triangular_ppf(self, u: NDArray[np.float64]) -> NDArray[np.float64]:
        a, b = self.lo, self.hi
        c = self.ml if self.ml is not None else (a + b) / 2.0
        if b <= a:
            return np.full(u.shape, a, dtype=np.float64)
        span = b - a
        split = (c - a) / span
        lower = a + np.sqrt(u * span * (c - a))
        upper = b - np.sqrt((1.0 - u) * span * (b - c))
        return np.where(u <= split, lower, upper)

    # -- exact moments, for regression tests and sanity reporting ---------------

    def mean(self) -> float:
        k = self.kind
        if k == "point":
            return self.lo
        if k == "uniform":
            return (self.lo + self.hi) / 2.0
        if k == "triangular":
            return (self.lo + float(self.ml or 0.0) + self.hi) / 3.0
        if k == "pert":
            if self.hi <= self.lo:
                return self.lo
            assert self.alpha is not None and self.beta is not None
            frac = self.alpha / (self.alpha + self.beta)
            return self.lo + frac * (self.hi - self.lo)
        if k == "cumulative":
            xs = [p.x for p in self.points]
            ps = [p.p for p in self.points]
            return sum(
                (ps[i + 1] - ps[i]) * (xs[i] + xs[i + 1]) / 2.0
                for i in range(len(xs) - 1)
            )
        xs = [p.x for p in self.points]
        ps = [p.p for p in self.points]
        total = sum(ps)
        return sum(x * p for x, p in zip(xs, ps)) / total


def spec_from_moments(m: Any) -> DistributionSpec:
    """Build a spec from anything shaped like ``quant_validation.Moments``.

    Structural rather than typed on purpose. ``Moments`` lives in ``services`` and this
    package may not import it; reading ``.kind``, ``.lo``, ``.hi``, ``.ml``, ``.alpha``
    and ``.beta`` keeps the dependency pointing the way the layering says it should.

    ``trigen`` collapses to ``triangular`` because by this point the percentile bounds
    have already been widened into absolute ones.
    """
    kind = str(m.kind)
    if kind == "trigen":
        kind = "triangular"

    if kind in ("cumulative", "discrete"):
        raise ValueError(
            f"{kind} carries its own points and cannot be rebuilt from moments; "
            "construct the spec from the elicited point list instead"
        )

    lo = float(m.lo)
    hi = float(m.hi)
    if hi <= lo:
        return DistributionSpec(kind="point", lo=lo, hi=lo)

    return DistributionSpec(
        kind=kind,  # type: ignore[arg-type]
        lo=lo,
        hi=hi,
        ml=None if getattr(m, "ml", None) is None else float(m.ml),
        alpha=None if getattr(m, "alpha", None) is None else float(m.alpha),
        beta=None if getattr(m, "beta", None) is None else float(m.beta),
    )
