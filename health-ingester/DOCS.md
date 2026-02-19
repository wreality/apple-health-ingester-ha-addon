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
| API Key | A secret string of your choice — used to authenticate requests from the iOS app |

### 4. Remote Access via Nabu Casa Webhook

To receive data from the Health Auto Export app remotely (outside your LAN), set up an HA webhook that forwards to the add-on.

**Step 1:** Add a `rest_command` to your `configuration.yaml`:

```yaml
rest_command:
  forward_health_data:
    url: "http://localhost:8099/api/ingest"
    method: POST
    headers:
      Authorization: "Bearer YOUR_API_KEY"
      Content-Type: "application/json"
    payload: '{{ trigger_data }}'
    content_type: "application/json"
```

**Step 2:** Create an automation (Settings → Automations → Create):

```yaml
alias: Health Data Webhook
trigger:
  - platform: webhook
    webhook_id: health_data_ingest
    allowed_methods:
      - POST
    local_only: false
action:
  - service: rest_command.forward_health_data
    data:
      trigger_data: "{{ trigger.data | tojson }}"
mode: single
```

**Step 3:** Restart HA to load the `rest_command`.

Your webhook URL is now:
```
https://<your-nabu-casa-url>/api/webhook/health_data_ingest
```

### 5. Health Auto Export iOS App

In the app, configure a REST API automation:

**Via Nabu Casa webhook (remote):**

| Setting | Value |
|---------|-------|
| URL | `https://<nabu-casa-url>/api/webhook/health_data_ingest` |
| Method | POST |
| Headers | _(none needed — webhook ID acts as auth)_ |
| Body | JSON |
| Schedule | Every 6 hours (or your preference) |

**Via direct access (LAN or VPN):**

| Setting | Value |
|---------|-------|
| URL | `http://<ha-ip>:8099/api/ingest` |
| Method | POST |
| Headers | `Authorization: Bearer <your_api_key>` |
| Body | JSON |
| Schedule | Every 6 hours (or your preference) |

## API Endpoints

### POST /api/ingest

Accepts the Health Auto Export JSON payload and writes all metrics to InfluxDB.

**Headers:**
- `Authorization: Bearer <api_key>` or `X-API-Key: <api_key>`
- `Content-Type: application/json`

**Response:**
```json
{"status": "ok", "points_written": 523}
```

### GET /api/health

Simple healthcheck endpoint. No authentication required.

**Response:**
```json
{"status": "ok"}
```

## Testing

```bash
curl -X POST http://<ha-ip>:8099/api/ingest \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d '{"data":{"metrics":[{"name":"step_count","units":"count","data":[{"date":"2026-01-19 00:00:00 -0500","qty":5000,"source":"iPhone"}]}]}}'
```

## Security

This add-on is **write-only** by design:
- No endpoints exist to query or read back health data
- The InfluxDB token should be scoped to write-only access
- All ingest requests require a valid API key
- No InfluxDB query API is proxied

## InfluxDB Schema

Each metric type becomes an InfluxDB measurement:

- **Measurement**: metric name (e.g., `active_energy`, `heart_rate`, `sleep_analysis`)
- **Tags**: `source` (device), `units`, plus sleep time strings (`inBedStart`, `sleepStart`, etc.)
- **Fields**: all numeric values from the data point (e.g., `qty`, `avg`, `min`, `max`, `systolic`, `diastolic`, `core`, `deep`, etc.)
- **Timestamp**: parsed from the `date` field in the payload
