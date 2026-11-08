#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
译路畅通 · 后端（TTS + 账号 + 地址簿 + 送餐记录）
================================================

零第三方依赖，Windows / macOS / Ubuntu 都能直接跑。

功能
----
1. MiMo TTS：把页面上的句子合成自然中文语音（Key 只放在服务端）。
2. 账号系统：手机号 + 密码注册 / 登录，可选短信验证码登录；
   会话用 HttpOnly Cookie，密码 PBKDF2 加盐哈希，登录限流。
3. 地址簿：登录后把「送餐地址 / 取餐点」保存到云端，
   同一小区再填时前端可按上次自动补全。
4. 送餐记录：登录后按账号存订单，游客仍用浏览器本地存储。
5. 语音外呼：可接一个自建/云端语音网关（webhook），
   由服务器打电话给顾客并朗读文本；未配置时前端只用免提播报。
6. 部署：HOST / PORT / 数据目录 / Cookie 安全策略全部可用环境变量配置，
   反向代理（Nginx）下自动识别 https。

环境变量
--------
MIMO_API_KEY         MiMo TTS 的 API Key（不配置则前端回退浏览器语音）
MIMO_VOICE           默认音色：冰糖 / 茉莉 / 苏打 / 白桦
YLCT_HOST            监听地址，默认 127.0.0.1；前面有 Nginx 时保持 127.0.0.1
YLCT_PORT            监听端口，默认 8818
YLCT_DATA_DIR        数据目录，默认 ./data
YLCT_DB              SQLite 路径，默认 <数据目录>/app.db
YLCT_COOKIE_SECURE   auto / 1 / 0，默认 auto（反向代理传 X-Forwarded-Proto: https 时自动 Secure）
YLCT_TRUST_PROXY     1 时信任 X-Forwarded-For / X-Forwarded-Proto
YLCT_SMS_PROVIDER    none / console / webhook，默认 none
YLCT_SMS_WEBHOOK     短信 webhook 地址（POST {phone, code, text}）
YLCT_SMS_TOKEN       短信 webhook 的 Bearer Token
YLCT_SMS_DEBUG       1 时 /api/auth/sms/send 会把验证码返回给前端（只在本机演示用！）
YLCT_CALL_WEBHOOK    语音外呼网关地址（POST {phone, text}）
YLCT_CALL_TOKEN      语音外呼网关的 Bearer Token
YLCT_CALL_REQUIRE_LOGIN  1 时外呼必须登录，默认 0（游客也可用，但有严格限流）

启动
----
    python3 server.py
    python3 server.py --host 127.0.0.1 --port 8818
    python3 server.py --test "您好，我是送外卖的"

零第三方依赖：只用 Python 标准库。
"""

import os
import sys
import json
import re
import time
import hmac
import gzip
import base64
import sqlite3
import secrets
import hashlib
import threading
import mimetypes
import shutil
import io
import random
import socket
import subprocess
import concurrent.futures
import tempfile
import html as html_lib
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


# ────────────────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────────────────
def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    try:
        return int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


HOST = os.environ.get("YLCT_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _env_int("YLCT_PORT", 8818)

DATA_DIR = os.path.abspath(
    os.environ.get("YLCT_DATA_DIR", "").strip() or os.path.join(HERE, "data")
)
DB_PATH = os.path.abspath(
    os.environ.get("YLCT_DB", "").strip() or os.path.join(DATA_DIR, "app.db")
)

COOKIE_NAME = "ylct_sid"
COOKIE_SECURE_MODE = (os.environ.get("YLCT_COOKIE_SECURE", "auto").strip().lower() or "auto")
TRUST_PROXY = _env_bool("YLCT_TRUST_PROXY", True)

SMS_PROVIDER = (os.environ.get("YLCT_SMS_PROVIDER", "none").strip().lower() or "none")
SMS_WEBHOOK = os.environ.get("YLCT_SMS_WEBHOOK", "").strip()
SMS_TOKEN = os.environ.get("YLCT_SMS_TOKEN", "").strip()
SMS_DEBUG = _env_bool("YLCT_SMS_DEBUG", False)

CALL_WEBHOOK = os.environ.get("YLCT_CALL_WEBHOOK", "").strip()
CALL_TOKEN = os.environ.get("YLCT_CALL_TOKEN", "").strip()
CALL_REQUIRE_LOGIN = _env_bool("YLCT_CALL_REQUIRE_LOGIN", False)

# ── 高德 / OCR / 壁纸 ──────────────────────────────────────
AMAP_KEY = os.environ.get("AMAP_KEY", "").strip()
AMAP_BASE = "https://restapi.amap.com/v3"
AMAP_TIMEOUT = 7
GEO_CACHE_TTL = 600
WALLPAPER_CACHE_TTL = 86400
MAX_OCR_BYTES = 8 * 1024 * 1024
OCR_LANG = os.environ.get("YLCT_OCR_LANG", "chi_sim+eng").strip() or "chi_sim+eng"
OCR_REMOTE_URL = os.environ.get("OCR_REMOTE_URL", "").strip()
OCR_REMOTE_TOKEN = os.environ.get("OCR_REMOTE_TOKEN", "").strip()
OCR_REMOTE_TIMEOUT = _env_int("OCR_REMOTE_TIMEOUT", 4)

# ── 腾讯云 OCR（TC3 直连，无需 SDK） ──────────────────────
TENCENT_SECRET_ID = os.environ.get("TENCENT_SECRET_ID", "").strip()
TENCENT_SECRET_KEY = os.environ.get("TENCENT_SECRET_KEY", "").strip()
TENCENT_REGION = os.environ.get("TENCENT_REGION", "ap-guangzhou").strip() or "ap-guangzhou"
OCR_PROVIDER = (os.environ.get("OCR_PROVIDER", "auto").strip().lower() or "auto")
_TENCENT_BLOCK_UNTIL = 0.0
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ── MiMo TTS ────────────────────────────────────────────────
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "").strip()
BASE_URL = "https://api.xiaomimimo.com/v1"
MODEL = "mimo-v2.5-tts"
VOICE = os.environ.get("MIMO_VOICE", "冰糖").strip() or "冰糖"
VOICES = ("冰糖", "茉莉", "苏打", "白桦")

STYLE = (
    "用自然、温和、亲切的普通话口语朗读下面这句话，"
    "语速适中偏慢、吐字清晰，语气礼貌友善，"
    "像普通外卖骑手在跟顾客说话，不要播音腔，不要机械朗读。"
)

SESSION_DAYS = 30
SESSION_DAYS_REMEMBER = 90
MAX_BODY = 64 * 1024
TTS_MAX_TEXT = 200
TTS_RATE_GUEST = 60           # 游客每 IP 每小时
TTS_RATE_USER = 300           # 登录用户每小时
TTS_GLOBAL_RATE = 1500        # 全站每小时上限，兜底成本
CALL_RATE_IP = 10             # 每 IP 每小时
CALL_RATE_PHONE = 3           # 每手机号每小时


# ────────────────────────────────────────────────────────────
# 数字读法（原逻辑保留）
# ────────────────────────────────────────────────────────────
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


_tts_cache = {}
_tts_lock = threading.Lock()
TTS_CACHE_MAX = 400


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


# ────────────────────────────────────────────────────────────
# 工具
# ────────────────────────────────────────────────────────────
def now_ts():
    return int(time.time() * 1000)


def sha256_hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def valid_phone(p):
    return bool(re.fullmatch(r"1[3-9]\d{9}", p or ""))


def normalize_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    return digits


def valid_day(s):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def valid_hm(s):
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s or ""))


def mask_phone(p):
    p = str(p or "")
    return p[:3] + "****" + p[-4:] if len(p) >= 11 else p


def now_utc_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_nickname(phone):
    return "骑手" + (phone[-4:] if len(phone) >= 4 else phone)


def hash_password(password):
    """PBKDF2-HMAC-SHA256，格式：pbkdf2_sha256$迭代次数$盐$哈希"""
    iterations = 210_000
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, salt, dk.hex())


def verify_password(password, stored):
    try:
        algo, iters, salt, digest = str(stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def code_hash(phone, code):
    salt = hashlib.sha256((phone + "|" + str(code)).encode()).hexdigest()[:16]
    return salt + "$" + hashlib.sha256((salt + "|" + phone + "|" + code).encode()).hexdigest()


def verify_code_hash(phone, code, stored):
    try:
        salt, digest = stored.split("$", 1)
        calc = hashlib.sha256((salt + "|" + phone + "|" + code).encode()).hexdigest()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


class RateLimiter:
    """进程内滑动窗口限流，够单机用；多实例部署请换 Redis。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.hits = {}
        self.last_prune = time.time()

    def allow(self, key, limit, window):
        now = time.time()
        with self.lock:
            q = [t for t in self.hits.get(key, []) if now - t < window]
            if len(q) >= limit:
                self.hits[key] = q
                return False, int(window - (now - q[0])) + 1
            q.append(now)
            self.hits[key] = q
            if now - self.last_prune > 300:
                for k in list(self.hits.keys()):
                    if not self.hits[k] or now - self.hits[k][-1] > 3600:
                        del self.hits[k]
                self.last_prune = now
            return True, 0


