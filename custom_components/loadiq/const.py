"""Constants for the LoadIQ Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "loadiq"
DOMAIN_TITLE = "LoadIQ"

PLATFORMS: list[str] = ["sensor", "binary_sensor"]

DATA_COORDINATOR = "coordinator"
DATA_CONFIG = "config"

UPDATE_INTERVAL = timedelta(minutes=1)
LOOKBACK_WINDOW = timedelta(hours=3)

CONF_BACKEND = "backend"
CONF_HOMEASSISTANT = "homeassistant"
CONF_INFLUX = "influx"
CONF_ENTITIES = "entities"

CONF_HOUSE_SENSOR = "house_sensor"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_KNOWN_LOADS = "known_loads"
CONF_AGGREGATE_WINDOW = "aggregate_window"

CONF_INFLUX_URL = "url"
CONF_INFLUX_TOKEN = "token"
CONF_INFLUX_ORG = "org"
CONF_INFLUX_BUCKET = "bucket"
CONF_INFLUX_VERIFY_SSL = "verify_ssl"
CONF_INFLUX_TIMEOUT = "timeout"

BACKEND_HOME_ASSISTANT = "homeassistant"
BACKEND_INFLUXDB = "influxdb"

DEFAULT_AGGREGATE_WINDOW = "10s"
