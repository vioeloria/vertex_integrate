#!/usr/bin/env python3
"""
Hetzner Cloud Web Manager - Backend API
支持 Vertex 下载器同步 / 服务器流量监控 / 自动重建 / 定时删建机器
"""

import requests
import json
import time
import logging
import os
import re
import sys
import threading
import secrets
from collections import Counter
from datetime import datetime, time as dt_time
from typing import Optional, List, Dict, Tuple
from functools import wraps
from logging.handlers import RotatingFileHandler
from flask import Flask, jsonify, request, session, send_from_directory

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_rotating_handler = RotatingFileHandler(
    'hetzner_web.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
_rotating_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[_rotating_handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)

try:
    from vertex_cookie import VertexCookieManager
    _VCM_AVAILABLE = True
except ImportError:
    _VCM_AVAILABLE = False
    logging.getLogger(__name__).warning("[vertex_cookie] 模块未找到，Cookie 将不会自动刷新")

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# ─── 服务器型号完整映射表 ──────────────────────────────────────────────────────
SERVER_TYPE_CATALOG: Dict[str, Dict] = {
    "cx22":  {"cores": 2,  "memory": 4,   "disk": 40,  "traffic": "20TB",           "series": "CX",  "arch": "x86"},
    "cx32":  {"cores": 4,  "memory": 8,   "disk": 80,  "traffic": "20TB",           "series": "CX",  "arch": "x86"},
    "cx33":  {"cores": 4,  "memory": 8,   "disk": 80,  "traffic": "20TB",           "series": "CX",  "arch": "x86"},
    "cx43":  {"cores": 8,  "memory": 16,  "disk": 160, "traffic": "20TB",           "series": "CX",  "arch": "x86"},
    "cx53":  {"cores": 16, "memory": 32,  "disk": 320, "traffic": "20TB",           "series": "CX",  "arch": "x86"},
    "cpx11": {"cores": 2,  "memory": 2,   "disk": 40,  "traffic": "20TB",           "series": "CPX", "arch": "x86"},
    "cpx21": {"cores": 3,  "memory": 4,   "disk": 80,  "traffic": "20TB",           "series": "CPX", "arch": "x86"},
    "cpx22": {"cores": 2,  "memory": 4,   "disk": 80,  "traffic": "20TB/0.5TB(SIN)","series": "CPX", "arch": "x86"},
    "cpx31": {"cores": 4,  "memory": 8,   "disk": 160, "traffic": "20TB",           "series": "CPX", "arch": "x86"},
    "cpx32": {"cores": 4,  "memory": 8,   "disk": 160, "traffic": "20TB/0.5TB(SIN)","series": "CPX", "arch": "x86"},
    "cpx41": {"cores": 8,  "memory": 16,  "disk": 240, "traffic": "20TB",           "series": "CPX", "arch": "x86"},
    "cpx42": {"cores": 8,  "memory": 16,  "disk": 320, "traffic": "20TB/0.5TB(SIN)","series": "CPX", "arch": "x86"},
    "cpx51": {"cores": 16, "memory": 32,  "disk": 360, "traffic": "20TB",           "series": "CPX", "arch": "x86"},
    "cax11": {"cores": 2,  "memory": 4,   "disk": 40,  "traffic": "20TB",           "series": "CAX", "arch": "arm64"},
    "cax21": {"cores": 4,  "memory": 8,   "disk": 80,  "traffic": "20TB",           "series": "CAX", "arch": "arm64"},
    "cax31": {"cores": 8,  "memory": 16,  "disk": 160, "traffic": "20TB",           "series": "CAX", "arch": "arm64"},
    "cax41": {"cores": 16, "memory": 32,  "disk": 320, "traffic": "20TB",           "series": "CAX", "arch": "arm64"},
}

# ─── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    "hetzner_api_key": "",
    "traffic_threshold": 0.8,
    "check_interval": 1200,
    "max_servers": 3,
    "initial_snapshot_id": "",
    "ssh_keys": [],
    "server_types": ["cx43", "cpx32", "cx43", "cpx42", "cpx22"],
    "default_location": "nbg1",
    "enable_time_window": False,
    "work_start_hour": 8,
    "work_end_hour": 23,
    "work_end_minute": 30,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "web_password": "admin123",
    "auto_rebuild_enabled": True,
    "vertex_api_url": "",
    "vertex_cookies": "",
    "vertex_sync_enabled": True,
    "vertex_downloader_keyword": "Hetzner",
    "vertex_username": "",
    "vertex_password": "",
    "vertex_password_md5": "",
    "vertex_cookie_check_interval": 300,
    # ── 定时删建配置 ──
    "scheduled_tasks_enabled": False,       # 总开关
    "schedule_timezone": "Asia/Shanghai",   # 时区（IANA 名称）
    "schedule_delete_enabled": False,       # 定时删除开关
    "schedule_delete_hour": 23,             # 删除时刻（小时，本地时间）
    "schedule_delete_minute": 0,            # 删除时刻（分钟）
    "schedule_create_enabled": False,       # 定时创建开关
    "schedule_create_hour": 8,              # 创建时刻（小时，本地时间）
    "schedule_create_minute": 0,            # 创建时刻（分钟）
    "schedule_create_count": 3,             # [旧] 定时创建台数（兼容保留）
    "schedule_server_name_prefix": "hetzner-auto",  # 创建时名称前缀
    # ── 通用定时任务（新版）──
    "schedule_max_servers": 3,              # 定时创建的目标/上限台数（补足至此数）
    "scheduled_tasks": [],                  # 通用任务列表，见 _default_tasks_from_legacy
    "scheduler_tick_seconds": 20,           # 调度器检查间隔（秒）
    "schedule_retry_interval_minutes": 5,   # 指定型号缺货时的重试间隔（分钟）
}


def load_config() -> Dict:
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                had_tasks = isinstance(cfg.get("scheduled_tasks"), list)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in cfg:
                        cfg[k] = v
                # 旧配置迁移：首次出现时把旧的删/建字段转成通用任务列表并落盘
                if not had_tasks:
                    cfg["scheduled_tasks"] = _default_tasks_from_legacy(cfg)
                    save_config(cfg)
                return cfg
    except Exception as e:
        logger.error(f"Load config error: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(cfg: Dict):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Save config error: {e}")


# ─── 通用定时任务模型 ──────────────────────────────────────────────────────────
# 每个任务形如：
# {
#   "id": "task-xxxx", "type": "create" | "delete" | "sync",
#   "enabled": true, "mode": "daily" | "interval",
#   "hour": 8, "minute": 0, "days": [0,1,2,3,4,5,6],   # 0=周一 … 6=周日（Python weekday）
#   "interval_minutes": 60,
#   "options": { ... 见各任务类型 ... }
# }
VALID_TASK_TYPES = ("create", "delete", "sync")


def _default_tasks_from_legacy(cfg: Dict) -> List[Dict]:
    """把旧的 schedule_* 配置迁移成通用任务列表。"""
    return [
        {
            "id": "create-daily",
            "type": "create",
            "enabled": bool(cfg.get("schedule_create_enabled", False)),
            "mode": "daily",
            "hour": int(cfg.get("schedule_create_hour", 8) or 0),
            "minute": int(cfg.get("schedule_create_minute", 0) or 0),
            "days": [0, 1, 2, 3, 4, 5, 6],
            "interval_minutes": 60,
            "options": {
                "max_servers": int(cfg.get("schedule_max_servers",
                                           cfg.get("schedule_create_count", 3)) or 3),
                "prefix": cfg.get("schedule_server_name_prefix", "hetzner-auto"),
                "location": cfg.get("default_location", "nbg1"),
                "image_id": cfg.get("initial_snapshot_id", ""),
                "ssh_keys": list(cfg.get("ssh_keys", []) or []),
                "use_locked_ips": True,
                "lock_new_ips": False,
            },
        },
        {
            "id": "delete-daily",
            "type": "delete",
            "enabled": bool(cfg.get("schedule_delete_enabled", False)),
            "mode": "daily",
            "hour": int(cfg.get("schedule_delete_hour", 23) or 0),
            "minute": int(cfg.get("schedule_delete_minute", 0) or 0),
            "days": [0, 1, 2, 3, 4, 5, 6],
            "interval_minutes": 1440,
            "options": {},
        },
    ]


def _new_task_id() -> str:
    return f"task-{int(time.time() * 1000) % 10_000_000}-{secrets.token_hex(2)}"


# ─── Monitor State ─────────────────────────────────────────────────────────────
monitor_state = {
    "running": False,
    "last_check": None,
    "next_check": None,
    "servers_cache": [],
    "logs": [],
    "stop_event": threading.Event(),
    # ── 从 Hetzner API 拉取的动态数据 ──
    "catalog": {},            # 型号目录（含价格 / 可用性）
    "locations": [],          # 地区列表
    "datacenters": [],        # 数据中心列表
    "pricing": {},            # 全局价格
    "catalog_updated": None,  # 目录最后刷新时间
    # 定时任务状态
    "scheduler_running": False,
    "scheduler_stop_event": threading.Event(),
    "last_scheduled_delete": None,
    "last_scheduled_create": None,
    "next_scheduled_delete": None,
    "next_scheduled_create": None,
    # 通用任务运行状态： {task_id: iso时间}
    "task_last_run": {},
    "task_next_run": {},
    # 创建任务缺货重试队列
    "retry_queue": [],
    "retry_lock": threading.Lock(),
}


def add_log(msg: str, level: str = "info"):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    monitor_state["logs"].insert(0, entry)
    if len(monitor_state["logs"]) > 200:
        monitor_state["logs"] = monitor_state["logs"][:200]
    getattr(logger, "warning" if level == "warn" else level, logger.info)(msg)


