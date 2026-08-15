#!/usr/bin/env python3
"""Small numpy-only nonlinear least squares, so analysis needs no scipy.

The dev shell carries numpy and pillow and nothing else, and the fits these
scripts need are two to five parameters over a few hundred residuals - well
inside what plain Levenberg-Marquardt with a numerical Jacobian handles.
Adding scipy to flake.nix for that would be a heavier dependency than the
problem deserves.
"""

from collections.abc import Callable

import numpy as np


def least_squares(
    residual: Callable[[np.ndarray], np.ndarray],
    initial: list[float] | np.ndarray,
    *,
    iterations: int = 200,
    tolerance: float = 1e-12,
) -> np.ndarray:
    """Minimise sum(residual(p)**2), returning the parameter vector."""
    p = np.array(initial, dtype=float)
    current = np.asarray(residual(p), dtype=float)
    cost = float(current @ current)
    damping = 1e-3
    for _ in range(iterations):
        # Central differences: the step is scaled to each parameter so a
        # parameter near zero still gets a meaningful perturbation.
        jacobian = np.empty((current.size, p.size))
        for i in range(p.size):
            step = 1e-6 * max(abs(p[i]), 1.0)
            forward, backward = p.copy(), p.copy()
            forward[i] += step
            backward[i] -= step
            jacobian[:, i] = (
                np.asarray(residual(forward)) - np.asarray(residual(backward))
            ) / (2.0 * step)

        gradient = jacobian.T @ current
        normal = jacobian.T @ jacobian
        improved = False
        for _ in range(30):
            try:
                delta = np.linalg.solve(
                    normal + damping * np.diag(np.maximum(np.diag(normal), 1e-12)),
                    -gradient,
                )
            except np.linalg.LinAlgError:
                damping *= 10.0
                continue
            candidate = p + delta
            trial = np.asarray(residual(candidate), dtype=float)
            trial_cost = float(trial @ trial)
            if trial_cost < cost:
                p, current, cost = candidate, trial, trial_cost
                damping = max(damping * 0.3, 1e-12)
                improved = True
                break
            damping *= 10.0
        if not improved or cost < tolerance:
            break
    return p


def erf(x: np.ndarray) -> np.ndarray:
    """Vectorised error function, to the accuracy the stdlib's scalar one has."""
    from math import erf as scalar_erf

    return np.vectorize(scalar_erf, otypes=[float])(np.asarray(x, dtype=float))
