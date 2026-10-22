#!/usr/bin/env bash
# Prepare a single-node k3s cluster for the chart: namespaces, random
# credentials, Secrets, the JWT signing key, and the dependencies.
# Idempotent: existing Secrets are never regenerated (that would orphan the
# database's passwords and invalidate every issued token).
#
#   deploy/vm/bootstrap.sh          (from the repository root, kubectl configured)
set -euo pipefail

cd "$(dirname "$0")/../.."
APP_NS=kb
DEPS_NS=kb-deps
DEPS_HOST=kb-deps.svc.cluster.local

secret_exists() { kubectl -n "$1" get secret "$2" >/dev/null 2>&1; }
random() { openssl rand -hex 24; }

kubectl create namespace "${DEPS_NS}" --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace "${APP_NS}" --dry-run=client -o yaml | kubectl apply -f -
# Every pod of the application must meet the restricted Pod Security Standard.
kubectl label namespace "${APP_NS}" pod-security.kubernetes.io/enforce=restricted --overwrite

if ! secret_exists "${DEPS_NS}" kb-deps; then
  owner=$(random) app=$(random) redis=$(random)
  s3_key="kb-$(openssl rand -hex 8)" s3_secret=$(random)
  seed="demo-$(openssl rand -hex 8)"
  kubectl -n "${DEPS_NS}" create secret generic kb-deps \
    --from-literal=POSTGRES_PASSWORD="${owner}" \
    --from-literal=KB_APP_DB_PASSWORD="${app}" \
    --from-literal=REDIS_PASSWORD="${redis}" \
    --from-literal=AWS_ACCESS_KEY_ID="${s3_key}" \
    --from-literal=AWS_SECRET_ACCESS_KEY="${s3_secret}"
  kubectl -n "${APP_NS}" create secret generic kb-assistant-backend \
    --from-literal=KB_DATABASE__URL="postgresql+asyncpg://kb_app:${app}@postgres.${DEPS_HOST}:5432/kb" \
    --from-literal=KB_DATABASE__MIGRATION_URL="postgresql+asyncpg://kb_owner:${owner}@postgres.${DEPS_HOST}:5432/kb" \
    --from-literal=KB_REDIS__URL="redis://:${redis}@redis.${DEPS_HOST}:6379/0" \
    --from-literal=KB_STORAGE__ACCESS_KEY="${s3_key}" \
    --from-literal=KB_STORAGE__SECRET_KEY="${s3_secret}"
  kubectl -n "${APP_NS}" create secret generic kb-demo-seed --from-literal=KB_SEED_PASSWORD="${seed}"
  echo "created credentials"
fi

if ! secret_exists "${APP_NS}" kb-assistant-signing-key; then
  key=$(mktemp)
  trap 'rm -f "${key}"' EXIT
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "${key}" 2>/dev/null
  kubectl -n "${APP_NS}" create secret generic kb-assistant-signing-key \
    --from-file=jwt_signing_key.pem="${key}"
  echo "created the JWT signing key"
fi

kubectl -n "${DEPS_NS}" create configmap postgres-init \
  --from-file=deploy/compose/postgres-init/01-application-role.sh \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/vm/dependencies.yaml
for name in postgres redis s3; do
  kubectl -n "${DEPS_NS}" rollout status "statefulset/${name}" --timeout=300s
done
echo "dependencies ready; next: helm upgrade --install (deploy/vm/README.md)"
