"""The predictive step of Analytics Automation (K26): a trained, calibrated, explainable
risk score over rows of a fact -- an open quote and its account here -- with nothing a
model can be persuaded by. ``model`` is logistic regression in numpy with its own
evaluation; ``features`` turns rows into the frame it reads, leak-free; ``rfm`` is the
standard recency-frequency-monetary segmentation. The executor in
``executors/predict.py`` fetches the rows and calls these.
"""

from egpt_retail.predict.model import (
    Fitted,
    auc,
    brier,
    calibration,
    fit_logistic,
    predict_proba,
)
from egpt_retail.predict.rfm import RFM_SEGMENTS, rfm_segments

__all__ = [
    "Fitted",
    "RFM_SEGMENTS",
    "auc",
    "brier",
    "calibration",
    "fit_logistic",
    "predict_proba",
    "rfm_segments",
]
