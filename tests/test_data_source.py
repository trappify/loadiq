from datetime import datetime, timezone

import pandas as pd
import pytest

from loadiq.config import EntityRef, InfluxConnection
from loadiq.data.source import InfluxDBSource


class DummyQueryAPI:
    def __init__(self, frames):
        self.frames = frames

    def query_data_frame(self, org, query):
        return self.frames


class DummyClient:
    def __init__(self, url, token, org, verify_ssl, timeout):
        self.kwargs = {
            "url": url,
            "token": token,
            "org": org,
            "verify_ssl": verify_ssl,
            "timeout": timeout,
        }
        self.query = pd.DataFrame(
            {
                "_time": ["2025-01-01T00:00:00Z", "2025-01-01T00:00:10Z"],
                "_value": [1000.0, 1100.0],
            }
        )

    def query_api(self):
        return DummyQueryAPI(self.query)

    def close(self):
        pass


def test_influx_source_fetch_series(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("influxdb_client.InfluxDBClient", DummyClient)

    conn = InfluxConnection(
        url="http://localhost:8086",
        token="secret",
        org="org",
        bucket="bucket",
    )
    entity = EntityRef(entity_id="sensor.house")

    with InfluxDBSource(conn) as source:
        df = source.fetch_series(entity, datetime.now(timezone.utc), datetime.now(timezone.utc))

    assert not df.empty
    assert df.index.tz is not None
    assert df.iloc[0]["value"] == 1000.0
