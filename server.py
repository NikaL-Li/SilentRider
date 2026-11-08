#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
译路畅通 · MiMo TTS 后端（音频总监版）
=====================================
把原来的「晓晓(Azure 神经语音)」替换为 MiMo-V2.5-TTS 系列的自然人声。

为什么是这个模型
------------------
- MiMo-V2.6-Flash 是「文本输出」的多模态大模型，不直接出音频；
- 语音合成要走 MiMo 的 TTS 系列：mimo-v2.5-tts（预置音色）
  / mimo-v2.5-tts-voicedesign（文字设计音色）/ mimo-v2.5-tts-voiceclone（音频复刻）。
- 预置中文音色：冰糖(女/清亮)、茉莉(女/温润)、苏打(男)、白桦(男)。

默认音色
--------
- 音色：冰糖（最贴近原「晓晓」的年轻女声定位，但更自然、少机械感）
- 风格：自然、温和、亲切的普通话口语，语速适中偏慢、吐字清晰（见 STYLE）
- 可用环境变量覆盖：MIMO_VOICE=茉莉 / 苏打 / 白桦 / 冰糖

用法
----
1. 获取 API Key：https://platform.xiaomimimo.com/console/api-keys
2. 运行（二选一）：
   Windows PowerShell:
       $env:MIMO_API_KEY="你的key"; python server.py
   Windows CMD:
       set MIMO_API_KEY=你的key && python server.py
3. 浏览器打开 http://127.0.0.1:8818 ，前端 index.html 会自动走 /api/tts
4. 想先听一条样例（不启动服务）：
       python server.py --test "您好，我是送外卖的，麻烦开一下门"
   会生成 sample.wav

零第三方依赖：只用 Python 标准库。
"""

import os
import sys
import json
import re
import base64
import threading
import urllib.request
import urllib.parse
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8818

MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "").strip()
BASE_URL = "https://api.xiaomimimo.com/v1"

# ── 音色 / 模型 ─────────────────────────────────────────────
MODEL = "mimo-v2.5-tts"
VOICE = os.environ.get("MIMO_VOICE", "冰糖").strip() or "冰糖"

# ── 自然语言风格指令（放在 user 消息，控制语气，不进入合成文本） ──
STYLE = (
    "用自然、温和、亲切的普通话口语朗读下面这句话，"
    "语速适中偏慢、吐字清晰，语气礼貌友善，"
    "像普通外卖骑手在跟顾客说话，不要播音腔，不要机械朗读。"
)

# ── 数字读法规则 ───────────────────────────────────────────
# 1 位：正常读（5→五）
# 2 位：连起来读（38→三十八、18→十八、25→二十五、10→十）
# 3 位及以上：逐位读（602→六零二、5025→五零二五、1203→一二零三）
# 前导 0 的号码（如 02、005）逐位读，避免丢 0
_CN_DIGITS = "零一二三四五六七八九"


def _read_two(s):
    n = int(s)
    if n < 10:
        return _CN_DIGITS[n]
    tens, ones = divmod(n, 10)
    if tens == 1:
        return "十" + (_CN_DIGITS[ones] if ones else "")
    return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")


def digits_individual(text):
    def repl(m):
        s = m.group(0)
        if len(s) == 1:
            return _CN_DIGITS[int(s)]
        if len(s) == 2 and s[0] != "0":
            return _read_two(s)
        return "".join(_CN_DIGITS[int(c)] for c in s)
    return re.sub(r"\d+", repl, text)

# 简单进程内缓存：text+voice -> wav bytes（前端已有客户端缓存，这里兜底提速）
_cache = {}
_cache_lock = threading.Lock()
CACHE_MAX = 200

HERE = os.path.dirname(os.path.abspath(__file__))


def synth(text, voice=VOICE):
    """调用 MiMo TTS，返回 wav 字节。"""
    text = digits_individual(text)
    body = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": STYLE},
            {"role": "assistant", "content": text},
        ],
        "audio": {"format": "wav", "voice": voice},
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "api-key": MIMO_API_KEY,
            "Authorization": "Bearer " + MIMO_API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        j = json.loads(resp.read().decode("utf-8"))
    audio = j["choices"][0]["message"]["audio"]["data"]
    return base64.b64decode(audio)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, relpath):
        """从脚本目录安全地返回静态文件内容与类型。"""
        safe = os.path.normpath(relpath.lstrip("/\\"))
        full = os.path.join(HERE, safe)
        if not os.path.abspath(full).startswith(HERE) or not os.path.isfile(full):
            return None, None
        with open(full, "rb") as f:
            content = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        return content, ctype

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)

        # 健康检查：前端据此决定是否启用服务端 TTS / AI
        if url.path == "/api/health":
            self._send(200, json.dumps({
                "ai": False,
                "tts": bool(MIMO_API_KEY),
            }).encode())
            return

        # TTS 合成
        if url.path == "/api/tts":
            if not MIMO_API_KEY:
                self._send(503, json.dumps({"error": "MIMO_API_KEY 未配置"}).encode())
                return
            text = (q.get("text", [""])[0] or "").strip()
            if not text:
                self._send(400, json.dumps({"error": "empty text"}).encode())
                return
            voice = (q.get("voice", [VOICE])[0] or "").strip() or VOICE
            cache_key = voice + "\x00" + text
            with _cache_lock:
                hit = _cache.get(cache_key)
            if hit is not None:
                self._send(200, hit, "audio/wav")
                return
            try:
                wav = synth(text, voice)
            except Exception as e:  # noqa: BLE001
                self._send(502, json.dumps({"error": str(e)}).encode())
                return
            with _cache_lock:
                if len(_cache) < CACHE_MAX:
                    _cache[cache_key] = wav
            self._send(200, wav, "audio/wav")
            return

        # 静态文件（首页 + manifest + 图标）
        rel = url.path if url.path not in ("/", "") else "/index.html"
        content, ctype = self._file(rel)
        if content is None:
            self._send(404, json.dumps({"error": "not found"}).encode())
            return
        self._send(200, content, ctype)


def run_test(text):
    """离线合成一条样例，存成 sample.wav，用于快速听效果。"""
    if not MIMO_API_KEY:
        print("请先设置环境变量 MIMO_API_KEY（在 platform.xiaomimimo.com 控制台获取）")
        sys.exit(1)
    print("音色：%s | 模型：%s" % (VOICE, MODEL))
    print("合成中：%s" % text)
    wav = synth(text, VOICE)
    out = os.path.join(HERE, "sample.wav")
    with open(out, "wb") as f:
        f.write(wav)
    print("已保存：%s" % out)


if __name__ == "__main__":
    if "--test" in sys.argv:
        i = sys.argv.index("--test")
        text = sys.argv[i + 1] if i + 1 < len(sys.argv) else "您好，我是送外卖的，麻烦开一下门"
        run_test(text)
        sys.exit(0)

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("译路畅通 TTS 后端已启动： http://%s:%d" % (HOST, PORT))
    print("音色：%s | 模型：%s | API Key：%s" % (
        VOICE, MODEL, "已配置" if MIMO_API_KEY else "未配置（前端将回退浏览器语音）"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.server_close()
