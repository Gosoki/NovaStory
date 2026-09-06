# 公开采数部署清单（自有服务器）

> 目的：把「设计好的系统」安全地架起来跑试测/正式采数。评估点名的两个灾难项——**弱口令泄露** 和 **零备份丢数据**——在此清零。
> 就绪自检：`.venv/bin/python scripts/deploy_check.py`（全绿再公开）。数据合规细节见 `docs/paper/07_采数前执行清单.md` §2。

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
- [ ] **同意书含**：外部 AI(OpenAI)处理、勿填隐私、可随时退出、数据用途与匿名（APPI 越境条款，见 `docs/paper/07` §2.3）。

## 2. 起服务

**本项目采用的方式（2026-09-06 拍板）：不装 systemd 单元，用 `scripts/start.sh` 起普通后台进程。**

```bash
scripts/start.sh            # 启动（已在跑就什么都不做）
scripts/start.sh status     # 看状态
scripts/start.sh stop       # 停止
scripts/start.sh restart    # 重启
```

脚本走 `scripts/serve.py`（静态资源预压缩生效）、写日志到 `data/serve.log`（超过 20 MB 自动转存）、pid 记在 `data/novastory.pid`。端口被别的进程占着时它**拒绝启动并说明是谁占的**，不会去杀别人的进程。

开机自启靠 crontab 而不是 `systemctl enable`：

```
@reboot sleep 20 && cd /root/coding/local/Claude/NovaStory && scripts/start.sh >> data/serve.log 2>&1
```

⚠️ **那两个 `cd` 不能省**：cron 的工作目录是 `$HOME`，脚本自己虽然会 `cd`，但日志重定向的相对路径是在 cron 的工作目录下解析的。`deploy_check` 的「运行时」项会检查这一行存在且带 `cd`，缺了报红。

**已知并接受的取舍**：以 root 运行、绑 `0.0.0.0`。前者要改就得把仓库和 uv 解释器一起搬出 `/root`（见下方模板注释），后者是因为 nginx 在另一台机器（10.0.0.1）上，绑回环会切断反代。

---

### 备选：systemd 守护（本项目没采用）
模板已入库：**`deploy/novastory.service`**（非 root 账号、只监听 127.0.0.1、`ProtectSystem=strict`），按文件头的命令安装——仓库在 `/root` 下时**必须先搬到 `/opt/novastory`**（非 root 账号进不了 /root）。⚠️ 现在机器上跑的是 `systemd-run` 造的 **transient** 单元（重启即消失、root、0.0.0.0），`scripts/deploy_check.py` 的「运行时」项会为此亮红灯。研究者画面：网址加 `?admin=1`。
下面是同一份模板的摘要（以文件为准）：
```ini
[Unit]
Description=NovaStory experiment app
After=network.target
[Service]
WorkingDirectory=/path/to/NovaStory
ExecStartPre=/path/to/NovaStory/.venv/bin/python /path/to/NovaStory/scripts/precompress_static.py --quiet
ExecStart=/path/to/NovaStory/.venv/bin/python /path/to/NovaStory/scripts/serve.py run app.py \
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

## 4.5 首屏加速（静态资源 gzip）
Streamlit 1.60 的 `SelectiveGZipMiddleware` **主动让 `/static/` 绕过 gzip**（源码注释说是按局域网压测调的）。局域网上这么做没错，跨境链路上正好相反：实测首屏 122 个请求、**2.50 MB 全部以原文传输**。

`scripts/serve.py` 在 Starlette app 构建前替换 `StaticFiles.file_response`，让它直接发预压缩产物；运行期不做压缩，所以 Streamlit 顾虑的 CPU/RSS 开销为零。产物由 `make precompress` 生成（在 `.venv` 里，不进 git）。

**装完依赖或升级 Streamlit 后要重跑 `make precompress`**；忘了也不会发错内容——`serve.py` 比对 mtime，产物过期就回退发原文。`deploy_check.py` 的「静态压缩」项会同时检查产物是否最新、服务是否真由 `serve.py` 启动。

### 反代层也必须开压缩（2026-09-03 实测）
本项目的 nginx 反代**已经是 HTTPS + HTTP/2**（`server: nginx/1.30.3`，证书有效），所以 HTTP/1.1 六并发那笔往返开销本来就没有。但实测线上静态资源**完全没有压缩**，主 JS 以 440 KB 原文下发。

在模拟 200 ms 往返 / 4 Mbps 的链路上量到：

| 状态 | 协议 | 线上字节 | 首屏 |
|---|---|---|---|
| 线上现状（未压缩） | h2 | 1.96 MB | 5.8 s |
| 开启压缩后 | h2 | 0.61 MB | **2.4 s** |

nginx 侧加这段（`http` 块或对应 `server` 块）：

```nginx
gzip on;
gzip_vary on;
gzip_proxied any;          # 默认 off，反代来的响应一律不压，漏了这行等于没开
gzip_comp_level 5;
gzip_min_length 1024;
gzip_types text/plain text/css text/xml
           application/javascript application/json application/xml
           image/svg+xml;   # 默认只有 text/html，不写这行那 2 MB 的 JS 一字节都不压
