#!/bin/sh
set -eu

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

archive=/smoke/baseline.dump
expected=${SUPPORTOPS_SMOKE_BASELINE_SHA256:-}
case "$expected" in
  ''|*[!0-9a-f]*) fail SMOKE_BASELINE_DIGEST_INVALID ;;
esac
[ "${#expected}" -eq 64 ] || fail SMOKE_BASELINE_DIGEST_INVALID
[ -f "$archive" ] && [ ! -L "$archive" ] || fail SMOKE_BASELINE_FILE_INVALID

size=$(wc -c < "$archive" | tr -d ' ')
case "$size" in
  ''|*[!0-9]*) fail SMOKE_BASELINE_FILE_INVALID ;;
esac
[ "$size" -gt 0 ] && [ "$size" -le 536870912 ] || fail SMOKE_BASELINE_SIZE_INVALID

actual=$(sha256sum "$archive" | cut -d ' ' -f 1)
[ "$actual" = "$expected" ] || fail SMOKE_BASELINE_DIGEST_MISMATCH

if ! pg_restore --exit-on-error --no-owner --no-acl --dbname="$POSTGRES_DB" "$archive" >/dev/null 2>&1; then
  fail SMOKE_BASELINE_RESTORE_FAILED
fi

revision=$(psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c 'SELECT version_num FROM public.alembic_version LIMIT 2' 2>/dev/null) \
  || fail SMOKE_BASELINE_REVISION_UNAVAILABLE
[ "$revision" = 20261010_0017 ] || fail SMOKE_BASELINE_REVISION_MISMATCH

active_models=$(psql -XAtq -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c 'SELECT count(*) FROM public.model_endpoints WHERE is_enabled AND deleted_at IS NULL' 2>/dev/null) \
  || fail SMOKE_BASELINE_MODEL_STATE_UNAVAILABLE
[ "$active_models" = 0 ] || fail SMOKE_BASELINE_HAS_ACTIVE_MODEL_ENDPOINTS

printf '%s\n' SMOKE_BASELINE_RESTORED
