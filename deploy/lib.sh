#!/bin/sh
# Shared helpers for deploy/run-local.sh and deploy/run-docker.sh.
# Plain POSIX sh (matches entrypoint.sh style).

DEPLOY_DIR="${DEPLOY_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}"

# die <message>
die() {
    echo "error: $1" >&2
    exit 1
}

# info <message>
info() {
    echo "[deploy] $1"
}

# load_env <dev|prod> — source deploy/envs/<name>.env and export the Visla
# variables the gateway resolves at startup. Dies on unknown environment.
load_env() {
    [ "$#" -eq 1 ] || die "load_env: expected exactly one argument (dev|prod)"
    env_file="$DEPLOY_DIR/envs/$1.env"
    [ -f "$env_file" ] || die "unknown environment '$1' (expected $DEPLOY_DIR/envs/$1.env to exist)"
    # shellcheck disable=SC1090
    . "$env_file"
    export NANOBOT_ENV
    export VISLA_ADMIN_API_BASE_URL VISLA_CURRENT_USER_URL
    export NANOBOT_DATA_DIR NANOBOT_WORKSPACE
    export NANOBOT_GATEWAY_PORT NANOBOT_WEBSOCKET_PORT NANOBOT_API_PORT
    export NANOBOT_CONTAINER_PREFIX NANOBOT_COMPOSE_PROJECT
    info "environment: $NANOBOT_ENV  (admin-api: $VISLA_ADMIN_API_BASE_URL)"
}

# require_visla_env — fail fast when the Visla variables are empty. The
# gateway refuses to start on an unset ${VISLA_CURRENT_USER_URL} anyway, but
# failing here prints an actionable message before any process is spawned.
require_visla_env() {
    [ -n "${VISLA_CURRENT_USER_URL:-}" ] || die "VISLA_CURRENT_USER_URL is empty — check deploy/envs/$NANOBOT_ENV.env"
    [ -n "${VISLA_ADMIN_API_BASE_URL:-}" ] || die "VISLA_ADMIN_API_BASE_URL is empty — check deploy/envs/$NANOBOT_ENV.env"
}

# gen_secret — random 32-hex token for channels.websocket.tokenIssueSecret.
gen_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 16
    else
        od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
    fi
}

# render_config <local|docker> — create $NANOBOT_DATA_DIR/config.json from
# config.template.json when it does not exist yet. Never overwrites an
# existing config (runtime edits via WebUI settings must survive redeploys,
# same policy as entrypoint.sh's Render path).
#
# The two modes differ in what the config must contain:
#   local : listen ports = host ports ($NANOBOT_GATEWAY_PORT/$NANOBOT_WEBSOCKET_PORT),
#           workspace = host path ($NANOBOT_WORKSPACE), WS bound to 127.0.0.1
#   docker: listen ports = fixed container ports (18790/8765) because host
#           ports only appear on the left side of the compose port maps,
#           workspace = the in-container data-dir projection, WS on 0.0.0.0
#
# ${VISLA_CURRENT_USER_URL} inside the template is left literal on purpose:
# nanobot's config loader resolves it from the process environment at every
# startup, so the same config.json works for both local and docker modes.
render_config() {
    mode="$1"
    case "$mode" in
    local)
        ws_host="127.0.0.1"
        gateway_port="$NANOBOT_GATEWAY_PORT"
        websocket_port="$NANOBOT_WEBSOCKET_PORT"
        workspace_value="$NANOBOT_WORKSPACE"
        ;;
    docker)
        ws_host="0.0.0.0"
        gateway_port="18790"
        websocket_port="8765"
        workspace_value="/home/nanobot/.nanobot/workspace"
        ;;
    *)
        die "render_config: mode must be 'local' or 'docker'"
        ;;
    esac
    mkdir -p "$NANOBOT_DATA_DIR" "$NANOBOT_WORKSPACE/skills" || die "cannot create $NANOBOT_DATA_DIR"
    config="$NANOBOT_DATA_DIR/config.json"
    if [ -f "$config" ]; then
        info "existing config found — leaving it in place: $config"
        if grep -q 'VISLA_CURRENT_USER_URL' "$config"; then
            info "config already references \${VISLA_CURRENT_USER_URL}"
        else
            info "WARNING: config has no \${VISLA_CURRENT_USER_URL} reference; Visla SSO will validate against the built-in prod URL. See deploy/README.md."
        fi
        return 0
    fi
    sed \
        -e "s|__GATEWAY_PORT__|$gateway_port|g" \
        -e "s|__WEBSOCKET_PORT__|$websocket_port|g" \
        -e "s|__WS_HOST__|$ws_host|g" \
        -e "s|__WORKSPACE__|$workspace_value|g" \
        -e "s|__TOKEN_ISSUE_SECRET__|$(gen_secret)|g" \
        "$DEPLOY_DIR/config.template.json" >"$config" || die "failed to render $config"
    chmod 600 "$config"
    info "rendered config: $config"
    info "next: add your model provider API keys to $config (or via WebUI settings), then start the instance"
}

# print_ports — show the host-side endpoints for the selected environment.
print_ports() {
    info "gateway health : http://127.0.0.1:$NANOBOT_GATEWAY_PORT/health"
    info "WebUI / WS     : http://127.0.0.1:$NANOBOT_WEBSOCKET_PORT/"
    info "data directory : $NANOBOT_DATA_DIR"
    info "skills         : $NANOBOT_WORKSPACE/skills (drop skill dirs here)"
}
