#!/bin/sh
set -eu

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

require_digest_image() {
  variable=$1
  value=$2
  case "$value" in
    ?*@sha256:????????????????????????????????????????????????????????????????) ;;
    *) fail "${variable}_MUST_BE_DIGEST_PINNED" ;;
  esac
}

require_digest_image SUPPORTOPS_TEST_POSTGRES_IMAGE "${SUPPORTOPS_TEST_POSTGRES_IMAGE:-}"
require_digest_image SUPPORTOPS_TEST_REDIS_IMAGE "${SUPPORTOPS_TEST_REDIS_IMAGE:-}"
require_digest_image SUPPORTOPS_TEST_UV_IMAGE "${SUPPORTOPS_TEST_UV_IMAGE:-}"

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
compose_file="$root/tests/isolated.compose.yml"
nonce=$(od -A n -N 8 -t x1 /dev/urandom | tr -d ' \n')
project="supportops-integration-$nonce"
export SUPPORTOPS_TEST_SECRET=$(od -A n -N 32 -t x1 /dev/urandom | tr -d ' \n')
export SUPPORTOPS_TEST_RUNNER_IMAGE="supportops-test-runner:$project"

compose() {
  docker compose --project-name "$project" --file "$compose_file" "$@"
}

cleanup() {
  status=$?
  trap - 0
  compose down --volumes --remove-orphans --timeout 5 >/dev/null 2>&1 || true
  docker image rm "$SUPPORTOPS_TEST_RUNNER_IMAGE" >/dev/null 2>&1 || true
  exit "$status"
}

trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

compose run --build --rm integration
