"""Pure-numpy PCA via SVD (no scikit-learn in the inspector image).

Used to project 384-dim halfvec embeddings down to 3D (Modes Map) for display.
Deterministic, sign-stabilised, and safe on degenerate inputs (0/1 rows, all
identical points) so the frontend always gets a well-formed payload or an honest
empty signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PCAResult:
    coords: np.ndarray  # (n, k) projected coordinates
    explained_variance_ratio: list[float]  # length k
    n_components: int


def pca_project(vectors: np.ndarray, n_components: int = 3) -> PCAResult:
    """Project ``vectors`` (n, d) onto its top-``n_components`` principal axes.

    Sign convention: each component's largest-magnitude loading is forced
    positive, making the embedding orientation reproducible across runs.
    """
    x = np.asarray(vectors, dtype=np.float64)
    if x.ndim != 2:
        raise ValueError("vectors must be a 2D array")

    n, d = x.shape
    k = int(min(n_components, n, d)) if n and d else 0
    if k == 0:
        return PCAResult(coords=np.zeros((n, 0)), explained_variance_ratio=[], n_components=0)

    mean = x.mean(axis=0, keepdims=True)
    centered = x - mean

    # SVD of the centered data: centered = U S Vt. Principal axes are rows of Vt.
    u, s, vt = np.linalg.svd(centered, full_matrices=False)

    components = vt[:k]  # (k, d)

    # Sign stabilisation.
    for i in range(k):
        row = components[i]
        j = int(np.argmax(np.abs(row)))
        if row[j] < 0:
            components[i] = -row
            u[:, i] = -u[:, i]

    coords = centered @ components.T  # (n, k)

    total_var = float((s**2).sum())
    if total_var <= 0.0:
        evr = [0.0] * k
    else:
        evr = [float(v) for v in (s[:k] ** 2 / total_var)]

    return PCAResult(coords=coords, explained_variance_ratio=evr, n_components=k)


def parse_halfvec(literal: str | None) -> list[float] | None:
    """Parse a pgvector/halfvec text literal ``[a,b,c]`` into a list of floats."""
    if not literal:
        return None
    s = literal.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    if not s:
        return None
    try:
        return [float(p) for p in s.split(",")]
    except ValueError:
        return None
