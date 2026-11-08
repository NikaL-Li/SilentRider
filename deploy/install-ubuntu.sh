#!/usr/bin/env bash
# 译路畅通 · Ubuntu 一键安装（在项目根目录执行 sudo bash deploy/install-ubuntu.sh）
# 做的事：建用户、复制程序、建数据目录、装 systemd、启动服务。
# 不覆盖已有 .env 和 data；不改 Nginx（Nginx 配置见 docs/Ubuntu部署.md）。
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash deploy/install-ubuntu.sh" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/ylct
DATA=/var/lib/ylct

echo "==> 检查 Python"
command -v python3 >/dev/null || { echo "缺 python3：sudo apt install python3" >&2; exit 1; }
python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    raise SystemExit("需要 Python 3.9+，当前 %s" % sys.version.split()[0])
PY

echo "==> 创建用户和目录"
if ! id ylct >/dev/null 2>&1; then
  useradd --system --home "$APP" --shell /usr/sbin/nologin ylct
  echo "    已创建用户 ylct"
fi
mkdir -p "$APP" "$DATA"
chmod 700 "$DATA"

echo "==> 复制程序到 $APP（保留已有 .env 和 data）"
tar --exclude=./data --exclude=./.env --exclude=./ocr-service/ocr_token.txt -C "$SRC" -cf - . | tar -C "$APP" -xf -

if [ ! -f "$APP/.env" ]; then
  cp "$APP/deploy/ylct.env.example" "$APP/.env"
  chmod 600 "$APP/.env"
  echo "    已生成 $APP/.env，请填写 MIMO_API_KEY 后重启服务"
fi

chown -R ylct:ylct "$APP" "$DATA"
chmod 600 "$APP/.env"

echo "==> 安装 systemd 服务"
install -m 644 "$APP/deploy/ylct.service" /etc/systemd/system/ylct.service
systemctl daemon-reload
systemctl enable --now ylct

sleep 1
echo "==> 状态"
systemctl --no-pager --full status ylct || true

echo
echo "完成。接下来："
echo "  1. 编辑 /opt/ylct/.env 填 MIMO_API_KEY，然后 sudo systemctl restart ylct"
echo "  2. 按 docs/Ubuntu部署.md 配置 Nginx + HTTPS"
echo "  3. 本机自测：curl http://127.0.0.1:8818/api/health"