# ─── Hetzner API ───────────────────────────────────────────────────────────────
class HetznerAPI:
    BASE = "https://api.hetzner.cloud/v1"

    def __init__(self, api_key: str):
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _get(self, path: str, params: dict = None):
        r = requests.get(f"{self.BASE}{path}", headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_servers(self) -> List[Dict]:
        try:
            return self._get("/servers").get("servers", [])
        except Exception as e:
            add_log(f"获取服务器列表失败: {e}", "error"); return []

    def get_server(self, server_id: int) -> Optional[Dict]:
        try:
            r = requests.get(f"{self.BASE}/servers/{server_id}", headers=self.headers, timeout=15)
            return None if r.status_code == 404 else r.json().get("server")
        except Exception as e:
            add_log(f"获取服务器 {server_id} 失败: {e}", "error"); return None

    def get_images(self, image_type: str = "snapshot") -> List[Dict]:
        try:
            return self._get("/images", {"type": image_type, "include_deprecated": "false"}).get("images", [])
        except Exception as e:
            add_log(f"获取镜像失败: {e}", "error"); return []

    def get_ssh_keys(self) -> List[Dict]:
        try:
            return self._get("/ssh_keys").get("ssh_keys", [])
        except Exception as e:
            add_log(f"获取SSH密钥失败: {e}", "error"); return []

    # ── 目录 / 价格 / 地区 ──────────────────────────────────────────────
    def get_server_types(self) -> List[Dict]:
        try:
            return self._get("/server_types").get("server_types", [])
        except Exception as e:
            add_log(f"获取型号列表失败: {e}", "error"); return []

    def get_pricing(self) -> Dict:
        try:
            return self._get("/pricing").get("pricing", {})
        except Exception as e:
            add_log(f"获取价格失败: {e}", "error"); return {}

    def get_locations(self) -> List[Dict]:
        try:
            return self._get("/locations").get("locations", [])
        except Exception as e:
            add_log(f"获取地区失败: {e}", "error"); return []

    def get_datacenters(self) -> List[Dict]:
        try:
            return self._get("/datacenters").get("datacenters", [])
        except Exception as e:
            add_log(f"获取数据中心失败: {e}", "error"); return []

    # ── 其他产品列表 ────────────────────────────────────────────────────
    def get_primary_ips(self) -> List[Dict]:
        try:
            return self._get("/primary_ips").get("primary_ips", [])
        except Exception as e:
            add_log(f"获取 Primary IP 列表失败: {e}", "error"); return []

    def get_floating_ips(self) -> List[Dict]:
        try:
            return self._get("/floating_ips").get("floating_ips", [])
        except Exception as e:
            add_log(f"获取 Floating IP 列表失败: {e}", "error"); return []

    def get_volumes(self) -> List[Dict]:
        try:
            return self._get("/volumes").get("volumes", [])
        except Exception as e:
            add_log(f"获取 Volume 列表失败: {e}", "error"); return []

    def get_load_balancers(self) -> List[Dict]:
        try:
            return self._get("/load_balancers").get("load_balancers", [])
        except Exception as e:
            add_log(f"获取负载均衡列表失败: {e}", "error"); return []

    def get_firewalls(self) -> List[Dict]:
        try:
            return self._get("/firewalls").get("firewalls", [])
        except Exception as e:
            add_log(f"获取防火墙列表失败: {e}", "error"); return []

    # ── Primary IP 操作（锁 IP / 批量创建删除） ─────────────────────────
    @staticmethod
    def _api_err(r) -> str:
        try:
            e = r.json().get("error", {})
            code = e.get("code", "")
            msg = e.get("message", r.text)
            return f"[{code}] {msg}" if code else msg
        except Exception:
            return r.text

    def create_primary_ip(self, name: str, ip_type: str = "ipv4",
                          location: Optional[str] = None,
                          assignee_id: Optional[int] = None,
                          auto_delete: bool = False) -> Dict:
        payload: Dict = {"name": self.sanitize_name(name), "type": ip_type,
                         "auto_delete": bool(auto_delete)}
        if assignee_id:
            payload["assignee_type"] = "server"
            payload["assignee_id"] = int(assignee_id)
        elif location:
            payload["location"] = location
        try:
            r = requests.post(f"{self.BASE}/primary_ips", headers=self.headers,
                              json=payload, timeout=20)
        except Exception as e:
            return {"ok": False, "name": name, "error": str(e)}
        if r.status_code == 201:
            ip = r.json().get("primary_ip", {})
            return {"ok": True, "name": name, "id": ip.get("id"),
                    "ip": ip.get("ip"), "type": ip.get("type"),
                    "location": (ip.get("location") or {}).get("name", location)}
        return {"ok": False, "name": name, "error": self._api_err(r)}

    def delete_primary_ip(self, ip_id: int) -> Tuple[bool, str]:
        try:
            r = requests.delete(f"{self.BASE}/primary_ips/{ip_id}",
                                headers=self.headers, timeout=15)
        except Exception as e:
            return False, str(e)
        if r.status_code in (200, 204):
            return True, ""
        if r.status_code == 404:
            return True, "已不存在"
        return False, self._api_err(r)

    def update_primary_ip(self, ip_id: int, **fields) -> Tuple[bool, str]:
        try:
            r = requests.put(f"{self.BASE}/primary_ips/{ip_id}", headers=self.headers,
                             json=fields, timeout=15)
        except Exception as e:
            return False, str(e)
        if r.status_code == 200:
            return True, ""
        return False, self._api_err(r)

    def assign_primary_ip(self, ip_id: int, server_id: int) -> Tuple[bool, str]:
        try:
            r = requests.post(f"{self.BASE}/primary_ips/{ip_id}/actions/assign",
                              headers=self.headers, timeout=15,
                              json={"assignee_type": "server", "assignee_id": int(server_id)})
        except Exception as e:
            return False, str(e)
        if r.status_code == 201:
            return True, ""
        return False, self._api_err(r)

    def unassign_primary_ip(self, ip_id: int) -> Tuple[bool, str]:
        try:
            r = requests.post(f"{self.BASE}/primary_ips/{ip_id}/actions/unassign",
                              headers=self.headers, timeout=15, json={})
        except Exception as e:
            return False, str(e)
        if r.status_code == 201:
            return True, ""
        return False, self._api_err(r)

    def change_primary_ip_protection(self, ip_id: int, delete: bool) -> Tuple[bool, str]:
        try:
            r = requests.post(f"{self.BASE}/primary_ips/{ip_id}/actions/change_protection",
                              headers=self.headers, timeout=15, json={"delete": bool(delete)})
        except Exception as e:
            return False, str(e)
        if r.status_code == 201:
            return True, ""
        return False, self._api_err(r)

    @staticmethod
    def sanitize_name(name: str) -> str:
        import re as _re
        n = name.lower()
        n = _re.sub(r'[^a-z0-9-]', '-', n)
        n = _re.sub(r'-{2,}', '-', n)
        n = n.strip('-')
        n = n[:63]
        n = n.rstrip('-')
        if not n:
            n = f"server-{int(time.time())}"
        return n

    def create_server(self, name: str, server_type: str, image_id: int,
                      ssh_keys: List, location: str = "nbg1",
                      primary_ipv4: Optional[int] = None,
                      primary_ipv6: Optional[int] = None) -> Optional[Dict]:
        safe_name = self.sanitize_name(name)
        if safe_name != name:
            add_log(f"  名称规范化: '{name}' → '{safe_name}'")
        public_net: Dict = {"enable_ipv4": True, "enable_ipv6": True}
        if primary_ipv4:
            public_net["ipv4"] = int(primary_ipv4)
        if primary_ipv6:
            public_net["ipv6"] = int(primary_ipv6)
        try:
            r = requests.post(f"{self.BASE}/servers", headers=self.headers, timeout=30, json={
                "name": safe_name, "server_type": server_type, "image": int(image_id),
                "location": location, "ssh_keys": ssh_keys,
                "public_net": public_net,
                "start_after_create": True
            })
            if r.status_code == 201:
                d = r.json()
                srv = d["server"]
                srv_v4 = (srv.get("public_net") or {}).get("ipv4") or {}
                srv_v6 = (srv.get("public_net") or {}).get("ipv6") or {}
                return {
                    "id": srv["id"], "name": srv["name"],
                    "ip": srv_v4.get("ip", ""),
                    "server_type": srv["server_type"]["name"],
                    "root_password": d.get("root_password"),
                    "primary_ipv4_id": srv_v4.get("id"),
                    "primary_ipv6_id": srv_v6.get("id"),
                }
            err_body = r.json()
            err_code = err_body.get("error", {}).get("code", "")
            err_msg  = err_body.get("error", {}).get("message", r.text)
            add_log(f"  创建失败 [{server_type}] code={err_code}: {err_msg}", "error")
            if err_code in ("uniqueness_error", "invalid_input") and "name" in err_msg.lower():
                return {"_name_conflict": True}
            return None
        except Exception as e:
            add_log(f"  创建异常: {e}", "error"); return None

    def create_server_with_fallback(self, name: str, server_types: List[str],
                                    image_id: int, ssh_keys: List,
                                    location: str = "nbg1",
                                    primary_ipv4: Optional[int] = None,
                                    primary_ipv6: Optional[int] = None) -> Optional[Dict]:
        base_name = self.sanitize_name(name)
        fallback_name = self.sanitize_name(f"{base_name}-{int(time.time()) % 100000}")

        for st in server_types:
            add_log(f"  → 尝试型号 [{st}] name={base_name} ...")
            result = self.create_server(base_name, st, image_id, ssh_keys, location,
                                        primary_ipv4, primary_ipv6)

            if result and not result.get("_name_conflict"):
                add_log(f"  ✅ [{st}] 创建成功: {result['ip']}")
                return result

            if result and result.get("_name_conflict"):
                add_log(f"  ⚠ 名称冲突，改用备用名称 [{fallback_name}] 重试...", "warn")
                result2 = self.create_server(fallback_name, st, image_id, ssh_keys, location,
                                             primary_ipv4, primary_ipv6)
                if result2 and not result2.get("_name_conflict"):
                    add_log(f"  ✅ [{st}] 备用名创建成功: {result2['ip']}")
                    return result2
                add_log(f"  ✗ [{st}] 备用名仍失败，尝试下一型号...", "warn")
            else:
                add_log(f"  ✗ [{st}] 无货或其他错误，尝试下一个...", "warn")

        add_log("❌ 所有型号均失败", "error")
        return None

    def delete_server(self, server_id: int) -> bool:
        try:
            r = requests.delete(f"{self.BASE}/servers/{server_id}",
                                headers=self.headers, timeout=15)
            if r.status_code == 404:
                add_log(f"  服务器 {server_id} 已不存在（视为删除成功）")
                return True
            r.raise_for_status()
            for i in range(30):
                time.sleep(3)
                chk = requests.get(f"{self.BASE}/servers/{server_id}",
                                   headers=self.headers, timeout=10)
                if chk.status_code == 404:
                    add_log(f"  服务器 {server_id} 已确认删除 ({(i+1)*3}s)")
                    return True
            add_log(f"  服务器 {server_id} 等待删除超时", "warn")
            return False
        except Exception as e:
            add_log(f"  删除服务器 {server_id} 失败: {e}", "error"); return False


def get_hetzner() -> Optional[HetznerAPI]:
    cfg = load_config()
    return HetznerAPI(cfg["hetzner_api_key"]) if cfg.get("hetzner_api_key") else None


# ─── 动态型号目录（从 Hetzner API 拉取配置 / 价格 / 可用性）──────────────────
def _series_of(name: str) -> str:
    m = re.match(r'([a-z]+)', name or '')
    return m.group(1).upper() if m else ''


def _fmt_bytes_traffic(b) -> str:
    try:
        b = int(b or 0)
    except (TypeError, ValueError):
        return ""
    if b <= 0:
        return ""
    tb = b / (1024 ** 4)
    if tb >= 1:
        return f"{tb:g}TB"
    return f"{b / (1024 ** 3):g}GB"


def build_dynamic_catalog(server_types: List[Dict]) -> Dict[str, Dict]:
    """把 /server_types 的响应整理成前端友好结构（含各地区的价格与库存）。"""
    catalog: Dict[str, Dict] = {}
    for st in server_types or []:
        name = st.get("name", "")
        if not name:
            continue
        prices: Dict[str, Dict] = {}
        for p in st.get("prices") or []:
            loc = p.get("location")
            if not loc:
                continue
            h = p.get("price_hourly") or {}
            m = p.get("price_monthly") or {}
            t = p.get("price_per_tb_traffic") or {}
            prices[loc] = {
                "hourly_net": h.get("net"), "hourly_gross": h.get("gross"),
                "monthly_net": m.get("net"), "monthly_gross": m.get("gross"),
                "included_traffic": p.get("included_traffic", 0),
                "traffic_per_tb_net": t.get("net"),
            }
        locs: Dict[str, Dict] = {}
        for l in st.get("locations") or []:
            locs[l.get("name")] = {
                "available": l.get("available", True),
                "deprecation": l.get("deprecation"),
            }
        dep = st.get("deprecation") or {}
        deprecated = bool(st.get("deprecated")) or bool(dep.get("unavailable_after"))
        default_loc = "nbg1" if "nbg1" in prices else (next(iter(prices), ""))
        catalog[name] = {
            "id": st.get("id"),
            "cores": st.get("cores"),
            "memory": st.get("memory"),
            "disk": st.get("disk"),
            "series": _series_of(name),
            "arch": st.get("architecture", "x86"),
            "cpu_type": st.get("cpu_type", ""),
            "category": st.get("category", ""),
            "storage_type": st.get("storage_type", ""),
            "deprecated": deprecated,
            "deprecation": dep,
            "prices": prices,
            "locations": locs,
            "traffic": _fmt_bytes_traffic((prices.get(default_loc) or {}).get("included_traffic")),
        }
    return catalog


def refresh_catalog(reason: str = "") -> bool:
    """从 Hetzner API 拉取型号/地区/价格并缓存到 monitor_state。"""
    hz = get_hetzner()
    if not hz:
        return False
    try:
        server_types = hz.get_server_types()
        if not server_types:
            add_log(f"目录刷新失败：未获取到型号{' ('+reason+')' if reason else ''}", "warn")
            return False
        catalog = build_dynamic_catalog(server_types)
        monitor_state["catalog"] = catalog
        monitor_state["locations"] = hz.get_locations()
        monitor_state["datacenters"] = hz.get_datacenters()
        monitor_state["pricing"] = hz.get_pricing()
        monitor_state["catalog_updated"] = datetime.now().isoformat()
        add_log(f"📦 已从 API 刷新型号目录：{len(catalog)} 个型号 / "
                f"{len(monitor_state['locations'])} 个地区{' ('+reason+')' if reason else ''}")
        return True
    except Exception as e:
        add_log(f"目录刷新异常: {e}", "error")
        return False


def get_catalog() -> Dict[str, Dict]:
    """优先返回 API 目录，未拉取时回退到内置目录。"""
    cat = monitor_state.get("catalog")
    if cat:
        return cat
    return {k: dict(v) for k, v in SERVER_TYPE_CATALOG.items()}


# ─── Primary IP 锁 / 保留辅助 ─────────────────────────────────────────────────
def _set_ip_lock(ip_id: int, locked: bool) -> Tuple[bool, str]:
    """锁定 = 关闭自动删除 + 开启删除保护；解锁相反。"""
    hz = get_hetzner()
    if not hz:
        return False, "API Key 未配置"
    ok1, e1 = hz.update_primary_ip(ip_id, auto_delete=not locked)
    ok2, e2 = hz.change_primary_ip_protection(ip_id, delete=bool(locked))
    if ok1 and ok2:
        return True, ("已锁定" if locked else "已解锁")
    return False, (e1 or e2 or "操作失败")


def _preserved_primary_ips(server: Dict, ip_map: Dict[int, Dict]) -> Tuple[Optional[int], Optional[int]]:
    """判断服务器重建时是否保留其 Primary IP（已锁定 / 关闭自动删除）。"""
    keep_v4 = keep_v6 = None
    for key, out in (("primary_ipv4_id", "v4"), ("primary_ipv6_id", "v6")):
        ip_id = server.get(key)
        if not ip_id:
            continue
        info = ip_map.get(ip_id)
        if not info:
            continue
        if (not info.get("auto_delete")) or (info.get("protection") or {}).get("delete"):
            if out == "v4":
                keep_v4 = ip_id
            else:
                keep_v6 = ip_id
    return keep_v4, keep_v6


def _free_locked_primary_ips(hz: "HetznerAPI", location: Optional[str] = None,
                             ip_type: str = "ipv4",
                             exclude: Optional[set] = None) -> List[Dict]:
    """
    返回「已被保护（锁定）且未分配」的 Primary IP 列表，供创建服务器时优先复用。
    location 为空则不限制地区；exclude 用于排除本批次已占用的 IP id。
    """
    exclude = exclude or set()
    try:
        ips = hz.get_primary_ips()
    except Exception as e:
        add_log(f"获取 Primary IP 列表失败: {e}", "warn")
        return []
    out = []
    for ip in ips or []:
        if not ip.get("id") or ip.get("id") in exclude:
            continue
        if ip.get("type") != ip_type:
            continue
        if ip.get("assignee_id"):
            continue
        locked = (not ip.get("auto_delete")) or (ip.get("protection") or {}).get("delete")
        if not locked:
            continue
        if location and (ip.get("location") or {}).get("name") != location:
            continue
        out.append({"id": ip["id"], "ip": ip.get("ip"), "name": ip.get("name"),
                    "location": (ip.get("location") or {}).get("name", "")})
    return out


# ─── 全局 Cookie 管理器单例 ────────────────────────────────────────────────────
_vcm_instance: Optional["VertexCookieManager"] = None
_vcm_signature: Optional[tuple] = None
_vcm_lock = threading.Lock()


def reset_vcm():
    """配置变更后强制下次重建 Cookie 管理器。"""
    global _vcm_instance, _vcm_signature
    with _vcm_lock:
        _vcm_instance = None
        _vcm_signature = None


def _build_vcm() -> Optional["VertexCookieManager"]:
    global _vcm_instance, _vcm_signature
    if not _VCM_AVAILABLE:
        return None
    with _vcm_lock:
        cfg   = load_config()
        url   = cfg.get("vertex_api_url", "")
        user  = cfg.get("vertex_username", "")
        plain  = cfg.get("vertex_password", "")
        hashed = cfg.get("vertex_password_md5", "")
        interval = int(cfg.get("vertex_cookie_check_interval", 300))
        # 凭据签名：任一字段变化则重建实例
        signature = (url, user, plain, hashed, interval)
        if _vcm_instance is not None and _vcm_signature == signature:
            return _vcm_instance
        _vcm_instance = None
        _vcm_signature = signature
        if url and user and (plain or hashed):
            if plain:
                _vcm_instance = VertexCookieManager(
                    login_url      = url,
                    username       = user,
                    password       = plain,
                    check_interval = interval,
                )
            else:
                _vcm_instance = VertexCookieManager(
                    login_url          = url,
                    username           = user,
                    password           = hashed,
                    password_is_hashed = True,
                    check_interval     = interval,
                )
        return _vcm_instance


# ─── Vertex Downloader API ────────────────────────────────────────────────────────────
class VertexAPI:
    def __init__(self, base_url: str, cookies: str, keyword: str = "Hetzner",
                 cookie_manager=None):
        self.base_url       = base_url.rstrip('/')
        self.keyword        = keyword
        self.cookie_manager = cookie_manager
        self._cookies       = cookies
        self.headers        = {'Content-Type': 'application/json', 'Cookie': cookies}

    def _refresh_cookie(self) -> bool:
        if not self.cookie_manager:
            return False
        new_cookie = self.cookie_manager.get_valid_cookie(force_refresh=True)
        if new_cookie and new_cookie != self._cookies:
            self._cookies = new_cookie
            self.headers['Cookie'] = new_cookie
            cfg = load_config()
            cfg['vertex_cookies'] = new_cookie
            save_config(cfg)
            add_log(f"[VTCookie] Cookie 已自动刷新并保存")
            return True
        return False

    def _get(self, path: str, retry: bool = True):
        r = requests.get(f"{self.base_url}{path}", headers=self.headers, timeout=10)
        if r.status_code in (401, 403) and retry and self._refresh_cookie():
            add_log(f"Vertex: GET {path} 收到 {r.status_code}，尝试刷新 Cookie 后重试", "warn")
            r = requests.get(f"{self.base_url}{path}", headers=self.headers, timeout=10)
        r.raise_for_status()
        return r

    def _post(self, path: str, json_data=None, retry: bool = True):
        r = requests.post(f"{self.base_url}{path}", headers=self.headers,
                          json=json_data, timeout=10)
        if r.status_code in (401, 403) and retry and self._refresh_cookie():
            add_log(f"Vertex: POST {path} 收到 {r.status_code}，尝试刷新 Cookie 后重试", "warn")
            r = requests.post(f"{self.base_url}{path}", headers=self.headers,
                              json=json_data, timeout=10)
        r.raise_for_status()
        return r

    def _extract_ip(self, url: str) -> Optional[str]:
        m = re.search(r'(\d{1,3}(?:\.\d{1,3}){3})', url or '')
        return m.group(1) if m else None

    def get_all_downloaders(self) -> List[Dict]:
        try:
            r = self._get("/api/downloader/list")
            data = r.json()
            items = data.get('data', data) if isinstance(data, dict) else data
            return items if isinstance(items, list) else []
        except Exception as e:
            add_log(f"Vertex: 获取下载器失败: {e}", "error"); return []

    def get_hetzner_downloaders(self) -> List[Dict]:
        return [d for d in self.get_all_downloaders()
                if self.keyword.lower() in d.get('alias', '').lower()]

    def update_downloader_ip(self, downloader: Dict, new_ip: str) -> bool:
        alias = downloader.get('alias', '?')
        old_url = downloader.get('clientUrl', '') or ''
        old_ip = self._extract_ip(old_url)

        # ── 修复：若 clientUrl 为空，直接用 ip 字段组装 URL ──
        if not old_url:
            add_log(f"Vertex: [{alias}] clientUrl 为空，跳过", "warn")
            return False

        # 若 URL 中没有旧 IP，也尝试从其他字段提取
        if not old_ip:
            add_log(f"Vertex: [{alias}] 无法从 URL 提取 IP: {old_url}，跳过", "warn")
            return False

        updated = dict(downloader)
        updated['clientUrl'] = old_url.replace(old_ip, new_ip)

        # 同时更新 url/host 等字段（兼容不同版本的 Vertex）
        for field in ('url', 'host'):
            if field in updated and updated[field]:
                updated[field] = str(updated[field]).replace(old_ip, new_ip)

        try:
            self._post("/api/downloader/modify", json_data=updated)
            add_log(f"Vertex: [{alias}] {old_ip} → {new_ip} ✓")
            return True
        except Exception as e:
            add_log(f"Vertex: 更新 [{alias}] 失败: {e}", "error")
            return False

    def sync_with_server_ips(self, server_ips: List[str]) -> Dict[str, int]:
        """
        将 Hetzner 下载器与当前服务器 IP 列表做负载均衡分配。
        每台下载器分配一个 IP，多于服务器数量时循环分配。
        """
        if not server_ips:
            add_log("Vertex: 无服务器 IP，跳过", "warn")
            return {'updated': 0, 'kept': 0, 'failed': 0}

        downloaders = self.get_hetzner_downloaders()
        if not downloaders:
            add_log(f"Vertex: 无匹配关键词 '{self.keyword}' 的下载器", "warn")
            return {'updated': 0, 'kept': 0, 'failed': 0}

        add_log(f"Vertex: 开始同步 {len(downloaders)} 个下载器，服务器IP: {server_ips}")

        # 当前各下载器已有的 IP
        current_ips: Dict[str, Optional[str]] = {}
        ip_counter = Counter()
        for dl in downloaders:
            alias = dl.get('alias', '')
            ip = self._extract_ip(dl.get('clientUrl', '') or '')
            current_ips[alias] = ip
            if ip:
                ip_counter[ip] += 1

        # 冲突 IP（同一 IP 被多个下载器使用）
        duplicate_ips = {ip for ip, cnt in ip_counter.items() if cnt > 1}
        if duplicate_ips:
            add_log(f"Vertex: 检测到冲突IP: {', '.join(duplicate_ips)}", "warn")

        # 过期 IP（当前不在服务器列表中的 IP）
        stale_ips = {ip for ip in ip_counter if ip not in server_ips}
        if stale_ips:
            add_log(f"Vertex: 检测到过期IP: {', '.join(stale_ips)}", "warn")

        # ── 重新分配策略 ──
        # 1. 先保留无冲突、无过期的已有分配
        assignment: Dict[str, str] = {}
        used_ips: List[str] = []

        for dl in downloaders:
            alias = dl.get('alias', '')
            ip = current_ips.get(alias)
            if (ip and ip in server_ips
                    and ip not in duplicate_ips
                    and ip not in stale_ips):
                assignment[alias] = ip
                used_ips.append(ip)

        # 2. 为剩余下载器分配（循环轮转所有服务器 IP）
        unassigned = [dl for dl in downloaders if dl.get('alias', '') not in assignment]

        # 构建轮转队列：优先补充未被使用的 IP
        available_pool = []
        for ip in server_ips:
            if ip not in used_ips:
                available_pool.append(ip)
        # 如果池不够，循环补全
        idx = 0
        for i, dl in enumerate(unassigned):
            alias = dl.get('alias', '')
            if available_pool:
                target_ip = available_pool.pop(0)
            else:
                # 所有 IP 都已分配，循环复用
                target_ip = server_ips[idx % len(server_ips)]
                idx += 1
            assignment[alias] = target_ip

        # ── 执行更新 ──
        updated = kept = failed = 0
        for dl in downloaders:
            alias = dl.get('alias', '')
            target = assignment.get(alias)
            if not target:
                add_log(f"Vertex: [{alias}] 无分配目标IP，跳过", "warn")
                failed += 1
                continue
            cur = current_ips.get(alias)
            if cur == target:
                add_log(f"Vertex: [{alias}] IP 无需变更 ({target})")
                kept += 1
            else:
                if self.update_downloader_ip(dl, target):
                    updated += 1
                else:
                    failed += 1

        add_log(f"Vertex: 同步完成 — 更新 {updated} / 保持 {kept} / 失败 {failed}")
        return {'updated': updated, 'kept': kept, 'failed': failed}

    def test_connection(self) -> Tuple[bool, str]:
        try:
            r = self._get("/api/downloader/list")
            if r.status_code == 200:
                data = r.json()
                items = data.get('data', data) if isinstance(data, dict) else data
                return True, f"连接成功，共 {len(items) if isinstance(items, list) else '?'} 个下载器"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)


