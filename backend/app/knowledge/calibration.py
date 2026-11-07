"""Loader and resolver for Pydantic-validated embedding calibration file."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError

from app.embeddings.models import CalibrationFile, ModelCalibration
from app.knowledge.settings import get_settings as get_knowledge_settings

logger = logging.getLogger("app.knowledge.calibration")


def load_calibration_file(path: Path | None = None) -> CalibrationFile:
    """Loads knowledge/calibration/embedding_calibration.json, returning empty CalibrationFile if missing/invalid."""
    calib_path = path or get_knowledge_settings().calibration_path
    if not calib_path.is_file():
        return CalibrationFile()

    try:
        with open(calib_path, "r", encoding="utf-8") as f:
            raw = f.read()
        return CalibrationFile.model_validate_json(raw)
    except (ValidationError, ValueError, OSError) as exc:
        logger.warning("Failed loading embedding calibration from %s: %s", calib_path, exc)
        return CalibrationFile()


def get_model_calibration(
    model: str,
    dimensions: int | None = None,
    path: Path | None = None,
) -> ModelCalibration | None:
    """Retrieves calibrated thresholds for model and dimension pair, or None if uncalibrated."""
    calib_file = load_calibration_file(path)
    # Check specific model_dim key first
    exact_key = f"{model}_{dimensions}"
    if exact_key in calib_file.models:
        return calib_file.models[exact_key]

    # Check model_None fallback
    none_key = f"{model}_None"
    if none_key in calib_file.models:
        return calib_file.models[none_key]

    # Check bare model key
    if model in calib_file.models:
        return calib_file.models[model]

    return None


def is_model_calibrated(
    model: str,
    dimensions: int | None = None,
    path: Path | None = None,
) -> bool:
    """Returns True if the active model and dimensions have calibrated thresholds."""
    return get_model_calibration(model, dimensions, path) is not None
