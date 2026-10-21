#!/usr/bin/env bash
# Smoke test of the web app as a browser uses it (ADR-0008, ADR-0020): nonce CSP,
# cookie session, CSRF origin check, and an answer streamed through the BFF.
#
#   scripts/bff-smoke.sh [web-url]      default http://localhost:${KB_WEB_PORT:-3000}
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .env
web="${1:-http://localhost:${KB_WEB_PORT:-3000}}"
jar=$(mktemp)
trap 'rm -f "${jar}"' EXIT
json=(-H 'content-type: application/json' -H "Origin: ${web}")

echo "CSP with a per-response nonce on pages"
curl -sf -D - -o /dev/null "${web}/login" | grep -qi "content-security-policy: .*'nonce-"

echo "anonymous pages redirect to /login"
[ "$(curl -s -o /dev/null -w '%{redirect_url}' "${web}/admin")" = "${web}/login?next=%2Fadmin" ]

echo "sign in: tokens land in HttpOnly cookies, not in the body"
body=$(curl -sf -c "${jar}" "${web}/api/auth/login" "${json[@]}" \
  -d "{\"email\":\"carol@acme.example\",\"password\":\"${KB_SEED_PASSWORD}\"}")
! grep -q token <<<"${body}"
grep -q 'HttpOnly' "${jar}" || grep -q '#HttpOnly_' "${jar}"

echo "a state change from another origin is refused"
[ "$(curl -s -o /dev/null -w '%{http_code}' -b "${jar}" "${web}/api/v1/conversations" \
  -H 'content-type: application/json' -H 'Origin: https://evil.example' -d '{}')" = 403 ]

echo "an answer streams through the BFF"
conversation=$(curl -sf -b "${jar}" "${web}/api/v1/conversations" "${json[@]}" -d '{}' | jq -r .id)
curl -sfN -b "${jar}" "${web}/api/v1/conversations/${conversation}/messages" "${json[@]}" \
  -H 'Accept: text/event-stream' \
  -d '{"question": "How many days of paid leave do employees get?"}' >"${jar}.sse"
grep -q '^event: sentence' "${jar}.sse"
grep '^data: ' "${jar}.sse" | tail -1 | cut -c7- | jq -e '.abstained == false and (.citations | length) > 0' >/dev/null
rm -f "${jar}.sse"
echo "ok"