def get_vertex() -> Optional[VertexAPI]:
    cfg = load_config()
    url  = cfg.get("vertex_api_url")
    user = cfg.get("vertex_username")
    pwd  = cfg.get("vertex_password") or cfg.get("vertex_password_md5")

    if url and (cfg.get("vertex_cookies") or (user and pwd)):
        vcm = _build_vcm()
        cookies = cfg.get("vertex_cookies") or ""
        if vcm:
            fresh = vcm.get_valid_cookie()
            if fresh:
                cookies = fresh
        return VertexAPI(
            cfg["vertex_api_url"], cookies,
            cfg.get("vertex_downloader_keyword", "Hetzner"),
            cookie_manager=vcm,
        )
    return None


# ─── 通用：同步 Vertex IP（从当前服务器缓存/API 获取 IP）─────────────────────
def sync_vertex_ips(reason: str = ""):
    """同步 Vertex 下载器 IP，优先用缓存，无缓存则实时拉取"""
    cfg = load_config()
    if not cfg.get("vertex_sync_enabled"):
        return
    vertex = get_vertex()
    if not vertex:
        return

    ips = [s["ipv4"] for s in monitor_state["servers_cache"] if s.get("ipv4")]
    if not ips:
        hz = get_hetzner()
        if hz:
            ips = [(s.get("public_net", {}).get("ipv4") or {}).get("ip", "")
                   for s in hz.get_servers() if s.get("status") == "running"]
            ips = [ip for ip in ips if ip]

    if not ips:
        add_log(f"Vertex 同步跳过：无可用服务器IP{' ('+reason+')' if reason else ''}", "warn")
        return

    add_log(f"🔄 Vertex 同步 [{reason}]，IP列表: {ips}")
    vertex.sync_with_server_ips(ips)


