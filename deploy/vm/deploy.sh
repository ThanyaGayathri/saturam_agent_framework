#!/usr/bin/env bash
# Deploy (or redeploy) a branch of this repository on the VM (deploy/vm/README.md).
#
#     bash ~/saturam-agent-framework/deploy/vm/deploy.sh [branch]      # default: demo/retail-prep
#
# Every run: fast-forward the checkout to origin/<branch> (discarding local edits -- nothing
# but this checkout lives here; env files are in ~/egpt), rebuild the image, bring the compose
# stack up (migrations run first, the api waits for them), build the frontend with the VITE_*
# values from ~/egpt/frontend.env, restart the frontend service, switch nginx to the egpt site
# if it is not already, and check /health end to end. Safe to re-run; a failed step stops it.
#
# Environment overrides: REPO, ENV_DIR, SKIP_FRONTEND=1 (api only), SKIP_API=1 (frontend only).
set -euo pipefail

BRANCH="${1:-demo/retail-prep}"
REPO="${REPO:-$HOME/saturam-agent-framework}"
ENV_DIR="${ENV_DIR:-$HOME/egpt}"
SITE=enterprisegpt.saturam.com
COMPOSE=(docker compose -f "$REPO/deploy/vm/compose.vm.yaml" --env-file "$ENV_DIR/egpt.env")
export EGPT_VM_ENV_FILE="$ENV_DIR/egpt.env"

for f in "$ENV_DIR/egpt.env" "$ENV_DIR/frontend.env"; do
    [ -f "$f" ] || { echo "missing $f -- run setup.sh and fill it in"; exit 1; }
done
grep -q '^AZURE_OPENAI_API_KEY=.\+' "$ENV_DIR/egpt.env" || {
    echo "AZURE_OPENAI_API_KEY is empty in $ENV_DIR/egpt.env"; exit 1; }

echo "== checkout $BRANCH"
cd "$REPO"
git fetch --prune origin "$BRANCH"
git checkout -q -B "$BRANCH" "origin/$BRANCH"
git reset -q --hard "origin/$BRANCH"
git log --oneline -1

if [ -z "${SKIP_API:-}" ]; then
    echo "== build image"
    "${COMPOSE[@]}" build --pull migrate
    echo "== stack up"
    "${COMPOSE[@]}" up -d --remove-orphans
    echo "== waiting for api"
    for _ in $(seq 1 60); do
        if curl -fsS http://127.0.0.1:7777/health >/dev/null 2>&1; then break; fi
        sleep 3
    done
    curl -fsS http://127.0.0.1:7777/health || {
        echo "api did not become healthy; last log lines:"; "${COMPOSE[@]}" logs --tail 80 api; exit 1; }
    echo
    "${COMPOSE[@]}" ps
fi

if [ -z "${SKIP_FRONTEND:-}" ]; then
    echo "== frontend build"
    cd "$REPO/frontend"
    npm ci --no-audit --no-fund --loglevel=error
    # VITE_* values are read from the env at build time and baked into dist/.
    set -a; . "$ENV_DIR/frontend.env"; set +a
    npm run build 2>&1 | tail -5
    echo "== frontend restart"
    sudo systemctl enable --now egpt-frontend >/dev/null
    sudo systemctl restart egpt-frontend
    sleep 2
    curl -fsS -o /dev/null -w "frontend http %{http_code}\n" http://127.0.0.1:8081/
fi

echo "== nginx"
if [ "$(readlink -f /etc/nginx/sites-enabled/$SITE)" != "/etc/nginx/sites-available/$SITE.egpt" ]; then
    sudo install -m 644 "$REPO/deploy/vm/nginx.site" "/etc/nginx/sites-available/$SITE.egpt"
    sudo ln -sfn "/etc/nginx/sites-available/$SITE.egpt" "/etc/nginx/sites-enabled/$SITE"
    echo "   switched $SITE to the egpt site (the prototype's config stays in sites-available)"
else
    sudo install -m 644 "$REPO/deploy/vm/nginx.site" "/etc/nginx/sites-available/$SITE.egpt"
fi
sudo nginx -t >/dev/null && sudo systemctl reload nginx
if systemctl is-active --quiet retailgpt; then
    sudo systemctl disable --now retailgpt >/dev/null
    echo "   stopped the prototype's retailgpt.service (port 8080); re-enable it to roll back"
fi

echo "== end to end"
curl -fsS -o /dev/null -w "https://$SITE/            -> %{http_code}\n" "https://$SITE/"
curl -fsS -w "\nhttps://$SITE/backend/health -> %{http_code}\n" "https://$SITE/backend/health"
echo "deployed $BRANCH @ $(git -C "$REPO" rev-parse --short HEAD)"
