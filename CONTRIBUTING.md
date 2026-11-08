# 贡献指南

## 分支

- `feat/<short-name>`：新功能
- `fix/<short-name>`：缺陷修复
- `docs/<short-name>`：文档

## 提交前

1. 不提交任何 `.env`、Token、密钥、数据库、截图原图；
2. 运行后端测试：`python3 tests/test_api.py`；
3. 前端至少检查桌面与手机宽度；
4. PR 描述写清：改了什么、为什么、怎么验证。

## 代码风格

- 前端保持单文件、无构建步骤；
- 后端只用 Python 标准库（OCR/图片压缩依赖可选）；
- 新接口必须处理未登录、参数错误、限流和异常回退；
- 不在日志中打印密码、Token、完整手机号。

## 安全

发现密钥泄漏请先私信维护者，不要开公开 Issue。见 [SECURITY.md](SECURITY.md)。
