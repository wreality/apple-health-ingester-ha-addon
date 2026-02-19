"""Health Data Ingester — receives Apple Health data and writes to InfluxDB."""

from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from .const import (
    CONF_INFLUXDB_BUCKET,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Fields that are not numeric values — skip when building InfluxDB points
SKIP_FIELDS = {"date", "source"}
# String-valued fields to store as tags instead of fields
STRING_FIELDS = {"inBedStart", "inBedEnd", "sleepStart", "sleepEnd"}


def parse_timestamp(date_str: str) -> datetime:
    """Parse Health Auto Export date strings like '2026-01-19 00:00:00 -0500'."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return datetime.fromisoformat(date_str)


def build_points(metrics: list[dict]) -> list[Point]:
    """Convert Health Auto Export metrics into InfluxDB points."""
    points = []
    for metric in metrics:
        name = metric.get("name", "unknown")
        units = metric.get("units", "")

        for dp in metric.get("data", []):
            date_str = dp.get("date")
            if not date_str:
                continue

            try:
                ts = parse_timestamp(date_str)
            except (ValueError, TypeError):
                _LOGGER.warning("Skipping data point with unparseable date: %s", date_str)
                continue

            point = Point(name).time(ts, WritePrecision.S)

            source = dp.get("source")
            if source:
                point = point.tag("source", source)
            if units:
                point = point.tag("units", units)

            field_count = 0
            for key, value in dp.items():
                if key in SKIP_FIELDS:
                    continue
                if key in STRING_FIELDS:
                    if isinstance(value, str):
                        point = point.tag(key, value)
                    continue
                if isinstance(value, (int, float)):
                    point = point.field(key.lower(), float(value))
                    field_count += 1

            if field_count > 0:
                points.append(point)

    return points


class HealthIngestView(HomeAssistantView):
    """Handle POST requests with Apple Health data."""

    url = "/api/healthrip/ingest"
    name = "api:healthrip:ingest"
    requires_auth = True

    def __init__(self, config_data: dict) -> None:
        """Initialize with InfluxDB config."""
        self._config = config_data

    async def post(self, request: web.Request) -> web.Response:
        """Handle incoming health data."""
        try:
            body = await request.json()
        except Exception:
            return self.json({"error": "Invalid JSON"}, status_code=400)

        data = body.get("data", {})
        metrics = data.get("metrics", [])

        if not metrics:
            return self.json({"status": "ok", "points_written": 0})

        points = build_points(metrics)
        if not points:
            return self.json({"status": "ok", "points_written": 0})

        try:
            hass = request.app["hass"]
            result = await hass.async_add_executor_job(
                self._write_points, points
            )
        except Exception as err:
            _LOGGER.error("InfluxDB write failed: %s", err)
            return self.json(
                {"error": f"InfluxDB write failed: {err}"}, status_code=502
            )

        _LOGGER.info("Wrote %d points across %d metrics", len(points), len(metrics))
        return self.json({"status": "ok", "points_written": len(points)})

    def _write_points(self, points: list[Point]) -> None:
        """Write points to InfluxDB (blocking, run in executor)."""
        client = InfluxDBClient(
            url=self._config[CONF_INFLUXDB_URL],
            token=self._config[CONF_INFLUXDB_TOKEN],
            org=self._config[CONF_INFLUXDB_ORG],
        )
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=self._config[CONF_INFLUXDB_BUCKET], record=points)
        client.close()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Health Data Ingester from a config entry."""
    hass.http.register_view(HealthIngestView(dict(entry.data)))
    _LOGGER.info(
        "Health Data Ingester ready at /api/healthrip/ingest"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