LIMITER = RateLimiter()


# ────────────────────────────────────────────────────────────
# 数据库
# ────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    phone         TEXT NOT NULL UNIQUE,
    nickname      TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    expires_at   INTEGER NOT NULL,
    ua           TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS deliver_places (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    place_key    TEXT NOT NULL,
    place        TEXT NOT NULL,
    building     TEXT NOT NULL DEFAULT '',
    room         TEXT NOT NULL DEFAULT '',
    floor        INTEGER NOT NULL DEFAULT 0,
    last_used_at INTEGER NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, place_key)
);

CREATE TABLE IF NOT EXISTS pickup_places (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shop_key     TEXT NOT NULL,
    shop         TEXT NOT NULL,
    phone        TEXT NOT NULL DEFAULT '',
    code         TEXT NOT NULL DEFAULT '',
    last_used_at INTEGER NOT NULL,
    use_count    INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, shop_key)
);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id   TEXT NOT NULL,
    day        TEXT NOT NULL,
    hm         TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT 'deliver',
    place      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user_day ON orders(user_id, day);

CREATE TABLE IF NOT EXISTS sms_codes (
    phone      TEXT PRIMARY KEY,
    code_hash  TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    sent_at    INTEGER NOT NULL,
    tries      INTEGER NOT NULL DEFAULT 0
);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or DATA_DIR, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def user_payload(row):
    return {
        "id": row["id"],
        "phone": row["phone"],
        "phoneMask": mask_phone(row["phone"]),
        "nickname": row["nickname"] or default_nickname(row["phone"]),
        "createdAt": row["created_at"],
        "hasPassword": bool(row["password_hash"]),
    }


# ────────────────────────────────────────────────────────────
# 账号 / 会话
# ────────────────────────────────────────────────────────────
def create_session(user_id, remember, ua=""):
    token = secrets.token_urlsafe(36)
    days = SESSION_DAYS_REMEMBER if remember else SESSION_DAYS
    expires = int(time.time()) + days * 86400
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, ua) VALUES(?,?,?,?,?)",
            (sha256_hex(token), user_id, now_utc_iso(), expires, (ua or "")[:200]),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        conn.commit()
    finally:
        conn.close()
    # Cookie 的 Max-Age 要的是“秒数”，不是过期时间戳
    return token, days * 86400


def find_session_user(token):
    if not token:
        return None
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (sha256_hex(token), int(time.time())),
        ).fetchone()
        return row
    finally:
        conn.close()


def create_user(phone, password, nickname):
    conn = db_connect()
    try:
        ts = now_utc_iso()
        cur = conn.execute(
            "INSERT INTO users(phone, nickname, password_hash, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            (phone, (nickname or "").strip()[:20] or default_nickname(phone),
             hash_password(password) if password else None, ts, ts),
        )
        conn.commit()
        uid = cur.lastrowid
        return conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()


def get_user_by_phone(phone):
    conn = db_connect()
    try:
        return conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────
# 短信验证码
# ────────────────────────────────────────────────────────────
def sms_enabled():
    if SMS_PROVIDER == "console":
        return True
    if SMS_PROVIDER == "webhook":
        return bool(SMS_WEBHOOK)
    return False


def send_sms(phone, code):
    text = "【译路畅通】验证码 %s，5 分钟内有效。请勿告诉他人。" % code
    if SMS_PROVIDER == "console":
        # 演示模式：验证码只打印在服务器控制台，不返回给页面（除非 SMS_DEBUG=1）
        print("[SMS] %s -> %s" % (mask_phone(phone), text), flush=True)
        return
    if SMS_PROVIDER == "webhook":
        data = json.dumps({"phone": phone, "code": code, "text": text}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if SMS_TOKEN:
            headers["Authorization"] = "Bearer " + SMS_TOKEN
        req = urllib.request.Request(SMS_WEBHOOK, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                raise RuntimeError("sms webhook status %s" % resp.status)
        return
    raise RuntimeError("短信服务未配置")


def issue_sms_code(phone):
    code = "%06d" % secrets.randbelow(1000000)
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO sms_codes(phone, code_hash, expires_at, sent_at, tries) VALUES(?,?,?,?,0) "
            "ON CONFLICT(phone) DO UPDATE SET code_hash=excluded.code_hash, "
            "expires_at=excluded.expires_at, sent_at=excluded.sent_at, tries=0",
            (phone, code_hash(phone, code), int(time.time()) + 300, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()
    send_sms(phone, code)
    return code


def consume_sms_code(phone, code):
    conn = db_connect()
    try:
        row = conn.execute("SELECT * FROM sms_codes WHERE phone = ?", (phone,)).fetchone()
        if not row:
            return False, "验证码已过期，请重新获取"
        if row["expires_at"] < int(time.time()):
            conn.execute("DELETE FROM sms_codes WHERE phone = ?", (phone,))
            conn.commit()
            return False, "验证码已过期，请重新获取"
        if row["tries"] >= 5:
            conn.execute("DELETE FROM sms_codes WHERE phone = ?", (phone,))
            conn.commit()
            return False, "错误次数太多，请重新获取"
        if not verify_code_hash(phone, code, row["code_hash"]):
            conn.execute("UPDATE sms_codes SET tries=tries+1 WHERE phone=?", (phone,))
            conn.commit()
            return False, "验证码不正确"
        conn.execute("DELETE FROM sms_codes WHERE phone = ?", (phone,))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────
# 地址簿 / 订单
# ────────────────────────────────────────────────────────────
def get_addrbook(uid):
    conn = db_connect()
    try:
        deliver = conn.execute(
            "SELECT place, building AS b, room AS r, floor, last_used_at AS last, "
            "use_count AS count FROM deliver_places WHERE user_id = ? "
            "ORDER BY last_used_at DESC LIMIT 30",
            (uid,),
        ).fetchall()
        pickup = conn.execute(
            "SELECT shop, phone, code, last_used_at AS last, use_count AS count "
            "FROM pickup_places WHERE user_id = ? ORDER BY last_used_at DESC LIMIT 30",
            (uid,),
        ).fetchall()
        return {
            "deliver": [dict(x) for x in deliver],
            "pickup": [dict(x) for x in pickup],
        }
    finally:
        conn.close()


def save_deliver(uid, place, building, room, floor):
    key = re.sub(r"\s+", "", place)[:60]
    if not key:
        return
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO deliver_places(user_id, place_key, place, building, room, floor, last_used_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, place_key) DO UPDATE SET "
            "place=excluded.place, building=excluded.building, room=excluded.room, "
            "floor=excluded.floor, last_used_at=excluded.last_used_at, use_count=use_count+1",
            (uid, key, place.strip()[:40], (building or "")[:40], (room or "")[:20],
             int(floor or 0), now_ts()),
        )
        conn.commit()
    finally:
        conn.close()


def save_pickup(uid, shop, phone, code):
    key = re.sub(r"\s+", "", shop)[:60]
    if not key:
        return
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO pickup_places(user_id, shop_key, shop, phone, code, last_used_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(user_id, shop_key) DO UPDATE SET "
            "shop=excluded.shop, phone=excluded.phone, code=excluded.code, "
            "last_used_at=excluded.last_used_at, use_count=use_count+1",
            (uid, key, shop.strip()[:60], (phone or "")[:20], (code or "")[:20], now_ts()),
        )
        conn.commit()
    finally:
        conn.close()


def get_orders(uid, days=31):
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT day, COUNT(*) AS count, MIN(hm) AS first, MAX(hm) AS last "
            "FROM orders WHERE user_id = ? AND day >= ? GROUP BY day ORDER BY day DESC LIMIT ?",
            (uid, (datetime.now() - timedelta(days=max(1, min(days, 366)))).strftime("%Y-%m-%d"), days),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (uid,)).fetchone()[0]
        return {"days": {r["day"]: {"count": r["count"], "first": r["first"], "last": r["last"]} for r in rows},
                "total": total}
    finally:
        conn.close()


def add_order(uid, event_id, day, hm, mode="deliver", place=""):
    conn = db_connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO orders(user_id, event_id, day, hm, mode, place, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, event_id, day, hm, (mode or "deliver")[:12], (place or "")[:80], now_utc_iso()),
        )
        conn.commit()
        inserted = cur.rowcount > 0
        row = conn.execute(
            "SELECT COUNT(*) AS count, MIN(hm) AS first, MAX(hm) AS last FROM orders "
            "WHERE user_id = ? AND day = ?",
            (uid, day),
        ).fetchone()
        return inserted, {"count": row["count"], "first": row["first"], "last": row["last"]}
    finally:
        conn.close()


