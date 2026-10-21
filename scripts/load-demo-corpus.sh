#!/usr/bin/env bash
# Upload eval/corpus into the demo tenants created by `make seed`, through the
# public API, as each tenant's administrator. Idempotent: the API recognises a
# file it already has (same checksum) and returns the existing document.
#
#   scripts/load-demo-corpus.sh            # API on 127.0.0.1:${KB_API_PORT:-8000}
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .env
api="http://127.0.0.1:${KB_API_PORT:-8000}"
declare -A admins=([acme]="alice@acme.example" [globex]="erin@globex.example")

for tenant in acme globex; do
  token=$(curl -sf "${api}/v1/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"${admins[$tenant]}\",\"password\":\"${KB_SEED_PASSWORD}\"}" | jq -r .access_token)
  departments=$(curl -sf "${api}/v1/departments" -H "Authorization: Bearer ${token}")
  for file in eval/corpus/"${tenant}"/*/*.md; do
    slug=$(basename "$(dirname "${file}")")
    department=$(jq -r --arg slug "${slug}" '.items[] | select(.slug == $slug) | .id' <<<"${departments}")
    status=$(curl -s -o /dev/null -w '%{http_code}' "${api}/v1/documents" \
      -H "Authorization: Bearer ${token}" \
      -F "department_id=${department}" -F "file=@${file};type=text/markdown")
    echo "${tenant}/${slug}/$(basename "${file}"): ${status}"
  done
done