```

两个最容易漏的点已写在注释里。`nginx -t && nginx -s reload` 后验证：

```bash
curl -sI -H 'Accept-Encoding: gzip' https://你的域名/static/js/index.*.js | grep -i 'content-encoding\|content-length'
```

出现 `content-encoding: gzip`、长度降到 13 万左右即成功。`deploy_check.py` 的「对外地址」项会自动跑这个验证，前提是 `secrets.toml` 里配了 `public_url`。

如果反代把 `Accept-Encoding` 透传给了后端，那么后端的 `serve.py` 会直接给出 level 9 的预压缩产物，nginx 只需转发，省掉现场压缩的 CPU，压得也比 `gzip_comp_level 5` 更小。两条路都通，先把 nginx 这段配上，它立即见效且不依赖后端。

## 5. HTTPS（本项目已具备）
**现状：已有 nginx 反代 + 有效证书 + HTTP/2**，对外地址 `https://test.jp.wuzuxi.com`，后端指向本机 8501（etag 比对确认）。nginx **不在这台机器上**，所以「本机监听 443」这类本地检查判断不了 HTTPS 有没有——`deploy_check.py` 已改成读 `secrets.toml` 的 `public_url` 从外面实测。

剩下的事只有 §4.5 那段 gzip 配置。

没有反代时的备选模板仍入库在 **`deploy/Caddyfile`**（Caddy 自动签发续期证书，`encode zstd gzip` 一行搞定压缩），需域名。

> ⚠️ app 目前仍绑 `0.0.0.0:8501`，等于反代之外还留着一个明文直连入口，可以绕过 HTTPS 摸到应用。改成 `--server.address 127.0.0.1` 后只有 nginx 能连上。`deploy_check.py` 的「运行时」项为此亮红灯。
`caddy run`（或做成 systemd）。Caddy 自动签发/续期证书。防火墙只放 80/443，**关掉外部直连 8501**（否则有人绕过反代直接摸到 app）。
> 只有裸 IP、无域名时 HTTPS 很麻烦（自签证书→浏览器红警告吓跑被试），强烈建议弄个域名。

## 6. 采数前最后一跑
```bash
.venv/bin/python scripts/deploy_check.py   # 期望全绿
.venv/bin/python scripts/dev_smoke_e2e.py  # E2E 冒烟(LLM 打桩)应通过
```
全绿后发链接给试测被试；试测数据回来跑 `make pilot`（4 生死问题 → 后手见 `docs/paper/05` §2）。

---
**你需要给我 3 个信息，我就把 §2/§5 的 systemd + Caddy 配置填成你能直接用的版本:** ① 服务器发行版(Ubuntu/Debian?)② 有没有域名 ③ 有没有 sudo。
