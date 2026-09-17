"""
vertex_cookie.py — Vertex 下载器 Cookie 获取 / 自动刷新模块（各子项目共用）
==========================================================================

本文件在仓库中被复制到多个子项目目录下，内容保持一致：
  * autobrr_loadbalance_vt / hetzner-usagemonit_vt
  * netcup-control-RESTAPI_vt / vertex-configedit
如需修改，请同步更新所有副本。

────────────────────────────── 功能 ──────────────────────────────
1. 自动登录 Vertex，获取会话 Cookie（connect.sid）；
2. 缓存到本文件同目录的 vertex_cookie_cache.json（按 地址::用户名 区分多实例）；
3. 定时探测 Cookie 有效性，失效时自动重新登录；
4. 提供命令行输出，便于 shell / 其它语言脚本直接调用。

────────────────────────────── 快速使用（模块导入）──────────────────────────────
    from vertex_cookie import get_cookie, force_refresh

    cookie = get_cookie("http://YOUR-VERTEX-IP:3077", username="admin", password="你的密码")
    cookie = force_refresh("http://YOUR-VERTEX-IP:3077", username="admin", password="你的密码")
    headers = {"Cookie": cookie, "Content-Type": "application/json"}

────────────────────────────── 面向对象使用（常驻服务推荐）──────────────────────────────
    from vertex_cookie import VertexCookieManager

    vcm = VertexCookieManager(
        login_url = "http://YOUR-VERTEX-IP:3077",   # Vertex 服务地址
        username  = "admin",                          # 登录用户名
        password  = "你的明文密码",                  # 明文密码，程序自动转 MD5
        # password_is_hashed = True,                # 若直接提供 32 位 MD5，置 True
        check_interval = 300,                        # Cookie 有效性探测最小间隔（秒）
        timeout = 10,                                # 请求超时（秒）
    )
    cookie = vcm.get_valid_cookie()                        # 复用缓存 / 失效自动刷新
    cookie = vcm.get_valid_cookie(force_refresh=True)      # 强制重新登录
    cookie = vcm.force_refresh()                           # 同上（语义化别名）

────────────────────────────── 从环境变量构造 ──────────────────────────────
    # 环境变量：
    #   VTURL=http://YOUR-VERTEX-IP:3077
    #   VT_USERNAME=admin
    #   VT_PASSWORD=你的明文密码        # 或 VT_PASSWORD_MD5=<32位MD5>
    from vertex_cookie import from_env
    vcm = from_env()      # 未配置完整时返回 None

────────────────────────────── 命令行输出 Cookie ──────────────────────────────
    python vertex_cookie.py http://YOUR-VERTEX-IP:3077 --user admin --pwd 你的密码 [--force]

────────────────────────────── 登录细节 ──────────────────────────────
* 登录接口：POST /api/user/login
* 载荷格式：{"username": 用户名, "password": MD5(密码), "otpPw": ""}
* Cookie 名：connect.sid（优先取响应头 Set-Cookie，兼容 JSON body 中的 sid）
* 有效性探测：GET /api/downloader/list，状态码 200/304 视为有效
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import warnings
from typing import Optional

warnings.filterwarnings("ignore", message=".*urllib3.*")

# Windows 控制台默认 GBK，强制 UTF-8，避免中文/符号输出报错
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

# ── 默认登录用户名（仓库不内置任何真实凭据，密码请按需传入）──
DEFAULT_USERNAME = "admin"

# ── 缓存文件固定在本文件同目录 ─────────────────────────────
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_MODULE_DIR, "vertex_cookie_cache.json")
_CACHE_LOCK = threading.Lock()   # 进程内缓存读写锁


def md5_password(plaintext: str) -> str:
    """明文密码 -> 32 位小写 MD5（与 Vertex 前端加密行为一致）"""
    return hashlib.md5(plaintext.encode("utf-8")).hexdigest()


class VertexCookieManager:
    """单个 Vertex 实例的 Cookie 管理：缓存 + 校验 + 失效重登。"""

    def __init__(self, login_url: str, username: str = DEFAULT_USERNAME,
                 password: str = None, password_is_hashed: bool = False,
                 timeout: int = 10, check_interval: int = 300):
        self.login_url = login_url.rstrip("/")
        self.username = username
        self.timeout = timeout
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._key = f"{self.login_url}::{self.username}"

        raw = (password or "").strip()
        if not raw:
            raise ValueError("[VertexCookie] 必须提供 password（明文密码或 MD5 字符串）")
        # 显式声明已哈希 / 或本身就是 32 位 hex（MD5 特征）→ 直接使用；否则视为明文自动 MD5
        is_md5_hex = len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw)
        if password_is_hashed or is_md5_hex:
            self._password_md5 = raw.lower()
        else:
            self._password_md5 = md5_password(raw)

    # ── 缓存读写 ──────────────────────────────────────────
    def _read_cache(self) -> dict:
        with _CACHE_LOCK:
            try:
                if os.path.exists(_CACHE_FILE):
                    with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
            return {}

    def _write_cache(self, data: dict):
        with _CACHE_LOCK:
            try:
                with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def _entry(self) -> dict:
        return self._read_cache().get(self._key, {})

    def _save(self, cookie: str):
        data = self._read_cache()
        data[self._key] = {
            "cookie": cookie,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_check": time.time(),
        }
        self._write_cache(data)

    def _touch(self):
        data = self._read_cache()
        if self._key not in data:
            data[self._key] = {}
        data[self._key]["last_check"] = time.time()
        self._write_cache(data)

    # ── 登录 ──────────────────────────────────────────────
    def login(self) -> Optional[str]:
        """登录，返回 "connect.sid=xxx"；失败返回 None。"""
        try:
            resp = requests.post(
                f"{self.login_url}/api/user/login",
                json={"username": self.username, "password": self._password_md5, "otpPw": ""},
                timeout=self.timeout,
            )
            # 优先从 Set-Cookie 头提取
            for part in (resp.headers.get("Set-Cookie") or "").split(","):
                first = part.split(";")[0].strip()
                if first.lower().startswith("connect.sid="):
                    return first
            # 部分版本把 sid 放在 JSON body 里
            try:
                body = resp.json()
                sid = (body.get("data") or {}).get("sid") or body.get("sid")
                if sid:
                    return f"connect.sid={sid}"
            except Exception:
                pass
            return None
        except Exception:
            return None

    # ── 有效性探测 ────────────────────────────────────────
    def is_cookie_valid(self, cookie: str) -> bool:
        try:
            r = requests.get(
                f"{self.login_url}/api/downloader/list",
                headers={"Cookie": cookie},
                timeout=self.timeout,
            )
            return r.status_code in (200, 304)
        except Exception:
            return False

    # ── 核心：获取有效 Cookie ─────────────────────────────
    def get_valid_cookie(self, force_refresh: bool = False) -> str:
        """
        返回有效 Cookie 字符串；无可用 Cookie 时返回空串。

        逻辑：读取缓存 → 未到探测周期直接复用 → 探测有效性 →
        失效则重新登录 → 登录失败返回空串（不再降级返回失效缓存）。
        """
        with self._lock:
            entry = self._entry()
            cached = entry.get("cookie", "")

            if cached and not force_refresh:
                elapsed = time.time() - float(entry.get("last_check", 0))
                if elapsed < self.check_interval:
                    return cached
                if self.is_cookie_valid(cached):
                    self._touch()
                    return cached

            new_cookie = self.login()
            if new_cookie:
                self._save(new_cookie)
                return new_cookie
            return ""

    def refresh_if_needed(self) -> str:
        """与 get_valid_cookie 等价的别名，语义更清晰。"""
        return self.get_valid_cookie()

    def force_refresh(self) -> str:
        """强制重新登录获取新 Cookie，忽略探测周期。"""
        return self.get_valid_cookie(force_refresh=True)


# ── 便捷函数：一条命令获取有效 Cookie ─────────────────────────
def get_cookie(login_url: str, username: str = DEFAULT_USERNAME,
               password: str = None) -> str:
    """获取有效 Cookie（自动缓存 + 失效刷新）。"""
    return VertexCookieManager(login_url, username, password).get_valid_cookie()


def force_refresh(login_url: str, username: str = DEFAULT_USERNAME,
                  password: str = None) -> str:
    """强制重新登录获取最新 Cookie。"""
    return VertexCookieManager(login_url, username, password).get_valid_cookie(force_refresh=True)


# ── 便捷工厂：从环境变量构造 ────────────────────────────────
def from_env(url_env: str = "VTURL", user_env: str = "VT_USERNAME",
             pwd_env: str = "VT_PASSWORD", pwd_md5_env: str = "VT_PASSWORD_MD5",
             fallback_url: str = "") -> Optional[VertexCookieManager]:
    """
    从环境变量构造 VertexCookieManager。
    优先级：VT_PASSWORD（明文，自动 MD5）> VT_PASSWORD_MD5（直接使用）。
    返回 None 表示环境变量未配置完整。
    """
    login_url = os.getenv(url_env, fallback_url).rstrip("/")
    username = os.getenv(user_env, "")
    if not (login_url and username):
        return None
    plain = os.getenv(pwd_env, "")
    if plain:
        return VertexCookieManager(login_url, username, plain)
    hashed = os.getenv(pwd_md5_env, "")
    if hashed:
        return VertexCookieManager(login_url, username, hashed, password_is_hashed=True)
    return None


# ── 命令行入口：直接输出 Cookie，方便其它脚本调用 ─────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="获取 Vertex 最新 Cookie 并输出")
    ap.add_argument("url", nargs="?", default="http://YOUR-VERTEX-IP:3077",
                    help="Vertex 地址")
    ap.add_argument("--force", action="store_true", help="强制重新登录")
    ap.add_argument("--user", default=DEFAULT_USERNAME, help="登录用户名，默认 admin")
    ap.add_argument("--pwd", default=None, help="密码（明文，自动 MD5；或直接传 MD5）")
    args = ap.parse_args()

    if not args.pwd:
        args.pwd = input("请输入密码（明文，将自动 MD5）: ").strip()
    mgr = VertexCookieManager(args.url, args.user, args.pwd)
    cookie = mgr.get_valid_cookie(force_refresh=args.force)
    if cookie:
        print(cookie)
    else:
        print("ERROR: 未能获取 Cookie，请检查地址/账号/密码", file=sys.stderr)
        sys.exit(1)