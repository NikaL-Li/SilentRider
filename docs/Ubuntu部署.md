# Ubuntu 部署说明

目标：把「译路畅通」放到一台 Ubuntu 云服务器上，手机浏览器用 HTTPS 打开。
项目是 **纯 Python 标准库**，不需要 pip install、不需要 Node、不需要数据库服务。

---

## 1. 目录规划

```
/opt/ylct/            程序目录（server.py、index.html、图标、manifest、sw.js）
/opt/ylct/.env        环境变量（不要提交到 Git）
/var/lib/ylct/        SQLite 数据库、WAL 文件（服务用户可写）
```

系统要求：Ubuntu 20.04+，Python 3.9+，Nginx（反向代理 + HTTPS）。

---

## 2. 上传代码

在本地项目目录执行（把 `你的服务器` 换掉）：

```bash
rsync -av --exclude 'data' --exclude '.env' \
  ./ root@你的服务器:/opt/ylct/
```

或者先打包：

```bash
tar czf ylct.tar.gz --exclude data --exclude .env .
scp ylct.tar.gz root@你的服务器:/tmp/
ssh root@你的服务器
mkdir -p /opt/ylct && tar xzf /tmp/ylct.tar.gz -C /opt/ylct
```

> 不要把 `data/` 和 `.env` 传到 Git；`data/` 里是用户手机号哈希后的账号、地址和记录。

---

## 3. 创建运行用户与数据目录

```bash
sudo useradd --system --home /opt/ylct --shell /usr/sbin/nologin ylct
sudo mkdir -p /var/lib/ylct
sudo chown -R ylct:ylct /opt/ylct /var/lib/ylct
sudo chmod 700 /var/lib/ylct
```

---

## 4. 配置环境变量

```bash
sudo -u ylct cp /opt/ylct/deploy/ylct.env.example /opt/ylct/.env
sudo -u ylct nano /opt/ylct/.env
sudo chmod 600 /opt/ylct/.env
```

至少填：

```ini
MIMO_API_KEY=sk-你的Key
YLCT_HOST=127.0.0.1
YLCT_PORT=8818
YLCT_DATA_DIR=/var/lib/ylct
YLCT_TRUST_PROXY=1
YLCT_COOKIE_SECURE=auto
```

完整变量说明见 `deploy/ylct.env.example` 和 `README.md`。

> 没有 `MIMO_API_KEY` 也能跑，页面会回退到浏览器自带语音；
> 但 MiMo 的声音更自然，建议配置。

---

## 5. 试运行

```bash
sudo -u ylct bash -c 'cd /opt/ylct && set -a && . ./.env && set +a && python3 server.py'
```

另开终端：

```bash
curl http://127.0.0.1:8818/api/health
```

看到 `{"ok":true,...}` 就说明通了。`Ctrl+C` 停掉，交给 systemd。

---

## 6. systemd 常驻

```bash
sudo cp /opt/ylct/deploy/ylct.service /etc/systemd/system/ylct.service
sudo systemctl daemon-reload
sudo systemctl enable --now ylct
sudo systemctl status ylct --no-pager
sudo journalctl -u ylct -f
```

`deploy/ylct.service` 已经包含：

- `User=ylct`、`WorkingDirectory=/opt/ylct`
- `EnvironmentFile=/opt/ylct/.env`
- `Restart=on-failure`
- `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict`
- 只允许写 `/var/lib/ylct`

---

## 7. Nginx + HTTPS（必须）

手机上的**麦克风、剪贴板、PWA 安装**都要求安全上下文：
`https://` 或 `localhost`。用 IP + HTTP 打开时，语音识别/粘贴按钮会不可用。
所以公网部署必须上 HTTPS。

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
sudo cp /opt/ylct/deploy/nginx-ylct.conf /etc/nginx/sites-available/ylct
sudo nano /etc/nginx/sites-available/ylct   # 改 server_name
sudo ln -s /etc/nginx/sites-available/ylct /etc/nginx/sites-enabled/ylct
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.com
```

没有域名时，可以先用 `https://你的IP` 自签证书测试，但手机浏览器会告警，PWA 也不稳。

防火墙：

```bash
sudo ufw allow 'Nginx Full'
sudo ufw allow OpenSSH
sudo ufw enable
```

