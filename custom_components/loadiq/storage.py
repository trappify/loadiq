"""Persistence and simple classification helpers for LoadIQ."""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, Iterable, List, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

from .const import DOMAIN

STORAGE_VERSION = 1
FEATURE_KEYS = ("mean_power_w", "peak_power_w", "energy_kwh", "duration_s")
LABEL_HEATPUMP = "heatpump"
LABEL_OTHER = "other"


def _extract_features(segment) -> Dict[str, float]:
    """Return the numeric features used for classification."""
    return {
        "mean_power_w": float(segment.mean_power_w),
        "peak_power_w": float(getattr(segment, "clamped_peak_w", segment.peak_power_w)),
        "energy_kwh": float(segment.energy_kwh),
        "duration_s": float(segment.duration.total_seconds()),
    }


def _centre(records: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    payload = list(records)
    if not payload:
        return {key: 0.0 for key in FEATURE_KEYS}
    return {
        key: mean(float(item["features"][key]) for item in payload if key in item["features"])
        for key in FEATURE_KEYS
    }


def _distance(features: Dict[str, float], centre: Dict[str, float]) -> float:
    """Scaled Manhattan distance between feature vector and centre."""
    total = 0.0
    for key in FEATURE_KEYS:
        target = centre.get(key, 0.0)
        denom = max(abs(target), 1.0)
        total += abs(features[key] - target) / denom
    return total / len(FEATURE_KEYS)


class LoadIQStorage:
    """Persist user feedback and provide basic classification utilities."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = storage.Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}.json")
        self._data: Dict[str, Any] = {"labels": []}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._data = data

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    def iter_labels(self) -> Iterable[Dict[str, Any]]:
        return self._data.get("labels", [])

    async def async_add_label(self, segment, label: str) -> None:
        """Persist a label for the provided segment."""
        label = LABEL_HEATPUMP if label == LABEL_HEATPUMP else LABEL_OTHER
        features = _extract_features(segment)
        record = {
            "start": segment.start.isoformat(),
            "end": segment.end.isoformat(),
            "label": label,
            "features": features,
        }
        labels: List[Dict[str, Any]] = self._data.setdefault("labels", [])
        for existing in labels:
            if existing["start"] == record["start"]:
                existing.update(record)
                break
        else:
            labels.append(record)
        await self.async_save()

    def classify_segment(self, segment) -> Tuple[str, float]:
        """Return (classification, confidence) for a detected segment."""
        labels = list(self.iter_labels())
        positives = [item for item in labels if item.get("label") == LABEL_HEATPUMP]
        negatives = [item for item in labels if item.get("label") == LABEL_OTHER]

        features = _extract_features(segment)

        if not positives:
            if negatives:
                neg_centre = _centre(negatives)
                dist_neg = _distance(features, neg_centre)
                score = max(0.0, 1.0 - dist_neg)
                classification = LABEL_OTHER if score >= 0.6 else "uncertain"
                return classification, round(score, 3)
            return "unknown", 0.0

        pos_centre = _centre(positives)
        dist_pos = _distance(features, pos_centre)

        if not negatives:
            score = max(0.0, 1.0 - dist_pos)
            classification = LABEL_HEATPUMP if score >= 0.6 else "uncertain"
            return classification, round(score, 3)

        neg_centre = _centre(negatives)
        dist_neg = _distance(features, neg_centre)
        total = dist_pos + dist_neg
        if total == 0.0:
            return LABEL_HEATPUMP, 1.0

        score = 1.0 - (dist_pos / total)
        if score >= 0.65:
            classification = LABEL_HEATPUMP
        elif score <= 0.35:
            classification = LABEL_OTHER
            score = 1.0 - score
        else:
            classification = "uncertain"
        return classification, round(score, 3)

    def has_positive_training(self) -> bool:
        return any(item.get("label") == LABEL_HEATPUMP for item in self.iter_labels())

    def has_negative_training(self) -> bool:
        return any(item.get("label") == LABEL_OTHER for item in self.iter_labels())

    def has_training_data(self) -> bool:
        return any(True for _ in self.iter_labels())
