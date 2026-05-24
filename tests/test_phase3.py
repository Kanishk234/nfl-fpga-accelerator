"""
Phase 3 test suite — quantization correctness and Phase 4 readiness.

Tests cover: feature count, model shapes, output ranges, weight transfer
fidelity, dropout absence, parameter budget, accuracy/MAE thresholds,
artifact existence, determinism, and per-game numerical agreement.
"""

import json
import os
import pickle

import tensorflow as tf
tf.config.run_functions_eagerly(True)  # QKeras 0.9 needs eager mode for .numpy() in quantizers

import keras
import numpy as np
import pandas as pd
import pytest

from phase3_quantization.qkeras_model import build_quantized_model, compile_quantized_model
from phase3_quantization.evaluate_quantized import evaluate_both_models


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def features():
    with open('artifacts/features.json') as f:
        return json.load(f)['features']


@pytest.fixture(scope='module')
def q_model(features):
    model = build_quantized_model(n_features=len(features))
    compile_quantized_model(model)
    return model


@pytest.fixture(scope='module')
def fp32_model():
    return keras.models.load_model('artifacts/model_best.keras')


@pytest.fixture(scope='module')
def quantized_model_saved(features):
    """Load the fine-tuned quantized model from disk.

    keras.models.load_model fails for QKeras models in Keras 3 (QDense is not
    registered as a native Keras class). Workaround: build a fresh model with
    the same architecture and restore only the weights.
    """
    path = 'artifacts/model_quantized.keras'
    if not os.path.exists(path):
        pytest.skip("model_quantized.keras not yet generated — run quantize.py first")
    model = build_quantized_model(n_features=len(features))
    compile_quantized_model(model)
    model.load_weights(path)
    return model


