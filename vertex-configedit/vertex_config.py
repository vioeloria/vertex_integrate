"""
vertex_config.py — Vertex 批量修改工具（下载器 / RSS，单行指令 + CLI 一键执行）
=============================================================================
特性：
  * 支持更多字段（下载器 / RSS 全部常用设置，含补种 reseed 关联字段）
  * 单行指令格式：字段=值; 字段=值 （一次输入所有修改，只需确认一次）
  * 支持命令行参数一键执行（--yes 跳过确认），方便定时任务 / 脚本调用
  * Cookie 自动获取/刷新（依赖同目录 vertex_cookie.py）

配置来源（优先级从高到低）：
  1. 命令行参数  --url / --user / --pwd
  2. 同目录 config.yaml 的 vertex 段（url / username / password 或 password_md5）
  3. 环境变量  VTURL / VT_USERNAME / VT_PASSWORD
  4. 交互式询问

示例：
  # 交互式（启动时选择 1=下载器 2=RSS任务）
  python vertex_config.py

  # 一键修改某关键字下的下载器（不加 --yes 前会确认一次）
  python vertex_config.py --url http://YOUR-VERTEX-IP:3077 --pwd 你的密码 \
      --kw netcup leech=30 cron=3 up=50MiB rules=1,2,3 --yes

  # 一键修改 RSS 任务
  python vertex_config.py --rss --kw 动画 sort=upload maxdl=5 skip=on --yes

  # 仅查看
  python vertex_config.py --kw netcup --list
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore", message=".*urllib3.*")

# Windows 控制台默认 GBK，无法输出 ✓/⚠ 等符号，强制 UTF-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

from vertex_cookie import get_cookie, force_refresh, DEFAULT_USERNAME

DEFAULT_USER = os.getenv("VT_USERNAME", DEFAULT_USERNAME)


def load_yaml_config() -> dict:
    """读取本文件同目录 config.yaml 的 vertex 段；未安装 yaml 或读取失败返回空 dict。"""
    try:
        import yaml
    except ImportError:
        return {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("vertex", data)
    except Exception:
        pass
    return {}


# ══════════════════════════════════════════════════════════════════
# API 访问（Cookie 自动获取）
# ══════════════════════════════════════════════════════════════════

class Api:
    def __init__(self, url: str, user: str, pwd: str, force_cookie: bool = False):
        self.url = url.rstrip("/")
        self.user = user
        self.pwd = pwd
        self.force_cookie = force_cookie

    def _headers(self) -> dict:
        if self.force_cookie:
            cookie = force_refresh(self.url, self.user, self.pwd)
        else:
            cookie = get_cookie(self.url, self.user, self.pwd)
        if not cookie:
            sys.exit("❌ 无法获取有效 Cookie，请检查地址/账号/密码")
        return {"Cookie": cookie, "Content-Type": "application/json"}

    def get(self, path: str) -> dict:
        r = requests.get(self.url + path, headers=self._headers(), timeout=15)
        return r.json()

    def post(self, path: str, payload: dict) -> dict:
        r = requests.post(self.url + path, json=payload, headers=self._headers(), timeout=15)
        return r.json()


# ══════════════════════════════════════════════════════════════════
# 字段解析
# ══════════════════════════════════════════════════════════════════

_UNITS = {
    "g": "GiB", "gi": "GiB", "gib": "GiB", "gb": "GiB",
    "m": "MiB", "mi": "MiB", "mib": "MiB", "mb": "MiB",
    "k": "KiB", "ki": "KiB", "kib": "KiB", "kb": "KiB",
    "t": "TiB", "ti": "TiB", "tib": "TiB", "tb": "TiB",
}

_SORTS = {
    "up": "uploadSpeed", "upload": "uploadSpeed", "uploadspeed": "uploadSpeed",
    "leech": "leechingCount", "leeching": "leechingCount", "leechingcount": "leechingCount",
    "down": "downloadSpeed", "download": "downloadSpeed", "downloadspeed": "downloadSpeed",
    "space": "freeSpaceOnDisk", "freespace": "freeSpaceOnDisk", "freespaceondisk": "freeSpaceOnDisk",
}

_CLEAR_VALUES = {"", "clear", "清空", "none", "0"}


def parse_ids(v: str):
    if v.strip().lower() in _CLEAR_VALUES:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def parse_int(v: str):
    v = v.strip()
    if not v or v.lower() in ("unlimited", "不限", "none", "0"):
        return ""
    if v.isdigit():
        return str(int(v))
    return None


def parse_bool(v: str):
    v = v.strip().lower()
    if v in ("1", "on", "true", "yes", "y", "开", "启用"):
        return True
    if v in ("0", "off", "false", "no", "n", "关", "禁用"):
        return False
    return None


def parse_cron(v: str, presets: dict):
    v = v.strip()
    if v in presets:
        return presets[v]
    return v or None


def parse_size(v: str, default_unit: str = "MiB"):
    v = v.strip()
    if not v or v.lower() in ("unlimited", "不限", "none", "0"):
        return ("", default_unit)
    m = re.fullmatch(r"(\d+)\s*([A-Za-z]*)", v)
    if not m:
        return None
    unit = m.group(2).lower() or default_unit.lower()
    return (m.group(1), _UNITS.get(unit, unit.capitalize() if len(unit) <= 2 else unit))


def parse_sort(v: str):
    return _SORTS.get(v.strip().lower())


def _size_fields(field: str, unit_field: str, v: str, default_unit: str):
    r = parse_size(v, default_unit)
    if r is None:
        return None
    val, unit = r
    return {field: val, unit_field: unit}


def _wrap(field: str, parser, v: str):
    """解析值并打包为 {字段: 值}；解析失败返回 None（跳过该字段）"""
    r = parser(v)
    return None if r is None else {field: r}


DL_CRON = {"1": "*/15 * * * * *", "2": "*/30 * * * * *", "3": "0 */1 * * * *",
           "4": "0 */5 * * * *", "5": "0 */10 * * * *"}
RSS_CRON = {"1": "*/5 * * * * *", "2": "*/46 * * * * *", "3": "* * * * *",
            "4": "*/5 * * * *", "5": "*/10 * * * *", "6": "*/30 * * * *"}


# (alias -> (显示名, 解析函数->{字段:值}))
DL_SPEC = {
    "rules":  ("删种规则(deleteRules)",        lambda v: {"deleteRules": parse_ids(v)}),
    "r":      ("删种规则(deleteRules)",        lambda v: {"deleteRules": parse_ids(v)}),
    "reject": ("保护规则(rejectDeleteRules)",  lambda v: {"rejectDeleteRules": parse_ids(v)}),
    "rej":    ("保护规则(rejectDeleteRules)",  lambda v: {"rejectDeleteRules": parse_ids(v)}),
    "leech":  ("最大同时下载数(maxLeechNum)",  lambda v: _wrap("maxLeechNum", parse_int, v)),
    "l":      ("最大同时下载数(maxLeechNum)",  lambda v: _wrap("maxLeechNum", parse_int, v)),
    "ad":     ("自动删除(autoDelete)",         lambda v: _wrap("autoDelete", parse_bool, v)),
    "cron":   ("删种周期(autoDeleteCron)",     lambda v: _wrap("autoDeleteCron", lambda x: parse_cron(x, DL_CRON), v)),
    "c":      ("删种周期(autoDeleteCron)",     lambda v: _wrap("autoDeleteCron", lambda x: parse_cron(x, DL_CRON), v)),
    "space":  ("最小剩余空间(minFreeSpace)",   lambda v: _size_fields("minFreeSpace", "minFreeSpaceUnit", v, "GiB")),
    "sp":     ("最小剩余空间(minFreeSpace)",   lambda v: _size_fields("minFreeSpace", "minFreeSpaceUnit", v, "GiB")),
    "up":     ("上传速度上限(maxUploadSpeed)", lambda v: _size_fields("maxUploadSpeed", "maxUploadSpeedUnit", v, "MiB")),
    "down":   ("下载速度上限(maxDownloadSpeed)", lambda v: _size_fields("maxDownloadSpeed", "maxDownloadSpeedUnit", v, "MiB")),
    "d":      ("下载速度上限(maxDownloadSpeed)", lambda v: _size_fields("maxDownloadSpeed", "maxDownloadSpeedUnit", v, "MiB")),
    "en":     ("启用(enable)",                 lambda v: _wrap("enable", parse_bool, v)),
    "monitor":("监控开关(monitor)",            lambda v: _wrap("monitor", parse_bool, v)),
    "push":   ("推送通知(pushNotify)",         lambda v: _wrap("pushNotify", parse_bool, v)),
    "reann":  ("自动重公告(autoReannounce)",   lambda v: _wrap("autoReannounce", parse_bool, v)),
    "alarm":  ("空间告警(alarmSpace)",         lambda v: _size_fields("alarmSpace", "alarmSpaceUnit", v, "GiB")),
}

RSS_SPEC = {
    "sort":   ("客户端排序(clientSortBy)",       lambda v: _wrap("clientSortBy", parse_sort, v)),
    "maxdl":  ("单下载器任务上限(maxClientDownloadCount)", lambda v: _wrap("maxClientDownloadCount", parse_int, v)),
    "maxupspeed": ("上传速度上限(maxClientUploadSpeed)", lambda v: _size_fields("maxClientUploadSpeed", "maxClientUploadSpeedUnit", v, "MiB")),
    "us":     ("上传速度上限(maxClientUploadSpeed)", lambda v: _size_fields("maxClientUploadSpeed", "maxClientUploadSpeedUnit", v, "MiB")),
    "maxdownspeed": ("下载速度上限(maxClientDownloadSpeed)", lambda v: _size_fields("maxClientDownloadSpeed", "maxClientDownloadSpeedUnit", v, "MiB")),
    "ds":     ("下载速度上限(maxClientDownloadSpeed)", lambda v: _size_fields("maxClientDownloadSpeed", "maxClientDownloadSpeedUnit", v, "MiB")),
    "skip":   ("跳过相同种子(skipSameTorrent)",  lambda v: _wrap("skipSameTorrent", parse_bool, v)),
    "cron":   ("抓取间隔(cron)",                 lambda v: _wrap("cron", lambda x: parse_cron(x, RSS_CRON), v)),
    "c":      ("抓取间隔(cron)",                 lambda v: _wrap("cron", lambda x: parse_cron(x, RSS_CRON), v)),
    "arr":    ("下载器列表(clientArr)[覆盖]",    lambda v: {"clientArr": parse_ids(v)}),
    "arr+":   ("下载器列表(clientArr)[追加]",    lambda v: {"clientArr": {"append": parse_ids(v)}}),
    "arr-":   ("下载器列表(clientArr)[移除]",    lambda v: {"clientArr": {"remove": parse_ids(v)}}),
    "path":   ("保存路径(savePath)",             lambda v: {"savePath": v.strip()} if v.strip() else None),
    "reseed": ("自动补种(rssReseed)",            lambda v: _wrap("rssReseed", parse_bool, v)),
    "sleep":  ("最大休眠(maxSleepTime)",         lambda v: _wrap("maxSleepTime", parse_int, v)),
    "en":     ("启用(enable)",                   lambda v: _wrap("enable", parse_bool, v)),
    "push":   ("推送通知(pushNotify)",           lambda v: _wrap("pushNotify", parse_bool, v)),
}


def parse_instructions(segments, spec: dict) -> dict:
    """把指令文本(可含 ';')解析为 {字段: 值}"""
    changes: dict = {}
    for seg in segments:
        for token in seg.split(";"):
            token = token.strip()
            if not token:
                continue
            if "=" not in token:
                print(f"  ⚠ 忽略无法解析: {token}")
                continue
            key, _, val = token.partition("=")
            key, val = key.strip().lower(), val.strip()
            if key not in spec:
                print(f"  ⚠ 未知字段: {key}")
                continue
            _, func = spec[key]
            result = func(val)
            if result is None:
                print(f"  ⚠ 字段 {key} 的值无效: {val}")
                continue
            changes.update(result)
    return changes


# ══════════════════════════════════════════════════════════════════
# 展示
# ══════════════════════════════════════════════════════════════════

def fetch_delete_rules(api: Api) -> dict:
    data = api.get("/api/deleteRule/list")
    if data and data.get("success"):
        return {r["id"]: (r.get("alias") or r.get("name") or r["id"]) for r in data.get("data", [])}
    return {}


def show_items(api: Api, kind: str, items: list):
    rule_names = fetch_delete_rules(api) if kind == "downloader" else {}
    print(f"\n共 {len(items)} 个匹配项:\n")
    for it in items:
        alias = it.get("alias", it.get("id", "?"))
        print(f"  ┌ {alias}  (ID: {it['id']})")
        if kind == "downloader":
            rules = it.get("deleteRules", [])
            print(f"  │ 删除规则: {rules}"
                  f"  | 规则名: {[rule_names.get(r, '?') for r in rules]}")
            print(f"  │ 下载数: {it.get('maxLeechNum','')}  自动删除: {it.get('autoDelete')}"
                  f"  删种周期: {it.get('autoDeleteCron','')}")
            print(f"  │ 最小空间: {it.get('minFreeSpace','')} {it.get('minFreeSpaceUnit','GiB')}"
                  f"  上传上限: {it.get('maxUploadSpeed','')} {it.get('maxUploadSpeedUnit','MiB')}"
                  f"  下载上限: {it.get('maxDownloadSpeed','')} {it.get('maxDownloadSpeedUnit','MiB')}")
            print(f"  │ 保护规则: {it.get('rejectDeleteRules', [])}  启用: {it.get('enable')}")
        else:
            arr = it.get("clientArr", [])
            print(f"  │ 排序: {it.get('clientSortBy','')}  单下载器上限: {it.get('maxClientDownloadCount','')}"
                  f"  跳过相同: {it.get('skipSameTorrent')}")
            print(f"  │ 抓取间隔: {it.get('cron','')}  启用: {it.get('enable')}"
                  f"  补种: {it.get('rssReseed')}  保存路径: {it.get('savePath','')}")
            print(f"  │ 下载器列表[{len(arr)}]: {','.join(arr) if arr else '（空）'}")
        print(f"  └")


def print_help(kind: str):
    print("\n可用字段（多个用 ';' 分隔，格式 字段=值）:")
    if kind == "downloader":
        print("  rules=ID列表      删种规则（逗号分隔，clear 清空）")
        print("  reject=ID列表     保护/拒绝删种规则")
        print("  leech=数字        最大同时下载数（0 或留空 = 不限制）")
        print("  ad=on/off         自动删除开关")
        print("  cron=1-5          删种周期（1=15秒 2=30秒 3=1分钟 4=5分钟 5=10分钟，或直接写cron表达式）")
        print("  space=20GiB       最小剩余空间（0 = 关闭）")
        print("  up=50MiB          上传速度上限（0 = 不限制）")
        print("  down=100MiB       下载速度上限（0 = 不限制）")
        print("  en=on/off         启用/禁用")
        print("  monitor=on/off    监控开关")
        print("  push=on/off       推送通知")
        print("  reann=on/off      自动重公告")
        print("  alarm=20GiB       空间告警")
    else:
        print("  sort=upload/leech/download/space   客户端排序")
        print("  maxdl=数字        单下载器任务上限（0 = 不限制）")
        print("  maxupspeed=50MiB  上传速度上限（0 = 不限制）")
        print("  maxdownspeed=100MiB 下载速度上限")
        print("  skip=on/off       跳过相同种子")
        print("  cron=1-6          抓取间隔（1=5秒 2=46秒 3=1分钟 4=5分钟 5=10分钟 6=30分钟）")
        print("  arr=ID列表        覆盖下载器列表（留空 = 清空）")
        print("  arr+=ID列表       追加到现有列表（已开启补种时同步加入 reseedClients）")
        print("  arr-=ID列表       从现有列表移除（同步从 reseedClients 移除）")
        print("  path=保存路径     保存路径")
        print("  reseed=on/off     自动补种（开启时自动补齐 reseedClients；无该键的版本不受影响）")
        print("  sleep=数字        最大休眠（秒）")
        print("  en=on/off         启用/禁用")


def show_summary(kind: str, changes: dict):
    print(f"\n即将修改{('RSS任务' if kind == 'rss' else '下载器')}:")
    for field, value in changes.items():
        if field == "clientArr" and isinstance(value, dict):
            op = "追加" if "append" in value else "移除"
            ids = value.get("append") or value.get("remove")
            print(f"  - clientArr {op}: {ids}")
        else:
            print(f"  - {field}: {value}")


# ══════════════════════════════════════════════════════════════════
# 执行修改
# ══════════════════════════════════════════════════════════════════

def apply_changes(api: Api, kind: str, items: list, changes: dict):
    path = "/api/downloader/modify" if kind == "downloader" else "/api/rss/modify"
    ok = fail = 0
    for it in items:
        payload = dict(it)
        for field, value in changes.items():
            if field == "clientArr":
                # 计算新 clientArr
                base = list(it.get("clientArr", []))
                if isinstance(value, dict):
                    if "append" in value:
                        base += [x for x in value["append"] if x not in base]
                    if "remove" in value:
                        base = [x for x in base if x not in value["remove"]]
                else:
                    base = list(value)
                payload["clientArr"] = base
                # 联动维护 reseedClients（仅当该键存在于响应中，缺失版本不处理）
                if isinstance(it.get("reseedClients"), list):
                    if isinstance(value, dict) and "remove" in value:
                        payload["reseedClients"] = [x for x in it["reseedClients"]
                                                    if x not in value["remove"]]
                    elif it.get("rssReseed"):
                        if isinstance(value, dict) and "append" in value:
                            cur = list(it["reseedClients"])
                            cur += [x for x in value["append"] if x not in cur]
                            payload["reseedClients"] = cur
                        else:  # 覆盖/清空：只保留仍在新列表中的
                            payload["reseedClients"] = [x for x in it["reseedClients"]
                                                        if x in base]
            else:
                payload[field] = value
        # reseed=on 且响应带 reseedClients 键但为空时，自动填该任务 clientArr
        if (changes.get("rssReseed") is True and "reseedClients" in payload
                and not payload.get("reseedClients")):
            payload["reseedClients"] = list(payload.get("clientArr") or [])
        res = api.post(path, payload)
        if res and res.get("success"):
            print(f"  ✓ {it.get('alias', it['id'])} 修改成功")
            ok += 1
        else:
            print(f"  ✗ {it.get('alias', it['id'])} 修改失败: {res}")
            fail += 1
    print(f"\n完成！成功 {ok}，失败 {fail}")


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Vertex 批量修改工具（下载器 / RSS）")
    ap.add_argument("--url", default=None, help="Vertex 地址（默认读 config.yaml / 环境变量 VTURL）")
    ap.add_argument("--user", default=None, help=f"账号（默认 {DEFAULT_USERNAME}）")
    ap.add_argument("--pwd", default=None, help="密码（明文，自动MD5；或直接传MD5）")
    ap.add_argument("--kw", default="", help="筛选 alias 关键字")
    ap.add_argument("--rss", action="store_true", help="修改 RSS 任务")
    ap.add_argument("--dl", action="store_true", help="修改下载器（默认）")
    ap.add_argument("--force-cookie", action="store_true", help="强制重新登录获取 Cookie")
    ap.add_argument("--yes", "-y", action="store_true", help="跳过确认直接执行")
    ap.add_argument("--list", action="store_true", help="仅查看列表与规则，不修改")
    ap.add_argument("instructions", nargs="*", help="修改指令，如 rules=1,2 cron=3 up=50MiB")
    args = ap.parse_args()

    # ── 配置来源：命令行 > config.yaml > 环境变量 > 交互 ──────────
    cfg = load_yaml_config()
    url = args.url or cfg.get("url") or os.getenv("VTURL") or ""
    user = args.user or cfg.get("username") or os.getenv("VT_USERNAME") or DEFAULT_USER
    pwd = (args.pwd or cfg.get("password") or cfg.get("password_md5")
           or os.getenv("VT_PASSWORD") or "")

    if not url:
        url = input("Vertex 地址（例: http://YOUR-VERTEX-IP:3077）: ").strip().rstrip("/")
    if not pwd:
        pwd = input("Vertex 密码（明文，将自动 MD5）: ").strip()
    if not url or not pwd:
        sys.exit("❌ 缺少 Vertex 地址/密码，程序退出")
    user = user or DEFAULT_USER

    api = Api(url, user, pwd, args.force_cookie)
    if args.rss:
        kind = "rss"
    elif args.dl:
        kind = "downloader"
    elif args.instructions or args.list or args.kw:
        kind = "downloader"
    else:
        print("\n请选择操作对象:")
        print("  1. 下载器 (Downloader)")
        print("  2. RSS 任务")
        kind = "rss" if input("请输入选项 (1/2，默认1): ").strip() == "2" else "downloader"
    spec = RSS_SPEC if kind == "rss" else DL_SPEC
    list_path = "/api/rss/list" if kind == "rss" else "/api/downloader/list"

    print(f"正在获取列表: {url}{list_path} ...")
    data = api.get(list_path)
    if not data or not data.get("success"):
        sys.exit(f"❌ 获取列表失败: {data}")
    all_items = data.get("data", [])

    kw = args.kw or input("筛选 alias 关键字（留空 = 全部）: ").strip()
    filtered = [it for it in all_items if kw in it.get("alias", "")] if kw else all_items
    if not filtered:
        sys.exit("❌ 未找到匹配项")

    show_items(api, kind, filtered)
    if args.list:
        return

    if args.instructions:
        segments = args.instructions
    else:
        print_help(kind)
        print("\n输入修改指令（回车 = 仅查看）:")
        segments = [input(">>> ").strip()]

    changes = parse_instructions(segments, spec) if any(segments) else {}
    if not changes:
        print("\n未输入有效指令，本次仅查看。")
        return

    show_summary(kind, changes)
    if not args.yes:
        if input("\n确认执行？(回车确认 / n 取消): ").strip().lower() in ("n", "no", "否"):
            print("已取消")
            return

    apply_changes(api, kind, filtered, changes)


if __name__ == "__main__":
    main()