**不要**把 8818 暴露到公网；它只监听 127.0.0.1，由 Nginx 转发即可。

---

## 8. 验证清单

```bash
# 首页
curl -I https://your-domain.com/

# 健康检查
curl https://your-domain.com/api/health

# 确认后端文件不可下载
curl -o /dev/null -w '%{http_code}\n' https://your-domain.com/server.py   # 404
curl -o /dev/null -w '%{http_code}\n' https://your-domain.com/data/app.db # 404
```

手机浏览器打开：

1. 首页能看天气、能进「送餐 / 取餐」；
2. 注册 → 登录 → 退出；
3. 填地址 → 再进一次，能自动补全；
4. 点「这单送完了」，记录页数字增加；
5. 对话页右上角「电话」能打开播报面板。

---

## 9. 数据备份

先装 sqlite3 命令行（如果还没有）：

```bash
sudo apt install -y sqlite3
```

```bash
sudo -u ylct sqlite3 /var/lib/ylct/app.db ".backup '/var/lib/ylct/backup-$(date +%F).db'"
```

建议加一条 cron，每天备份到另一块盘：

```cron
0 4 * * * ylct sqlite3 /var/lib/ylct/app.db ".backup '/var/backups/ylct-$(date +\%F).db'"
```

恢复：停服务 → 用备份覆盖 `app.db` → 删掉 `app.db-wal` / `app.db-shm` → 启服务。

---

## 10. 升级

```bash
sudo systemctl stop ylct
sudo rsync -av --exclude 'data' --exclude '.env' /tmp/ylct-new/ /opt/ylct/
sudo chown -R ylct:ylct /opt/ylct
sudo systemctl start ylct
```

数据库表用 `CREATE TABLE IF NOT EXISTS`，直接兼容旧库；
如果改了表结构，先在测试库跑一遍再升生产。

---

## 11. 常见问题

**页面能开，但语音识别不可用**
- 不是 HTTPS；或浏览器不支持（Chrome/Edge 支持，微信内置浏览器常被限制）。
- 页面本来就有「对方说」打字兜底，功能不受影响。

**登录后刷新就掉线**
- Nginx 没传 `X-Forwarded-Proto`，Cookie 没带 `Secure`；或用 HTTP 访问。
- 检查 `YLCT_COOKIE_SECURE=auto` 和 Nginx 配置里的 `proxy_set_header X-Forwarded-Proto $scheme;`。

**TTS 返回 502**
- `MIMO_API_KEY` 无效/欠费；或服务器访问不了 `api.xiaomimimo.com`。
- 看 `journalctl -u ylct -f` 的具体报错。

**数据库锁**
- 已开 WAL；不要用 NFS 挂载数据库目录。
- 单机、几百并发以内 SQLite 足够。

**忘记密码**
- 默认没有短信服务，不能自助找回；管理员可重置：
  ```bash
  sudo -u ylct python3 - <<'PY'
  import sqlite3, sys
  sys.path.insert(0, '/opt/ylct')
  import server
  conn = sqlite3.connect('/var/lib/ylct/app.db')
  conn.execute('UPDATE users SET password_hash=? WHERE phone=?',
               (server.hash_password('新密码至少8位'), '13800135025'))
  conn.commit()
  print('ok')
  PY
  ```
- 或配置短信服务后使用页面「忘记密码」。

**限流太严**
- 登录：10 次 / 10 分钟 / IP+手机号；短信：1 次 / 分钟 / 手机号；
- 外呼：10 次 / 小时 / IP、3 次 / 小时 / 号码；TTS：300 次 / 小时 / IP。
- 需要调整改 `server.py` 顶部常量后重启。

---

## 12. 安全清单

- [ ] 修改默认没有的密码（本项目没有默认管理员，但请保护 `.env`）
- [ ] `.env` 权限 600，`data/` 权限 700
- [ ] 公网只开 80/443，8818 只监听 127.0.0.1
- [ ] HTTPS 证书自动续期：`sudo certbot renew --dry-run`
- [ ] 定期备份 `app.db`
- [ ] 如果直接暴露服务（不经 Nginx），把 `YLCT_TRUST_PROXY=0`，避免伪造 `X-Forwarded-For`
- [ ] 语音外呼网关必须校验 Token，并设置预算/白名单
- [ ] 不要把 `data/`、`.env`、`tests/`、`deploy/` 放进网站根目录（后端已拒绝这些路径）
---

