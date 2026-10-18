#!/bin/sh
# 专用维护容器入口；默认只帮助，不能由API/Worker或启动流程隐式触发。
set -eu
umask 077

case "$#:$*" in
  '1:--help'|'1:-h')
    printf '%s\n' '用法：backup.sh --create。仅获授权维护者可导出指定专用数据库；不恢复、不清理旧备份。'
    exit 0 ;;
  '1:--create') ;;
  *) printf '%s\n' 'BACKUP_ARGUMENTS_INVALID' >&2; exit 2 ;;
esac

work=''
dump_pid=''
counter_pid=''
complete=false
failure=BACKUP_FAILED
finish() {
  code=$?
  trap - EXIT INT TERM
  if [ -n "$dump_pid" ]; then kill -TERM "$dump_pid" 2>/dev/null || :; fi
  if [ -n "$counter_pid" ]; then kill -TERM "$counter_pid" 2>/dev/null || :; fi
  if [ "$complete" != true ]; then
    if [ -n "$work" ]; then
      # 只移除本次目录的临时FIFO，部分归档和失败事实保留。
      rm -f -- "$work/.stderr" 2>/dev/null || :
      printf '%s\n' "$failure" > "$work/FAILED" 2>/dev/null || :
    fi
    printf '{"schema_version":"supportops_backup_report_v1","status":"incomplete","code":"%s"}\n' "$failure" >&2
    [ "$code" -ne 0 ] || code=1
  fi
  exit "$code"
}
trap finish EXIT
trap 'failure=BACKUP_INTERRUPTED; exit 130' INT TERM
fail() { failure=$1; exit 1; }

for tool in pg_dump pg_restore timeout stat sha256sum mktemp mkfifo wc grep sed date id sync cat mv rm; do
  command -v "$tool" >/dev/null 2>&1 || fail BACKUP_TOOL_UNAVAILABLE
done
[ "${SUPPORTOPS_BACKUP_STORAGE_APPROVED:-}" = true ] || fail BACKUP_STORAGE_NOT_APPROVED
[ "${SUPPORTOPS_BACKUP_DATABASE_SCOPE:-}" = dedicated_supportops_database ] || fail BACKUP_SCOPE_NOT_APPROVED
target=${SUPPORTOPS_BACKUP_TARGET_ALIAS:-}
schema=${SUPPORTOPS_BACKUP_CHECKPOINT_SCHEMA:-}
references=${SUPPORTOPS_BACKUP_RECOVERY_REFERENCES_SHA256:-}
seconds=${SUPPORTOPS_BACKUP_TIMEOUT_SECONDS:-900}
max_bytes=${SUPPORTOPS_BACKUP_MAX_BYTES:-1073741824}
case "$target" in ''|*[!a-zA-Z0-9_-]*) fail BACKUP_CONFIG_INVALID ;; esac
case "$schema" in ''|[!a-z_]*|*[!a-z0-9_]*) fail BACKUP_CONFIG_INVALID ;; esac
case "$references" in ''|*[!a-f0-9]*) fail BACKUP_CONFIG_INVALID ;; esac
case "$seconds" in ''|0*|*[!0-9]*) fail BACKUP_CONFIG_INVALID ;; esac
case "$max_bytes" in ''|0*|*[!0-9]*) fail BACKUP_CONFIG_INVALID ;; esac
[ "${#target}" -le 64 ] && [ "${#schema}" -le 63 ] && [ "${#references}" -eq 64 ] || fail BACKUP_CONFIG_INVALID
[ "${#seconds}" -le 5 ] && [ "${#max_bytes}" -le 12 ] || fail BACKUP_CONFIG_INVALID
[ "$seconds" -ge 1 ] && [ "$seconds" -le 86400 ] || fail BACKUP_CONFIG_INVALID
[ "$max_bytes" -ge 1048576 ] && [ "$max_bytes" -le 68719476736 ] || fail BACKUP_CONFIG_INVALID

