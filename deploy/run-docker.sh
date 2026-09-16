#!/bin/sh
# Docker Compose deployment for visla-nanobot with per-environment isolation.
#
#   prod:  containers nanobot-prod-*   ~/.nanobot-prod   WebUI :8765
#   dev:   containers nanobot-dev-*    ~/.nanobot-dev    WebUI :8766
#
# Environment differences (Visla admin-api URLs, ports, container names) come
# solely from deploy/envs/<env>.env; deploy/docker-compose.yml is fully
# parameterized so both environments share it. The Visla variables are injected
# into the containers via `environment:`, where nanobot resolves the
# "${VISLA_CURRENT_USER_URL}" config reference at startup.
#
# Usage:
#   deploy/run-docker.sh <dev|prod> <init|up|down|restart|status|logs|health|build|shell>
#
# Examples:
#   deploy/run-docker.sh dev init
#   deploy/run-docker.sh dev up          # build (if needed) + start
#   deploy/run-docker.sh dev logs
#   deploy/run-docker.sh dev shell       # one-off CLI: agent/status/...

set -eu

. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/lib.sh"

usage() {
  cat >&2 <<EOF
usage: $0 <dev|prod> <command> [extra args]

commands:
  init     create the data dir + render config.json on the host (mount source)
  up       docker compose up -d --build
  down     docker compose down
  restart  docker compose restart
  status   docker compose ps
  logs     docker compose logs -f
  health   curl the gateway /health endpoint on the host
  build    docker compose build (pass NANOBOT_CHANNELS=... to preinstall)
  shell    run a one-off command in nanobot-cli, e.g.:
             $0 dev shell agent -m "hello"
             $0 dev shell status
EOF
  exit 2
}

[ "$#" -ge 1 ] || usage
ENV_NAME="$1"
ACTION="${2:-up}"
shift $(($# > 1 ? 2 : 1))

load_env "$ENV_NAME"
require_visla_env

# Exported so docker compose interpolates ${...} in deploy/docker-compose.yml
# from this shell (takes precedence over any .env file).
export VISLA_CURRENT_USER_URL VISLA_ADMIN_API_BASE_URL
export NANOBOT_DATA_DIR NANOBOT_GATEWAY_PORT NANOBOT_WEBSOCKET_PORT NANOBOT_API_PORT
export NANOBOT_CONTAINER_PREFIX

# compose — run docker compose with the per-environment project name and
# parameterized file. The docker availability check lives here (not at script
# top) so docker-free subcommands like `init` and `health` still work.
compose() {
  command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
  docker compose version >/dev/null 2>&1 || die "docker compose plugin not available"
  docker compose --project-name "$NANOBOT_COMPOSE_PROJECT" \
    --file "$DEPLOY_DIR/docker-compose.yml" "$@"
}

case "$ACTION" in
init)
  render_config "docker"
  print_ports
  info "config lives on the host and is bind-mounted into the containers"
  ;;
up)
  [ -f "$NANOBOT_DATA_DIR/config.json" ] || die "no config at $NANOBOT_DATA_DIR/config.json — run '$0 $ENV_NAME init' first"
  compose up -d --build
  print_ports
  ;;
down)
  compose down
  ;;
restart)
  compose restart
  print_ports
  ;;
status)
  compose ps
  ;;
logs)
  compose logs -f
  ;;
health)
  info "GET http://127.0.0.1:$NANOBOT_GATEWAY_PORT/health"
  exec curl -fsS "http://127.0.0.1:$NANOBOT_GATEWAY_PORT/health"
  ;;
build)
  compose build
  ;;
shell)
  [ "$#" -ge 1 ] || set -- status
  compose run --rm nanobot-cli "$@"
  ;;
*)
  usage
  ;;
esac