# ─── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    cfg = load_config()
    token, chat_id = cfg.get("telegram_bot_token"), cfg.get("telegram_chat_id")
    if not token or not chat_id:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        add_log(f"Telegram 失败: {e}", "warn")


# ─── Server Enricher ───────────────────────────────────────────────────────────
def enrich_server(s: Dict) -> Dict:
    outgoing = int(s.get("outgoing_traffic") or 0)
    included = int(s.get("included_traffic") or 1)
    ratio = outgoing / included if included > 0 else 0
    pub = s.get("public_net", {})
    pub_v4 = pub.get("ipv4") or {}
    pub_v6 = pub.get("ipv6") or {}
    ipv4 = pub_v4.get("ip", "")
    ipv6 = pub_v6.get("ip", "")
    img = s.get("image") or {}
    stype_name = (s.get("server_type") or {}).get("name", "")
    location = (s.get("datacenter") or {}).get("location", {}).get("name", "")
    catalog = get_catalog()
    cat = catalog.get(stype_name, {})
    price = (cat.get("prices") or {}).get(location, {})
    return {
        "id": s["id"], "name": s["name"], "status": s.get("status", "unknown"),
        "ipv4": ipv4, "ipv6": ipv6, "server_type": stype_name,
        "server_type_info": cat or SERVER_TYPE_CATALOG.get(stype_name, {}),
        "location": location,
        "datacenter": (s.get("datacenter") or {}).get("name", ""),
        "outgoing_traffic": outgoing, "included_traffic": included,
        "usage_percent": round(ratio * 100, 2), "usage_ratio": ratio,
        "created": s.get("created", ""),
        # Primary IP（用于锁 IP / 重建保留）
        "primary_ipv4_id": pub_v4.get("id"),
        "primary_ipv6_id": pub_v6.get("id"),
        # 价格（net，单位 EUR）
        "price": price,
        "monthly_price": price.get("monthly_net"),
        "hourly_price": price.get("hourly_net"),
        "image": {"id": img.get("id"), "name": img.get("name") or img.get("description", ""),
                  "type": img.get("type", "")}
    }


# ─── Core Monitor Logic ─────────────────────────────────────────────────────────
def do_check_and_rebuild():
    cfg = load_config()
    hz = get_hetzner()
    if not hz:
        add_log("未配置 API Key", "warn"); return

    add_log("━━━ 开始流量检查 ━━━")
    servers = hz.get_servers()
    if not servers:
        add_log("未获取到服务器", "warn"); return

    enriched = [enrich_server(s) for s in servers]
    monitor_state["servers_cache"] = enriched
    monitor_state["last_check"] = datetime.now().isoformat()

    threshold = float(cfg.get("traffic_threshold", 0.8))
    auto_rebuild = cfg.get("auto_rebuild_enabled", True)
    server_types = cfg.get("server_types", ["cx43"])
    ssh_keys = cfg.get("ssh_keys", [])
    snapshot_id = cfg.get("initial_snapshot_id", "")
    location = cfg.get("default_location", "nbg1")

    high_traffic = [s for s in enriched if s["usage_ratio"] >= threshold]
    add_log(f"📊 {len(enriched)} 台，{len(high_traffic)} 台超 {threshold*100:.0f}% 阈值")

    summary = []
    for s in enriched:
        icon = "🔴" if s["usage_ratio"] >= threshold else ("🟡" if s["usage_ratio"] >= 0.6 else "🟢")
        add_log(f"{icon} {s['name']} ({s['ipv4']}) {s['usage_percent']}%")
        summary.append(f"{icon} <b>{s['name']}</b> ({s['ipv4']})\n"
                        f"   {s['usage_percent']}% — {s['outgoing_traffic']/1024**3:.2f}/"
                        f"{s['included_traffic']/1024**3:.2f}GB")

    rebuild_results = []
    if high_traffic and auto_rebuild and snapshot_id:
        try:
            ip_map = {ip["id"]: ip for ip in hz.get_primary_ips() if ip.get("id")}
        except Exception:
            ip_map = {}
        for s in high_traffic:
            add_log(f"⚠️ {s['name']} ({s['ipv4']}) 超阈值 {s['usage_percent']}%，开始重建...")

            old_name = s["name"]
            old_ip   = s["ipv4"]
            old_id   = s["id"]

            keep_v4, keep_v6 = _preserved_primary_ips(s, ip_map)
            if keep_v4 or keep_v6:
                # 关闭自动删除，避免随旧服务器一起被销毁
                for kid in (keep_v4, keep_v6):
                    if kid:
                        hz.update_primary_ip(kid, auto_delete=False)
                add_log(f"  🔒 锁定 IP 保留：IPv4#{keep_v4} IPv6#{keep_v6}")

            add_log(f"  [1/2] 删除旧服务器 {old_name} (id={old_id})...")
            if not hz.delete_server(old_id):
                add_log(f"  ❌ 删除旧服务器失败，跳过重建", "error")
                rebuild_results.append({"name": old_name, "ok": False, "reason": "旧服务器删除失败"})
                continue

            add_log(f"  ✅ 旧服务器已删除，等待 5s 确保名称释放...")
            time.sleep(5)

            add_log(f"  [2/2] 创建新服务器 {old_name}...")
            new_sv = hz.create_server_with_fallback(
                old_name, server_types, int(snapshot_id), ssh_keys, location,
                primary_ipv4=keep_v4, primary_ipv6=keep_v6
            )
            if not new_sv:
                add_log(f"  ❌ 新服务器创建失败", "error")
                rebuild_results.append({"name": old_name, "ok": False, "reason": "新服务器创建失败（旧服务器已删除）"})
                continue

            add_log(f"  ✅ 重建完成: {old_ip} → {new_sv['ip']} [{new_sv['server_type']}]  name={new_sv['name']}")
            rebuild_results.append({
                "name": old_name, "ok": True,
                "new_name": new_sv["name"],
                "old_ip": old_ip, "new_ip": new_sv["ip"],
                "server_type": new_sv["server_type"]
            })

    # 刷新服务器列表
    time.sleep(2)
    final = hz.get_servers()
    if final:
        monitor_state["servers_cache"] = [enrich_server(s) for s in final]

    # 同步 Vertex（有重建时才同步）
    if rebuild_results:
        sync_vertex_ips("流量重建后")

    # Telegram
    tg = [f"<b>🖥 Hetzner 流量报告</b>",
          f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
          f"服务器: {len(enriched)} | 超阈值: {len(high_traffic)}\n",
          *summary]
    if rebuild_results:
        tg.append("\n<b>⚙️ 重建结果:</b>")
        for r in rebuild_results:
            if r["ok"]:
                tg.append(f"✅ {r['name']}: {r.get('old_ip')} → <code>{r['new_ip']}</code> [{r['server_type']}]")
            else:
                tg.append(f"❌ {r['name']}: {r['reason']}")
    send_telegram("\n".join(tg))
    add_log("━━━ 检查完成 ━━━")


def monitor_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        cfg = load_config()
        interval = int(cfg.get("check_interval", 1200))
        if cfg.get("enable_time_window"):
            now = datetime.now().time()
            start = dt_time(int(cfg.get("work_start_hour", 8)), 0)
            end = dt_time(int(cfg.get("work_end_hour", 23)), int(cfg.get("work_end_minute", 30)))
            if not (start <= now <= end):
                add_log("⏸ 非工作时段，跳过")
                stop_event.wait(timeout=60)
                continue
        try:
            do_check_and_rebuild()
        except Exception as e:
            add_log(f"监控异常: {e}", "error")
        monitor_state["next_check"] = datetime.fromtimestamp(
            datetime.now().timestamp() + interval).isoformat()
        stop_event.wait(timeout=interval)


# ─── 定时删建任务 ────────────────────────────────────────────────────────────────

def do_scheduled_delete_all(opts: Optional[Dict] = None):
    """定时删除服务器。opts.only_prefix 可只删除指定前缀的机器（留空=全部）。"""
    opts = opts or {}
    add_log("🗑️ ━━━ 定时任务：删除服务器 ━━━")
    hz = get_hetzner()
    if not hz:
        add_log("定时删除：未配置 API Key", "warn"); return

    servers = hz.get_servers()
    only_prefix = (opts.get("only_prefix") or "").strip()
    if only_prefix:
        servers = [s for s in servers if str(s.get("name", "")).startswith(only_prefix)]
        add_log(f"定时删除：仅匹配前缀 '{only_prefix}'")
    if not servers:
        add_log("定时删除：无可删除服务器，跳过"); return

    add_log(f"定时删除：共 {len(servers)} 台服务器，开始删除...")
    deleted = 0
    failed_list = []
    names = [s.get("name", str(s["id"])) for s in servers]

    for s in servers:
        sid = s["id"]
        sname = s.get("name", str(sid))
        add_log(f"  删除 {sname} (id={sid})...")
        if hz.delete_server(sid):
            deleted += 1
        else:
            failed_list.append(sname)

    monitor_state["servers_cache"] = []
    monitor_state["last_scheduled_delete"] = datetime.now().isoformat()
    add_log(f"定时删除完成：成功 {deleted} / 失败 {len(failed_list)} 台")

    send_telegram(
        f"<b>🗑️ 定时删除完成</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"删除服务器: {', '.join(names)}\n"
        f"结果: ✅ {deleted} 台 / ❌ {len(failed_list)} 台失败"
        + (f"\n失败: {', '.join(failed_list)}" if failed_list else "")
    )


