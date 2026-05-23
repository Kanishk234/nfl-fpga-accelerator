"""
Phase 2 test suite.

Tests cover: scaler correctness, model output shapes, parameter count,
feature order sensitivity, artifact existence, and validation accuracy threshold.
"""

import json
import os
import pickle

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def artifacts():
    """Load all three artifacts once per module."""
    scaler_path = 'artifacts/scaler.pkl'
    model_path  = 'artifacts/model_best.keras'
    feat_path   = 'artifacts/features.json'

    for path in [scaler_path, model_path, feat_path]:
        if not os.path.exists(path):
            pytest.skip(f"{path} not found — run phase2_model/train.py first")

    import keras
    model  = keras.models.load_model(model_path)
    with open(scaler_path, 'rb') as f:
        scaler = pickle.load(f)
    with open(feat_path) as f:
        meta = json.load(f)

    return {'model': model, 'scaler': scaler, 'meta': meta}


@pytest.fixture(scope='module')
def split_data(artifacts):
    """Load games.parquet and apply the same temporal split as training."""
    parquet_path = 'data/processed/games.parquet'
    if not os.path.exists(parquet_path):
        pytest.skip("games.parquet not found — run pipeline first")

    games    = pd.read_parquet(parquet_path)
    FEATURES = artifacts['meta']['features']

    train = games[games['season'] <= 2020]
    val   = games[games['season'].isin([2021, 2022])]

    X_train = train[FEATURES].values.astype('float32')
    X_val   = val[FEATURES].values.astype('float32')

    scaler = artifacts['scaler']
    X_train_scaled = scaler.transform(X_train)
    X_val_scaled   = scaler.transform(X_val)

    y_val = {
        'win':    val['home_win'].values.astype('float32'),
        'spread': val['spread'].values.astype('float32'),
    }

    return {
        'X_train': X_train,
        'X_val': X_val,
        'X_train_scaled': X_train_scaled,
        'X_val_scaled': X_val_scaled,
        'y_val': y_val,
        'FEATURES': FEATURES,
        'val_games': val,
    }


# ---------------------------------------------------------------------------
# Scaler tests
# ---------------------------------------------------------------------------

class TestScalerFitOnTrainOnly:
    def test_scaler_min_matches_train_min(self, artifacts, split_data):
        """scaler.data_min_ must match X_train column minimums, not val/test."""
        scaler  = artifacts['scaler']
        X_train = split_data['X_train']
        np.testing.assert_allclose(
            scaler.data_min_, X_train.min(axis=0),
            rtol=1e-5,
            err_msg="scaler.data_min_ does not match training data minimums",
        )

    def test_scaler_max_matches_train_max(self, artifacts, split_data):
        scaler  = artifacts['scaler']
        X_train = split_data['X_train']
        np.testing.assert_allclose(
            scaler.data_max_, X_train.max(axis=0),
            rtol=1e-5,
            err_msg="scaler.data_max_ does not match training data maximums",
        )


class TestScaledTrainRange:
    def test_train_scaled_values_in_unit_range(self, split_data):
        """All scaled training values must be in [0, 1] (float32 tolerance applied)."""
        X_s = split_data['X_train_scaled']
        assert X_s.min() >= -1e-6, f"Scaled train min {X_s.min():.6f} < 0"
        assert X_s.max() <= 1.0 + 1e-6, f"Scaled train max {X_s.max():.6f} > 1"


class TestScalerReproducibility:
    def test_transform_is_deterministic(self, artifacts, split_data):
        """Transforming the same input twice must yield identical results."""
        scaler = artifacts['scaler']
        X      = split_data['X_val'][:10]
        out1   = scaler.transform(X)
        out2   = scaler.transform(X)
        np.testing.assert_array_equal(out1, out2)


# ---------------------------------------------------------------------------
# Model output tests
# ---------------------------------------------------------------------------

class TestModelOutputShapes:
    def test_win_output_shape(self, artifacts, split_data):
        """Win head must return shape (N, 1) for a batch of N samples."""
        model = artifacts['model']
        X     = split_data['X_val_scaled'][:10]
        preds = model.predict(X, verbose=0)
        assert preds[0].shape == (10, 1), \
            f"Win output shape {preds[0].shape} != (10, 1)"

    def test_spread_output_shape(self, artifacts, split_data):
        """Spread head must return shape (N, 1) for a batch of N samples."""
        model = artifacts['model']
        X     = split_data['X_val_scaled'][:10]
        preds = model.predict(X, verbose=0)
        assert preds[1].shape == (10, 1), \
            f"Spread output shape {preds[1].shape} != (10, 1)"


