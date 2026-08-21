#!/usr/bin/env bash
# Expose local Frappe to Render middleware + print ERP_BASE_URL / API credentials.
set -euo pipefail

BENCH_ROOT="${BENCH_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
SITE="${SITE:-site1.local}"
FRAPPE_PORT="${FRAPPE_PORT:-8000}"
API_USER_EMAIL="${API_USER_EMAIL:-support.ticket.api@example.com}"

cd "$BENCH_ROOT"

echo "==> Migrating $SITE (Support Ticket DocTypes + permissions)..."
bench --site "$SITE" migrate
bench --site "$SITE" clear-cache

echo "==> Ensuring API user + Support Ticket API role..."
bench --site "$SITE" execute mobile_app.scripts.ensure_support_ticket_api_user.ensure \
  --kwargs "{\"email\": \"$API_USER_EMAIL\"}"

PUBLIC_URL="${PUBLIC_ERP_URL:-}"
if [[ -z "$PUBLIC_URL" ]]; then
  echo ""
  echo "==> Start a tunnel (keep this terminal open)..."
  echo "    Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/downloads/"
  echo "    Then set Render ERP_BASE_URL to the https://*.trycloudflare.com URL printed below."
  echo ""
  if command -v cloudflared >/dev/null 2>&1; then
    cloudflared tunnel --url "http://127.0.0.1:${FRAPPE_PORT}"
  else
    echo "cloudflared not found. Alternative: ngrok http ${FRAPPE_PORT}"
    echo "After you have a public URL, run:"
    echo "  bench --site $SITE set-config host_name 'https://YOUR-TUNNEL-URL'"
    exit 1
  fi
else
  bench --site "$SITE" set-config host_name "$PUBLIC_URL"
  echo "Set host_name=$PUBLIC_URL on $SITE"
fi