def do_scheduled_create(opts: Optional[Dict] = None):
    """
    创建服务器：把账户内服务器「补足到上限」（max_servers 是目标总数，不是每次新增数）。
    - 优先复用已保护（锁定）且空闲的 Primary IP
    - 按 server_types 的先后顺序作为型号优先级依次尝试
    """
    opts = opts or {}
    cfg = load_config()
    add_log("🚀 ━━━ 定时任务：创建服务器（补足至上限）━━━")

    hz = get_hetzner()
    if not hz:
        add_log("定时创建：未配置 API Key", "warn"); return None

    def _opt(key, default):
        v = opts.get(key)
        return default if v in (None, "") else v

    max_servers = int(_opt("max_servers",
                           cfg.get("schedule_max_servers", cfg.get("schedule_create_count", 3))) or 3)
    snapshot_id = _opt("image_id", cfg.get("initial_snapshot_id", ""))
    # 型号严格取「监控配置」里的优先级，绝不使用任务级的历史快照，
    # 也绝不创建配置以外的机器（例如配置 cx43/cx33 就不会创建 cpx42）。
    server_types = list(cfg.get("server_types") or [])
    if isinstance(server_types, str):
        server_types = [server_types]
    ssh_keys = _opt("ssh_keys", cfg.get("ssh_keys", []))
    location = _opt("location", cfg.get("default_location", "nbg1"))
    prefix = _opt("prefix", cfg.get("schedule_server_name_prefix", "hetzner-auto"))
    use_locked = bool(opts.get("use_locked_ips", True))   # 有锁定的空闲 IP 就优先复用
    lock_new = bool(opts.get("lock_new_ips", False))      # 默认不额外锁定

    if not snapshot_id:
        add_log("定时创建：未配置快照 ID，跳过", "warn"); return None
    if not server_types:
        add_log("定时创建：监控配置未设置任何型号，跳过（不会创建配置以外的机器）", "warn")
        return None

    # ── 计算需要补充的数量（上限语义）─────────────────────────────
    existing_servers = hz.get_servers()
    existing_count = len(existing_servers)
    need = max_servers - existing_count

    if need <= 0:
        add_log(f"✅ 定时创建：当前已有 {existing_count} 台（上限 {max_servers} 台），无需创建")
        monitor_state["servers_cache"] = [enrich_server(s) for s in existing_servers]
        monitor_state["last_scheduled_create"] = datetime.now().isoformat()
        return {"created": [], "failed": 0, "max_servers": max_servers, "shortfall": 0}

    add_log(f"定时创建：当前 {existing_count} 台 / 上限 {max_servers} 台，需补充 {need} 台")
    add_log(f"  指定型号优先级：{' → '.join(server_types)}（仅创建这些型号）")

    # ── 优先复用已保护的空闲 IP ──────────────────────────────────
    free_locked = _free_locked_primary_ips(hz, location) if use_locked else []
    if free_locked:
        add_log(f"  🔒 发现 {len(free_locked)} 个已保护的空闲 IP，将优先复用")

    # 生成不与已有服务器名称冲突的候选名称
    existing_names = {s.get("name", "") for s in existing_servers}
    slots = []
    i = 1
    while len(slots) < need and i <= 999:
        candidate = f"{prefix}-{i:02d}"
        if candidate not in existing_names:
            slots.append(candidate)
        i += 1

    created_list = []
    failed = 0

    for idx, name in enumerate(slots):
        reuse = free_locked.pop(0) if free_locked else None
        pip = reuse["id"] if reuse else None
        tip = f"（复用 IP {reuse['ip']}）" if reuse else "（自动分配新 IP）"
        add_log(f"  创建第 {idx+1}/{len(slots)} 台: {name} {tip}")
        result = hz.create_server_with_fallback(
            name, server_types, int(snapshot_id), ssh_keys, location, primary_ipv4=pip
        )
        if result:
            if lock_new and not reuse:
                for pid in (result.get("primary_ipv4_id"), result.get("primary_ipv6_id")):
                    if pid:
                        _set_ip_lock(pid, True)
            result["reused_ip"] = bool(reuse)
            created_list.append(result)
            add_log(f"  ✅ {name} → {result['ip']} [{result['server_type']}]")
        else:
            failed += 1
            add_log(f"  ❌ {name} 创建失败", "error")
        time.sleep(2)

    # 刷新缓存
    time.sleep(3)
    final = hz.get_servers()
    if final:
        monitor_state["servers_cache"] = [enrich_server(s) for s in final]

    monitor_state["last_scheduled_create"] = datetime.now().isoformat()
    shortfall = max(0, need - len(created_list))
    add_log(f"定时创建完成：成功 {len(created_list)} / 失败 {failed} 台（上限 {max_servers}）"
            + (f"，仍有 {shortfall} 台待重试" if shortfall else ""))

    # 同步 Vertex
    if created_list:
        sync_vertex_ips("定时创建后")

    send_telegram(
        f"<b>🚀 定时创建完成</b>\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"已有 {existing_count} 台 → 补充 {len(created_list)} 台 / 失败 {failed} 台（上限 {max_servers}）\n"
        + "\n".join([f"✅ <code>{s['ip']}</code> [{s['server_type']}] {s['name']}"
                     + ("（复用IP）" if s.get("reused_ip") else "") for s in created_list])
    )
    return {"created": created_list, "failed": failed, "max_servers": max_servers,
            "shortfall": shortfall}


def _get_tz_now(cfg: Dict) -> datetime:
    """
    获取指定时区的当前时间。
    优先使用 pytz，若未安装则尝试 zoneinfo（Python 3.9+），
    都不可用则回退到系统本地时间并记录警告。
    """
    tz_name = cfg.get("schedule_timezone", "Asia/Shanghai").strip()
    utc_now = datetime.utcnow().replace(tzinfo=None)

    # 尝试 pytz
    try:
        import pytz
        tz = pytz.timezone(tz_name)
        return datetime.now(tz).replace(tzinfo=None)
    except Exception:
        pass

    # 尝试 zoneinfo (Python 3.9+)
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:
        pass

    # 回退：系统本地时间
    add_log(f"⚠️ 时区 '{tz_name}' 解析失败（需安装 pytz 或 Python≥3.9），使用系统本地时间", "warn")
    return datetime.now()


def _parse_hhmm(task: Dict) -> Tuple[int, int]:
    try:
        return int(task.get("hour", 0)) % 24, int(task.get("minute", 0)) % 60
    except (TypeError, ValueError):
        return 0, 0


def _task_days(task: Dict) -> List[int]:
    days = task.get("days") or [0, 1, 2, 3, 4, 5, 6]
    try:
        return sorted({int(d) % 7 for d in days})
    except (TypeError, ValueError):
        return [0, 1, 2, 3, 4, 5, 6]


def _task_due(task: Dict, now: datetime, last_run_iso: Optional[str]) -> bool:
    """判断任务此刻是否应当触发（含去重）。"""
    if task.get("mode") == "interval":
        try:
            interval = max(1, int(task.get("interval_minutes", 60)))
        except (TypeError, ValueError):
            interval = 60
        if not last_run_iso:
            return True
        try:
            last = datetime.fromisoformat(last_run_iso)
        except Exception:
            return True
        return (now - last).total_seconds() >= interval * 60

    # daily 模式
    if now.weekday() not in _task_days(task):
        return False
    h, m = _parse_hhmm(task)
    if now.hour != h or now.minute != m:
        return False
    if last_run_iso:
        try:
            last = datetime.fromisoformat(last_run_iso)
            if last.date() == now.date() and last.hour == h and last.minute == m:
                return False
        except Exception:
            pass
    return True


def _task_next_run(task: Dict, now: datetime, last_run_iso: Optional[str]) -> Optional[datetime]:
    """计算任务下一次触发时间（用于前端展示）。"""
    from datetime import timedelta
    if task.get("mode") == "interval":
        try:
            interval = max(1, int(task.get("interval_minutes", 60)))
        except (TypeError, ValueError):
            interval = 60
        if last_run_iso:
            try:
                nxt = datetime.fromisoformat(last_run_iso) + timedelta(minutes=interval)
                return nxt if nxt > now else now
            except Exception:
                pass
        return now
    days = _task_days(task)
    if not days:
        return None
    h, m = _parse_hhmm(task)
    for delta in range(0, 8):
        cand = (now + timedelta(days=delta)).replace(hour=h, minute=m, second=0, microsecond=0)
        if cand < now:
            continue
        if cand.weekday() in days:
            return cand
    return None


def _retry_interval_minutes(cfg: Dict) -> int:
    try:
        return max(1, int(cfg.get("schedule_retry_interval_minutes", 5) or 5))
    except (TypeError, ValueError):
        return 5


def _clear_retry(task_id: str):
    with monitor_state["retry_lock"]:
        monitor_state["retry_queue"] = [
            x for x in monitor_state["retry_queue"] if x.get("task_id") != task_id
        ]


def _enqueue_retry(task: Dict, result: Dict, cfg: Dict):
    """指定型号缺货时，登记一个 N 分钟后自动重试的条目。"""
    from datetime import timedelta
    task_id = task.get("id") or task.get("type")
    interval = _retry_interval_minutes(cfg)
    now = _get_tz_now(cfg)
    entry = {
        "task_id": task_id,
        "options": dict(task.get("options") or {}),
        "remaining": int(result.get("shortfall", 0)),
        "attempts": 0,
        "next_at": (now + timedelta(minutes=interval)).isoformat(),
    }
    with monitor_state["retry_lock"]:
        monitor_state["retry_queue"] = [
            x for x in monitor_state["retry_queue"] if x.get("task_id") != task_id
        ] + [entry]
    add_log(f"⏳ 指定型号缺货，{interval} 分钟后自动重试（剩余 {entry['remaining']} 台，仅重试指定型号）", "warn")


def _process_retries(cfg: Dict):
    """处理创建任务的重试队列（只重试指定型号，绝不创建配置以外的机器）。"""
    from datetime import timedelta
    now = _get_tz_now(cfg)
    interval = _retry_interval_minutes(cfg)
    tasks = {t.get("id"): t for t in (cfg.get("scheduled_tasks") or [])}
    with monitor_state["retry_lock"]:
        queue = list(monitor_state["retry_queue"])

    for item in queue:
        task_id = item.get("task_id")
        task = tasks.get(task_id)
        if not task or not task.get("enabled"):
            _clear_retry(task_id)
            continue
        try:
            next_at = datetime.fromisoformat(item.get("next_at") or "")
        except Exception:
            next_at = now
        if now < next_at:
            continue

        add_log(f"🔁 定时创建缺货重试（{task_id}）…")
        try:
            result = do_scheduled_create(item.get("options") or {})
        except Exception as e:
            add_log(f"重试异常: {e}", "error")
            result = None

        if result is not None and result.get("shortfall", 0) <= 0:
            _clear_retry(task_id)
            add_log(f"✅ 缺货重试成功，创建任务已完成（{task_id}）")
        else:
            # 仍缺货或执行异常 → 继续排下一次重试
            with monitor_state["retry_lock"]:
                for x in monitor_state["retry_queue"]:
                    if x.get("task_id") == task_id:
                        x["remaining"] = int((result or {}).get("shortfall", x.get("remaining", 0)))
                        x["attempts"] = int(x.get("attempts", 0)) + 1
                        x["next_at"] = (now + timedelta(minutes=interval)).isoformat()


