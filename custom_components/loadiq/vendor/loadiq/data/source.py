"""Data source abstractions for LoadIQ."""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Mapping, Optional

import pandas as pd

from ..config import EntityRef, InfluxConnection


class PowerDataSource(abc.ABC):
    """Abstract base class for retrieving power and contextual time series."""

    @abc.abstractmethod
    def fetch_series(
        self,
        entity: EntityRef,
        start: datetime,
        end: datetime,
        aggregate: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return a dataframe indexed by timestamp with a `value` column."""


class InfluxDBSource(PowerDataSource):
    """InfluxDB-backed implementation."""

    def __init__(self, connection: InfluxConnection):
        from influxdb_client import InfluxDBClient

        self._cfg = connection
        self._client = InfluxDBClient(
            url=connection.url,
            token=connection.token.get_secret_value(),
            org=connection.org,
            verify_ssl=connection.verify_ssl,
            timeout=connection.timeout_s * 1000,
        )

    def _format_flux(self, entity: EntityRef, start: datetime, end: datetime, aggregate: Optional[str]) -> str:
        window = aggregate or entity.aggregate_every
        start_iso = start.astimezone(timezone.utc).isoformat()
        end_iso = end.astimezone(timezone.utc).isoformat()
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_iso}, stop: {end_iso})
  |> filter(fn: (r) => r["_measurement"] == "{entity.measurement}")
  |> filter(fn: (r) => r["_field"] == "{entity.field}")
  |> filter(fn: (r) => r["domain"] == "{entity.domain}")
  |> filter(fn: (r) => r["entity_id"] == "{entity.entity_id}")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> yield(name: "mean")
"""
        return flux

    def fetch_series(
        self,
        entity: EntityRef,
        start: datetime,
        end: datetime,
        aggregate: Optional[str] = None,
    ) -> pd.DataFrame:
        query = self._format_flux(entity, start, end, aggregate)
        query_api = self._client.query_api()
        tables = query_api.query_data_frame(org=self._cfg.org, query=query)
        if isinstance(tables, list):
            df = pd.concat(tables, ignore_index=True)
        else:
            df = tables
        if df.empty:
            return pd.DataFrame(columns=["value"]).set_index(pd.DatetimeIndex([], name="time"))

        df = df.rename(columns={"_value": "value", "_time": "time"})
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df[["time", "value"]].dropna()
        df = df.set_index("time").sort_index()
        df.index = df.index.tz_convert("UTC")
        return df

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "InfluxDBSource":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