class TestWinOutputRange:
    def test_win_probs_in_unit_interval(self, artifacts, split_data):
        """Sigmoid output must always be in [0, 1]."""
        model     = artifacts['model']
        X         = split_data['X_val_scaled']
        win_probs = model.predict(X, verbose=0)[0].flatten()
        assert win_probs.min() >= 0.0, f"Win prob min {win_probs.min():.4f} < 0"
        assert win_probs.max() <= 1.0, f"Win prob max {win_probs.max():.4f} > 1"


# ---------------------------------------------------------------------------
# Architecture / FPGA constraint tests
# ---------------------------------------------------------------------------

class TestModelParameterCount:
    def test_params_within_basys3_budget(self, artifacts):
        """Total parameters must stay under 50,000 (Basys 3 DSP/BRAM constraint)."""
        total = artifacts['model'].count_params()
        assert total < 50_000, \
            f"Model has {total:,} parameters — exceeds Basys 3 budget of 50,000"


class TestFeatureOrderSensitivity:
    def test_shuffled_features_produce_different_predictions(self, artifacts, split_data):
        """
        The model must be sensitive to feature order.
        Passing features in a different order should change predictions,
        which confirms the canonical order is being respected during training.
        """
        model    = artifacts['model']
        scaler   = artifacts['scaler']
        FEATURES = split_data['FEATURES']
        X        = split_data['X_val'][:20]

        X_scaled    = scaler.transform(X)
        preds_orig  = model.predict(X_scaled, verbose=0)[0].flatten()

        # Shuffle column order
        rng         = np.random.default_rng(seed=42)
        shuffled_ix = rng.permutation(len(FEATURES))
        X_shuffled  = X[:, shuffled_ix]
        # Re-scale with shuffled columns (each column now means something different)
        from sklearn.preprocessing import MinMaxScaler
        scaler_shuf = MinMaxScaler().fit(X_shuffled)
        X_shuf_s    = scaler_shuf.transform(X_shuffled)

        preds_shuf = model.predict(X_shuf_s, verbose=0)[0].flatten()

        assert not np.allclose(preds_orig, preds_shuf, atol=1e-3), \
            "Shuffling feature order produced identical predictions — order may not matter"


# ---------------------------------------------------------------------------
# Artifact existence
# ---------------------------------------------------------------------------

class TestArtifactsExistAfterTraining:
    def test_model_exists(self):
        assert os.path.exists('artifacts/model_best.keras'), \
            "artifacts/model_best.keras missing — run train.py first"

    def test_scaler_exists(self):
        assert os.path.exists('artifacts/scaler.pkl'), \
            "artifacts/scaler.pkl missing — run train.py first"

    def test_features_json_exists(self):
        assert os.path.exists('artifacts/features.json'), \
            "artifacts/features.json missing — run phase 1 pipeline first"


# ---------------------------------------------------------------------------
# Full inference pipeline round-trip
# ---------------------------------------------------------------------------

class TestInferencePipeline:
    def test_full_roundtrip_output_ranges(self, artifacts, split_data):
        """
        Raw features → scale → predict → verify output ranges.
        Win prob in [0,1], spread in [-60, 60].
        """
        model  = artifacts['model']
        scaler = artifacts['scaler']
        X_raw  = split_data['X_val'][:5]

        X_scaled = scaler.transform(X_raw)
        preds    = model.predict(X_scaled, verbose=0)

        win_probs    = preds[0].flatten()
        spread_preds = preds[1].flatten()

        assert all(0 <= p <= 1 for p in win_probs), \
            f"Win probs outside [0,1]: {win_probs}"
        assert all(-60 <= p <= 60 for p in spread_preds), \
            f"Spread predictions outside [-60, 60]: {spread_preds}"


# ---------------------------------------------------------------------------
# Performance threshold (aspirational — must pass before Phase 3)
# ---------------------------------------------------------------------------

class TestValAccuracyThreshold:
    def test_validation_accuracy_at_least_63_percent(self, artifacts, split_data):
        """
        Trained model must achieve >= 63% win accuracy on 2021–2022 validation set.
        This test is intentionally aspirational — it should FAIL until the model
        is good enough. Do not lower the threshold; improve the model instead.
        """
        model     = artifacts['model']
        X_val_s   = split_data['X_val_scaled']
        y_val     = split_data['y_val']

        win_probs = model.predict(X_val_s, verbose=0)[0].flatten()
        win_preds = (win_probs >= 0.5).astype(int)
        accuracy  = (win_preds == y_val['win']).mean()

        assert accuracy >= 0.63, \
            (f"Validation accuracy {accuracy:.3f} below 63% threshold. "
             f"Improve the model before proceeding to Phase 3.")