def execute_task(task: Dict):
    """执行单个定时任务。"""
    ttype = (task.get("type") or "").lower()
    opts = task.get("options") or {}
    if ttype == "create":
        result = do_scheduled_create(opts)
        task_id = task.get("id") or "create"
        # 有缺口 → 排重试；已补齐 → 取消该任务的待重试
        if result is not None and result.get("shortfall", 0) <= 0:
            _clear_retry(task_id)
        else:
            _enqueue_retry(task, result or {}, load_config())
        return result
    if ttype == "delete":
        _clear_retry(task.get("id") or "delete")
        return do_scheduled_delete_all(opts)
    if ttype == "sync":
        sync_vertex_ips(f"定时同步任务 {task.get('id', '')}")
        return {"synced": True}
    add_log(f"未知定时任务类型: {ttype}", "warn")
    return None


def scheduler_loop(stop_event: threading.Event):
    """
    通用任务调度器：按配置的 tick 周期唤醒，遍历 scheduled_tasks。

    触发规则：
      - daily 模式：匹配 星期 + HH:MM，同一分钟当天只触发一次。
      - interval 模式：距上次执行超过 interval_minutes 才触发。
      - 所有状态放在 monitor_state['task_last_run']，重启后 interval 任务会立即补跑一次。
    """
    add_log("⏰ 定时任务调度器已启动")

    while not stop_event.is_set():
        cfg = load_config()
        try:
            tick = max(5, int(cfg.get("scheduler_tick_seconds", 20) or 20))
        except (TypeError, ValueError):
            tick = 20
        stop_event.wait(timeout=tick)
        if stop_event.is_set():
            break

        cfg = load_config()
        if not cfg.get("scheduled_tasks_enabled"):
            continue

        now = _get_tz_now(cfg)
        for task in (cfg.get("scheduled_tasks") or []):
            if not task.get("enabled"):
                continue
            tid = task.get("id") or task.get("type")
            last_iso = monitor_state["task_last_run"].get(tid)
            try:
                nxt = _task_next_run(task, now, last_iso)
                monitor_state["task_next_run"][tid] = nxt.isoformat() if nxt else None
            except Exception:
                monitor_state["task_next_run"][tid] = None

            if not _task_due(task, now, last_iso):
                continue

            # 先记录，避免任务执行期间被重复触发
            monitor_state["task_last_run"][tid] = now.isoformat()
            add_log(f"⏰ 触发定时任务 [{task.get('type')}] {tid}"
                    f"（{cfg.get('schedule_timezone', '本地')} {now.strftime('%H:%M')}）")
            try:
                execute_task(task)
            except Exception as e:
                add_log(f"定时任务 {tid} 异常: {e}", "error")

            if task.get("type") == "delete":
                monitor_state["last_scheduled_delete"] = now.isoformat()
            elif task.get("type") == "create":
                monitor_state["last_scheduled_create"] = now.isoformat()

        # 处理缺货重试队列（每 N 分钟尝试一次，直到创建成功或任务被关闭）
        try:
            _process_retries(cfg)
        except Exception as e:
            add_log(f"重试队列处理异常: {e}", "error")

    add_log("⏰ 定时任务调度器已停止")


def start_scheduler():
    if monitor_state["scheduler_running"]:
        return
    monitor_state["scheduler_stop_event"].clear()
    threading.Thread(
        target=scheduler_loop,
        args=(monitor_state["scheduler_stop_event"],),
        daemon=True, name="scheduler"
    ).start()
    monitor_state["scheduler_running"] = True
    add_log("⏰ 定时调度器启动")


def stop_scheduler():
    monitor_state["scheduler_stop_event"].set()
    monitor_state["scheduler_running"] = False
    add_log("⏰ 定时调度器停止")


# ─── Auth ──────────────────────────────────────────────────────────────────────
def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ═══════════════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json or {}
    cfg = load_config()
    if data.get("password") == cfg.get("web_password", "admin123"):
        session["authenticated"] = True
        session.permanent = True
        return jsonify({"success": True})
    return jsonify({"error": "密码错误"}), 401

@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/auth/status")
def auth_status():
    return jsonify({"authenticated": bool(session.get("authenticated"))})

# ─── Config ────────────────────────────────────────────────────────────────────
@app.route("/api/config", methods=["GET"])
@require_auth
def get_config():
    cfg = load_config()
    safe = {k: v for k, v in cfg.items() if k != "web_password"}
    for field in ("hetzner_api_key", "telegram_bot_token"):
        if safe.get(field):
            v = safe[field]
            safe[f"{field}_masked"] = v[:8] + "..." + v[-4:] if len(v) > 12 else "***"
            safe[field] = ""
    if safe.get("vertex_cookies"):
        safe["vertex_cookies_set"] = True
        safe["vertex_cookies"] = ""
    return jsonify(safe)

def _normalize_tasks(raw_tasks) -> List[Dict]:
    """清洗前端提交的任务列表，避免脏数据进入调度器。"""
    out: List[Dict] = []
    if not isinstance(raw_tasks, list):
        return out
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        ttype = str(t.get("type", "")).lower()
        if ttype not in VALID_TASK_TYPES:
            continue
        try:
            hour = max(0, min(23, int(t.get("hour", 0))))
            minute = max(0, min(59, int(t.get("minute", 0))))
            interval = max(1, int(t.get("interval_minutes", 60)))
        except (TypeError, ValueError):
            hour, minute, interval = 0, 0, 60
        try:
            days = sorted({max(0, min(6, int(d))) for d in (t.get("days") or [0, 1, 2, 3, 4, 5, 6])})
        except (TypeError, ValueError):
            days = [0, 1, 2, 3, 4, 5, 6]
        if not days:
            days = [0, 1, 2, 3, 4, 5, 6]
        out.append({
            "id": t.get("id") or _new_task_id(),
            "type": ttype,
            "enabled": bool(t.get("enabled", False)),
            "mode": "interval" if t.get("mode") == "interval" else "daily",
            "hour": hour,
            "minute": minute,
            "days": days,
            "interval_minutes": interval,
            "options": t.get("options") if isinstance(t.get("options"), dict) else {},
        })
    return out


@app.route("/api/config", methods=["POST"])
@require_auth
def update_config():
    data = request.json or {}
    cfg = load_config()
    sensitive = ("hetzner_api_key", "telegram_bot_token", "vertex_cookies")
    for k in DEFAULT_CONFIG.keys():
        if k in data:
            if k in sensitive and data[k] == "":
                continue
            cfg[k] = data[k]
    if "scheduled_tasks" in data:
        cfg["scheduled_tasks"] = _normalize_tasks(data.get("scheduled_tasks"))
    save_config(cfg)

    # Vertex 凭据变更 → 重建 Cookie 管理器
    if any(k in data for k in ("vertex_api_url", "vertex_username", "vertex_password",
                               "vertex_password_md5", "vertex_cookie_check_interval")):
        reset_vcm()

    add_log("⚙️ 配置已更新")
    # 若定时任务总开关变动，联动启停
    if "scheduled_tasks_enabled" in data:
        if data["scheduled_tasks_enabled"]:
            start_scheduler()
        else:
            stop_scheduler()
    return jsonify({"success": True})

@app.route("/api/config/server-type-catalog")
@require_auth
def server_type_catalog():
    if request.args.get("refresh") == "1" or not monitor_state.get("catalog"):
        threading.Thread(target=lambda: _safe(refresh_catalog), daemon=True).start()
    return jsonify({
        "catalog": get_catalog(),
        "locations": monitor_state.get("locations", []),
        "pricing": monitor_state.get("pricing", {}),
        "updated": monitor_state.get("catalog_updated"),
        "source": "api" if monitor_state.get("catalog") else "builtin",
    })

# ─── 地区 / 价格 / 产品总览 ───────────────────────────────────────────────────
@app.route("/api/locations")
@require_auth
def list_locations():
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    if not monitor_state.get("locations"):
        refresh_catalog("拉取地区")
    return jsonify({"locations": monitor_state.get("locations", [])})

@app.route("/api/pricing")
@require_auth
def get_pricing_route():
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    if not monitor_state.get("pricing"):
        refresh_catalog("拉取价格")
    return jsonify({"pricing": monitor_state.get("pricing", {})})

@app.route("/api/products")
@require_auth
def list_products():
    """列出账户下各类产品，便于总览。"""
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    servers = hz.get_servers()
    primary_ips = hz.get_primary_ips()
    floating_ips = hz.get_floating_ips()
    volumes = hz.get_volumes()
    load_balancers = hz.get_load_balancers()
    firewalls = hz.get_firewalls()
    images = hz.get_images("snapshot")
    ssh_keys = hz.get_ssh_keys()
    return jsonify({
        "counts": {
            "servers": len(servers),
            "primary_ips": len(primary_ips),
            "floating_ips": len(floating_ips),
            "volumes": len(volumes),
            "load_balancers": len(load_balancers),
            "firewalls": len(firewalls),
            "snapshots": len(images),
            "ssh_keys": len(ssh_keys),
        },
        "servers": [{"id": s["id"], "name": s["name"], "status": s.get("status"),
                     "ipv4": ((s.get("public_net") or {}).get("ipv4") or {}).get("ip", ""),
                     "server_type": (s.get("server_type") or {}).get("name", ""),
                     "location": ((s.get("datacenter") or {}).get("location") or {}).get("name", "")}
                    for s in servers],
        "primary_ips": [{
            "id": ip["id"], "name": ip.get("name"), "ip": ip.get("ip"), "type": ip.get("type"),
            "location": (ip.get("location") or {}).get("name", ""),
            "assignee_id": ip.get("assignee_id"), "assignee_type": ip.get("assignee_type"),
            "auto_delete": ip.get("auto_delete", False),
            "locked": bool((ip.get("protection") or {}).get("delete")) or not ip.get("auto_delete"),
            "delete_protection": bool((ip.get("protection") or {}).get("delete")),
            "blocked": ip.get("blocked", False),
            "created": ip.get("created"),
        } for ip in primary_ips],
        "floating_ips": [{
            "id": f["id"], "name": f.get("name"), "ip": f.get("ip"), "type": f.get("type"),
            "location": (f.get("home_location") or {}).get("name", ""),
            "server": f.get("server"),
        } for f in floating_ips],
        "volumes": [{
            "id": v["id"], "name": v.get("name"), "size": v.get("size"),
            "location": (v.get("location") or {}).get("name", ""),
            "server": v.get("server"), "status": v.get("status"),
        } for v in volumes],
        "load_balancers": [{
            "id": lb["id"], "name": lb.get("name"),
            "ipv4": ((lb.get("public_net") or {}).get("ipv4") or {}).get("ip", ""),
            "location": (lb.get("location") or {}).get("name", ""),
            "type": (lb.get("load_balancer_type") or {}).get("name", ""),
        } for lb in load_balancers],
        "firewalls": [{"id": fw["id"], "name": fw.get("name"),
                       "rules": len(fw.get("rules") or [])} for fw in firewalls],
        "snapshots": [{"id": i["id"], "name": i.get("name") or i.get("description", ""),
                       "disk_size": i.get("disk_size"), "created": i.get("created")}
                      for i in images],
        "ssh_keys": [{"id": k["id"], "name": k["name"]} for k in ssh_keys],
    })

