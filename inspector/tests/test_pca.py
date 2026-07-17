"""Unit tests for the numpy-SVD PCA projector and halfvec parsing."""

from __future__ import annotations

import numpy as np

from backend.pca import parse_halfvec, pca_project


def test_pca_recovers_dominant_axis():
    # Points along a line in 3D → first component captures ~all variance.
    t = np.linspace(-1, 1, 20)
    x = np.stack([t, 2 * t, -t], axis=1) + np.random.default_rng(0).normal(0, 1e-3, (20, 3))
    r = pca_project(x, 3)
    assert r.n_components == 3
    assert r.explained_variance_ratio[0] > 0.99
    assert abs(sum(r.explained_variance_ratio) - 1.0) < 1e-6


def test_pca_is_sign_stable():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(30, 8))
    a = pca_project(x, 3).coords
    b = pca_project(x, 3).coords
    assert np.allclose(a, b)


def test_pca_handles_few_points():
    # A single point → zero usable components, well-formed empty result.
    r = pca_project(np.zeros((1, 384)), 3)
    assert r.n_components in (0, 1)
    assert r.coords.shape[0] == 1


def test_pca_fewer_dims_than_requested():
    # Two identical-direction points collapse to <3 dims; no crash.
    x = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    r = pca_project(x, 3)
    assert r.n_components <= 2
    assert r.coords.shape == (3, r.n_components)


def test_parse_halfvec():
    assert parse_halfvec("[1,2,3]") == [1.0, 2.0, 3.0]
    assert parse_halfvec("[]") is None
    assert parse_halfvec(None) is None
    assert parse_halfvec("1.5, 2.5") == [1.5, 2.5]
    assert parse_halfvec("garbage") is None
