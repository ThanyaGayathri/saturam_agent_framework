"""Evaluation datasets for the consolidation harness."""

from harness.datasets.base import Dataset
from harness.datasets.synthetic import build_synthetic

__all__ = ["Dataset", "build_synthetic"]