# ─── Primary IP 管理（锁 IP / 批量创建 / 批量删除）────────────────────────────
def _serialize_primary_ip(ip: Dict) -> Dict:
    return {
        "id": ip["id"], "name": ip.get("name"), "ip": ip.get("ip"), "type": ip.get("type"),
        "location": (ip.get("location") or {}).get("name", ""),
        "assignee_id": ip.get("assignee_id"), "assignee_type": ip.get("assignee_type"),
        "auto_delete": ip.get("auto_delete", False),
        "delete_protection": bool((ip.get("protection") or {}).get("delete")),
        "locked": bool((ip.get("protection") or {}).get("delete")) or not ip.get("auto_delete"),
        "blocked": ip.get("blocked", False),
        "dns_ptr": ip.get("dns_ptr", []),
        "created": ip.get("created"),
        "labels": ip.get("labels", {}),
    }

@app.route("/api/primary-ips")
@require_auth
def list_primary_ips():
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    ips = hz.get_primary_ips()
    # 服务器 id -> 名称 便于展示
    server_names = {}
    try:
        server_names = {s["id"]: s.get("name", str(s["id"])) for s in hz.get_servers()}
    except Exception:
        pass
    out = []
    for ip in ips:
        d = _serialize_primary_ip(ip)
        d["assignee_name"] = server_names.get(ip.get("assignee_id"), "")
        out.append(d)
    return jsonify({"primary_ips": out, "count": len(out)})

@app.route("/api/primary-ips/create", methods=["POST"])
@require_auth
def create_primary_ips():
    data = request.json or {}
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    try:
        count = max(1, min(int(data.get("count", 1)), 50))
    except (TypeError, ValueError):
        count = 1
    ip_type = data.get("type", "ipv4")
    location = data.get("location") or load_config().get("default_location", "nbg1")
    prefix = (data.get("name_prefix") or f"ip-{location}").strip() or f"ip-{location}"
    auto_delete = bool(data.get("auto_delete", False))
    lock = bool(data.get("lock", True))
    add_log(f"➕ 批量创建 {count} 个 {ip_type}（{location}，前缀 {prefix}）...")
    results = []
    for i in range(1, count + 1):
        name = f"{prefix}-{i:02d}" if count > 1 else prefix
        res = hz.create_primary_ip(name, ip_type, location=location, auto_delete=auto_delete)
        if res.get("ok") and lock:
            ok_l, _ = _set_ip_lock(res["id"], True)
            res["locked"] = ok_l
        results.append(res)
        if res.get("ok"):
            add_log(f"  ✅ {name} → {res.get('ip')}")
        else:
            add_log(f"  ❌ {name}: {res.get('error')}", "error")
        time.sleep(0.4)
    ok_count = sum(1 for r in results if r.get("ok"))
    return jsonify({"success": True, "results": results,
                    "created": ok_count, "failed": len(results) - ok_count})

@app.route("/api/primary-ips/delete", methods=["POST"])
@require_auth
def delete_primary_ips():
    data = request.json or {}
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    ids = data.get("ids") or ([data["id"]] if data.get("id") else [])
    force = bool(data.get("force", False))
    results = []
    for raw in ids:
        try:
            ip_id = int(raw)
        except (TypeError, ValueError):
            continue
        if force:
            hz.change_primary_ip_protection(ip_id, delete=False)
        ok, msg = hz.delete_primary_ip(ip_id)
        results.append({"id": ip_id, "ok": ok, "message": msg})
        add_log(f"  {'🗑️' if ok else '❌'} 删除 IP #{ip_id}: {'成功' if ok else msg}",
                "info" if ok else "error")
    ok_count = sum(1 for r in results if r.get("ok"))
    return jsonify({"success": True, "results": results,
                    "deleted": ok_count, "failed": len(results) - ok_count})

@app.route("/api/primary-ips/<int:ip_id>/lock", methods=["POST"])
@require_auth
def lock_primary_ip(ip_id):
    data = request.json or {}
    locked = bool(data.get("locked", True))
    ok, msg = _set_ip_lock(ip_id, locked)
    if ok:
        add_log(f"🔒 IP #{ip_id} {msg}")
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": msg}), 400

