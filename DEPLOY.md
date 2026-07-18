# 公开采数部署清单（自有服务器）

> 目的：把「设计好的系统」安全地架起来跑试测/正式采数。评估点名的两个灾难项——**弱口令泄露** 和 **零备份丢数据**——在此清零。
> 就绪自检：`.venv/bin/python scripts/deploy_check.py`（全绿再公开）。数据合规细节见 `paper/13`。

---

## 0. 一句话流程
`deploy_check 全绿` → 起服务（systemd）→ 反代 + HTTPS（Caddy/nginx）→ 备份 cron → 发链接跑**试测** → `make pilot` 看 4 读数 → 没问题再大范围。

## 1. 硬门禁（`deploy_check.py` 会逐条检查）

- [ ] **研究员强密码**：`.streamlit/secrets.toml` 设 `researcher_password = "…"`（≥12 位，**不能是 `nova`**）。否则任何人输 `nova` 就能下载全部被试数据 + 看到实验目的（去盲）。
- [ ] **OpenAI 置顶 + 快照钉死**：`api_configs[0]` = OpenAI，`model` 用**带日期的快照**（如 `gpt-4o-mini-2024-07-18`），避免采数跨周撞上模型静默更新。开发期的 edgefn/KAT 从首位挪走。
- [ ] **空库起跑**：把含开发数据的 `data/novastory.db` 归档，从空库开始（见 §3）。
- [ ] **`config.toml` 关 `runOnSave`**（dev 设置）。
- [ ] **`.gitignore` 覆盖 `data/*.db`**（已就位）。
- [ ] **备份脚本进 cron**（见 §4）。
- [ ] **HTTPS**：被试要填同意/人口学，明文 HTTP 不可（见 §5）。
- [ ] **同意书含**：外部 AI(OpenAI)处理、勿填隐私、可随时退出、数据用途与匿名（APPI 越境条款，见 paper/13 §5）。

## 2. 起服务（systemd 守护，别用 tmux）
`/etc/systemd/system/novastory.service`（占位，按你的路径/用户改）：
```ini
[Unit]
Description=NovaStory experiment app
After=network.target
[Service]
WorkingDirectory=/path/to/NovaStory
ExecStart=/path/to/NovaStory/.venv/bin/streamlit run app.py \
  --server.port 8501 --server.address 127.0.0.1 --server.headless true
Restart=always
User=youruser
[Install]
WantedBy=multi-user.target
```
`sudo systemctl enable --now novastory` 。只监听 127.0.0.1，外部经反代访问。

## 3. 空库起跑（归档开发数据）
```bash
mkdir -p data/archive
mv data/novastory.db data/novastory.db-wal data/novastory.db-shm data/archive/ 2>/dev/null || true
mv data/experiment_results.csv data/archive/ 2>/dev/null || true   # 旧 v1 数据
# 首次启动会自动新建空库
```

## 4. 备份（采数期的命根子）
```bash
chmod +x scripts/backup_db.sh
# 手动测一次：
scripts/backup_db.sh
# 进 cron（每日 03:00；建议再加「每场被试后」手动跑一次）：
crontab -e
# 0 3 * * * cd /path/to/NovaStory && scripts/backup_db.sh >> backup/backup.log 2>&1
```
**再把 `backup/` 异地同步一份**（另一台 / 对象存储 / 网盘）——单机磁盘故障 = 毕业数据灭失。

## 5. HTTPS（Caddy 最省心，需域名）
`Caddyfile`（占位）：
```
your.domain.example {
    reverse_proxy 127.0.0.1:8501
}
```
`caddy run`（或做成 systemd）。Caddy 自动签发/续期证书。防火墙只放 80/443，**关掉外部直连 8501**（否则有人绕过反代直接摸到 app）。
> 只有裸 IP、无域名时 HTTPS 很麻烦（自签证书→浏览器红警告吓跑被试），强烈建议弄个域名。

## 6. 采数前最后一跑
```bash
.venv/bin/python scripts/deploy_check.py   # 期望全绿
.venv/bin/python scripts/dev_smoke_e2e.py  # E2E 冒烟(LLM 打桩)应通过
```
全绿后发链接给试测被试；试测数据回来跑 `make pilot`（4 生死问题 → 后手见 paper/16）。

---
**你需要给我 3 个信息，我就把 §2/§5 的 systemd + Caddy 配置填成你能直接用的版本:** ① 服务器发行版(Ubuntu/Debian?)② 有没有域名 ③ 有没有 sudo。
