# 译路畅通 · 独立 OCR 服务（Mac / Linux）

把截图识别从云服务器搬到性能更好的机器上。云服务器只发送图片，拿回文字。

## 一、启动服务

macOS：双击 `run.command`，或者终端运行：

```bash
cd ocr-service
./run.command
```

首次会自动建 `.venv` 并安装 `rapidocr_onnxruntime`（约几十 MB）。
启动后默认监听 `127.0.0.1:9000`。

第一次启动会在同目录生成 **`ocr_token.txt`**，这是服务端调用令牌；不要发到公开群/仓库。

健康检查：

```bash
curl http://127.0.0.1:9000/health
```

识别测试：

```bash
TOKEN=$(cat ocr_token.txt)
curl -sS -X POST "http://127.0.0.1:9000/ocr" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: image/png" \
  --data-binary @/path/to/screenshot.png
```

## 二、让云服务器访问到它

### 方案 A：Cloudflare Tunnel（推荐，家里没公网 IP 也行）

在 Mac 上安装 cloudflared（任选其一）：

```bash
brew install cloudflared
# 或从 Cloudflare 官网下载 macOS 安装包
```

**临时试用（地址每次重启会变）：**

```bash
cloudflared tunnel --url http://127.0.0.1:9000
```

终端会输出一个 `https://xxxx.trycloudflare.com`，把它填到云服务器：

```ini
OCR_REMOTE_URL=https://xxxx.trycloudflare.com/ocr
OCR_REMOTE_TOKEN=ocr_token.txt 里的内容
```

**长期使用（固定域名）：**

```bash
cloudflared tunnel login
cloudflared tunnel create ylct-ocr
cloudflared tunnel route dns ylct-ocr ocr.lzc2006.cn
```

`~/.cloudflared/config.yml`：

```yaml
tunnel: ylct-ocr
credentials-file: /Users/你的用户名/.cloudflared/ylct-ocr.json
ingress:
  - hostname: ocr.lzc2006.cn
    service: http://127.0.0.1:9000
  - service: http_status:404
```

```bash
cloudflared tunnel run ylct-ocr
# 或安装成开机服务
sudo cloudflared service install
```

然后云服务器 `.env`：

```ini
OCR_REMOTE_URL=https://ocr.lzc2006.cn/ocr
OCR_REMOTE_TOKEN=...
OCR_REMOTE_TIMEOUT=4
```

### 方案 B：SSH 反向隧道（不暴露公网，临时）

在 Mac 上保持这个连接：

```bash
ssh -N -R 9000:127.0.0.1:9000 root@117.72.107.56
```

云服务器 `.env`：

```ini
OCR_REMOTE_URL=http://127.0.0.1:9000/ocr
OCR_REMOTE_TOKEN=...
```

## 三、服务端行为

- 配了 `OCR_REMOTE_URL`：优先调用远程 OCR，约 1–2 秒；
- 远程超时/未启动：自动回退云服务器本地 Tesseract；
- 只发送截图，不发送账号、订单等其它数据；
- 服务只监听 127.0.0.1，必须经 Tunnel/SSH 访问，并带 Bearer Token。

## 四、注意

- Mac 不能休眠（系统设置 → 电池/电源 → 防止自动休眠）；
- 关掉这个终端/进程，远程 OCR 就不可用，但网页会回退本地 OCR；
- 截图含手机号/地址，尽量只用自己的机器，不要接来路不明的 Tunnel。