@app.route("/api/primary-ips/<int:ip_id>/assign", methods=["POST"])
@require_auth
def assign_primary_ip(ip_id):
    data = request.json or {}
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    try:
        server_id = int(data.get("server_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "请提供有效的服务器 ID"}), 400
    ok, msg = hz.assign_primary_ip(ip_id, server_id)
    if ok:
        add_log(f"🔗 IP #{ip_id} 已分配给服务器 #{server_id}")
        return jsonify({"success": True})
    return jsonify({"error": msg}), 400

@app.route("/api/primary-ips/<int:ip_id>/unassign", methods=["POST"])
@require_auth
def unassign_primary_ip(ip_id):
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    ok, msg = hz.unassign_primary_ip(ip_id)
    if ok:
        add_log(f"⛓️ IP #{ip_id} 已解绑")
        return jsonify({"success": True})
    return jsonify({"error": msg}), 400

@app.route("/api/primary-ips/refresh-catalog", methods=["POST"])
@require_auth
def refresh_catalog_route():
    ok = refresh_catalog("手动刷新")
    return jsonify({"success": ok})

# ─── Servers ──────────────────────────────────────────────────────────────────
@app.route("/api/servers")
@require_auth
def list_servers():
    force = request.args.get("refresh") == "1"
    if force or not monitor_state["servers_cache"]:
        hz = get_hetzner()
        if hz:
            monitor_state["servers_cache"] = [enrich_server(s) for s in hz.get_servers()]
            monitor_state["last_check"] = datetime.now().isoformat()
    # 标注 Primary IP 是否已锁定（重建时保留）
    out = monitor_state["servers_cache"]
    try:
        hz = get_hetzner()
        if hz and out:
            ip_map = {ip["id"]: ip for ip in hz.get_primary_ips() if ip.get("id")}
            for s in out:
                v4 = ip_map.get(s.get("primary_ipv4_id"))
                v6 = ip_map.get(s.get("primary_ipv6_id"))
                s["ipv4_locked"] = bool(v4 and ((v4.get("protection") or {}).get("delete") or not v4.get("auto_delete")))
                s["ipv6_locked"] = bool(v6 and ((v6.get("protection") or {}).get("delete") or not v6.get("auto_delete")))
    except Exception:
        pass
    return jsonify({"servers": out,
                    "last_check": monitor_state["last_check"],
                    "count": len(out)})

@app.route("/api/servers/<int:server_id>", methods=["DELETE"])
@require_auth
def delete_server(server_id):
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    add_log(f"🗑️ 删除服务器 {server_id}...")
    if hz.delete_server(server_id):
        monitor_state["servers_cache"] = [s for s in monitor_state["servers_cache"] if s["id"] != server_id]
        add_log(f"✅ 已删除 {server_id}")
        return jsonify({"success": True})
    return jsonify({"error": "删除失败"}), 500

@app.route("/api/servers/create", methods=["POST"])
@require_auth
def create_server():
    """
    手动创建服务器（支持批量 / 数量 / IP 分配策略 / 型号优先级）。

    body:
      count            创建数量
      name / name_prefix
      server_types     型号优先级数组（或 server_type 单个）
      image_id, location, ssh_keys
      ip_mode          auto | locked | manual
      primary_ipv4_ids 手动指定时使用
      lock_ip          创建后是否锁定新 IP
    """
    data = request.json or {}
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    cfg = load_config()

    image_id = data.get("image_id") or cfg.get("initial_snapshot_id")
    if not image_id:
        return jsonify({"error": "未指定镜像 ID"}), 400

    try:
        count = max(1, min(int(data.get("count", 1) or 1), 20))
    except (TypeError, ValueError):
        count = 1

    configured = list(cfg.get("server_types") or [])
    if isinstance(configured, str):
        configured = [configured]
    if data.get("server_types"):
        requested = data["server_types"]
    elif data.get("server_type"):
        requested = [data["server_type"]]
    else:
        requested = []
    if isinstance(requested, str):
        requested = [requested]
    # 只允许监控配置内的型号，绝不放行配置以外的机器
    server_types = [t for t in requested if t in configured] or configured
    if not server_types:
        return jsonify({"error": "监控配置未设置任何型号，请先在「监控配置」中添加"}), 400

    location = data.get("location") or cfg.get("default_location", "nbg1")
    ssh_keys = data.get("ssh_keys", cfg.get("ssh_keys", []))
    lock_ip = bool(data.get("lock_ip", False))   # 默认不额外锁定
    ip_mode = data.get("ip_mode") or ("manual" if data.get("primary_ipv4_id") else "auto")
    name_raw = data.get("name") or data.get("name_prefix") or f"server-{int(time.time())}"

    # ── 组装 IP 分配队列 ──
    manual_ids: List[int] = []
    for raw in (data.get("primary_ipv4_ids") or []):
        try:
            manual_ids.append(int(raw))
        except (TypeError, ValueError):
            pass
    single = data.get("primary_ipv4_id")
    if single and not manual_ids:
        try:
            manual_ids.append(int(single))
        except (TypeError, ValueError):
            pass

    locked_pool: List[Dict] = []
    if ip_mode == "manual":
        add_log(f"🔒 IP 策略：手动指定 {len(manual_ids)} 个 IP")
    else:
        # auto / locked：有已保护的空闲 IP 就优先复用，但不额外加锁
        locked_pool = _free_locked_primary_ips(hz, location)
        if locked_pool:
            add_log(f"🔒 IP 策略：发现 {len(locked_pool)} 个已保护的空闲 IP，优先复用")

    add_log(f"🔐 创建后{'锁定新 IP' if lock_ip else '不锁定新 IP（新地址随服务器删除，已有锁定保持不变）'}")
    add_log(f"➕ 手动创建 {count} 台服务器（型号优先级 {' → '.join(server_types)}，地区 {location}）")

    pad = max(2, len(str(count)))
    created, failed = [], 0

    for i in range(count):
        if count == 1:
            name = HetznerAPI.sanitize_name(name_raw)
        else:
            name = HetznerAPI.sanitize_name(f"{name_raw}-{i + 1:0{pad}d}")

        pip = None
        reused = False
        if i < len(manual_ids):
            pip = manual_ids[i]
        elif locked_pool:
            reuse = locked_pool.pop(0)
            pip = reuse["id"]
            reused = True

        tip = "（自动分配新 IP）"
        if pip:
            tip = f"（{'复用' if reused else '指定'} IP #{pip}）"
        add_log(f"  创建第 {i + 1}/{count} 台: {name} {tip}")

        result = hz.create_server_with_fallback(
            name, server_types, int(image_id), ssh_keys, location, primary_ipv4=pip
        )
        if not result:
            failed += 1
            created.append({"name": name, "ok": False, "error": "所有型号均失败"})
            add_log(f"  ❌ {name} 创建失败", "error")
            continue

        if lock_ip and not reused:
            for pid in (result.get("primary_ipv4_id"), result.get("primary_ipv6_id")):
                if pid:
                    _set_ip_lock(pid, True)

        result["ok"] = True
        result["reused_ip"] = reused
        created.append(result)
        add_log(f"  ✅ {name} → {result['ip']} [{result['server_type']}]")

    # 刷新缓存
    time.sleep(2)
    fresh = hz.get_servers()
    if fresh:
        monitor_state["servers_cache"] = [enrich_server(s) for s in fresh]

    if created:
        sync_vertex_ips("手动创建服务器后")

    ok_count = sum(1 for c in created if c.get("ok"))
    return jsonify({
        "success": ok_count > 0,
        "count": count,
        "created": ok_count,
        "failed": failed,
        "servers": created,
        # 兼容旧前端
        "server": next((c for c in created if c.get("ok")), None),
    })


@app.route("/api/primary-ips/free-locked")
@require_auth
def list_free_locked_ips():
    """列出已被保护且未分配的空闲 Primary IP（供创建服务器时优先复用）。"""
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    location = request.args.get("location") or None
    ip_type = request.args.get("type") or "ipv4"
    ips = _free_locked_primary_ips(hz, location, ip_type)
    return jsonify({"primary_ips": ips, "count": len(ips)})

@app.route("/api/servers/rebuild/<int:server_id>", methods=["POST"])
@require_auth
def rebuild_server(server_id):
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    cfg = load_config()
    target = next((s for s in monitor_state["servers_cache"] if s["id"] == server_id), None)
    if not target:
        raw = hz.get_server(server_id)
        target = enrich_server(raw) if raw else None
    if not target:
        return jsonify({"error": "服务器不存在"}), 404
    img_id = target["image"]["id"] if target["image"].get("type") == "snapshot" else None
    if not img_id and cfg.get("initial_snapshot_id"):
        img_id = int(cfg["initial_snapshot_id"])
    if not img_id:
        return jsonify({"error": "无可用快照"}), 400

    old_name = target["name"]
    old_ip   = target["ipv4"]
    server_types = cfg.get("server_types", ["cx43"])
    location = cfg.get("default_location", "nbg1")
    ssh_keys = cfg.get("ssh_keys", [])

    add_log(f"🔄 手动重建 {old_name} ({old_ip})...")

    try:
        ip_map = {ip["id"]: ip for ip in hz.get_primary_ips() if ip.get("id")}
    except Exception:
        ip_map = {}
    keep_v4, keep_v6 = _preserved_primary_ips(target, ip_map)
    if keep_v4 or keep_v6:
        for kid in (keep_v4, keep_v6):
            if kid:
                hz.update_primary_ip(kid, auto_delete=False)
        add_log(f"  🔒 锁定 IP 保留：IPv4#{keep_v4} IPv6#{keep_v6}")

    add_log(f"  [1/2] 删除旧服务器 {old_name} (id={server_id})...")
    if not hz.delete_server(server_id):
        return jsonify({"error": "旧服务器删除失败，重建取消"}), 500

    add_log(f"  等待 5s 确保名称释放...")
    time.sleep(5)

    add_log(f"  [2/2] 创建新服务器 {old_name}...")
    new_sv = hz.create_server_with_fallback(old_name, server_types, img_id, ssh_keys, location,
                                            primary_ipv4=keep_v4, primary_ipv6=keep_v6)
    if not new_sv:
        return jsonify({"error": "旧服务器已删除，但新服务器创建失败，请手动创建"}), 500

    add_log(f"✅ 手动重建完成: {old_ip} → {new_sv['ip']} [{new_sv['server_type']}] name={new_sv['name']}")

    time.sleep(2)
    monitor_state["servers_cache"] = [enrich_server(s) for s in hz.get_servers()]

    # 同步 Vertex
    sync_vertex_ips("手动重建后")

    return jsonify({"success": True, "new_server": new_sv, "old_ip": old_ip})

# ─── Images ───────────────────────────────────────────────────────────────────
@app.route("/api/images")
@require_auth
def list_images():
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    images = hz.get_images(request.args.get("type", "snapshot"))
    return jsonify({"images": [{
        "id": i["id"], "name": i.get("name") or i.get("description", ""),
        "description": i.get("description", ""), "type": i.get("type", ""),
        "status": i.get("status", ""), "created": i.get("created", ""),
        "disk_size": i.get("disk_size", 0), "image_size": i.get("image_size"),
        "os_flavor": i.get("os_flavor", ""), "os_version": i.get("os_version", ""),
        "labels": i.get("labels", {})
    } for i in images], "count": len(images)})

# ─── SSH Keys ─────────────────────────────────────────────────────────────────
@app.route("/api/ssh-keys")
@require_auth
def list_ssh_keys():
    hz = get_hetzner()
    if not hz:
        return jsonify({"error": "API Key 未配置"}), 400
    return jsonify({"ssh_keys": [{"id": k["id"], "name": k["name"]} for k in hz.get_ssh_keys()]})

# ─── Monitor ──────────────────────────────────────────────────────────────────
@app.route("/api/monitor/status")
@require_auth
def monitor_status():
    return jsonify({
        "running": monitor_state["running"],
        "last_check": monitor_state["last_check"],
        "next_check": monitor_state["next_check"],
        "scheduler_running": monitor_state["scheduler_running"],
        "last_scheduled_delete": monitor_state["last_scheduled_delete"],
        "last_scheduled_create": monitor_state["last_scheduled_create"],
        "next_scheduled_delete": monitor_state["next_scheduled_delete"],
        "next_scheduled_create": monitor_state["next_scheduled_create"],
        # 通用任务运行状态
        "task_last_run": monitor_state["task_last_run"],
        "task_next_run": monitor_state["task_next_run"],
        "task_retry": {
            x.get("task_id"): {
                "remaining": x.get("remaining", 0),
                "attempts": x.get("attempts", 0),
                "next_at": x.get("next_at"),
            } for x in monitor_state["retry_queue"]
        },
    })

@app.route("/api/monitor/start", methods=["POST"])
@require_auth
def start_monitor():
    if monitor_state["running"]:
        return jsonify({"message": "已在运行"})
    monitor_state["stop_event"].clear()
    threading.Thread(target=monitor_loop, args=(monitor_state["stop_event"],), daemon=True).start()
    monitor_state["running"] = True
    add_log("▶️ 监控已启动")
    return jsonify({"success": True})

@app.route("/api/monitor/stop", methods=["POST"])
@require_auth
def stop_monitor():
    monitor_state["stop_event"].set()
    monitor_state["running"] = False
    add_log("⏹️ 监控已停止")
    return jsonify({"success": True})

@app.route("/api/monitor/check-now", methods=["POST"])
@require_auth
def check_now():
    threading.Thread(target=lambda: _safe(do_check_and_rebuild), daemon=True).start()
    return jsonify({"success": True})

def _safe(fn):
    try:
        fn()
    except Exception as e:
        add_log(f"执行异常: {e}", "error")

@app.route("/api/monitor/logs")
@require_auth
def get_logs():
    return jsonify({"logs": monitor_state["logs"][:int(request.args.get("limit", 150))]})

# ─── 定时任务 API ─────────────────────────────────────────────────────────────
@app.route("/api/scheduler/trigger-delete", methods=["POST"])
@require_auth
def trigger_delete():
    """手动立即触发定时删除（可带 only_prefix 选项）"""
    opts = (request.json or {}).get("options") or {}
    threading.Thread(target=lambda: _safe(lambda: do_scheduled_delete_all(opts)), daemon=True).start()
    return jsonify({"success": True, "message": "定时删除任务已触发"})

@app.route("/api/scheduler/trigger-create", methods=["POST"])
@require_auth
def trigger_create():
    """手动立即触发定时创建（可带 options：max_servers / prefix 等）"""
    opts = (request.json or {}).get("options") or {}
    threading.Thread(target=lambda: _safe(lambda: do_scheduled_create(opts)), daemon=True).start()
    return jsonify({"success": True, "message": "定时创建任务已触发"})

@app.route("/api/scheduler/run-task", methods=["POST"])
@require_auth
def run_task_now():
    """按任务对象或任务 id 立即执行一次通用任务。"""
    data = request.json or {}
    task = data.get("task")
    if not task:
        tid = data.get("id")
        cfg = load_config()
        task = next((t for t in (cfg.get("scheduled_tasks") or []) if t.get("id") == tid), None)
    if not task or not task.get("type"):
        return jsonify({"error": "任务不存在"}), 404
    threading.Thread(target=lambda: _safe(lambda: execute_task(task)), daemon=True).start()
    return jsonify({"success": True, "message": f"已触发任务 [{task.get('type')}] {task.get('id', '')}"})

# ─── Vertex ───────────────────────────────────────────────────────────────────
@app.route("/api/vertex/test", methods=["POST"])
@require_auth
def vertex_test():
    vertex = get_vertex()
    if not vertex:
        return jsonify({"error": "Vertex 未配置 URL 或 Cookies"}), 400
    ok, msg = vertex.test_connection()
    return jsonify({"success": ok, "message": msg})

@app.route("/api/vertex/refresh-cookie", methods=["POST"])
@require_auth
def vertex_refresh_cookie():
    global _vcm_instance
    vcm = _build_vcm()
    if not vcm:
        return jsonify({"error": "未配置 vertex_username / vertex_password_md5，无法自动刷新"}), 400
    new_cookie = vcm.force_refresh()
    if not new_cookie:
        return jsonify({"error": "登录失败，请检查 Vertex 地址和密码"}), 500
    cfg = load_config()
    cfg["vertex_cookies"] = new_cookie
    save_config(cfg)
    add_log(f"🔑 [VTCookie] 手动刷新成功: {new_cookie[:40]}...")
    return jsonify({"success": True, "cookie_preview": new_cookie[:40] + "..."})

@app.route("/api/vertex/downloaders")
@require_auth
def vertex_list_downloaders():
    vertex = get_vertex()
    if not vertex:
        return jsonify({"error": "Vertex 未配置"}), 400
    all_dl = vertex.get_all_downloaders()
    keyword = load_config().get("vertex_downloader_keyword", "Hetzner")
    for dl in all_dl:
        dl["_is_hetzner"] = keyword.lower() in dl.get("alias", "").lower()
        dl["_current_ip"] = vertex._extract_ip(dl.get("clientUrl", "") or "")
    return jsonify({"downloaders": all_dl, "count": len(all_dl)})

@app.route("/api/vertex/sync", methods=["POST"])
@require_auth
def vertex_sync_now():
    vertex = get_vertex()
    if not vertex:
        return jsonify({"error": "Vertex 未配置"}), 400

    data = request.json or {}
    server_ips = data.get("ips")

    # ── 修复：始终从 API 实时拉取最新 IP，不依赖缓存 ──
    if not server_ips:
        hz = get_hetzner()
        if hz:
            raw_servers = hz.get_servers()
            # 刷新缓存
            if raw_servers:
                monitor_state["servers_cache"] = [enrich_server(s) for s in raw_servers]
            server_ips = [
                (s.get("public_net", {}).get("ipv4") or {}).get("ip", "")
                for s in raw_servers
                if s.get("status") == "running"
            ]
            server_ips = [ip for ip in server_ips if ip]

    # 若 API 也没拿到，再尝试缓存
    if not server_ips:
        server_ips = [s["ipv4"] for s in monitor_state["servers_cache"] if s.get("ipv4")]

    if not server_ips:
        return jsonify({"error": "无可用服务器 IP，请先创建服务器"}), 400

    add_log(f"🔄 手动 Vertex 同步，IP: {server_ips}")
    result = vertex.sync_with_server_ips(server_ips)
    return jsonify({"success": True, "result": result, "ips_used": server_ips})

# ─── Telegram ─────────────────────────────────────────────────────────────────
@app.route("/api/telegram/test", methods=["POST"])
@require_auth
def test_telegram():
    send_telegram(f"✅ <b>Hetzner Web Manager</b>\nTelegram 测试成功\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return jsonify({"success": True})

# ─── Static ────────────────────────────────────────────────────────────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    if path and os.path.exists(os.path.join("static", path)):
        return send_from_directory("static", path)
    return send_from_directory("static", "hetzner-manager.html")


if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Hetzner Web Manager on :{port}")

    # 启动时从 Hetzner API 拉取型号 / 价格 / 地区（后台，不阻塞启动）
    threading.Thread(target=lambda: _safe(lambda: refresh_catalog("启动时")),
                     daemon=True, name="catalog-init").start()

    # 若配置了定时任务总开关，启动时自动启动调度器
    cfg = load_config()
    if cfg.get("scheduled_tasks_enabled"):
        start_scheduler()

    app.run(host="0.0.0.0", port=port, debug=False)