def import_orders(uid, days, import_id):
    """把游客本机记录迁移到账号。用 event_id 幂等，重复登录不会重复计数。"""
    merged = 0
    cap = 2000
    conn = db_connect()
    try:
        for item in days[:62]:
            if merged >= cap:
                break
            day = str(item.get("day") or "")
            if not valid_day(day):
                continue
            try:
                count = int(item.get("count") or 0)
            except (TypeError, ValueError):
                continue
            count = max(0, min(count, 500, cap - merged))
            first = str(item.get("first") or "00:00")[:5]
            last = str(item.get("last") or first)[:5]
            if not valid_hm(first):
                first = "00:00"
            if not valid_hm(last):
                last = first
            for i in range(count):
                ev = "legacy:%s:%s:%d" % (import_id[:40], day, i)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO orders(user_id, event_id, day, hm, mode, place, created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (uid, ev, day, first if i == 0 else last, "legacy", "", now_utc_iso()),
                )
                merged += 1 if cur.rowcount > 0 else 0
        conn.commit()
        return merged
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────
# 高德定位 / 天气 / 壁纸 / OCR
# ────────────────────────────────────────────────────────────
_geo_cache = {}
_geo_cache_lock = threading.Lock()
_wall_sets_cache = {}
_wall_image_cache = {}
_wall_cache_lock = threading.Lock()


def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "application/json,text/plain,*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _http_text(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Accept": "text/html,application/xhtml+xml,*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(3_000_000)
    return raw.decode("utf-8", "replace")


def _amap(path, params):
    if not AMAP_KEY:
        raise RuntimeError("AMAP_KEY 未配置")
    p = dict(params or {})
    p["key"] = AMAP_KEY
    url = AMAP_BASE + "/" + path + "?" + urllib.parse.urlencode(p)
    j = _http_json(url, timeout=AMAP_TIMEOUT)
    if str(j.get("status")) != "1":
        raise RuntimeError(j.get("info") or "amap error")
    return j


def _as_text(v):
    return v if isinstance(v, str) else ""


def _is_public_ip(ip):
    try:
        a = ipaddress.ip_address(str(ip).strip())
        return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast)
    except ValueError:
        return False


def _weather_by_adcode(adcode, city_name=""):
    key = str(adcode or city_name or "370100")
    now = time.time()
    with _geo_cache_lock:
        hit = _geo_cache.get(key)
        if hit and now - hit[0] < GEO_CACHE_TTL:
            return hit[1], hit[2]
    live_j = _amap("weather/weatherInfo", {"city": key, "extensions": "base"})
    all_j = _amap("weather/weatherInfo", {"city": key, "extensions": "all"})
    lives = live_j.get("lives") or []
    forecasts = all_j.get("forecasts") or []
    casts = (forecasts[0].get("casts") if forecasts else []) or []
    live = lives[0] if lives else {}
    first = casts[0] if casts else {}
    text = _as_text(live.get("weather")) or _as_text(first.get("dayweather")) or "多云"
    temp = _as_text(live.get("temperature")) or _as_text(first.get("daytemp")) or "--"
    humidity = _as_text(live.get("humidity")) or "--"
    wind_dir = _as_text(live.get("winddirection"))
    wind_power = _as_text(live.get("windpower"))
    wind = ((wind_dir + "风 ") if wind_dir else "") + ((wind_power + "级") if wind_power else "")
    if not wind.strip():
        wind = "--"
    report = _as_text(live.get("reporttime")) or _as_text(forecasts[0].get("reporttime") if forecasts else "")
    fc = []
    for c in casts[:4]:
        fc.append({
            "date": _as_text(c.get("date")),
            "dayWeather": _as_text(c.get("dayweather")) or "多云",
            "nightWeather": _as_text(c.get("nightweather")) or "",
            "dayTemp": _as_text(c.get("daytemp")) or "--",
            "nightTemp": _as_text(c.get("nighttemp")) or "--",
            "dayWind": _as_text(c.get("daywind")),
            "dayPower": _as_text(c.get("daypower")),
        })
    weather = {
        "temp": temp,
        "text": text,
        "humidity": humidity,
        "wind": wind.strip(),
        "reportTime": report,
        "forecast": fc,
    }
    with _geo_cache_lock:
        _geo_cache[key] = (now, weather, city_name)
    return weather, city_name


def _wttr_fallback():
    """高德不可用时的兜底：让 wttr 按服务器出口自动定位，不写死济南。"""
    try:
        j = _http_json("https://wttr.in/?format=j1", timeout=6)
        cur = (j.get("current_condition") or [{}])[0]
        nearest = (j.get("nearest_area") or [{}])[0]
        def val(name):
            arr = nearest.get(name) or []
            return _as_text((arr[0] or {}).get("value")) if arr else ""
        city = val("areaName") or "当前位置"
        province = val("region")
        code_map = {
            "113": "晴", "116": "多云", "119": "阴", "122": "阴",
            "143": "雾", "248": "雾", "260": "雾",
            "176": "阵雨", "263": "毛毛雨", "266": "毛毛雨",
            "293": "小雨", "296": "小雨", "299": "中雨", "302": "中雨",
            "305": "大雨", "308": "大雨", "353": "小阵雨", "356": "阵雨", "359": "大阵雨",
            "179": "阵雪", "182": "雨夹雪", "185": "冻雨", "227": "雪", "230": "暴雪",
            "323": "小雪", "326": "小雪", "329": "中雪", "332": "中雪",
            "335": "大雪", "338": "大雪", "200": "雷阵雨", "386": "雷阵雨", "389": "强雷雨",
        }
        fc = []
        for d in (j.get("weather") or [])[:4]:
            h = (d.get("hourly") or [{}])
            code = str((h[4] if len(h) > 4 else h[0]).get("weatherCode", "116"))
            fc.append({
                "date": _as_text(d.get("date")),
                "dayWeather": code_map.get(code, "多云"),
                "nightWeather": "",
                "dayTemp": _as_text(d.get("maxtempC")) or "--",
                "nightTemp": _as_text(d.get("mintempC")) or "--",
                "dayWind": "", "dayPower": "",
            })
        return {
            "ok": True, "source": "wttr", "city": city, "province": province,
            "district": "", "adcode": "", "location": {},
            "weather": {
                "temp": _as_text(cur.get("temp_C")) or "--",
                "text": code_map.get(str(cur.get("weatherCode", "116")), "多云"),
                "humidity": _as_text(cur.get("humidity")) or "--",
                "wind": (_as_text(cur.get("windspeedKmph")) + " km/h") if cur.get("windspeedKmph") else "--",
                "reportTime": "",
                "forecast": fc,
            },
        }
    except Exception:
        return {"ok": False, "error": "weather_unavailable"}

