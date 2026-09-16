#!/bin/sh
# Local multi-instance deployment for visla-nanobot (no Docker).
#
# Wraps `nanobot gateway` lifecycle commands around a per-environment config
# directory, so dev and prod instances can run side by side on one host:
#
#   prod:  ~/.nanobot-prod   gateway :18790  WebUI :8765
#   dev:   ~/.nanobot-dev    gateway :18791  WebUI :8766
#
# Environment differences (Visla admin-api URLs, ports, paths) come solely
# from deploy/envs/<env>.env. config.json references "${VISLA_CURRENT_USER_URL}"
# which nanobot resolves from the process environment at every startup, so the
# Visla SSO exchange always validates against the selected environment.
#
# Usage:
#   deploy/run-local.sh <dev|prod> <init|start|stop|restart|status|logs|health>
#
# Examples:
#   deploy/run-local.sh dev init
#   deploy/run-local.sh dev start
#   deploy/run-local.sh prod start
#   deploy/run-local.sh dev logs
#
# Skills: drop skill folders into $HOME/.nanobot-<env>/workspace/skills/

set -eu

. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/lib.sh"

usage() {
  cat >&2 <<EOF
usage: $0 <dev|prod> <command>

commands:
  init     create the data dir + render config.json (never overwrites)
  start    start the gateway in the background (exports Visla env first)
  stop     stop the background gateway
  restart  restart the gateway (re-exports Visla env first)
  status   gateway lifecycle status
  logs     follow gateway logs
  health   curl the gateway /health endpoint
EOF
  exit 2
}

[ "$#" -ge 1 ] || usage
ENV_NAME="$1"
ACTION="${2:-start}"
shift $(($# > 1 ? 2 : 1))

load_env "$ENV_NAME"
require_visla_env
CONFIG="$NANOBOT_DATA_DIR/config.json"

command -v nanobot >/dev/null 2>&1 || die "nanobot CLI not found in PATH — install it first (uv tool install / pipx install .)"

# Export for every subcommand so `restart` re-spawns with the same environment
# even when invoked from a fresh shell.
export VISLA_CURRENT_USER_URL VISLA_ADMIN_API_BASE_URL

case "$ACTION" in
init)
  render_config "local"
  print_ports
  ;;
start)
  [ -f "$CONFIG" ] || die "no config at $CONFIG — run '$0 $ENV_NAME init' first"
  info "starting gateway (config: $CONFIG)"
  nanobot gateway --background --config "$CONFIG"
  print_ports
  ;;
stop)
  nanobot gateway stop --config "$CONFIG"
  ;;
restart)
  [ -f "$CONFIG" ] || die "no config at $CONFIG — run '$0 $ENV_NAME init' first"
  nanobot gateway restart --config "$CONFIG"
  print_ports
  ;;
status)
  nanobot gateway status --config "$CONFIG"
  ;;
logs)
  nanobot gateway logs --config "$CONFIG"
  ;;
health)
  info "GET http://127.0.0.1:$NANOBOT_GATEWAY_PORT/health"
  exec curl -fsS "http://127.0.0.1:$NANOBOT_GATEWAY_PORT/health"
  ;;
*)
  usage
  ;;
esac
