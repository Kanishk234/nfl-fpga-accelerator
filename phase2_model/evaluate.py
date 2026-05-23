"""
Evaluation utilities for the NFL outcome predictor.

Computes classification metrics (accuracy, log loss, AUC) and regression
metrics (MAE, RMSE) against baseline predictors. Gates test-set access
behind validation thresholds to prevent overfitting to the test set.
"""

import numpy as np
from sklearn.metrics import log_loss, roc_auc_score


def evaluate_model(model, X_scaled, y, split_name='Validation', games_df=None):
    """
    Evaluate model on a scaled feature matrix and label dict.

    Args:
        model:       Trained Keras model.
        X_scaled:    Scaled feature matrix (float32).
        y:           Dict with keys 'win' (int array) and 'spread' (float array).
        split_name:  Label for print output ('Validation' or 'Test').
        games_df:    Optional — original games DataFrame, used to compute
                     Vegas accuracy baseline if vegas_spread column is present.

    Returns:
        True if thresholds pass (or split is not Validation), False otherwise.
    """
    predictions = model.predict(X_scaled, verbose=0)
    win_probs    = predictions[0].flatten()
    spread_preds = predictions[1].flatten()

    # Classification metrics
    win_preds = (win_probs >= 0.5).astype(int)
    accuracy  = (win_preds == y['win']).mean()
    logloss   = log_loss(y['win'], win_probs)
    auc       = roc_auc_score(y['win'], win_probs)

    # Regression metrics
    spread_mae  = np.abs(spread_preds - y['spread']).mean()
    spread_rmse = np.sqrt(((spread_preds - y['spread']) ** 2).mean())

    # Baselines
    always_home_accuracy = y['win'].mean()  # home teams win ~57% — real baseline

    vegas_win_accuracy = None
    if games_df is not None and 'vegas_spread' in games_df.columns:
        # In nflreadpy, positive spread_line = home team favored → predict home win
        vegas_preds = (games_df['vegas_spread'].values >= 0).astype(int)
        vegas_win_accuracy = (vegas_preds == y['win']).mean()

    print(f"\n=== {split_name} Results ===")
    print(f"Win Accuracy:        {accuracy:.3f}  (always-home baseline: {always_home_accuracy:.3f})")
    print(f"Win Log Loss:        {logloss:.4f}")
    print(f"Win AUC:             {auc:.4f}")
    print(f"Spread MAE:          {spread_mae:.2f} points")
    print(f"Spread RMSE:         {spread_rmse:.2f} points")
    if vegas_win_accuracy is not None:
        gap = accuracy - vegas_win_accuracy
        print(f"Vegas Win Accuracy:  {vegas_win_accuracy:.3f}  (your gap: {gap:+.3f})")

    if split_name == 'Validation':
        passed = True
        if accuracy < 0.63:
            print(f"\nWARNING: Accuracy {accuracy:.3f} below 63% threshold.")
            print("Do NOT evaluate on test set yet. Improve the model first.")
            passed = False
        if spread_mae > 9.0:
            print(f"\nWARNING: Spread MAE {spread_mae:.2f} above 9.0 threshold.")
            print("Consider tuning loss_weights or adding features.")
            passed = False
        if passed:
            print("\nValidation thresholds passed. OK to evaluate on test set.")
        return passed

    return True


def evaluate_on_test(model, X_test_scaled, y_test, games_test=None):
    """
    Final test-set evaluation. Call only once, after validation thresholds pass.
    """
    print("\n" + "=" * 50)
    print("FINAL TEST SET EVALUATION")
    print("Run this only once. These are your final reported numbers.")
    print("=" * 50)
    evaluate_model(model, X_test_scaled, y_test, split_name='Test', games_df=games_test)
