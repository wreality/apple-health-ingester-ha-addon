# Apple Health Ingester for Home Assistant

A Home Assistant add-on that receives Apple Health data from the [Health Auto Export](https://apps.apple.com/app/health-auto-export-json-csv/id1115567461) iOS app and writes it to InfluxDB 2.x.

## Features

- **Write-only** — accepts health data ingestion, never exposes existing data
- **Generic metric handling** — supports all Apple Health metrics without special-casing
- **HA Ingress** — no exposed ports; all traffic authenticated by Home Assistant
- **Nabu Casa ready** — accessible remotely with a long-lived access token

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

## Health Auto Export App Setup

1. Create a **long-lived access token** in HA (Profile → Long-Lived Access Tokens)
2. Find your **ingress URL** by opening the add-on's Web UI and noting the URL
3. In the iOS app, configure a REST API automation:

| Setting | Value |
|---------|-------|
| URL | `https://<nabu-casa-url>/api/hassio_ingress/<token>/ingest` |
| Method | POST |
| Headers | `Authorization: Bearer <ha_long_lived_token>` |
| Body | JSON |

## Security

- **No exposed ports** — all traffic flows through HA ingress
- **HA authentication** — every request must carry a valid HA access token
- **Write-only** — no endpoints exist to query or read back health data
- **InfluxDB token** should be scoped to write-only access
- **End-to-end encryption** via Nabu Casa when accessed remotely