def get_geo(ip, lat=None, lng=None):
    try:
        if lat is not None and lng is not None:
            j = _amap("geocode/regeo", {
                "location": "%.6f,%.6f" % (float(lng), float(lat)),
                "extensions": "base",
            })
            comp = (j.get("regeocode") or {}).get("addressComponent") or {}
            city = _as_text(comp.get("city")) or _as_text(comp.get("province")) or "济南市"
            province = _as_text(comp.get("province"))
            district = _as_text(comp.get("district"))
            adcode = _as_text(comp.get("adcode")) or "370100"
            loc = {"lng": float(lng), "lat": float(lat)}
        else:
            params = {}
            # 传入真实客户端 IP，避免拿到服务器出口 IP（北京）
            if ip and _is_public_ip(ip):
                params["ip"] = ip
            j = _amap("ip", params)
            city = _as_text(j.get("city")) or _as_text(j.get("province")) or "济南市"
            province = _as_text(j.get("province"))
            district = ""
            adcode = _as_text(j.get("adcode")) or "370100"
            loc = {}
        weather, cname = _weather_by_adcode(adcode, city)
        return {
            "ok": True,
            "source": "amap",
            "city": cname or city,
            "province": province,
            "district": district,
            "adcode": adcode,
            "location": loc,
            "weather": weather,
        }
    except Exception as exc:
        print("[GEO] amap failed: %r" % (exc,), flush=True)
        fb = _wttr_fallback()
        if not fb.get("ok"):
            return fb
        return fb


