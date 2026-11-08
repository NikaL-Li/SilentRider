# Authors

| 版本 | 姓名 | GitHub | 主要工作 |
|---|---|---|---|
| v0.1.0 | 焦龙心 | [@hejioxue](https://github.com/hejioxue) | 初始「传声」原型：语音识别、话术、TTS、大字牌、演示 |
| v0.1.2 | 邹昊龙 | [@IREXS-JPG](https://github.com/IREXS-JPG) | 模型接入与音色选型、数字读法规则、男/女声切换、端口占用修复 |
| v0.1.3 | 黎智诚 | [@NikaL-Li](https://github.com/NikaL-Li) | 接手初版，整体改进/优化/debug，完成全栈产品化、云服务器部署、网络与公网映射 |
| v1.0.0 | 三人团队 | @hejioxue · @IREXS-JPG · @NikaL-Li | 合并、安全加固、文档、测试与正式发布 |

## 黎智诚（@NikaL-Li）的主要工作

- **整体产品化**：重构首页、地址、取餐、对话、记录、账号页，完成移动端/PWA 与交互收敛；
- **全栈实现**：单文件前端 + Python 标准库后端 + SQLite，设计用户/会话/订单/地址簿/待同步队列；
- **账号与云同步**：密码哈希、HttpOnly Cookie、登录限流、游客数据迁移与幂等合并；
- **OCR 与双地址**：腾讯云 `RecognizeAgent` 接入、截图压缩、双地址/多单首单/A栋楼层/取餐同步、本地 Tesseract 兜底；
- **语音与外放**：MiMo TTS 服务端代理、男女声、数字读法、浏览器语音兜底、电话免提、音频竞态修复；
- **云部署与公网**：Ubuntu + systemd + Nginx + HTTPS + Cloudflare Tunnel + DNS/公网映射 + IP 备用入口；
- **Debug/性能**：OCR 从约 4s 优化到约 1.3s、壁纸渐进加载、地址解析纠错、蒙版/toast/安全区修复；
- **安全与质量**：静态 Token 阻断、代理头默认不信任、外呼默认登录、48 项 API 回归测试；
- **交付与协作**：README/AUTHORS/CHANGELOG/开发历程、版本标签、最终集成、线上验收与 GitHub 发布。

完整说明见 [项目介绍与使用说明](docs/项目介绍与使用说明.md)。

本项目由三位协作者共同完成，最终版本由黎智诚（@NikaL-Li）维护。
