# Health Data Ingester for Home Assistant

A Home Assistant custom integration that receives Apple Health data from the [Health Auto Export](https://apps.apple.com/app/health-auto-export-json-csv/id1115567461) iOS app and writes it to InfluxDB 2.x.

## Features

- **Native HA API endpoint** — accessible via Nabu Casa with a long-lived access token
- **No exposed ports** — runs on HA's own HTTP server (port 8123)
- **Write-only** — accepts health data ingestion, never exposes existing data
- **Generic metric handling** — supports all Apple Health metrics without special-casing

## Installation via HACS

1. In HACS, click the three-dot menu → **Custom repositories**
2. Add `https://github.com/wreality/ha-health-ingester` as an **Integration**
3. Find "Health Data Ingester" and install it
4. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "Health Data Ingester"
3. Enter your InfluxDB 2.x connection details:
   - **URL**: e.g., `http://a0d7b954-influxdb:8086`
   - **Token**: write-only API token for your health bucket
   - **Organization**: e.g., `homeassistant`
   - **Bucket**: e.g., `health`

## Health Auto Export App Setup

1. Create a **long-lived access token** in HA (Profile → Long-Lived Access Tokens)
2. In the iOS app, configure a REST API automation:

| Setting | Value |
|---------|-------|
| URL | `https://<nabu-casa-url>/api/health_ingester/ingest` |
| Method | POST |
| Headers | `Authorization: Bearer <ha_long_lived_token>` |
| Body | JSON |

For LAN access, use `http://<ha-ip>:8123/api/health_ingester/ingest`.

## Security

- **HA authentication required** — every request must carry a valid HA access token
- **Write-only** — no endpoints exist to query or read back health data
- **End-to-end encryption** via Nabu Casa when accessed remotely
- **InfluxDB token** should be scoped to write-only access