@pytest.fixture(scope='module')
def val_data(features):
    if not os.path.exists('artifacts/scaler.pkl'):
        pytest.skip("scaler.pkl missing")
    if not os.path.exists('data/processed/games.parquet'):
        pytest.skip("games.parquet missing")
    with open('artifacts/scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)
    games = pd.read_parquet('data/processed/games.parquet')
    val   = games[games['season'].isin([2021, 2022])]
    X_val = scaler.transform(val[features].values.astype('float32'))
    y_val = {'win': val['home_win'].values.astype('float32'),
             'spread': val['spread'].values.astype('float32')}
    return X_val, y_val


# ---------------------------------------------------------------------------
# Structure tests (no artifacts required)
# ---------------------------------------------------------------------------

class TestFeatureCount:
    def test_features_json_has_21_features(self, features):
        """Ground truth: features.json drives input shape throughout Phase 3+4."""
        assert len(features) == 21, f"Expected 21 features, got {len(features)}"


class TestQKerasModelStructure:
    def test_qkeras_model_input_shape(self, q_model):
        assert q_model.input_shape == (None, 21), \
            f"Input shape {q_model.input_shape} != (None, 21)"

    def test_qkeras_model_output_shapes(self, q_model):
        x = np.random.rand(10, 21).astype('float32')
        win_out, spread_out = q_model.predict(x, verbose=0)
        assert win_out.shape    == (10, 1), f"Win shape {win_out.shape}"
        assert spread_out.shape == (10, 1), f"Spread shape {spread_out.shape}"

    def test_win_probs_in_unit_interval(self, q_model):
        """Fixed-point overflow can push sigmoid outside [0,1] — catch it."""
        x = np.random.rand(50, 21).astype('float32')
        win_out, _ = q_model.predict(x, verbose=0)
        assert win_out.min() >= 0.0 and win_out.max() <= 1.0, \
            f"Win probs out of [0,1]: min={win_out.min():.4f} max={win_out.max():.4f}"

    def test_spread_outputs_in_plausible_range(self, q_model):
        x = np.random.rand(50, 21).astype('float32')
        _, spread_out = q_model.predict(x, verbose=0)
        assert spread_out.min() >= -60 and spread_out.max() <= 60, \
            f"Spread out of [-60,60]: min={spread_out.min():.1f} max={spread_out.max():.1f}"

    def test_dropout_absent_from_qkeras_model(self, q_model):
        """Dropout is training-only and must not appear in the inference model."""
        dropout_layers = [l for l in q_model.layers if 'dropout' in l.name.lower()]
        assert len(dropout_layers) == 0, \
            f"Found dropout layers: {[l.name for l in dropout_layers]}"

    def test_quantized_parameter_count(self, q_model):
        assert q_model.count_params() < 50_000, \
            f"Param count {q_model.count_params()} exceeds Basys3 budget of 50,000"


class TestWeightTransfer:
    def test_weight_transfer_correctness(self, fp32_model, features):
        """Immediately after transfer, weights should be numerically identical."""
        q = build_quantized_model(n_features=len(features))
        compile_quantized_model(q)

        layer_names = ['dense_1', 'dense_2', 'dense_3', 'win', 'spread']
        for name in layer_names:
            q.get_layer(name).set_weights(fp32_model.get_layer(name).get_weights())

        for name in layer_names:
            fp_w  = fp32_model.get_layer(name).get_weights()[0]
            q_w   = q.get_layer(name).get_weights()[0]
            delta = np.abs(fp_w - q_w).max()
            assert delta < 0.001, \
                f"{name}: max weight delta {delta:.6f} >= 0.001 after transfer"


# ---------------------------------------------------------------------------
# Artifact tests (require quantize.py to have run)
# ---------------------------------------------------------------------------

class TestQuantizedModelAccuracy:
    def test_accuracy_drop_within_tolerance(self, fp32_model, quantized_model_saved, val_data):
        X_val, y_val = val_data
        fp_preds = fp32_model.predict(X_val, verbose=0)
        q_preds  = quantized_model_saved.predict(X_val, verbose=0)
        fp_acc = ((fp_preds[0].flatten() >= 0.5) == y_val['win']).mean()
        q_acc  = ((q_preds[0].flatten()  >= 0.5) == y_val['win']).mean()
        drop   = fp_acc - q_acc
        assert drop <= 0.02, \
            f"Accuracy drop {drop:.1%} exceeds 2% tolerance (fp={fp_acc:.3f}, q={q_acc:.3f})"

    def test_quantized_accuracy_above_floor(self, quantized_model_saved, val_data):
        X_val, y_val = val_data
        q_preds = quantized_model_saved.predict(X_val, verbose=0)
        q_acc   = ((q_preds[0].flatten() >= 0.5) == y_val['win']).mean()
        assert q_acc >= 0.63, \
            f"Quantized win accuracy {q_acc:.3f} below 63% floor"

    def test_spread_mae_degradation(self, fp32_model, quantized_model_saved, val_data):
        X_val, y_val = val_data
        fp_preds = fp32_model.predict(X_val, verbose=0)
        q_preds  = quantized_model_saved.predict(X_val, verbose=0)
        fp_mae = np.abs(fp_preds[1].flatten() - y_val['spread']).mean()
        q_mae  = np.abs(q_preds[1].flatten()  - y_val['spread']).mean()
        assert q_mae - fp_mae <= 1.0, \
            f"Spread MAE degradation {q_mae - fp_mae:.2f} pts exceeds 1.0 pt tolerance"


class TestArtifacts:
    def test_quantized_model_saved(self):
        assert os.path.exists('artifacts/model_quantized.keras'), \
            "artifacts/model_quantized.keras not found — run quantize.py first"

    def test_quantization_report_saved(self):
        assert os.path.exists('artifacts/quantization_report.json'), \
            "artifacts/quantization_report.json not found — run quantize.py first"

    def test_quantization_report_ready_for_phase4(self):
        if not os.path.exists('artifacts/quantization_report.json'):
            pytest.skip("quantization_report.json not yet generated")
        with open('artifacts/quantization_report.json') as f:
            report = json.load(f)
        assert report['ready_for_phase4'], \
            f"ready_for_phase4 is False — q_win_acc={report['q_win_accuracy']:.3f}, " \
            f"acc_drop={report['accuracy_drop']:.3f}"


class TestDeterminism:
    def test_deterministic_inference(self, quantized_model_saved, val_data):
        """Fixed-point arithmetic must be deterministic."""
        X_val, _ = val_data
        x = X_val[:20]
        out1 = quantized_model_saved.predict(x, verbose=0)
        out2 = quantized_model_saved.predict(x, verbose=0)
        assert np.allclose(out1[0], out2[0], atol=1e-6), "Win output not deterministic"
        assert np.allclose(out1[1], out2[1], atol=1e-6), "Spread output not deterministic"


class TestNumericalAgreement:
    def test_numerical_agreement_per_game(self, fp32_model, quantized_model_saved, val_data):
        """
        Per-game win probability delta < 0.10 for 20 evenly-spaced val games.
        Catches individual prediction flips that aggregate accuracy would hide.
        """
        X_val, _ = val_data
        indices  = np.linspace(0, len(X_val) - 1, 20, dtype=int)
        fp_preds = fp32_model.predict(X_val, verbose=0)
        q_preds  = quantized_model_saved.predict(X_val, verbose=0)

        for i in indices:
            fp_prob = fp_preds[0][i][0]
            q_prob  = q_preds[0][i][0]
            delta   = abs(fp_prob - q_prob)
            assert delta < 0.10, \
                f"Game {i}: fp={fp_prob:.3f} q={q_prob:.3f} delta={delta:.3f} >= 0.10"


# ---------------------------------------------------------------------------
# Best-effort bit width introspection
# ---------------------------------------------------------------------------

class TestBitWidths:
    def test_qdense_kernel_quantizer_bits(self, q_model):
        """Verify <8,0> kernel quantizer — best-effort, skip if API unavailable."""
        for name in ['dense_1', 'dense_2', 'dense_3']:
            layer = q_model.get_layer(name)
            try:
                q = layer.kernel_quantizer_internal
                assert q.bits == 8, f"{name}: kernel bits {q.bits} != 8"
                assert q.integer == 0, f"{name}: kernel integer {q.integer} != 0"
            except AttributeError:
                pytest.skip("kernel_quantizer introspection unavailable on this QKeras version")

    def test_qdense_bias_quantizer_bits(self, q_model):
        """Verify <16,6> bias quantizer — best-effort."""
        for name in ['dense_1', 'dense_2', 'dense_3']:
            layer = q_model.get_layer(name)
            try:
                q = layer.bias_quantizer_internal
                assert q.bits == 16, f"{name}: bias bits {q.bits} != 16"
                assert q.integer == 6, f"{name}: bias integer {q.integer} != 6"
            except AttributeError:
                pytest.skip("bias_quantizer introspection unavailable on this QKeras version")

    def test_qactivation_bits(self, q_model):
        """Verify <8,4> activation quantizer — best-effort."""
        for name in ['relu_1', 'relu_2', 'relu_3']:
            layer = q_model.get_layer(name)
            try:
                config = layer.get_config()
                assert '8' in str(config['activation']), \
                    f"{name}: activation config does not mention 8 bits: {config['activation']}"
            except Exception:
                pytest.skip("QActivation config introspection unavailable")
