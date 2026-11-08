#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
译路畅通后端接口测试（只用标准库）
运行：python3 tests/test_api.py
"""

import http.cookiejar
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, extra))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_health(base, timeout=12):
    t = time.time()
    while time.time() - t < timeout:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="ylct-test-")
    port = free_port()
    env = dict(os.environ)
    env.update({
        "YLCT_DATA_DIR": os.path.join(tmp, "data"),
        "MIMO_API_KEY": "",
        "YLCT_SMS_PROVIDER": "none",
        "YLCT_CALL_WEBHOOK": "",
        "YLCT_SMS_DEBUG": "0",
    })
    proc = subprocess.Popen(
        [sys.executable, SERVER, "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = "http://127.0.0.1:%d" % port
    try:
        if not wait_health(base):
            print("服务没有启动：")
            try:
                print(proc.stdout.read(4000).decode("utf-8", "replace"))
            except Exception:
                pass
            sys.exit(1)

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def req(method, path, body=None, headers=None, cookie=True):
            data = None
            h = dict(headers or {})
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                h["Content-Type"] = "application/json"
            r = urllib.request.Request(base + path, data=data, headers=h, method=method)
            try:
                with opener.open(r, timeout=20) as resp:
                    raw = resp.read()
                    status = resp.status
                    rh = dict(resp.headers)
            except urllib.error.HTTPError as e:
                raw = e.read()
                status = e.code
                rh = dict(e.headers)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                parsed = None
            return status, rh, parsed

        print("== 健康检查 / 静态安全 ==")
        st, hd, js = req("GET", "/api/health")
        ok("health 200", st == 200)
        ok("health 字段", js and js.get("ok") is True and js.get("auth", {}).get("password") is True)
        ok("CSP 已开启", "Content-Security-Policy" in hd)
        st, hd, js = req("GET", "/")
        ok("index 200", st == 200)
        st, hd, js = req("GET", "/server.py")
        ok("server.py 不可下载", st == 404)
        st, hd, js = req("GET", "/data/app.db")
        ok("data 目录不可下载", st == 404)
        st, hd, js = req("GET", "/manifest.json")
        ok("manifest 200", st == 200)

        print("== 游客模式 ==")
        st, hd, js = req("GET", "/api/addrbook")
        ok("游客地址簿 401", st == 401)
        st, hd, js = req("GET", "/api/orders")
        ok("游客订单 401", st == 401)

        print("== 注册 / 登录 ==")
        phone = "139" + "".join(random.choice("0123456789") for _ in range(8))
        st, hd, js = req("POST", "/api/auth/register",
                         {"phone": phone, "password": "test12345", "nickname": "测试骑手"})
        ok("注册 200", st == 200, str(js))
        ok("注册返回用户", js and js.get("user", {}).get("phone") == phone)
        ok("注册下发 HttpOnly Cookie", "HttpOnly" in (hd.get("Set-Cookie") or ""))
        ok("注册下发 SameSite", "SameSite=Lax" in (hd.get("Set-Cookie") or ""))
        ok("Cookie Max-Age 是 90 天", "Max-Age=7776000" in (hd.get("Set-Cookie") or ""),
           hd.get("Set-Cookie") or "")
        st, hd, js = req("GET", "/api/auth/me")
        ok("me 200", st == 200 and js.get("user", {}).get("phone") == phone)
        st, hd, js = req("POST", "/api/auth/register",
                         {"phone": phone, "password": "test12345"})
        ok("重复注册 409", st == 409)
        st, hd, js = req("POST", "/api/auth/register",
                         {"phone": "12345", "password": "test12345"})
        ok("坏手机号 400", st == 400)
        st, hd, js = req("POST", "/api/auth/register",
                         {"phone": "13900000001", "password": "123"})
        ok("短密码 400", st == 400)
        for bad in ([], "abc", 123, [1, 2]):
            st, hd, js = req("POST", "/api/auth/login", bad)
            ok("非对象 JSON 400: %r" % (bad,), st == 400, str(js))
        st, hd, js = req("POST", "/api/auth/logout", {})
        ok("登出 200", st == 200)
        st, hd, js = req("GET", "/api/auth/me")
        ok("登出后 me 401", st == 401)
        st, hd, js = req("POST", "/api/auth/login",
                         {"phone": phone, "password": "wrongpass1"})
        ok("错误密码 401", st == 401)
        st, hd, js = req("POST", "/api/auth/login",
                         {"phone": phone, "password": "test12345", "remember": True})
        ok("登录 200", st == 200 and js.get("user", {}).get("phone") == phone)

        print("== 地址簿 ==")
        st, hd, js = req("POST", "/api/addrbook/deliver",
                         {"place": "测试花园", "b": "2号楼", "r": "1201", "floor": 12})
        ok("保存送餐地址 200", st == 200)
        st, hd, js = req("POST", "/api/addrbook/pickup",
                         {"shop": "测试面馆", "phone": phone, "code": "38"})
        ok("保存取餐点 200", st == 200)
        st, hd, js = req("GET", "/api/addrbook")
        ok("地址簿 200", st == 200)
        d = (js or {}).get("deliver") or []
        p = (js or {}).get("pickup") or []
        ok("送餐地址可读", d and d[0].get("place") == "测试花园" and d[0].get("r") == "1201")
        ok("取餐点可读", p and p[0].get("shop") == "测试面馆" and p[0].get("code") == "38")

        print("== 订单 / 幂等迁移 ==")
        st, hd, js = req("POST", "/api/orders",
                         {"event_id": "evt-1", "day": "2026-11-08", "hm": "14:20",
                          "mode": "deliver", "place": "测试花园"})
        ok("记一单 200", st == 200 and js.get("inserted") is True)
        st, hd, js = req("POST", "/api/orders",
                         {"event_id": "evt-1", "day": "2026-11-08", "hm": "14:20"})
        ok("重复 event_id 不重复计数", st == 200 and js.get("inserted") is False
           and js.get("stat", {}).get("count") == 1)
        days = [{"day": "2026-11-07", "count": 3, "first": "10:00", "last": "12:00"}]
        st, hd, js = req("POST", "/api/orders/import", {"import_id": "inst-1", "days": days})
        ok("游客记录迁移 3 单", st == 200 and js.get("merged") == 3)
        st, hd, js = req("POST", "/api/orders/import", {"import_id": "inst-1", "days": days})
        ok("重复迁移不重复计数", st == 200 and js.get("merged") == 0)
        st, hd, js = req("GET", "/api/orders?days=31")
        ok("订单统计 200", st == 200 and js.get("total") == 4)
        ok("按天统计", js.get("days", {}).get("2026-11-07", {}).get("count") == 3)

        print("== 未配置服务的行为 ==")
        st, hd, js = req("GET", "/api/tts?text=hello")
        ok("无 Key 时 TTS 503", st == 503)
        st, hd, js = req("POST", "/api/call", {"phone": phone, "text": "你好"})
        ok("未配置外呼 503", st == 503 and js.get("code") == "call_not_configured")
        ok("外呼 503 不泄露内部配置", "YLCT_" not in json.dumps(js, ensure_ascii=False)
           and "docs/" not in json.dumps(js, ensure_ascii=False))
        st, hd, js = req("POST", "/api/auth/sms/send", {"phone": phone})
        ok("未配置短信 503", st == 503 and js.get("code") == "sms_not_configured")

        print("== 修改密码 ==")
        st, hd, js = req("POST", "/api/auth/password",
                         {"old": "wrong-old", "new": "test67890"})
        ok("旧密码错误 400", st == 400 and js.get("code") == "bad_old_password")
        st, hd, js = req("POST", "/api/auth/password",
                         {"old": "test12345", "new": "123"})
        ok("新密码太短 400", st == 400)
        st, hd, js = req("POST", "/api/auth/password",
                         {"old": "test12345", "new": "test67890"})
        ok("修改密码 200", st == 200)
        st, hd, js = req("GET", "/api/auth/me")
        ok("改密后当前会话仍有效", st == 200)
        st, hd, js = req("POST", "/api/auth/logout", {})
        st, hd, js = req("POST", "/api/auth/login",
                         {"phone": phone, "password": "test12345"})
        ok("旧密码登录失败", st == 401)
        st, hd, js = req("POST", "/api/auth/login",
                         {"phone": phone, "password": "test67890"})
        ok("新密码登录成功", st == 200)

        print("== 反向代理 Cookie 安全 ==")
        st, hd, js = req("POST", "/api/auth/login",
                         {"phone": phone, "password": "test67890"},
                         {"X-Forwarded-Proto": "https"})
        ok("https 下 Cookie 带 Secure", "Secure" in (hd.get("Set-Cookie") or ""))

        print()
        print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
        return 0 if FAIL == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
