# Apple Health Ingester for Home Assistant

A Home Assistant add-on that receives Apple Health data from the [Health Auto Export](https://apps.apple.com/app/health-auto-export-json-csv/id1115567461) iOS app and writes it to InfluxDB 2.x.

## Features

- **Write-only** — accepts health data ingestion, never exposes existing data
- **Generic metric handling** — supports all Apple Health metrics without special-casing
- **HA Ingress support** — accessible remotely via Nabu Casa without exposing ports
- **Simple auth** — Bearer token authentication on the ingest endpoint

## Installation

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**
2. Click the three-dot menu → **Repositories**
3. Add this repository URL: `https://github.com/wreality/apple-health-ingester-ha-addon`
4. Find "Health Data Ingester" in the store and install it

## Prerequisites

- **InfluxDB 2.x** add-on installed and running in Home Assistant
- A bucket created in InfluxDB (default name: `health`)
- A write-only API token for that bucket

## Configuration

| Option | Description |
|--------|-------------|
| `influxdb_url` | InfluxDB URL (default: `http://a0d7b954-influxdb:8086`) |
| `influxdb_token` | Write-only API token for InfluxDB |
| `influxdb_org` | InfluxDB organization (default: `homeassistant`) |
| `influxdb_bucket` | Target bucket name (default: `health`) |
| `api_key` | Bearer token to authenticate incoming requests |

## Health Auto Export App Setup

In the iOS app, configure a REST API automation:

| Setting | Value |
|---------|-------|
| URL | `https://<nabu-casa-url>/api/hassio_ingress/<token>/api/ingest` |
| Method | POST |
| Headers | `Authorization: Bearer <your_api_key>` |
| Body | JSON |

## API Endpoints

### `POST /api/ingest`

Accepts the Health Auto Export JSON payload. Requires authentication.

```json
{"status": "ok", "points_written": 856}
```

### `GET /api/health`

Healthcheck endpoint. No authentication required.

## InfluxDB Schema

- **Measurement**: metric name (e.g., `active_energy`, `heart_rate`, `sleep_analysis`)
- **Tags**: `source` (device), `units`, sleep time strings
- **Fields**: all numeric values from each data point
- **Timestamp**: parsed from the `date` field

## Security

- No query/read endpoints exist
- InfluxDB token should be scoped to write-only
- All ingest requests require a valid API key
- No InfluxDB query API is proxied
