#!/usr/bin/env bash
# Validate the Helm chart: lint, render with every optional part on, check each
# manifest against the Kubernetes schemas, and prove the values schema refuses
# what it must. Needs `helm` (v4) and `kubeconform` on PATH.
#
#   scripts/check-chart.sh
set -euo pipefail

cd "$(dirname "$0")/.."
chart=deploy/helm/kb-assistant
kubernetes_version=1.33.0
rendered=$(mktemp --suffix=.yaml) # kubeconform reads .yaml files only
trap 'rm -f "${rendered}"' EXIT

helm lint --strict "${chart}"

for flags in "" "--set monitoring.serviceMonitor.enabled=true --set monitoring.prometheusRule.enabled=true" \
  "--set networkPolicy.enabled=false --set ingress.enabled=false --set api.autoscaling.enabled=false"; do
  # shellcheck disable=SC2086
  helm template kb "${chart}" ${flags} >"${rendered}"
  # Prometheus Operator CRDs have no published schema here; everything else must validate.
  kubeconform -strict -summary -kubernetes-version "${kubernetes_version}" \
    -skip ServiceMonitor,PrometheusRule "${rendered}"
done

refused() {
  if helm template kb "${chart}" "$@" >/dev/null 2>&1; then
    echo "the values schema accepted: $*" >&2
    exit 1
  fi
}
refused --set config.KB_LLM__API_KEY=not-here
refused --set config.KB_DATABASE__URL=postgresql://x
refused --set images.backend.digest=sha256:short
refused --set api.replicas=two
refused --set surprise=1
echo "chart ok"