# 不继承应用DSN/密码/连接覆盖；只用专用service和passfile，禁止交互索取密码。
unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGSSLMODE PGSSLROOTCERT PGSSLCERT PGSSLKEY PGOPTIONS
export PGSERVICE=supportops_backup PGSERVICEFILE=/run/backup/pg_service.conf PGPASSFILE=/run/backup/pgpass
export PGCONNECT_TIMEOUT=5 PGAPPNAME=supportops_backup
export PGOPTIONS='-c default_transaction_read_only=on -c lock_timeout=5000 -c statement_timeout=0'
uid=$(id -u)
[ "$uid" -gt 0 ] || fail BACKUP_IDENTITY_UNSAFE
for secret_file in "$PGSERVICEFILE" "$PGPASSFILE" /run/backup/recovery-references.json; do
  [ -f "$secret_file" ] && [ ! -L "$secret_file" ] || fail BACKUP_CREDENTIAL_FILE_UNSAFE
  [ "$(stat -c %u "$secret_file" 2>/dev/null)" = "$uid" ] || fail BACKUP_CREDENTIAL_FILE_UNSAFE
  case "$(stat -c %a "$secret_file" 2>/dev/null)" in 400|600) ;; *) fail BACKUP_CREDENTIAL_FILE_UNSAFE ;; esac
done
[ "$(stat -c %s /run/backup/recovery-references.json)" -le 131072 ] || fail BACKUP_REFERENCES_UNSAFE
reference_digest=$(sha256sum /run/backup/recovery-references.json)
[ "${reference_digest%% *}" = "$references" ] || fail BACKUP_REFERENCES_CHANGED
[ -d /backups ] && [ ! -L /backups ] || fail BACKUP_OUTPUT_UNSAFE
[ "$(stat -c %u /backups 2>/dev/null)" = "$uid" ] && [ "$(stat -c %a /backups 2>/dev/null)" = 700 ] || fail BACKUP_OUTPUT_UNSAFE

work=$(mktemp -d /backups/supportops-backup.XXXXXXXXXXXX) || fail BACKUP_OUTPUT_UNAVAILABLE
backup_id=${work##*/}
started=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
printf '%s\n' incomplete > "$work/INCOMPLETE"
# 按1024字节折算保守限制：512字节块的shell会更早失败，不冒称精确可用容量。
ulimit -f "$((max_bytes / 1024))" || fail BACKUP_FILE_LIMIT_UNAVAILABLE
# stderr只流经计数FIFO，不落原文；即使退出0，任何警告也不标为完整产物。
mkfifo "$work/.stderr" || fail BACKUP_OUTPUT_UNAVAILABLE
# Linux容器中父端先打开FIFO，导出在打开stderr之前失败也不会让计数者永远等写端。
exec 3<> "$work/.stderr"
wc -c < "$work/.stderr" 3>&- > "$work/.stderr-count" &
counter_pid=$!
timeout --signal=TERM --kill-after=10s "$seconds" pg_dump \
  --dbname="service=supportops_backup sslmode=verify-full sslrootcert=/run/backup/ca.crt connect_timeout=5 options='-c default_transaction_read_only=on -c lock_timeout=5000 -c statement_timeout=0'" \
  --no-password --format=custom --compress=6 --lock-wait-timeout=5s \
  --file="$work/database.dump.partial" 3>&- 2> "$work/.stderr" &
dump_pid=$!
dump_status=0
wait "$dump_pid" || dump_status=$?
dump_pid=''
exec 3>&-
wait "$counter_pid" || fail BACKUP_DIAGNOSTIC_UNAVAILABLE
counter_pid=''
[ "$dump_status" -eq 0 ] || fail BACKUP_DUMP_UNCONFIRMED
warnings=$(cat "$work/.stderr-count")
case "$warnings" in ''|*[!0-9\ ]*) fail BACKUP_DIAGNOSTIC_UNAVAILABLE ;; esac
[ "$warnings" -eq 0 ] || fail BACKUP_DUMP_WARNINGS
size=$(stat -c %s "$work/database.dump.partial" 2>/dev/null) || fail BACKUP_ARCHIVE_UNAVAILABLE
[ "$size" -gt 0 ] && [ "$size" -le "$max_bytes" ] || fail BACKUP_ARCHIVE_SIZE_INVALID

