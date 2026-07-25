"""
Raw-feature encoder for the GBDT accelerator.

Subclasses the MLP's FeatureBuilder rather than copying it: the game lookup and
matchup-construction logic (`from_historical_game`, `from_current_week`,
`_build_matchup_features`) are model-independent — they just assemble the 21
features in the locked `features.json` order. Only the ENCODING differs, so
that is all this overrides.

MLP encoding:  scaler.transform(x) -> clip(round(v * 255), 0, 255)   -> 21 bytes
GBDT encoding: round(x * 4096) as int24, LSB first                   -> 63 bytes

The GBDT deliberately does NOT use `scaler.pkl`. Gradient-boosted trees split on
raw thresholds, so scaling is a no-op at best; the models in `artifacts/gbdt/`
were trained on raw features and the hardware thresholds are raw. (The base
class still loads the scaler in __init__ — harmless, and it keeps the frozen
artifact on one code path. It is never applied here.)

The encoding must match `make_chain_golden.py`'s `quant()` bit-for-bit — that is
what lets the board be compared bit-exactly against the phase 6 golden.
"""
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from phase7_deploy.inference.feature_builder import FeatureBuilder
from phase7_deploy_conifer.inference.fpga_client_gbdt import SCALE, pack_features

logger = logging.getLogger(__name__)


class GBDTFeatureBuilder(FeatureBuilder):
    """FeatureBuilder that emits the 63-byte raw fixed-point packet."""

    def quantize(self, values) -> list[int]:
        """21 raw features -> 21 int24 words, matching the golden bit-for-bit.

        The float32 cast is load-bearing, not incidental. `make_chain_golden.py`
        quantizes `va[FEAT].values.astype('float32')`, and the models were
        trained on float32 features — so float32 is the canonical value a
        feature *has*. Going straight from the parquet's float64 shifts the LSB
        on roughly half the games (elo ~1500 exceeds float32's ~7 significant
        digits), which is a 1-ulp input skew: exactly the failure mode that
        broke bit-exactness in phase 6 (see PHASE6_CONIFER_COMPLETE.md
        "the round-vs-truncate 1-ulp bug"), just from the other direction.

        np.round (half-to-even) is used rather than Python's round for the same
        reason — it is what the golden used.
        """
        vals = np.asarray(values, dtype='float32')
        if vals.shape != (self.n_features,):
            raise ValueError(f"Expected {self.n_features} values, got {vals.shape}")
        bad = ~np.isfinite(vals)
        if bad.any():
            names = [self.features[i] for i in np.where(bad)[0]]
            raise ValueError(f"Non-finite feature value(s): {names}")

        q = np.round(vals.astype(np.float64) * SCALE).astype(np.int64)
        out_of_range = (q < -(1 << 23)) | (q >= (1 << 23))
        if out_of_range.any():
            names = [self.features[i] for i in np.where(out_of_range)[0]]
            raise ValueError(
                f"Feature(s) {names} do not fit ap_fixed<24,12> "
                f"(range [-2048, +2048)) — the hardware has the same limit"
            )
        return (q & 0xFFFFFF).tolist()

    def _encode_values(self, values) -> list[int]:
        """21 raw features -> 63 wire bytes."""
        return pack_features(self.quantize(values))

    @property
    def n_features(self) -> int:
        return len(self.features)

    def _encode_row(self, row: pd.Series) -> list[int]:
        return self._encode_values(row[self.features].values)

    def _encode_feature_dict(self, feat_dict: dict) -> list[int]:
        return self._encode_values([feat_dict.get(f, 0.0) for f in self.features])

    def fixed_words(self, row: pd.Series) -> list[int]:
        """The 21 int24 words for a row — comparable directly against
        chain_golden/tb_inputs.mem, which is stored word-per-line."""
        return self.quantize(row[self.features].values)
