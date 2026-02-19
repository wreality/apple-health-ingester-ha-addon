# Apple Health Ingester for Home Assistant

A Home Assistant add-on that receives Apple Health data from the [Health Auto Export](https://apps.apple.com/app/health-auto-export-json-csv/id1115567461) iOS app and writes it to InfluxDB 2.x.

## Features

- **Write-only** — accepts health data ingestion, never exposes existing data
- **Generic metric handling** — supports all Apple Health metrics without special-casing
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
| URL | `http://<ha-ip>:8099/api/ingest` |
| Method | POST |
| Headers | `Authorization: Bearer <your_api_key>` |
| Body | JSON |

The HA IP can be a LAN address or VPN address (Netbird, Tailscale, WireGuard).

## Security

- No query/read endpoints exist
- InfluxDB token should be scoped to write-only
- All ingest requests require a valid API key
- No InfluxDB query API is proxied
