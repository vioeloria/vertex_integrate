# vertex_integrate

围绕 **Vertex 下载器管理面板** 的一批实用脚本/服务集合。各子项目均基于 Vertex 的 HTTP API 工作，并共用同一个 `vertex_cookie.py` 实现会话 Cookie 的自动获取、缓存与失效刷新。

> 仓库内不含任何真实凭据。所有 IP、密码、Token、Cookie 均已脱敏，请按需填写自己的配置。

---

## 目录结构

| 目录 | 说明 |
| ---- | ---- |
| `vertex-configedit/` | Vertex 批量修改工具（下载器 / RSS 任务），支持单行指令与 CLI 一键执行 |
| `autobrr_loadbalance_vt/` | autobrr Webhook 负载均衡服务：把种子推送给在线且最合适的 qBittorrent（经 Vertex 代理） |
| `hetzner-usagemonit_vt/` | Hetzner 云主机流量监控 + 超阈值自动重建 + 定时删建，并同步 Vertex 下载器 IP |
| `netcup-control-RESTAPI_vt/` | Netcup 流量监控 / 限速控制 REST API 服务 |

> `vertex_cookie.py` 在多个子项目目录中内容保持一致，修改时请同步所有副本。

---

## vertex_cookie.py — Cookie 获取 / 自动刷新模块

自动登录 Vertex（`POST /api/user/login`，密码自动转 MD5），缓存 `connect.sid`，失效时自动重新登录。

**Python 调用**

```python
from vertex_cookie import get_cookie

cookie = get_cookie("http://YOUR-VERTEX-IP:3077", username="admin", password="你的密码")
headers = {"Cookie": cookie, "Content-Type": "application/json"}
```

**常驻服务（面向对象，可配置探测间隔）**

```python
from vertex_cookie import VertexCookieManager

vcm = VertexCookieManager(
    login_url = "http://YOUR-VERTEX-IP:3077",
    username  = "admin",
    password  = "你的明文密码",        # 自动转 MD5；直接传 32 位 MD5 亦可
    check_interval = 300,              # Cookie 探测最小间隔（秒）
)
cookie = vcm.get_valid_cookie()               # 自动复用缓存 / 失效刷新
cookie = vcm.force_refresh()                  # 强制重新登录
```

**命令行输出（供 shell / 其他语言调用）**

```bash
python vertex_cookie.py http://YOUR-VERTEX-IP:3077 --user admin --pwd 你的密码 [--force]
```

---

## vertex-configedit — 批量修改工具

支持下载器与 RSS 任务的全部常用设置，单行指令一次输入、只需确认一次，也支持 `--yes` 完全免确认。

**配置来源（优先级从高到低）**：命令行参数 → `config.yaml` → 环境变量（`VTURL` / `VT_USERNAME` / `VT_PASSWORD`）→ 交互询问。

```bash
# 交互式（启动后选择 1=下载器 2=RSS任务）
python vertex_config.py

# 一键修改 netcup 关键字下的下载器（--yes 免确认）
python vertex_config.py --kw netcup leech=30 cron=3 up=50MiB rules=1,2,3 --yes

# 修改 RSS 任务
python vertex_config.py --rss --kw 动画 sort=upload maxdl=5 skip=on --yes

# 仅查看
python vertex_config.py --kw netcup --list
```

**指令格式**：`字段=值`，多个用 `;` 分隔，如 `leech=30; cron=3; up=50MiB; rules=1,2,3`。

- 下载器：`rules`、`reject`、`leech`、`ad`、`cron`、`space`、`up`、`down`、`en`、`monitor`、`push`、`reann`、`alarm`
- RSS：`sort`、`maxdl`、`maxupspeed`、`maxdownspeed`、`skip`、`cron`、`arr`/`arr+`/`arr-`、`path`、`reseed`、`sleep`、`en`、`push`

> RSS 的 `arr+`/`arr-`/`arr=` 会自动联动维护 `reseedClients`，`reseed=on` 时自动补齐补种下载器列表；不同版本响应缺少相关键时不受影响。

---

## 其他子项目

- **autobrr_loadbalance_vt**：Flask Webhook，接收 autobrr 推送，按策略（上传最低 / 种子最少 / 空间最大 / 综合评分）把种子推给在线的 qBittorrent。配置见 `.env`。
- **hetzner-usagemonit_vt**：Hetzner API 监控，流量超阈值自动删除并重建机器，支持定时删建；机器 IP 变化时自动同步 Vertex 下载器 `clientUrl`。配置见 `config.json`。
- **netcup-control-RESTAPI_vt**：Netcup 流量监控 REST API，支持限速（暂停/删除）策略与 Telegram 通知。配置见 `config.json`。

---

## 安全说明

- 各配置文件的真实值（API Key、Token、密码、Cookie、服务器地址）已清空，首次使用请自行填写。
- `vertex_cookie.py` / `vertex_config.py` 不内置任何默认凭据，密码一律运行时提供。
- 请勿把含真实凭据的 `config.json`、`.env`、`vertex_cookie_cache.json` 提交到仓库。