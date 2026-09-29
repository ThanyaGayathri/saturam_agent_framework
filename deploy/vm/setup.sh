#!/usr/bin/env bash
# One-time preparation of the VM (deploy/vm/README.md). Idempotent: safe to re-run.
#
#     bash ~/saturam-agent-framework/deploy/vm/setup.sh
#
# Installs Docker (Ubuntu's docker.io + compose v2), nginx and Node 20 (NodeSource) where
# missing, puts the login user in the docker group, creates ~/egpt with the env files from
# their examples (only if absent -- never overwrites a filled-in one), installs the
# egpt-frontend systemd unit and the nginx site. ~/egpt/site.env (site.env.example) selects
# the hostname and HTTP-only mode; without it the original demo VM's TLS site is assumed.
# It does NOT switch nginx over or start anything: deploy.sh does that, once the env files
# are filled in.
set -euo pipefail

REPO="${REPO:-$HOME/saturam-agent-framework}"
ENV_DIR="${ENV_DIR:-$HOME/egpt}"
HERE="$REPO/deploy/vm"
[ -f "$ENV_DIR/site.env" ] && . "$ENV_DIR/site.env"
SITE="${SITE:-enterprisegpt.saturam.com}"
NGINX_TEMPLATE=nginx.site; [ -n "${HTTP_ONLY:-}" ] && NGINX_TEMPLATE=nginx.http.site

echo "== docker"
if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2
fi
sudo systemctl enable --now docker >/dev/null
if ! id -nG "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    echo "   added $USER to the docker group -- open a new ssh session before deploy.sh"
fi
docker --version
sudo docker compose version

echo "== nginx"
if ! command -v nginx >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx
fi
sudo systemctl enable --now nginx >/dev/null
nginx -v

echo "== node"
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
fi
node --version; npm --version

echo "== env files in $ENV_DIR"
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"
for name in egpt frontend; do
    if [ ! -f "$ENV_DIR/$name.env" ]; then
        cp "$HERE/$name.env.example" "$ENV_DIR/$name.env"
        chmod 600 "$ENV_DIR/$name.env"
        echo "   created $ENV_DIR/$name.env from the example -- fill it in"
    else
        echo "   $ENV_DIR/$name.env present, left as is"
    fi
done

echo "== systemd unit egpt-frontend"
sudo install -m 644 "$HERE/egpt-frontend.service" /etc/systemd/system/egpt-frontend.service
sudo systemctl daemon-reload

echo "== nginx site $SITE from $NGINX_TEMPLATE (available, not yet enabled)"
# Rendered to a temp file first: `sudo install` cannot read /dev/stdin from a pipe.
RENDERED="$(mktemp)"
sed "s/__SITE__/$SITE/g" "$HERE/$NGINX_TEMPLATE" > "$RENDERED"
sudo install -m 644 "$RENDERED" "/etc/nginx/sites-available/$SITE.egpt"
rm -f "$RENDERED"
sudo nginx -t -c /etc/nginx/nginx.conf >/dev/null && echo "   current nginx config still valid"

echo
echo "Next: fill in $ENV_DIR/egpt.env and $ENV_DIR/frontend.env, then run deploy.sh."
