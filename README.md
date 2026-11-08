# 译路畅通

> **译路畅通（英文代号 SilentRider）是为听障配送骑手打造的无障碍沟通工具。**
> 内置取餐、送餐场景预设话术，支持文字转语音、大字举牌展示，帮助骑手无需通话，和商家、顾客顺畅沟通。

[![Python](https://img.shields.io/badge/Python-3.10-3776AB)](https://www.python.org/)
[![PWA](https://img.shields.io/badge/PWA-offline%20ready-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**在线体验** · [https://ylct.lzc2006.cn/](https://ylct.lzc2006.cn/)  
**备用入口** · [https://ylct.lzc2006.cn:8443/](https://ylct.lzc2006.cn:8443/)

**完整文档** · [项目介绍与使用说明](docs/项目介绍与使用说明.md)

![译路畅通 首页](docs/screenshots/home.jpg)

---

## 为什么做译路畅通

配送骑手每天要打几十个电话：确认楼栋、找门禁、请人按电梯、解释“我是听障骑手”。
对听障骑手来说，电话不是沟通，而是障碍。

译路畅通把这件事拆成三步：

1. **听对方说**：语音识别转成大字；
2. **替我说**：点一句预设话术，TTS 念给对方听；
3. **举牌**：说不清时，直接把手机屏幕举给对方看。

不需要通话，不需要打字，也不要求顾客理解手语。

---

## 功能

| 能力 | 说明 |
|---|---|
| 听对方说 | Web Speech API 实时转写，对方的话变成大字 |
| 替我说 | 取餐/送餐场景话术，点击播放；可切男/女声 |
| 大字牌 | 全屏高对比大字，适合门禁、前台、电梯场景 |
| 地址 | 小区/楼栋/房号/楼层；历史地址自动补全 |
| 取餐 | 商家、取货地址、手机尾号、取餐码 |
| 截图识别 | 订单截图 → OCR → 自动解析取货/送货地址 |
| 双地址 | 上面取货、下面送货；多单只取最上面一单 |
| 记录 | 今日、近 7 天、本月、今年；近 30 天每日记录 |
| 天气/壁纸 | 高德定位 + 天气；每天 5 张城市壁纸轮播 |
| 电话 | 免提外放、音量增强；服务器外呼接口预留 |
| PWA | 可添加到主屏幕，弱网/离线可用 |
| 多用户 | 未登录可用；登录后订单/地址簿云同步 |

## 技术架构

```text
浏览器 / PWA
   │ HTTPS
   ▼
Cloudflare ──► Tunnel ──► Python stdlib server
                              ├── SQLite（用户/订单/地址簿）
                              ├── 高德 Web 服务（定位/天气）
                              ├── 腾讯云 OCR
                              ├── 本地 Tesseract（兜底）
                              └── Bing 图片代理（城市壁纸）
```

- 前端保持**单文件、无构建步骤**，方便弱网/离线分发；
- 后端只用 **Python 标准库**，降低部署门槛；
- 外部服务全部经服务端代理，Key 不出现在前端；
- OCR 云服务失败时自动回退本地 Tesseract。

## 快速开始

```bash
# 1. 配置服务端 Key（不提交到仓库）
export AMAP_KEY='高德 Web服务 Key'
export TENCENT_SECRET_ID='腾讯云 SecretId'
export TENCENT_SECRET_KEY='腾讯云 SecretKey'
export MIMO_API_KEY='MiMo TTS Key'      # 可选
python3 server.py

# 2. 打开
# http://127.0.0.1:8818
```

Windows 可双击 `启动译路畅通.bat`。

### OCR 可选依赖

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim python3-pil
```

不安装也能运行：腾讯云 OCR 优先，失败后回退浏览器语音/本地能力。

## 目录结构

```text
index.html                单文件前端
server.py                 Python 后端（认证/记录/地址簿/天气/OCR/壁纸）
manifest.json             PWA
sw.js                     Service Worker
deploy/                   systemd / Nginx / 环境变量示例
docs/                     部署记录、电话方案、开发历程、截图
ocr-service/              可选：独立 RapidOCR 服务
tests/test_api.py          API 回归测试
.github/                  Issue/PR 模板、测试 workflow
```

## 版本与协作者

| 版本 | 协作者 | 主要工作 |
|---|---|---|
| `v0.1.0` | [@hejioxue](https://github.com/hejioxue) | 初始「传声」原型：语音识别、话术、TTS、大字牌、演示 |
| `v0.1.2` | [@IREXS-JPG](https://github.com/IREXS-JPG) | 模型接入与音色选型、数字读法规则（多轮迭代）、男/女声切换、端口占用修复 |
| `v0.1.3` | [@NikaL-Li](https://github.com/NikaL-Li) | 产品化整合：登录/云同步、订单记录/地址簿、高德/壁纸、OCR、双地址、部署 |
| `v1.0.0` | @hejioxue、@IREXS-JPG、@NikaL-Li | 合并、文档、音频竞态修复、UI 收敛、正式发布 |

完整说明见 [项目介绍与使用说明](docs/项目介绍与使用说明.md)、[CHANGELOG.md](CHANGELOG.md)、[AUTHORS.md](AUTHORS.md)、[开发历程](docs/开发历程.md)。

## 测试

```bash
python3 tests/test_api.py
# 通过 48 项
```

GitHub Actions 可手动触发；当前账号 billing 受限时以本地测试为准。

## 部署

- [Ubuntu 部署](docs/Ubuntu部署.md)
- [线上部署记录](docs/部署记录-117.72.107.56.md)
- [电话播报方案](docs/电话播报方案.md)

生产环境建议：HTTPS、反向代理、登录限流、上传大小限制、最小权限云账号。

## 安全与隐私

- `.env`、Key、OCR Token、服务器密码**永不入库**；
- 截图含手机号/地址，云 OCR 会把图片发送给服务商；
- 上线需准备隐私说明，并允许用户选择本地 OCR；
- 公开部署建议开启 `HttpOnly` Cookie、CSRF 防护、登录限流。

## Roadmap

见 [ROADMAP.md](ROADMAP.md)：
- 语音播放状态/停止按钮
- OCR 结果确认页
- 大字牌字号/横屏
- 账号级城市偏好、记录导出

## 参与贡献

- [贡献指南](CONTRIBUTING.md)
- [安全问题](SECURITY.md)
- `.github/ISSUE_TEMPLATE`、`.github/pull_request_template.md`
- 分支：`feat/*`、`fix/*`，通过 Pull Request 合并

## License

[MIT](LICENSE) © 2026 SilentRider contributors

---

**译路畅通 · 让听障骑手少一通电话，多一份确定性。**
