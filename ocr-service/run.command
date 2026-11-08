#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "首次运行：创建 Python 环境并安装 RapidOCR..."
  python3 -m venv .venv
  .venv/bin/pip install --disable-pip-version-check -r requirements.txt
fi
exec .venv/bin/python ocr_server.py
