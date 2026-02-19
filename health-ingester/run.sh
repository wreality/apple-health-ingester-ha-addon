#!/usr/bin/with-contenv bashio

export INFLUXDB_URL=$(bashio::config 'influxdb_url')
export INFLUXDB_TOKEN=$(bashio::config 'influxdb_token')
export INFLUXDB_ORG=$(bashio::config 'influxdb_org')
export INFLUXDB_BUCKET=$(bashio::config 'influxdb_bucket')
export INGRESS_PATH=$(bashio::addon.ingress_entry)

bashio::log.info "Ingress path: ${INGRESS_PATH}"
exec uvicorn server:app --host 0.0.0.0 --port 8099