def _image_magic_ok(data):
    return (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")
            or data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _safe_public_host(host):
    if not host or host.lower() in ("localhost", "localhost.localdomain"):
        return False
    try:
        ipaddress.ip_address(host)
        return _is_public_ip(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = info[4][0]
            if not _is_public_ip(ip):
                return False
        return bool(infos)
    except Exception:
        return False


def _download_image(url, timeout=12):
    try:
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not _safe_public_host(u.hostname):
            return None, None
        req = urllib.request.Request(url, headers={
            "User-Agent": BROWSER_UA,
            "Referer": "https://cn.bing.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_host = urllib.parse.urlparse(resp.geturl()).hostname
            if final_host and not _safe_public_host(final_host):
                return None, None
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and not ctype.startswith("image/"):
                return None, None
            data = resp.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024 or not _image_magic_ok(data):
                return None, None
            if not ctype:
                ctype = "image/jpeg"
            return data, ctype
    except Exception:
        return None, None


def _wall_day():
    """按北京时间分天，跨天自动换一组壁纸。"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _bing_image_urls(city):
    queries = [
        city + " 城市 风景 壁纸",
        city + " 风景",
        city + " 地标",
        city + " 城市",
    ]
    all_urls = []
    seen = set()
    for query in queries:
        try:
            url = "https://cn.bing.com/images/async?" + urllib.parse.urlencode({
                "q": query, "first": 0, "count": 24, "adlt": "off", "mkt": "zh-CN",
            })
            raw = _http_text(url, timeout=8)
        except Exception:
            continue
        for x in re.findall(r"murl&quot;:&quot;(.*?)&quot;", raw):
            u = html_lib.unescape(x)
            if u not in seen:
                seen.add(u)
                all_urls.append(u)
        if len(all_urls) >= 24:
            break
    return all_urls


def _resize_image(data, max_w=1400):
    """有 Pillow 就压到适合手机首屏的尺寸；没有就原图返回。"""
    try:
        from PIL import Image
    except Exception:
        return data, None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((max_w, max_w * 2), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return data, None


def _bing_daily(idx=0):
    try:
        j = _http_json(
            "https://www.bing.com/HPImageArchive.aspx?format=js&idx=%d&n=1&mkt=zh-CN" % int(idx),
            timeout=8,
        )
        img = ((j.get("images") or [{}])[0].get("url") or "")
        if img.startswith("/"):
            img = "https://www.bing.com" + img
        return _download_image(img, timeout=12)
    except Exception:
        return None, None


def _wallpaper_candidates(city):
    bad_hosts = ("tripomatic.com", "699pic.com", "hdslb.com", "zhimg.com", "qunarzz.com")
    good_hosts = ("cdn.pixabay.com", "images.pexels.com", "images.unsplash.com", "upload.wikimedia.org")
    ranked = []
    for u in _bing_image_urls(city):
        p = urllib.parse.urlparse(u)
        host = (p.hostname or "").lower()
        if not host or any(b in host for b in bad_hosts):
            continue
        score = 10 if any(g in host for g in good_hosts) else (5 if "bing.net" in host else 0)
        ranked.append((-score, u))
    ranked.sort()
    return [u for _, u in ranked]


def _bing_daily_urls(n=5):
    try:
        j = _http_json(
            "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=%d&mkt=zh-CN" % int(n),
            timeout=8,
        )
        out = []
        for item in (j.get("images") or []):
            u = item.get("url") or ""
            if u.startswith("/"):
                u = "https://www.bing.com" + u
            if u:
                out.append(u)
        return out
    except Exception:
        return []


def get_wallpaper_candidates(city, day=None):
    """当天该城市的候选壁纸 URL，只查列表，不提前下载；首屏更快。"""
    city = (city or "").strip()[:20]
    day = day or _wall_day()
    if not city:
        return []
    key = (city, day)
    now = time.time()
    with _wall_cache_lock:
        hit = _wall_sets_cache.get(key)
        if hit and now - hit[0] < WALLPAPER_CACHE_TTL:
            return hit[1]
    ranked = _wallpaper_candidates(city)
    rng = random.Random(hashlib.sha1((city + "|" + day).encode("utf-8")).hexdigest())
    top = ranked[:16]
    rng.shuffle(top)
    ordered = top + ranked[16:]
    seen = set()
    urls = []
    for u in ordered:
        if u in seen:
            continue
        seen.add(u)
        urls.append(u)
        if len(urls) >= 8:
            break
    # 候选太少时用 Bing 每日图补足
    if len(urls) < 5:
        for u in _bing_daily_urls(5):
            if u not in seen:
                seen.add(u)
                urls.append(u)
            if len(urls) >= 5:
                break
    with _wall_cache_lock:
        _wall_sets_cache[key] = (now, urls)
    return urls


def get_wallpaper(city, idx=0, day=None):
    city = (city or "").strip()[:20]
    day = day or _wall_day()
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 0
    candidates = get_wallpaper_candidates(city, day)
    if not candidates:
        return None, None
    key = (city, day, idx)
    now = time.time()
    with _wall_cache_lock:
        hit = _wall_image_cache.get(key)
        if hit and now - hit[0] < WALLPAPER_CACHE_TTL:
            return hit[1], hit[2]
    # 当前 idx 失败时，依次尝试后面的候选
    for offset in range(min(5, len(candidates))):
        u = candidates[(idx + offset) % len(candidates)]
        data, ctype = _download_image(u)
        if not data:
            continue
        data, new_type = _resize_image(data)
        ctype = new_type or ctype or "image/jpeg"
        with _wall_cache_lock:
            _wall_image_cache[key] = (now, data, ctype)
        return data, ctype
    return None, None


class CloudOcrError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code or ""


def _tencent_ocr(data):
    """腾讯云 GeneralAccurateOCR，TC3-HMAC-SHA256 直连。"""
    if not (TENCENT_SECRET_ID and TENCENT_SECRET_KEY):
        raise CloudOcrError("not_configured", "腾讯云 OCR 未配置")
    service = "ocr"
    host = "ocr.tencentcloudapi.com"
    action = "RecognizeAgent"   # 通用文字识别Agent，对应 1000 页免费资源包
    version = "2018-11-19"
    ct = "application/json; charset=utf-8"
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    payload = json.dumps({
        "QueryType": 0,          # 全文识别
        "SelectModel": 0,        # 推理模型
        "ImageBase64": base64.b64encode(data).decode("ascii"),
    }, ensure_ascii=False, separators=(",", ":"))
    canonical_headers = "content-type:%s\nhost:%s\nx-tc-action:%s\n" % (ct, host, action.lower())
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = ("POST\n/\n\n" + canonical_headers + "\n" +
                         signed_headers + "\n" + hashed_payload)
    scope = date + "/" + service + "/tc3_request"
    string_to_sign = ("TC3-HMAC-SHA256\n%d\n%s\n%s" %
                      (ts, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()))

    def sign(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = sign(("TC3" + TENCENT_SECRET_KEY).encode("utf-8"), date)
    secret_service = sign(secret_date, service)
    secret_signing = sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = ("TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" %
            (TENCENT_SECRET_ID, scope, signed_headers, signature))
    req = urllib.request.Request("https://" + host, data=payload.encode("utf-8"), method="POST", headers={
        "Authorization": auth,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(ts),
        "X-TC-Version": version,
        "X-TC-Region": TENCENT_REGION,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            j = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            j = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            raise CloudOcrError("http_%d" % exc.code, "腾讯云 OCR HTTP %d" % exc.code)
    except Exception as exc:
        raise CloudOcrError("network", "腾讯云 OCR 网络错误: %r" % (exc,))
    out = j.get("Response") or {}
    err = out.get("Error") or {}
    if err:
        raise CloudOcrError(err.get("Code") or "", err.get("Message") or "腾讯云 OCR 失败")
    lines = []
    items = out.get("Response") or []
    if isinstance(items, dict):
        items = [items]
    for item in items:
        for det in (item.get("TextDetections") or []):
            t = str(det.get("DetectedText") or "").strip()
            if t:
                lines.append(t)
    # 兼容某些返回直接给 TextDetections 的情况
    for det in (out.get("TextDetections") or []):
        t = str(det.get("DetectedText") or "").strip()
        if t:
            lines.append(t)
    return "\n".join(lines)


def _remote_ocr(data, ctype):
    if not OCR_REMOTE_URL:
        return ""
    url = OCR_REMOTE_URL
    if not url.rstrip("/").endswith("/ocr"):
        url = url.rstrip("/") + "/ocr"
    headers = {
        "Content-Type": ctype or "image/png",
        "User-Agent": "YLCT-Server/2.0",
    }
    if OCR_REMOTE_TOKEN:
        headers["Authorization"] = "Bearer " + OCR_REMOTE_TOKEN
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=OCR_REMOTE_TIMEOUT) as resp:
        j = json.loads(resp.read().decode("utf-8", "replace"))
    return str(j.get("text") or "")


def _tesseract_text(path):
    best = ""
    for psm in ("6", "3"):
        try:
            proc = subprocess.run(
                ["tesseract", path, "stdout", "-l", OCR_LANG, "--psm", psm],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=25,
            )
        except Exception as exc:
            raise RuntimeError("OCR 执行失败: %s" % exc)
        txt = (proc.stdout or "").replace("\r", "").strip()
        if len(txt) > len(best):
            best = txt
        if len(best) >= 12:
            break
    return best


# ────────────────────────────────────────────────────────────
# HTTP Handler
# ────────────────────────────────────────────────────────────
STATIC_EXT = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".webmanifest",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".otf", ".txt", ".mp3", ".wav",
}
BLOCKED_PARTS = {"data", "deploy", "docs", "tests", ".git", ".dsh-uploads", "backup"}


class Handler(BaseHTTPRequestHandler):
    server_version = "YiLuChangTong/2.0"
    sys_version = ""

    # ── 基础 ────────────────────────────────────────────────
    def log_message(self, fmt, *args):
        try:
            msg = fmt % args
            # TTS 的 query 里是播报内容，可能含地址，日志里不落原文
            if "/api/tts?" in msg:
                msg = re.sub(r"/api/tts\?[^ \"\s]+", "/api/tts?<redacted>", msg)
            print("[%s] %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), self.client_ip(), msg), flush=True)
        except Exception:
            pass

    def _safe_path(self):
        path = self.path or ""
        if path.startswith("/api/tts"):
            return "/api/tts?<redacted>"
        return path

    def client_ip(self):
        peer = self.client_address[0] if self.client_address else "-"
        trusted = (
            TRUST_PROXY and (
                peer in ("127.0.0.1", "::1")
                or peer.startswith("::ffff:127.")
                or peer.startswith("10.")
                or peer.startswith("192.168.")
                or any(peer.startswith("172.%d." % n) for n in range(16, 32))
            )
        )
        if trusted:
            # Nginx 会把 X-Real-IP / X-Forwarded-For 覆盖成真实客户端地址
            xri = (self.headers.get("X-Real-IP", "") or "").strip()
            if xri:
                return xri[:64]
            xff = (self.headers.get("X-Forwarded-For", "") or "").strip()
            if xff:
                # 只取最后一跳，防止客户端自己塞第一跳
                return xff.split(",")[-1].strip()[:64]
        return peer

    def is_https(self):
        if COOKIE_SECURE_MODE in ("1", "true", "yes", "on"):
            return True
        if COOKIE_SECURE_MODE in ("0", "false", "no", "off"):
            return False
        if not TRUST_PROXY:
            return False
        return (self.headers.get("X-Forwarded-Proto", "") or "").lower() == "https"

    def _headers_common(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self' blob:; font-src 'self' data:; "
            "base-uri 'self'; form-action 'self'; object-src 'none'; frame-ancestors 'self'",
        )

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store",
              extra=None, gzip_ok=True):
        headers = {}
        if extra:
            headers.update(extra)
        if (gzip_ok and isinstance(body, (bytes, bytearray)) and len(body) > 1024
                and "gzip" in (self.headers.get("Accept-Encoding", "") or "").lower()
                and ctype.startswith(("text/", "application/json", "application/javascript",
                                      "application/manifest", "image/svg"))):
            body = gzip.compress(bytes(body), 5)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in headers.items():
            self.send_header(k, v)
        self._headers_common()
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, code, obj, extra=None):
        self._send(code, json_bytes(obj), extra=extra)

    def _err(self, code, message, error_code="", extra=None, headers=None):
        payload = {"error": message}
        if error_code:
            payload["code"] = error_code
        if extra:
            payload.update(extra)
        self._json(code, payload, extra=headers)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        ctype = (self.headers.get("Content-Type") or "").lower()
        if "application/json" not in ctype:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ── Cookie / 会话 ───────────────────────────────────────
    def get_cookie(self, name):
        raw = self.headers.get("Cookie", "") or ""
        for part in raw.split(";"):
            if "=" not in part:
                continue
            k, v = part.strip().split("=", 1)
            if k == name:
                return urllib.parse.unquote(v)
        return ""

    def session_cookie(self, token, max_age):
        secure = "; Secure" if self.is_https() else ""
        return (
            "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s"
            % (COOKIE_NAME, urllib.parse.quote(token), max_age, secure)
        )

    def current_user(self):
        token = self.get_cookie(COOKIE_NAME)
        if not token:
            auth = self.headers.get("Authorization", "") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        return find_session_user(token)

    def require_user(self):
        row = self.current_user()
        if not row:
            self._err(401, "请先登录", "login_required")
            return None
        return row

    # ── 路由 ────────────────────────────────────────────────
    def do_GET(self):
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            print("[ERROR] GET %s: %r" % (self._safe_path(), exc), flush=True)
            self._err(500, "服务器开小差了")

    def do_POST(self):
        try:
            self._do_post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            print("[ERROR] POST %s: %r" % (self._safe_path(), exc), flush=True)
            self._err(500, "服务器开小差了")

    def do_HEAD(self):
        try:
            self._do_get()
        except Exception:
            pass

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", gzip_ok=False)

    # ── GET ─────────────────────────────────────────────────
    def _do_get(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        path = url.path

        if path == "/api/health":
            self._json(200, {
                "ok": True,
                "ai": False,
                "tts": bool(MIMO_API_KEY),
                "auth": {"password": True, "sms": sms_enabled()},
                "call": bool(CALL_WEBHOOK),
                "amap": bool(AMAP_KEY),
                "ocr": bool(shutil.which("tesseract")),
                "ocrRemote": bool(OCR_REMOTE_URL),
                "ocrCloud": bool(TENCENT_SECRET_ID and TENCENT_SECRET_KEY),
                "ocrProvider": OCR_PROVIDER,
                "serverTime": now_utc_iso(),
            })
            return

        if path == "/api/auth/me":
            row = self.current_user()
            if not row:
                self._err(401, "未登录", "login_required")
                return
            self._json(200, {"user": user_payload(row)})
            return

        if path == "/api/addrbook":
            row = self.require_user()
            if not row:
                return
            self._json(200, get_addrbook(row["id"]))
            return

        if path == "/api/orders":
            row = self.require_user()
            if not row:
                return
            try:
                days = int((q.get("days") or ["31"])[0])
            except ValueError:
                days = 31
            self._json(200, get_orders(row["id"], days))
            return

        if path == "/api/tts":
            self._handle_tts(q)
            return

        if path == "/api/geo":
            self._geo_route(q)
            return

        if path == "/api/wallpapers":
            self._wallpapers_route(q)
            return

        if path == "/api/wallpaper":
            self._wallpaper_route(q)
            return

        if path in ("/", ""):
            path = "/index.html"
        self._serve_static(path)

    # ── POST ────────────────────────────────────────────────
    def _do_post(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path == "/api/ocr":
            self._ocr_route()
            return
        body = self._read_json()
        if body is None and path != "/api/auth/logout":
            self._err(400, "请求格式不对", "bad_request")
            return
        if body is None:
            body = {}
        if not isinstance(body, dict):
            self._err(400, "请求格式不对", "bad_request")
            return

        if path == "/api/auth/register":
            self._auth_register(body)
        elif path == "/api/auth/login":
            self._auth_login(body)
        elif path == "/api/auth/logout":
            self._auth_logout()
        elif path == "/api/auth/sms/send":
            self._auth_sms_send(body)
        elif path == "/api/auth/sms/login":
            self._auth_sms_login(body)
        elif path == "/api/auth/reset":
            self._auth_reset(body)
        elif path == "/api/auth/password":
            self._auth_password(body)
        elif path == "/api/addrbook/deliver":
            self._save_deliver_route(body)
        elif path == "/api/addrbook/pickup":
            self._save_pickup_route(body)
        elif path == "/api/orders":
            self._add_order_route(body)
        elif path == "/api/orders/import":
            self._import_orders_route(body)
        elif path == "/api/call":
            self._call_route(body)
        else:
            self._err(404, "接口不存在", "not_found")

    # ── 高德 / 壁纸 / OCR 接口 ───────────────────────────────
    def _geo_route(self, q):
        ip = self.client_ip()
        if not self._rate("geo:" + ip, 120, 3600, "定位请求太频繁"):
            return
        lat = lng = None
        try:
            if (q.get("lat") or [""])[0] and (q.get("lng") or [""])[0]:
                lat = float((q.get("lat") or [""])[0])
                lng = float((q.get("lng") or [""])[0])
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    lat = lng = None
        except (TypeError, ValueError):
            lat = lng = None
        data = get_geo(ip, lat, lng)
        if not data or not data.get("ok"):
            self._err(503, "定位/天气暂时不可用", "geo_unavailable")
            return
        self._json(200, data)

    def _wallpapers_route(self, q):
        city = ((q.get("city") or [""])[0] or "").strip()[:20]
        if not city:
            self._err(400, "缺少城市", "bad_request")
            return
        ip = self.client_ip()
        if not self._rate("wallset:" + ip, 120, 3600, "壁纸请求太频繁"):
            return
        day = _wall_day()
        items = get_wallpaper_candidates(city, day)
        if not items:
            self._err(404, "暂时没有找到壁纸", "wallpaper_not_found")
            return
        items = items[:5]   # 首页固定 5 张轮播
        images = []
        for i in range(len(items)):
            qs = urllib.parse.urlencode({"city": city, "day": day, "idx": i})
            images.append({"url": "/api/wallpaper?" + qs, "credit": "Bing"})
        self._json(200, {
            "ok": True, "city": city, "day": day,
            "count": len(images), "images": images,
        }, extra={"Cache-Control": "private, no-store"})

    def _wallpaper_route(self, q):
        city = ((q.get("city") or [""])[0] or "").strip()[:20]
        if not city:
            self._err(400, "缺少城市", "bad_request")
            return
        ip = self.client_ip()
        if not self._rate("wall:" + ip, 300, 3600, "壁纸请求太频繁"):
            return
        try:
            idx = int((q.get("idx") or ["0"])[0])
        except ValueError:
            idx = 0
        day = (q.get("day") or [""])[0] or _wall_day()
        data, ctype = get_wallpaper(city, idx, day)
        if not data:
            self._err(404, "暂时没有找到壁纸", "wallpaper_not_found")
            return
        self._send(200, data, ctype or "image/jpeg",
                   cache="public, max-age=3600",
                   extra={"X-Wallpaper-City": urllib.parse.quote(city),
                          "X-Wallpaper-Day": day,
                          "X-Wallpaper-Credit": "bing-images"},
                   gzip_ok=False)

    def _read_raw(self, max_bytes):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return None, "empty"
        if length > max_bytes:
            return None, "too_large"
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            return None, "bad_type"
        try:
            data = self.rfile.read(length)
        except Exception:
            return None, "read_error"
        if len(data) != length or not _image_magic_ok(data):
            return None, "bad_image"
        return data, ""

    def _ocr_route(self):
        cloud_ready = bool(TENCENT_SECRET_ID and TENCENT_SECRET_KEY)
        if not cloud_ready and not OCR_REMOTE_URL and not shutil.which("tesseract"):
            self._err(503, "截图识别服务未安装", "ocr_not_configured")
            return
        ip = self.client_ip()
        if not self._rate("ocr:" + ip, 20, 3600, "截图识别太频繁，请稍后再试"):
            return
        data, why = self._read_raw(MAX_OCR_BYTES)
        if data is None:
            if why == "too_large":
                self._err(413, "图片太大，请先在相册里裁剪", "file_too_large")
            elif why == "bad_type":
                self._err(415, "只支持图片文件", "bad_type")
            else:
                self._err(400, "图片读取失败", "bad_image")
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        global _TENCENT_BLOCK_UNTIL
        if cloud_ready and OCR_PROVIDER in ("auto", "tencent") and time.time() >= _TENCENT_BLOCK_UNTIL:
            started_cloud = time.time()
            try:
                cloud_text = _tencent_ocr(data)
                if cloud_text.strip():
                    self._json(200, {
                        "text": cloud_text,
                        "chars": len(cloud_text),
                        "seconds": round(time.time() - started_cloud, 2),
                        "engine": "tencent",
                    })
                    return
            except CloudOcrError as exc:
                if exc.code in ("ResourceUnavailable.ResourcePackageRunOut",
                                "FailedOperation.UnOpenError",
                                "AuthFailure.SecretIdNotFound",
                                "AuthFailure.SignatureFailure"):
                    _TENCENT_BLOCK_UNTIL = time.time() + 600
                print("[OCR] tencent failed, fallback: %s %s" % (exc.code, exc), flush=True)
        if OCR_REMOTE_URL:
            started_remote = time.time()
            try:
                remote_text = _remote_ocr(data, ctype)
                if remote_text.strip():
                    self._json(200, {
                        "text": remote_text,
                        "chars": len(remote_text),
                        "seconds": round(time.time() - started_remote, 2),
                        "engine": "remote",
                    })
                    return
            except Exception as exc:  # noqa: BLE001
                print("[OCR] remote failed, fallback local: %r" % (exc,), flush=True)
        suffix = ".png" if "png" in ctype else (".webp" if "webp" in ctype else ".jpg")
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(data)
                path = f.name
            started = time.time()
            text = _tesseract_text(path)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            self._json(200, {
                "text": text,
                "chars": len(text),
                "seconds": round(time.time() - started, 2),
                "lang": OCR_LANG,
            })
        except Exception as exc:  # noqa: BLE001
            print("[OCR] failed: %r" % (exc,), flush=True)
            self._err(502, "截图识别失败，请改用粘贴", "ocr_failed")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    # ── 账号接口 ────────────────────────────────────────────
    def _rate(self, key, limit, window, message="操作太频繁，稍后再试"):
        ok, retry = LIMITER.allow(key, limit, window)
        if not ok:
            self._err(429, message, "rate_limited", {"retryAfter": retry},
                      headers={"Retry-After": str(retry)})
        return ok

    def _auth_register(self, body):
        phone = normalize_phone(body.get("phone"))
        password = str(body.get("password") or "")
        nickname = str(body.get("nickname") or "").strip()
        if not valid_phone(phone):
            self._err(400, "请输入正确的 11 位手机号", "bad_phone")
            return
        if len(password) < 8 or len(password) > 64:
            self._err(400, "密码至少 8 位", "bad_password")
            return
        if not self._rate("reg:" + self.client_ip(), 20, 3600):
            return
        if get_user_by_phone(phone):
            self._err(409, "这个手机号已经注册，直接登录吧", "phone_exists")
            return
        try:
            row = create_user(phone, password, nickname)
        except sqlite3.IntegrityError:
            self._err(409, "这个手机号已经注册，直接登录吧", "phone_exists")
            return
        token, max_age = create_session(row["id"], True, self.headers.get("User-Agent", ""))
        self._json(200, {"user": user_payload(row), "created": True},
                   extra={"Set-Cookie": self.session_cookie(token, max_age)})

    def _auth_login(self, body):
        phone = normalize_phone(body.get("phone"))
        password = str(body.get("password") or "")
        remember = bool(body.get("remember"))
        if not valid_phone(phone) or not password:
            self._err(400, "请填写手机号和密码", "bad_credentials")
            return
        if not self._rate("login:%s:%s" % (self.client_ip(), phone), 10, 600,
                          "尝试次数太多，请 10 分钟后再试"):
            return
        row = get_user_by_phone(phone)
        if not row or not row["password_hash"]:
            self._err(401, "手机号或密码不正确", "bad_credentials")
            return
        if not verify_password(password, row["password_hash"]):
            self._err(401, "手机号或密码不正确", "bad_credentials")
            return
        conn = db_connect()
        try:
            conn.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
                         (now_utc_iso(), now_utc_iso(), row["id"]))
            conn.commit()
        finally:
            conn.close()
        token, max_age = create_session(row["id"], remember, self.headers.get("User-Agent", ""))
        self._json(200, {"user": user_payload(row)},
                   extra={"Set-Cookie": self.session_cookie(token, max_age)})

    def _auth_logout(self):
        token = self.get_cookie(COOKIE_NAME)
        if not token:
            auth = self.headers.get("Authorization", "") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        if token:
            conn = db_connect()
            try:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (sha256_hex(token),))
                conn.commit()
            finally:
                conn.close()
        self._json(200, {"ok": True}, extra={"Set-Cookie": self.session_cookie("", 0)})

    def _auth_sms_send(self, body):
        phone = normalize_phone(body.get("phone"))
        if not valid_phone(phone):
            self._err(400, "请输入正确的 11 位手机号", "bad_phone")
            return
        if not sms_enabled():
            self._err(503, "短信服务还没配置，请用手机号 + 密码登录", "sms_not_configured")
            return
        if not self._rate("sms:" + phone, 1, 60, "验证码刚发过，1 分钟后再试"):
            return
        if not self._rate("smsh:" + phone, 5, 3600, "这个手机号今天验证码发得太多了"):
            return
        if not self._rate("smsip:" + self.client_ip(), 20, 3600, "操作太频繁，稍后再试"):
            return
        try:
            code = issue_sms_code(phone)
        except Exception as exc:  # noqa: BLE001
            print("[SMS] send failed: %r" % (exc,), flush=True)
            self._err(502, "验证码发送失败，请稍后再试", "sms_failed")
            return
        payload = {"ok": True, "ttl": 300}
        if SMS_DEBUG:
            payload["devCode"] = code
        self._json(200, payload)

    def _auth_sms_login(self, body):
        phone = normalize_phone(body.get("phone"))
        code = re.sub(r"\D", "", str(body.get("code") or ""))[:6]
        if not valid_phone(phone) or len(code) != 6:
            self._err(400, "请填写手机号和 6 位验证码", "bad_code")
            return
        if not self._rate("smslogin:%s:%s" % (self.client_ip(), phone), 12, 600,
                          "尝试次数太多，稍后再试"):
            return
        ok, msg = consume_sms_code(phone, code)
        if not ok:
            self._err(400, msg, "bad_code")
            return
        row = get_user_by_phone(phone)
        created = False
        if not row:
            row = create_user(phone, None, "")
            created = True
        conn = db_connect()
        try:
            conn.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
                         (now_utc_iso(), now_utc_iso(), row["id"]))
            conn.commit()
        finally:
            conn.close()
        token, max_age = create_session(row["id"], True, self.headers.get("User-Agent", ""))
        self._json(200, {"user": user_payload(row), "created": created},
                   extra={"Set-Cookie": self.session_cookie(token, max_age)})

    def _auth_reset(self, body):
        phone = normalize_phone(body.get("phone"))
        code = re.sub(r"\D", "", str(body.get("code") or ""))[:6]
        password = str(body.get("password") or "")
        if not sms_enabled():
            self._err(503, "短信服务还没配置，暂时不能自助找回密码", "sms_not_configured")
            return
        if not valid_phone(phone) or len(code) != 6 or len(password) < 8:
            self._err(400, "请填写手机号、验证码和至少 8 位的新密码", "bad_request")
            return
        if not self._rate("reset:" + phone, 6, 3600, "操作太频繁，稍后再试"):
            return
        ok, msg = consume_sms_code(phone, code)
        if not ok:
            self._err(400, msg, "bad_code")
            return
        row = get_user_by_phone(phone)
        if not row:
            self._err(404, "这个手机号还没有注册", "user_not_found")
            return
        conn = db_connect()
        try:
            conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                         (hash_password(password), now_utc_iso(), row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            conn.commit()
        finally:
            conn.close()
        self._json(200, {"ok": True})

    def _auth_password(self, body):
        row = self.require_user()
        if not row:
            return
        old = str(body.get("old") or "")
        new = str(body.get("new") or "")
        if len(new) < 8 or len(new) > 64:
            self._err(400, "新密码至少 8 位", "bad_password")
            return
        if row["password_hash"] and not verify_password(old, row["password_hash"]):
            self._err(400, "当前密码不正确", "bad_old_password")
            return
        if not self._rate("pwd:" + str(row["id"]), 6, 3600, "操作太频繁，稍后再试"):
            return
        token = self.get_cookie(COOKIE_NAME)
        conn = db_connect()
        try:
            conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                         (hash_password(new), now_utc_iso(), row["id"]))
            # 其他设备上的旧会话失效，当前会话保留
            if token:
                conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
                             (row["id"], sha256_hex(token)))
            conn.commit()
        finally:
            conn.close()
        self._json(200, {"ok": True})

    # ── 地址簿接口 ──────────────────────────────────────────
    def _save_deliver_route(self, body):
        row = self.require_user()
        if not row:
            return
        place = str(body.get("place") or "").strip()
        if not place:
            self._err(400, "小区名不能为空", "bad_request")
            return
        try:
            floor = int(body.get("floor") or 0)
        except (TypeError, ValueError):
            floor = 0
        save_deliver(row["id"], place, str(body.get("b") or "")[:40],
                     str(body.get("r") or "")[:20], max(0, min(floor, 99)))
        self._json(200, {"ok": True})

    def _save_pickup_route(self, body):
        row = self.require_user()
        if not row:
            return
        shop = str(body.get("shop") or "").strip()
        if not shop:
            self._err(400, "取餐点不能为空", "bad_request")
            return
        save_pickup(row["id"], shop, str(body.get("phone") or "")[:20],
                    str(body.get("code") or "")[:20])
        self._json(200, {"ok": True})

    # ── 订单接口 ────────────────────────────────────────────
    def _add_order_route(self, body):
        row = self.require_user()
        if not row:
            return
        day = str(body.get("day") or "")
        hm = str(body.get("hm") or "")
        if not valid_day(day) or not valid_hm(hm):
            self._err(400, "日期格式不对", "bad_request")
            return
        event_id = (str(body.get("event_id") or "").strip() or secrets.token_hex(8))[:80]
        mode = str(body.get("mode") or "deliver")
        place_txt = str(body.get("place") or "")
        inserted, stat = add_order(row["id"], event_id, day, hm, mode, place_txt)
        self._json(200, {"ok": True, "inserted": inserted, "day": day, "stat": stat,
                         "total": get_orders(row["id"], 366)["total"]})

    def _import_orders_route(self, body):
        row = self.require_user()
        if not row:
            return
        days = body.get("days")
        if not isinstance(days, list):
            self._err(400, "days 必须是数组", "bad_request")
            return
        import_id = str(body.get("import_id") or "local")[:40]
        merged = import_orders(row["id"], days, import_id)
        self._json(200, {"ok": True, "merged": merged})

    # ── 语音外呼 ────────────────────────────────────────────
    def _call_route(self, body):
        if not CALL_WEBHOOK:
            self._err(503, "语音外呼暂未开放", "call_not_configured")
            return
        user = self.current_user()
        if CALL_REQUIRE_LOGIN and not user:
            self._err(401, "语音外呼需要先登录", "login_required")
            return
        phone = normalize_phone(body.get("phone"))
        text = str(body.get("text") or "").strip()
        if not valid_phone(phone):
            self._err(400, "请输入正确的手机号", "bad_phone")
            return
        if not text or len(text) > 120:
            self._err(400, "一次最多播报 120 个字", "bad_text")
            return
        if not self._rate("call:" + self.client_ip(), CALL_RATE_IP, 3600,
                          "外呼太频繁，请稍后再试"):
            return
        if not self._rate("callp:" + phone, CALL_RATE_PHONE, 3600,
                          "这个号码刚打过，请稍后再试"):
            return
        data = json.dumps({"phone": phone, "text": text, "source": "ylct"}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if CALL_TOKEN:
            headers["Authorization"] = "Bearer " + CALL_TOKEN
        req = urllib.request.Request(CALL_WEBHOOK, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                detail = resp.read(2000).decode("utf-8", "replace")
                if resp.status >= 300:
                    raise RuntimeError("status %s" % resp.status)
        except Exception as exc:  # noqa: BLE001
            print("[CALL] webhook failed: %r" % (exc,), flush=True)
            self._err(502, "语音网关暂时没接通，先用免提播报", "call_failed")
            return
        self._json(200, {"ok": True, "detail": detail[:500]})

    # ── TTS ─────────────────────────────────────────────────
    def _handle_tts(self, q):
        if not MIMO_API_KEY:
            self._err(503, "MIMO_API_KEY 未配置，前端会用浏览器语音", "tts_not_configured")
            return
        user = self.current_user()
        ip = self.client_ip()
        if not self._rate("tts:global", TTS_GLOBAL_RATE, 3600, "语音服务繁忙，稍后再试"):
            return
        if user:
            allowed = self._rate("tts:u:%d" % user["id"], TTS_RATE_USER, 3600,
                                 "语音请求太频繁，稍后再试")
        else:
            allowed = self._rate("tts:ip:" + ip, TTS_RATE_GUEST, 3600,
                                 "游客语音次数已用完，登录后可继续")
        if not allowed:
            return
        text = (q.get("text", [""])[0] or "").strip()
        if not text:
            self._err(400, "empty text", "bad_request")
            return
        if len(text) > TTS_MAX_TEXT:
            self._err(400, "一次最多 %d 个字" % TTS_MAX_TEXT, "text_too_long")
            return
        voice = (q.get("voice", [VOICE])[0] or "").strip()
        if voice not in VOICES:
            voice = VOICE
        cache_key = voice + "\x00" + text
        with _tts_lock:
            hit = _tts_cache.get(cache_key)
        if hit is not None:
            self._send(200, hit, "audio/wav", cache="private, no-store", gzip_ok=False)
            return
        try:
            wav = synth(text, voice)
        except Exception as exc:  # noqa: BLE001
            print("[TTS] synth failed: %r" % (exc,), flush=True)
            self._err(502, "语音合成失败，稍后再试", "tts_failed")
            return
        with _tts_lock:
            if len(_tts_cache) >= TTS_CACHE_MAX:
                _tts_cache.pop(next(iter(_tts_cache)), None)
            _tts_cache[cache_key] = wav
        self._send(200, wav, "audio/wav", cache="private, no-store", gzip_ok=False)

    # ── 静态文件 ────────────────────────────────────────────
    def _resolve_static(self, url_path):
        rel = urllib.parse.unquote(url_path)
        if "\x00" in rel or rel.startswith("//"):
            return None
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if any(p == ".." or p.startswith(".") or p in BLOCKED_PARTS for p in parts):
            return None
        if not parts:
            parts = ["index.html"]
        ext = os.path.splitext(parts[-1])[1].lower()
        if ext not in STATIC_EXT:
            return None
        full = os.path.realpath(os.path.join(HERE, *parts))
        root = os.path.realpath(HERE)
        try:
            if os.path.commonpath([full, root]) != root:
                return None
        except ValueError:
            return None
        if not os.path.isfile(full):
            return None
        return full

    def _serve_static(self, url_path):
        full = self._resolve_static(url_path)
        if not full:
            self._err(404, "页面不存在", "not_found")
            return
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self._err(404, "页面不存在", "not_found")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        name = os.path.basename(full)
        if name == "index.html":
            cache = "no-cache"
        elif name in ("sw.js", "manifest.json", "manifest.webmanifest"):
            cache = "no-cache"
        else:
            cache = "public, max-age=86400"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype, cache=cache)


# ────────────────────────────────────────────────────────────
# 启动
# ────────────────────────────────────────────────────────────
def create_server(host=None, port=None):
    init_db()
    srv = ThreadingHTTPServer((host or HOST, port or PORT), Handler)
    srv.daemon_threads = True
    srv.allow_reuse_address = True
    return srv


def run_test(text):
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


def parse_args(argv):
    opts = {"host": None, "port": None, "test": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--host", "-H") and i + 1 < len(argv):
            opts["host"] = argv[i + 1]
            i += 2
        elif a in ("--port", "-p") and i + 1 < len(argv):
            try:
                opts["port"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--test":
            opts["test"] = argv[i + 1] if i + 1 < len(argv) else "您好，我是送外卖的，麻烦开一下门"
            i += 2
        else:
            i += 1
    return opts


def main():
    opts = parse_args(sys.argv[1:])
    if opts["test"] is not None:
        run_test(opts["test"])
        return
    host = opts["host"] or HOST
    port = opts["port"] or PORT
    srv = create_server(host, port)
    print("译路畅通后端已启动： http://%s:%d" % (host, port), flush=True)
    print("数据目录：%s" % DATA_DIR, flush=True)
    print("TTS：%s | 语音外呼：%s | 短信：%s" % (
        "已配置" if MIMO_API_KEY else "未配置（前端回退浏览器语音）",
        "已配置" if CALL_WEBHOOK else "未配置（用免提播报）",
        SMS_PROVIDER if sms_enabled() else "未配置",
    ), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
