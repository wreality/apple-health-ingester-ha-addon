# Health Data Ingester

Receives Apple Health data from the **Health Auto Export** iOS app and writes it to InfluxDB 2.x running as a Home Assistant add-on.

## Setup

### 1. InfluxDB Bucket

1. Open the InfluxDB add-on UI in Home Assistant
2. Go to **Buckets** → **Create Bucket**
3. Name it `health` (or your preferred name)
4. Set retention as desired (0 = forever)

### 2. InfluxDB API Token

1. In InfluxDB UI, go to **API Tokens** → **Generate API Token** → **Custom API Token**
2. Grant **Write** access to the `health` bucket only (no read access needed)
3. Copy the token

### 3. Add-on Configuration

In the add-on config:

| Option | Value |
|--------|-------|
| InfluxDB URL | `http://a0d7b954-influxdb:8086` (default, adjust if your InfluxDB add-on ID differs) |
| InfluxDB Token | The write-only token from step 2 |
| InfluxDB Organization | `homeassistant` (default InfluxDB org in HA) |
| InfluxDB Bucket | `health` |

### 4. HA Long-Lived Access Token

1. In HA, go to your **Profile** (click your name in the sidebar)
2. Scroll to **Long-Lived Access Tokens** → **Create Token**
3. Name it "Health Auto Export" and copy the token

### 5. Find Your Ingress URL

The add-on is accessible through HA ingress. To find the URL:

1. Open the add-on in HA (Settings → Add-ons → Health Data Ingester)
2. Click **Open Web UI** — note the URL in your browser
3. The ingress URL looks like: `https://<your-ha>/api/hassio_ingress/<token>/`

Your ingest endpoint is that URL with `/ingest` appended:
```
https://<your-ha>/api/hassio_ingress/<token>/ingest
```

This works through Nabu Casa too:
```
https://<nabu-casa-url>/api/hassio_ingress/<token>/ingest
```

### 6. Health Auto Export iOS App

In the app, configure a REST API automation:

| Setting | Value |
|---------|-------|
| URL | `https://<nabu-casa-url>/api/hassio_ingress/<token>/ingest` |
| Method | POST |
| Headers | `Authorization: Bearer <ha_long_lived_token>` |
| Body | JSON |
| Schedule | Every 6 hours (or your preference) |

## Authentication

This add-on uses **HA ingress** for authentication. No separate API key is needed — HA authenticates all requests before they reach the add-on. The HA long-lived access token in the `Authorization` header provides the auth.

No ports are exposed on the host. All traffic flows through HA's ingress proxy.

## API Endpoints

### POST /ingest

Accepts the Health Auto Export JSON payload and writes all metrics to InfluxDB.

**Response:**
```json
{"status": "ok", "points_written": 523}
```

### GET /

Simple healthcheck endpoint.

**Response:**
```json
{"status": "ok"}
```

## Security

This add-on is **write-only** by design:
- All requests authenticated by Home Assistant (ingress)
- No ports exposed on the host network
- No endpoints exist to query or read back health data
- The InfluxDB token should be scoped to write-only access
- Accessible remotely via Nabu Casa with end-to-end encryption

## InfluxDB Schema

Each metric type becomes an InfluxDB measurement:

- **Measurement**: metric name (e.g., `active_energy`, `heart_rate`, `sleep_analysis`)
- **Tags**: `source` (device), `units`, plus sleep time strings (`inBedStart`, `sleepStart`, etc.)
- **Fields**: all numeric values from the data point (e.g., `qty`, `avg`, `min`, `max`, `systolic`, `diastolic`, `core`, `deep`, etc.)
- **Timestamp**: parsed from the `date` field in the payload
