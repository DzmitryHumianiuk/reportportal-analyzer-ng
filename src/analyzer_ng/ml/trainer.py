"""LightGBM multiclass trainer over stored feature snapshots (spec 03 §6.5).

The trainer reads **only** the feature vectors that were snapshotted into
``suggestion.features`` at serving time (spec §6.4 / risk register #5) — it never
recomputes features from raw logs, so the training distribution matches serving by
construction. Ground truth is the base issue-type group of the ``label_event``
locator (custom sub-types fold to their base group); ``ti`` is never a class — it
is the abstain outcome, so ``ti`` events are dropped from the training set.

Determinism (spec §6.5): fixed hyperparameters, ``seed=42``, ``deterministic=true``,
single-threaded, row-wise histogram — the same rows produce the same booster bytes.
The trained model serializes to a self-contained JSON blob (booster text + class
order + feature-schema version), independent of any pickle format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
from lightgbm import Booster, LGBMClassifier

from analyzer_ng.core.features import (
    BASE_LABELS,
    FEATURE_SCHEMA_VER,
    FEATURES,
    base_group,
    feature_names,
    to_vector,
)
from analyzer_ng.ml.calibration import CALIB_MIN_EVENTS, IsotonicCalibrator

# Fixed v1 hyperparameters (spec §6.5). ``min_child_samples`` == min_data_in_leaf;
# ``colsample_bytree`` == feature_fraction. Single-thread + deterministic +
# row-wise histogram make the booster bytes reproducible.
GBM_PARAMS: dict = {
    "objective": "multiclass",
    "num_leaves": 31,
    "n_estimators": 200,
    "learning_rate": 0.05,
    "min_child_samples": 20,
    "colsample_bytree": 0.9,
    "class_weight": "balanced",
    "random_state": 42,
    "n_jobs": 1,
    "deterministic": True,
    "force_row_wise": True,
    "verbosity": -1,
}

# spec §6.5 cold model: with < 50 label_events install-wide, skip GBM entirely.
GBM_MIN_EVENTS = 50


class TrainingError(RuntimeError):
    """Not enough usable data to train a model (caller falls back to rules)."""


def base_label(locator: str | None) -> str | None:
    """Base issue-type group of a locator ('pb001'→'pb'); None if not a GBM class.

    Thin alias over :func:`analyzer_ng.core.features.base_group` — one mapping
    source shared by the trainer, the eval harness, and feature extraction so a
    custom subtype folds to its base group identically everywhere (spec §6.5).
    """
    return base_group(locator)


def _dedup_latest(rows: list[dict]) -> list[dict]:
    """Keep the latest label_event per (project_id, item_id) — spec §6.5.

    ``fetch_training_frame`` returns rows ordered by ``ts DESC``, so the first
    occurrence of each item is its final label; later corrections supersede.
    """
    seen: set[tuple[int, int]] = set()
    out: list[dict] = []
    for r in rows:
        key = (int(r.get("project_id", 0)), int(r.get("item_id", 0)))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build_xy(rows: list[dict]) -> tuple[np.ndarray, list[str], list[int]]:
    """Materialize (X, y, project_ids) from deduped training-frame rows.

    Rows without a stored feature snapshot or whose label is not a GBM class
    (``ti``/unknown) are dropped — training is snapshot-only (§6.4).
    """
    x_rows: list[list[float]] = []
    y: list[str] = []
    projects: list[int] = []
    for r in _dedup_latest(rows):
        features = r.get("features")
        if not features:
            continue
        label = base_label(r.get("new_label"))
        if label is None:
            continue
        x_rows.append(to_vector(features))
        y.append(label)
        projects.append(int(r.get("project_id", 0)))
    if not x_rows:
        return np.empty((0, len(FEATURES)), dtype=np.float64), [], []
    return np.asarray(x_rows, dtype=np.float64), y, projects


@dataclass
class GbmModel:
    """A trained multiclass GBM, serialized as a self-contained JSON blob.

    ``classes`` is the label order of the booster's probability columns (a subset
    of :data:`BASE_LABELS`, in the order scikit-learn assigned). Prediction always
    returns a full distribution over ``BASE_LABELS`` (absent classes → 0.0).
    """

    booster_text: str
    classes: list[str]
    feature_schema_ver: int = FEATURE_SCHEMA_VER
    # The ordered feature list the booster was trained on, stamped into the artifact
    # so serving assembles the vector from THIS list (not the ambient registry) — the
    # robust schema invariant: a model is always fed vectors in its own trained order,
    # new columns it never saw are dropped and columns missing from an old snapshot
    # are back-filled with defaults. Defaults to the current registry order.
    feature_names: list[str] = field(default_factory=feature_names)
    _booster: Booster | None = field(default=None, repr=False, compare=False)

    def _get_booster(self) -> Booster:
        if self._booster is None:
            self._booster = Booster(model_str=self.booster_text)
        return self._booster

    def predict_matrix(self, x: np.ndarray) -> np.ndarray:
        """Class-probability matrix (n, len(classes)) aligned to ``self.classes``."""
        proba = np.asarray(self._get_booster().predict(x))
        if proba.ndim == 1:  # a single-class degenerate booster returns a vector
            proba = proba.reshape(-1, 1)
        return proba

    def predict_label(self, vector: list[float]) -> tuple[str, float, dict[str, float]]:
        """(argmax base label, raw max-prob, full distribution over BASE_LABELS)."""
        row = self.predict_matrix(np.asarray([vector], dtype=np.float64))[0]
        probs: dict[str, float] = dict.fromkeys(BASE_LABELS, 0.0)
        for cls, p in zip(self.classes, row, strict=False):
            probs[cls] = float(p)
        label = max(BASE_LABELS, key=lambda b: probs[b])
        return label, probs[label], probs

    def to_bytes(self) -> bytes:
        payload = {
            "booster": self.booster_text,
            "classes": list(self.classes),
            "feature_schema_ver": self.feature_schema_ver,
            "feature_names": list(self.feature_names),
        }
        return json.dumps(payload).encode("utf-8")

    @classmethod
    def from_bytes(cls, blob: bytes) -> GbmModel:
        data = json.loads(blob.decode("utf-8"))
        # A pre-v3 blob has no stored feature_names; it was trained on the registry of
        # its own schema, which serving only ever loads at the matching schema — so the
        # current registry order is the correct fallback for those same-schema loads.
        names = data.get("feature_names")
        return cls(
            booster_text=data["booster"],
            classes=list(data["classes"]),
            feature_schema_ver=int(data["feature_schema_ver"]),
            feature_names=list(names) if names else feature_names(),
        )


# Cross-validation folds for out-of-sample (out-of-fold) calibration scoring.
# Deterministic fold assignment (row index % K) keeps calibration reproducible
# given the seeded booster.
CALIB_FOLDS = 5


def _fit_booster(x: np.ndarray, y: list[str]) -> GbmModel:
    """Fit one deterministic multiclass booster from a materialized (X, y)."""
    clf = LGBMClassifier(**GBM_PARAMS)
    clf.fit(x, y)
    return GbmModel(
        booster_text=clf.booster_.model_to_string(),
        classes=[str(c) for c in clf.classes_],
        feature_schema_ver=FEATURE_SCHEMA_VER,
    )


def train_gbm(rows: list[dict]) -> GbmModel:
    """Train the install-wide multiclass GBM from stored feature snapshots.

    Raises :class:`TrainingError` when there are too few events (< 50, §6.5 cold
    model) or fewer than two classes to separate (no multiclass boundary).
    """
    x, y, _ = build_xy(rows)
    if len(y) < GBM_MIN_EVENTS:
        raise TrainingError(f"cold model: {len(y)} events < {GBM_MIN_EVENTS}")
    if len(set(y)) < 2:
        raise TrainingError("need ≥ 2 label classes to train a multiclass model")
    return _fit_booster(x, y)


def _oof_scores(
    rows: list[dict], *, k: int = CALIB_FOLDS
) -> tuple[list[float], list[int], list[int]]:
    """Out-of-fold ``(raw_max_prob, correct, project_id)`` via K-fold cross-fit.

    Fixing calibration on in-sample predictions over-reports ``p*`` — the booster
    is optimistic on rows it trained on — which would inflate the ``τ_auto`` auto
    band. So each row is scored by a booster trained on the *other* folds only
    (spec §6 calibration intent). Fold assignment is ``index % k`` (deterministic)
    and the booster is seeded, so the calibrators are reproducible. Folds whose
    train slice is single-class are skipped (their rows contribute no calibration
    signal) rather than trained degenerately.
    """
    x, y, projects = build_xy(rows)
    n = len(y)
    if n == 0:
        return [], [], []
    k = max(2, min(k, n))
    raws: list[float] = []
    corrects: list[int] = []
    projs: list[int] = []
    for f in range(k):
        train_idx = [i for i in range(n) if i % k != f]
        test_idx = [i for i in range(n) if i % k == f]
        y_train = [y[i] for i in train_idx]
        if not test_idx or len(set(y_train)) < 2:
            continue
        fold_model = _fit_booster(x[train_idx], y_train)
        for i in test_idx:
            label, raw, _probs = fold_model.predict_label(list(x[i]))
            raws.append(raw)
            corrects.append(1 if label == y[i] else 0)
            projs.append(projects[i])
    return raws, corrects, projs


def fit_calibrators(rows: list[dict]) -> dict[int | None, IsotonicCalibrator]:
    """Fit isotonic calibrators keyed by project_id (``None`` = install-wide).

    Calibration is fit on **out-of-sample** (out-of-fold) predictions so ``p*`` is
    honest — never on the model's own training rows (spec §6). Per-project
    calibration requires ≥ 300 events for that project; the install-wide calibrator
    is fitted from all events when there are ≥ 300 (spec §6.5). Projects below
    threshold get no per-project entry and fall back to install-wide (or, absent
    that, raw softmax) at serving time.
    """
    raw_all, correct_all, projs = _oof_scores(rows)
    if not raw_all:
        return {}
    by_project: dict[int, tuple[list[float], list[int]]] = {}
    for raw, correct, pr in zip(raw_all, correct_all, projs, strict=True):
        bucket = by_project.setdefault(pr, ([], []))
        bucket[0].append(raw)
        bucket[1].append(correct)

    out: dict[int | None, IsotonicCalibrator] = {}
    if len(raw_all) >= CALIB_MIN_EVENTS:
        out[None] = IsotonicCalibrator.fit(raw_all, correct_all)
    for pr, (praw, pcorrect) in by_project.items():
        if len(praw) >= CALIB_MIN_EVENTS:
            out[pr] = IsotonicCalibrator.fit(praw, pcorrect)
    return out