## 13. 没有域名：用 Let's Encrypt IP 证书（8443）

2026 年起 Let's Encrypt 支持 **IP 证书**，但必须是 **160 小时短期证书**，
需要使用支持 `--cert-profile shortlived` 的 ACME 客户端（acme.sh 3.1.7+）。
好处是：没有域名也能拿到浏览器信任的证书，麦克风/剪贴板/PWA 都能用。

> 不要用 6666：Chrome / Edge 把 6666 列为 unsafe port，会直接报
> `ERR_UNSAFE_PORT`。8443 是浏览器允许的常用 HTTPS 端口。

### 13.1 让 80 端口能访问 ACME 验证文件

HTTP-01 验证走 80 端口。你可以用现有站点的 webroot（例如
`/var/www/html`），确保：

```bash
mkdir -p /var/www/html/.well-known/acme-challenge
echo test > /var/www/html/.well-known/acme-challenge/test
curl http://你的IP/.well-known/acme-challenge/test
```

必须能从公网访问到。

### 13.2 签发 IP 证书

```bash
# 如果 GitHub 下载超时，用 jsDelivr
mkdir -p /root/.acme.sh
curl -fsSL -o /root/.acme.sh/acme.sh \
  https://cdn.jsdelivr.net/gh/acmesh-official/acme.sh@master/acme.sh
chmod +x /root/.acme.sh/acme.sh

/root/.acme.sh/acme.sh --set-default-ca --server letsencrypt
/root/.acme.sh/acme.sh --issue --server letsencrypt \
  --cert-profile shortlived \
  -d 你的IP -w /var/www/html --keylength ec-256

/root/.acme.sh/acme.sh --install-cert -d 你的IP --ecc \
  --key-file /etc/nginx/ssl/ylct-ip/private.key \
  --fullchain-file /etc/nginx/ssl/ylct-ip/fullchain.cer \
  --reloadcmd "systemctl reload nginx"
```

### 13.3 Nginx 8443

```bash
sudo cp deploy/nginx-ip-8443.conf /etc/nginx/sites-available/ylct-https
sudo ln -s /etc/nginx/sites-available/ylct-https /etc/nginx/sites-enabled/ylct-https
sudo nginx -t && sudo systemctl reload nginx
```

访问：`https://你的IP:8443/`

### 13.4 自动续期（必须）

IP 证书只有约 6 天有效期，必须每天检查。加 `/etc/cron.d/ylct-acme`：

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 4 * * * root /root/.acme.sh/acme.sh --cron --home /root/.acme.sh >/var/log/ylct-acme.log 2>&1
```

确认续期命令没有报错：

```bash
/root/.acme.sh/acme.sh --cron --home /root/.acme.sh
```

如果续期失败，证书过期后浏览器会报错；建议加监控（证书剩余时间 < 2 天告警）。

---

## 14. 高德定位、地区壁纸、截图 OCR（可选依赖）

### 14.1 高德 Key（服务端）

在 `/opt/ylct/.env` 增加：

```ini
AMAP_KEY=你的高德Web服务Key
```

高德控制台创建 Key 时类型选 **Web服务**（不是 JS API）。
Key 只存在服务端，前端通过 `/api/geo` 使用，不会出现在网页源码里。

### 14.2 截图 OCR

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim python3-pil
tesseract --list-langs    # 应包含 chi_sim 和 eng
sudo systemctl restart ylct
curl http://127.0.0.1:8818/api/health
# 应看到 "ocr":true
```

- `/api/ocr` 接收原始图片字节（≤8MB），返回识别文字；
- 前端会把截图缩放到 1600px、JPEG 压缩后再上传；
- 不装 Tesseract 时，页面会自动回退到「粘贴识别」；
- `python3-pil` 只用于把 Bing 壁纸压缩到 1400px，没装也能用，只是图片更大。

### 14.3 地区壁纸

`/api/wallpaper?city=济南` 会按城市抓取 Bing 图片，缓存 1 小时并压缩。
正式商用请换成有明确授权的图库（Unsplash/Pexels/Pixabay）；Bing 搜索结果可能有版权限制。
