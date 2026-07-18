#!/usr/bin/env bash
# 安全备份 SQLite(WAL 模式)→ 带时间戳的快照 + 轮转保留。
# 采数期务必进 cron:每日一次 + 建议每场被试后一次;并把 backup/ 异地同步一份。
# 用法:  scripts/backup_db.sh [DB路径] [备份目录]
#   env KEEP=30  保留最近 N 份(默认 30)
# cron 例(每日 03:00): 0 3 * * * cd /path/to/NovaStory && scripts/backup_db.sh >> backup/backup.log 2>&1
set -euo pipefail

DB="${1:-data/novastory.db}"
DEST="${2:-backup}"
KEEP="${KEEP:-30}"

if [ ! -f "$DB" ]; then
  echo "backup skip: $DB 不存在" >&2
  exit 0
fi

mkdir -p "$DEST"
ts="$(date +%Y%m%d-%H%M%S)"
out="$DEST/novastory-$ts.db"

# .backup 对 WAL 安全(在线一致快照),优于直接 cp
sqlite3 "$DB" ".backup '$out'"

# 完整性自检
if ! sqlite3 "$out" "PRAGMA integrity_check;" | grep -q '^ok$'; then
  echo "backup FAIL: $out 完整性检查未通过" >&2
  exit 1
fi

# 轮转:超出 KEEP 份的旧快照删除
ls -1t "$DEST"/novastory-*.db 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

echo "backup ok: $out (保留 $(ls -1 "$DEST"/novastory-*.db 2>/dev/null | wc -l | tr -d ' ') 份)"
