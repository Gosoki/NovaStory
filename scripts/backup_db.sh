#!/usr/bin/env bash
# 安全备份 SQLite(WAL 模式)→ 带时间戳的快照 + 轮转保留,**并一并打包分镜插图**。
# ⚠️ 依赖 sqlite3 CLI(.backup 对 WAL 才安全)。缺它会 exit 127 —— 部署机务必先装。
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

# ---- 分镜插图(2026-09-01 拍板 1.5:必须一起备份) ----
# 它们不在主库里,却是两样东西的唯一凭据:①02 §8.4 声明的图像模型漂移检测证据
# (图像模型没有日期快照可钉,只能靠事后复算风格指标);②11 §5.4 已写进 Limitations 的
# 「问卷阶段的 AI 插图构成主观 DV 的测量情境」。纯磁盘故障即可造成、事后 100% 不可逆。
IMGDIR="$(dirname "$DB")/storyboard_images"
if [ -d "$IMGDIR" ] && [ -n "$(ls -A "$IMGDIR" 2>/dev/null)" ]; then
  imgout="$DEST/storyboard_images-$ts.tar.gz"
  tar -czf "$imgout" -C "$(dirname "$IMGDIR")" "$(basename "$IMGDIR")"
  echo "backup ok: $imgout ($(find "$IMGDIR" -name '*.jpg' | wc -l | tr -d ' ') 张)"
else
  echo "backup skip: $IMGDIR 为空或不存在(尚未产生插图)"
fi

# 轮转:超出 KEEP 份的旧快照删除(db 与插图包各自轮转)
ls -1t "$DEST"/novastory-*.db 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
ls -1t "$DEST"/storyboard_images-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

# ---- 剩余磁盘水位告警(12 §4④:DB 目录写不下时是诚实失败,但要提前知道) ----
avail_kb="$(df -Pk "$DEST" | awk 'NR==2{print $4}')"
if [ "${avail_kb:-0}" -lt 1048576 ]; then   # < 1 GiB
  echo "backup WARN: $DEST 所在分区剩余 $((avail_kb / 1024)) MiB(<1GiB),请清理或扩容" >&2
fi

echo "backup ok: $out (保留 $(ls -1 "$DEST"/novastory-*.db 2>/dev/null | wc -l | tr -d ' ') 份)"
