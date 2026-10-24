#!/bin/sh
set -eu

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

[ "$#" -eq 1 ] || fail '用法: deploy/runtime/smoke.sh /绝对路径/smoke-compose.env'
env_file=$1
case "$env_file" in
  /*) ;;
  *) fail SMOKE_ENV_FILE_MUST_BE_ABSOLUTE ;;
esac
[ -f "$env_file" ] && [ ! -L "$env_file" ] || fail SMOKE_ENV_FILE_INVALID

private_file() {
  path=$1
  prefix=$2
  [ -f "$path" ] && [ ! -L "$path" ] || fail "${prefix}_INVALID"
  case "$(uname -s)" in
    Darwin) owner=$(stat -f '%u' "$path"); mode=$(stat -f '%Lp' "$path") ;;
    Linux) owner=$(stat -c '%u' "$path"); mode=$(stat -c '%a' "$path") ;;
    *) fail SMOKE_HOST_OS_UNSUPPORTED ;;
  esac
  [ "$owner" = "$(id -u)" ] || fail "${prefix}_OWNER_INVALID"
  case "$mode" in
    400|600) ;;
    *) fail "${prefix}_PERMISSIONS_INVALID" ;;
  esac
}

private_file "$env_file" SMOKE_ENV_FILE
runtime_env=$(awk -F= '$1 == "SUPPORTOPS_RUNTIME_ENV_FILE" { count++; value=substr($0, index($0, "=") + 1) } END { if (count == 1) print value; else exit 1 }' "$env_file") \
  || fail SMOKE_RUNTIME_ENV_FILE_INVALID
case "$runtime_env" in
  /*) ;;
  *) fail SMOKE_RUNTIME_ENV_FILE_MUST_BE_ABSOLUTE ;;
esac
private_file "$runtime_env" SMOKE_RUNTIME_ENV_FILE
baseline_dump=$(awk -F= '$1 == "SUPPORTOPS_SMOKE_BASELINE_DUMP" { count++; value=substr($0, index($0, "=") + 1) } END { if (count == 1) print value; else exit 1 }' "$env_file") \
  || fail SMOKE_BASELINE_PATH_INVALID
case "$baseline_dump" in
  /*) ;;
  *) fail SMOKE_BASELINE_PATH_MUST_BE_ABSOLUTE ;;
esac
[ -f "$baseline_dump" ] && [ ! -L "$baseline_dump" ] || fail SMOKE_BASELINE_FILE_INVALID

root=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
nonce=$(od -A n -N 8 -t x1 /dev/urandom | tr -d ' \n')
project="supportops-smoke-$nonce"
backend_image="supportops-backend:$project"
if docker image inspect "$backend_image" >/dev/null 2>&1; then
  fail SMOKE_BACKEND_IMAGE_TAG_COLLISION
fi
export SUPPORTOPS_BACKEND_IMAGE=$backend_image

compose() {
  docker compose --project-name "$project" --env-file "$env_file" \
    --file "$root/deploy/runtime/compose.yml" \
    --file "$root/deploy/runtime/smoke.compose.yml" "$@"
}

cleanup() {
  status=$?
  trap - 0
  compose down --remove-orphans --timeout 5 >/dev/null 2>&1 || true
  docker image rm "$backend_image" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

compose build migrate >/dev/null 2>&1 || fail SMOKE_BACKEND_BUILD_FAILED
compose up --detach postgres redis >/dev/null 2>&1 || fail SMOKE_DEPENDENCIES_START_FAILED
compose --profile isolated-smoke run --rm seed || fail SMOKE_BASELINE_IMPORT_FAILED
compose --profile isolated-smoke run --rm migrate || fail SMOKE_MIGRATION_FAILED
compose --profile isolated-smoke run --rm preflight || fail SMOKE_PREFLIGHT_FAILED

printf '%s\n' ISOLATED_PREFLIGHT_SMOKE_PASSED
