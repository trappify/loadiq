"""Home Assistant-backed data source for LoadIQ."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import pandas as pd

from .source import PowerDataSource
from ..config import EntityRef

if TYPE_CHECKING:  # pragma: no cover
    from homeassistant.core import HomeAssistant


class HomeAssistantHistorySource(PowerDataSource):
    """Fetch power series from the Home Assistant recorder."""

    def __init__(self, hass: "HomeAssistant"):
        from homeassistant.core import HomeAssistant

        if not isinstance(hass, HomeAssistant):
            raise TypeError("HomeAssistantHistorySource expects a HomeAssistant instance.")
        self._hass = hass

    def fetch_series(
        self,
        entity: EntityRef,
        start: datetime,
        end: datetime,
        aggregate: Optional[str] = None,
    ) -> pd.DataFrame:
        from homeassistant.components.recorder.history import state_changes_during_period

        history = state_changes_during_period(
            self._hass,
            start,
            end,
            entity_id=entity.entity_id,
            include_start_time_state=True,
        )
        states = history.get(entity.entity_id, [])
        if not states:
            return pd.DataFrame(columns=["value"]).set_index(pd.DatetimeIndex([], name="time"))

        rows: list[tuple[pd.Timestamp, float]] = []
        for state in states:
            try:
                value = float(state.state)
            except (TypeError, ValueError):
                continue
            ts = state.last_changed or state.last_updated
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            rows.append((pd.Timestamp(ts), value))

        if not rows:
            return pd.DataFrame(columns=["value"]).set_index(pd.DatetimeIndex([], name="time"))

        df = pd.DataFrame(rows, columns=["time", "value"])
        df = df.drop_duplicates(subset=["time"]).set_index("time").sort_index()
        if aggregate:
            df = df.resample(aggregate).mean().ffill()
        return df

    def __enter__(self) -> "HomeAssistantHistorySource":
        return self

    def __exit__(self, *_exc) -> None:
        return None
