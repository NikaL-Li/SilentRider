#!/usr/bin/env python3
# 独立 OCR 服务：跑在性能更好的 Mac / Linux 上，供云服务器远程调用。
# 只监听 127.0.0.1，通过 Cloudflare Tunnel / SSH 反向隧道暴露，不要直接公网开放。
import hmac
import json
import os
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 8 * 1024 * 1024
HOST = os.environ.get("OCR_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.environ.get("OCR_PORT", "9000") or "9000")
BASE = os.path.dirname(os.path.abspath(__file__))


def load_token():
    token = os.environ.get("OCR_TOKEN", "").strip()
    if token:
        return token
    path = os.path.join(BASE, "ocr_token.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        token = ""
    if not token:
        token = secrets.token_urlsafe(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(token + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return token


TOKEN = load_token()
from rapidocr_onnxruntime import RapidOCR  # noqa: E402

ENGINE = RapidOCR()


def image_magic_ok(data):
    return (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def ocr_file(path):
    started = time.time()
    result, _ = ENGINE(path)
    lines = []
    for item in (result or []):
        try:
            text = str(item[1]).strip()
        except Exception:
            continue
        if text:
            lines.append(text)
    return "\n".join(lines), round(time.time() - started, 2)


class Handler(BaseHTTPRequestHandler):
    server_version = "YLCT-OCR/1.0"

    def _json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authorized(self):
        auth = self.headers.get("Authorization", "") or ""
        return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), TOKEN)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/health":
            self._json(200, {"ok": True, "engine": "rapidocr", "service": "ylct-ocr"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/ocr":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BYTES:
            self._json(413, {"error": "image too large or empty"})
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            self._json(415, {"error": "only image/* accepted"})
            return
        data = self.rfile.read(length)
        if not image_magic_ok(data):
            self._json(400, {"error": "bad image"})
            return
        suffix = ".png" if "png" in ctype else (".webp" if "webp" in ctype else ".jpg")
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(data)
                path = f.name
            text, seconds = ocr_file(path)
            self._json(200, {"text": text, "chars": len(text), "seconds": seconds, "engine": "rapidocr"})
        except Exception as exc:  # noqa: BLE001
            print("[OCR-SERVICE] failed: %r" % (exc,), flush=True)
            self._json(500, {"error": "ocr failed"})
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def log_message(self, fmt, *args):
        return


def main():
    print("YLCT OCR service on http://%s:%d" % (HOST, PORT), flush=True)
    print("token file: %s/ocr_token.txt" % BASE, flush=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