# 只列本次归档目录，不连接恢复目标、不执行归档SQL，不打印对象名称或拥有者。
timeout --signal=TERM --kill-after=10s 30 pg_restore --list "$work/database.dump.partial" \
  > "$work/.toc" 2>/dev/null || fail BACKUP_ARCHIVE_UNREADABLE
[ "$(stat -c %s "$work/.toc")" -le 2097152 ] || fail BACKUP_ARCHIVE_METADATA_LIMIT
source_major=$(sed -n 's/^; Dumped from database version: \([0-9][0-9]*\).*/\1/p' "$work/.toc")
client_major=$(sed -n 's/^; Dumped by pg_dump version: \([0-9][0-9]*\).*/\1/p' "$work/.toc")
case "$source_major" in ''|*[!0-9]*) fail BACKUP_VERSION_UNCONFIRMED ;; esac
[ "${#source_major}" -le 2 ] && [ "$source_major" -ge 10 ] && [ "$source_major" = "$client_major" ] || fail BACKUP_VERSION_MISMATCH
grep -F ' TABLE DATA public alembic_version ' "$work/.toc" >/dev/null || fail BACKUP_BUSINESS_SCHEMA_MISSING
for table in checkpoint_migrations checkpoints checkpoint_blobs checkpoint_writes; do
  grep -F " TABLE DATA $schema $table " "$work/.toc" >/dev/null || fail BACKUP_CHECKPOINT_SCHEMA_MISSING
done
digest=$(sha256sum "$work/database.dump.partial")
digest=${digest%% *}
case "$digest" in ''|*[!a-f0-9]*) fail BACKUP_DIGEST_UNAVAILABLE ;; esac
[ "${#digest}" -eq 64 ] || fail BACKUP_DIGEST_UNAVAILABLE
finished=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
reference_digest=$(sha256sum /run/backup/recovery-references.json)
[ "${reference_digest%% *}" = "$references" ] || fail BACKUP_REFERENCES_CHANGED
mv "$work/database.dump.partial" "$work/database.dump"
printf '{"schema_version":"supportops_backup_manifest_v1","backup_id":"%s","target_alias":"%s","started_at":"%s","finished_at":"%s","archive":"database.dump","archive_bytes":%s,"archive_sha256":"%s","scope":"single_database_all_schemas","postgres_major":%s,"checkpoint_schema":"%s","recovery_references_sha256":"%s","reference_contents_validated":false,"storage_approval":"operator_declared","restore_verified":false,"pitr_available":false,"external_files_included":false,"redis_included":false,"cluster_globals_included":false}\n' \
  "$backup_id" "$target" "$started" "$finished" "$size" "$digest" "$source_major" "$schema" "$references" > "$work/manifest.json.partial"
mv "$work/manifest.json.partial" "$work/manifest.json"
sync -f "$work/manifest.json" || fail BACKUP_FLUSH_UNCONFIRMED
rm -- "$work/.stderr" "$work/.stderr-count" "$work/.toc" "$work/INCOMPLETE"
printf '%s\n' "$digest" > "$work/COMPLETED"
sync -f "$work/COMPLETED" || fail BACKUP_FLUSH_UNCONFIRMED
complete=true
printf '{"schema_version":"supportops_backup_report_v1","status":"archive_created","backup_id":"%s","archive_bytes":%s,"archive_sha256":"%s","restore_verified":false}\n' "$backup_id" "$size" "$digest"
