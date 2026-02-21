"""Health Data Ingester — receives Apple Health data and writes to InfluxDB.

Uses the InfluxDB v2 HTTP write API directly (line protocol) to avoid
dependency on influxdb-client which has import issues on Python 3.13.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_INFLUXDB_BUCKET,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Fields that are not numeric values — skip when building InfluxDB points
SKIP_FIELDS = {"date", "source", "startDate"}
# String-valued fields to store as tags instead of fields
STRING_FIELDS = {"inBedStart", "inBedEnd", "sleepStart", "sleepEnd", "value", "endDate", "start", "end", "context"}


def parse_timestamp(date_str: str) -> datetime:
    """Parse Health Auto Export date strings like '2026-01-19 00:00:00 -0500'."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return datetime.fromisoformat(date_str)


def _escape_tag(value: str) -> str:
    """Escape tag key/value for line protocol."""
    return value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _escape_field_str(value: str) -> str:
    """Escape a string field value for line protocol."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_line_protocol(metrics: list[dict]) -> list[str]:
    """Convert Health Auto Export metrics into InfluxDB line protocol strings."""
    lines = []
    for metric in metrics:
        name = metric.get("name", "unknown")
        units = metric.get("units", "")

        for dp in metric.get("data", []):
            date_str = dp.get("date") or dp.get("startDate")
            if not date_str:
                continue

            try:
                ts = parse_timestamp(date_str)
            except (ValueError, TypeError):
                _LOGGER.warning("Skipping data point with unparseable date: %s", date_str)
                continue

            # Build tags
            tags = []
            source = dp.get("source")
            if source:
                tags.append(f"source={_escape_tag(source)}")
            if units:
                tags.append(f"units={_escape_tag(units)}")
            for key in STRING_FIELDS:
                val = dp.get(key)
                if isinstance(val, str) and val:
                    tags.append(f"{_escape_tag(key)}={_escape_tag(val)}")

            # Build fields
            fields = []
            for key, value in dp.items():
                if key in SKIP_FIELDS or key in STRING_FIELDS:
                    continue
                if isinstance(value, (int, float)):
                    fields.append(f"{key.lower()}={float(value)}")

            if not fields:
                continue

            # measurement[,tag=val...] field=val[,field=val...] timestamp
            tag_str = "," + ",".join(tags) if tags else ""
            field_str = ",".join(fields)
            ts_seconds = int(ts.timestamp())
            lines.append(f"{_escape_tag(name)}{tag_str} {field_str} {ts_seconds}")

    return lines


class HealthIngestView(HomeAssistantView):
    """Handle POST requests with Apple Health data."""

    url = "/api/healthrip/ingest"
    name = "api:healthrip:ingest"
    requires_auth = True

    def __init__(self, hass: HomeAssistant, config_data: dict) -> None:
        """Initialize with InfluxDB config."""
        self._config = config_data
        self._hass = hass

    async def _write_to_influx(self, body: str) -> None:
        """Write line protocol data to InfluxDB via HTTP API."""
        url = self._config[CONF_INFLUXDB_URL].rstrip("/")
        bucket = quote(self._config[CONF_INFLUXDB_BUCKET])
        org = quote(self._config[CONF_INFLUXDB_ORG])
        token = self._config[CONF_INFLUXDB_TOKEN]

        session = async_get_clientsession(self._hass)
        resp = await session.post(
            f"{url}/api/v2/write?org={org}&bucket={bucket}&precision=s",
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
            data=body,
        )
        if resp.status not in (200, 204):
            text = await resp.text()
            raise RuntimeError(f"InfluxDB write failed ({resp.status}): {text}")

    async def post(self, request: web.Request) -> web.Response:
        """Handle incoming health data."""
        request_start = time.monotonic()

        try:
            body = await request.json()
        except Exception:
            await self._write_telemetry(0, 0, 0, error="invalid_json")
            return self.json({"error": "Invalid JSON"}, status_code=400)

        data = body.get("data", {})
        metrics = data.get("metrics", [])

        if not metrics:
            elapsed = time.monotonic() - request_start
            await self._write_telemetry(0, 0, elapsed)
            return self.json({"status": "ok", "points_written": 0})

        lines = build_line_protocol(metrics)
        if not lines:
            elapsed = time.monotonic() - request_start
            await self._write_telemetry(len(metrics), 0, elapsed)
            return self.json({"status": "ok", "points_written": 0})

        try:
            write_start = time.monotonic()
            await self._write_to_influx("\n".join(lines))
            write_dur = time.monotonic() - write_start
        except Exception as err:
            _LOGGER.error("InfluxDB write failed: %s", err)
            elapsed = time.monotonic() - request_start
            await self._write_telemetry(len(metrics), len(lines), elapsed, error=type(err).__name__)
            return self.json(
                {"error": f"InfluxDB write failed: {err}"}, status_code=502
            )

        elapsed = time.monotonic() - request_start
        _LOGGER.info("Wrote %d points across %d metrics (%.1fs)", len(lines), len(metrics), elapsed)
        await self._write_telemetry(len(metrics), len(lines), elapsed, write_dur)
        return self.json({"status": "ok", "points_written": len(lines)})

    async def _write_telemetry(
        self,
        metric_count: int,
        point_count: int,
        total_dur: float,
        write_dur: float = 0.0,
        error: str = "",
    ) -> None:
        """Write ingest telemetry to InfluxDB. Failures are logged but not raised."""
        try:
            now_s = int(datetime.now(timezone.utc).timestamp())
            tags = ",error=" + _escape_tag(error) if error else ""
            success = 0.0 if error else 1.0
            line = (
                f"ingest_request{tags} "
                f"points={float(point_count)},"
                f"metrics={float(metric_count)},"
                f"total_duration_s={round(total_dur, 3)},"
                f"write_duration_s={round(write_dur, 3)},"
                f"success={success} "
                f"{now_s}"
            )
            await self._write_to_influx(line)
        except Exception as err:
            _LOGGER.warning("Failed to write telemetry: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Health Data Ingester from a config entry."""
    hass.http.register_view(HealthIngestView(hass, dict(entry.data)))
    _LOGGER.info(
        "Health Data Ingester ready at /api/healthrip/ingest"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True
