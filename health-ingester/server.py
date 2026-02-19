"""Health Data Ingester — receives Apple Health data and writes to InfluxDB."""

import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("health-ingester")

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "homeassistant")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "health")
API_KEY = os.environ.get("API_KEY", "")
INGRESS_PATH = os.environ.get("INGRESS_PATH", "")

# Fields that are not numeric values — skip when building InfluxDB points
SKIP_FIELDS = {"date", "source"}
# String-valued fields to store as tags instead of fields
STRING_FIELDS = {"inBedStart", "inBedEnd", "sleepStart", "sleepEnd"}

app = FastAPI(title="Health Data Ingester", root_path=INGRESS_PATH)


def verify_api_key(authorization: str | None = None, x_api_key: str | None = None):
    """Validate the request carries a valid API key."""
    if not API_KEY:
        return  # No key configured — allow all (dev/testing)
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif x_api_key:
        token = x_api_key
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def parse_timestamp(date_str: str) -> datetime:
    """Parse Health Auto Export date strings like '2026-01-19 00:00:00 -0500'."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        # Fallback: try ISO format
        return datetime.fromisoformat(date_str)


def build_points(metrics: list[dict]) -> list[Point]:
    """Convert Health Auto Export metrics into InfluxDB points."""
    points = []
    for metric in metrics:
        name = metric.get("name", "unknown")
        units = metric.get("units", "")
        data_points = metric.get("data", [])

        for dp in data_points:
            date_str = dp.get("date")
            if not date_str:
                continue

            try:
                ts = parse_timestamp(date_str)
            except (ValueError, TypeError):
                log.warning("Skipping data point with unparseable date: %s", date_str)
                continue

            point = Point(name).time(ts, WritePrecision.S)

            # Tags
            source = dp.get("source")
            if source:
                point = point.tag("source", source)
            if units:
                point = point.tag("units", units)

            # Fields — any numeric value in the data point
            field_count = 0
            for key, value in dp.items():
                if key in SKIP_FIELDS:
                    continue
                if key in STRING_FIELDS:
                    if isinstance(value, str):
                        point = point.tag(key, value)
                    continue
                if isinstance(value, (int, float)):
                    # Normalize field names to lowercase
                    point = point.field(key.lower(), float(value))
                    field_count += 1

            if field_count > 0:
                points.append(point)

    return points


@app.get("/api/health")
@app.get("/")
async def healthcheck():
    return {"status": "ok"}


@app.post("/api/ingest")
@app.post("/ingest")
async def ingest(
    request: Request,
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
):
    verify_api_key(authorization, x_api_key)

    body = await request.json()
    data = body.get("data", {})
    metrics = data.get("metrics", [])

    if not metrics:
        return {"status": "ok", "points_written": 0}

    points = build_points(metrics)

    if not points:
        return {"status": "ok", "points_written": 0}

    try:
        client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=INFLUXDB_BUCKET, record=points)
        client.close()
    except Exception as e:
        log.error("InfluxDB write failed: %s", e)
        raise HTTPException(status_code=502, detail=f"InfluxDB write failed: {e}")

    log.info("Wrote %d points across %d metrics", len(points), len(metrics))
    return {"status": "ok", "points_written": len(points)}
