#!/usr/bin/env bash
# Smoke test for a running monitoring stack (used by the Monitoring Stack Validation workflow).
# Checks that the exporter polls GitHub, Prometheus scrapes it and evaluates the recording rules,
# and that Grafana has provisioned its data source, dashboards and alert rules.
set -euo pipefail

GRAFANA="${GRAFANA_URL:-http://localhost:3000}"
PROM="${PROMETHEUS_URL:-http://localhost:9090}"
EXPORTER="${EXPORTER_URL:-http://localhost:9200}"
AUTH="admin:${GF_SECURITY_ADMIN_PASSWORD:-admin}"

retry() {
  local attempt=0
  until "$@"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 36 ]; then
      echo "Gave up after $attempt attempts: $*" >&2
      return 1
    fi
    sleep 5
  done
}

check() { echo "== $1"; }

check "exporter has polled GitHub"
retry bash -c "curl -sf '$EXPORTER/healthz' | jq -e '.poller.last_success > 0' > /dev/null"
curl -sf "$EXPORTER/healthz" | jq '{repository, tracked_runs, rate_limit}'
echo "cicd_* samples exposed: $(curl -sf "$EXPORTER/metrics" | grep -c '^cicd_')"

check "Prometheus scrapes the exporter"
retry bash -c "curl -sf '$PROM/api/v1/targets' | jq -e '.data.activeTargets[] | select(.labels.job == \"cicd-exporter\") | .health == \"up\"' > /dev/null"

check "recording rules are loaded"
curl -sf "$PROM/api/v1/rules?type=record" | jq -e '[.data.groups[].rules[]] | length >= 11' > /dev/null

check "Grafana data source is healthy"
retry bash -c "curl -sf -u '$AUTH' '$GRAFANA/api/datasources/uid/prometheus/health' | jq -e '.status == \"OK\"' > /dev/null"

check "Grafana dashboards are provisioned"
curl -sf -u "$AUTH" "$GRAFANA/api/search?type=dash-db" | jq -e 'length >= 5' > /dev/null
curl -sf -u "$AUTH" "$GRAFANA/api/search?type=dash-db" | jq -r '.[].title'

check "Grafana alert rules are provisioned"
curl -sf -u "$AUTH" "$GRAFANA/api/v1/provisioning/alert-rules" | jq -e 'length >= 12' > /dev/null

check "Grafana can query CI/CD metrics"
retry bash -c "curl -sf -u '$AUTH' -H 'Content-Type: application/json' -X POST '$GRAFANA/api/ds/query' \
  -d '{\"queries\":[{\"refId\":\"A\",\"datasource\":{\"uid\":\"prometheus\"},\"expr\":\"cicd_exporter_info\",\"instant\":true}],\"from\":\"now-5m\",\"to\":\"now\"}' \
  | jq -e '.results.A.frames | length > 0' > /dev/null"

echo "All monitoring stack checks passed"
