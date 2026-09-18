# vertex_integrate

围绕 **Vertex 下载器管理面板** 的一批实用脚本/服务集合，另附若干独立的运维小工具。基于 Vertex HTTP API 的子项目共用同一个 `vertex_cookie.py`，实现会话 Cookie 的自动获取、缓存与失效刷新。

> 仓库内不含任何真实凭据。所有用户名、IP、密码、Token、Cookie 均已脱敏为占位符，请按需填写自己的配置后使用。

---

## 目录结构

| 路径 | 说明 |
| ---- | ---- |
| `vertex-configedit/` | Vertex 批量修改工具（下载器 / RSS 任务），支持单行指令与 CLI 一键执行 |
| `autobrr_loadbalance_vt/` | autobrr Webhook 负载均衡服务：把种子推送给在线且最合适的 qBittorrent（经 Vertex 代理） |
| `hetzner-usagemonit_vt/` | Hetzner 云主机流量监控 + 超阈值自动重建 + 定时删建，并同步 Vertex 下载器 IP |
| `netcup-control-RESTAPI_vt/` | Netcup 流量监控 / 限速控制 REST API 服务 |
| `multqbittorrent.sh` | qBittorrent 多开一键脚本：批量创建多用户多实例 + systemd 服务（**独立工具，与 Vertex 无关**） |
| `frds3.3t_reseed.py` | FRDS 大包批量辅种脚本：读取种子列表后批量推送到 qBittorrent（**独立工具**） |

> `vertex_cookie.py` 在多个子项目目录中内容保持一致，修改时请同步所有副本。

---

## multqbittorrent.sh — qBittorrent 多开脚本

在 Linux（systemd 发行版）上批量创建 qBittorrent 多实例：每个实例一个系统用户、一份独立配置、一个独立 WebUI/BT 端口，并注册为开机自启的 systemd 服务。

**特性**

- 自动识别 **qBittorrent 4.x / 5.x** 配置格式差异，正确处理双端口字段（无需手动填版本号）
- 自动处理 5.x 的 `[BitTorrent] Session\Port` 与 `[Preferences] Connection\PortRangeMin` 双字段
- 复制“基础用户”配置作为模板，自动替换端口、下载路径与家目录引用
- 创建前做端口占用预检，避免中途失败
- 修改后回读校验，端口/随机端口设置不一致会给出警告
- 交互模式 + 命令行参数双支持，配置项已脱敏为占位符

**运行前提**

1. root 权限（`sudo` 运行）
2. 已安装 `qbittorrent-nox`（脚本自动查找可执行文件）
3. 已有一个跑过 qBittorrent 的“基础用户”，即存在：
   `/home/<基础用户名>/.config/qBittorrent/qBittorrent.conf`
4. 系统使用 systemd

**使用前：填写配置**

编辑脚本顶部「用户配置区」，把占位符改成你自己的值：

```bash
BASE_USER_DEFAULT="your_base_user"        # ← 改成已存在且有配置的用户
USER_PREFIX_DEFAULT="qbuser"              # ← 新实例用户名前缀，如 qbuser1
DEFAULT_PASSWORD="ChangeMe_Strong123"     # ← 改成你自己的强密码
START_PORT_DEFAULT=8081                   # ← WebUI 起始端口
BT_PORT_STEP=2                            # ← BT 端口步长（一般不用改）
```

> 未修改密码/基础用户占位符就运行，脚本会直接报错提示，避免误用。

**快速开始**

```bash
chmod +x multqbittorrent.sh

sudo ./multqbittorrent.sh              # 交互模式（推荐，跟着提示填）
sudo ./multqbittorrent.sh -h           # 查看帮助

# 命令行参数：<实例数量> [起始WebUI端口] [用户名前缀] [基础用户名]
sudo ./multqbittorrent.sh 3
sudo ./multqbittorrent.sh 2 8033
sudo ./multqbittorrent.sh 3 9000 qbuser alice
```

**端口规则**

| 类型 | 规则 | 示例（基础 BT 端口 6881） |
| ---- | ---- | ---- |
| WebUI | 起始端口 + (实例号 - 1) | 8081、8082、8083 ... |
| BT | 基础 BT 端口 + 实例号 × 步长(2) | 6883、6885、6887 ... |

**运行后**

- 脚本会输出端口分配表、WebUI 访问地址（`http://<本机IP>:<端口>`）与 systemd 管理命令
- qBittorrent 默认 WebUI 账号通常是 `admin / adminadmin`，首次登录后请立即修改
- 多开完成后，建议逐个执行 `sudo passwd <用户名>` 修改系统密码

**常用管理命令**

```bash
systemctl start   qbittorrent-<用户>
systemctl stop    qbittorrent-<用户>
systemctl restart qbittorrent-<用户>
systemctl status  qbittorrent-<用户>
journalctl -u qbittorrent-<用户> -f

# 删除某个实例
systemctl stop qbittorrent-<用户> && systemctl disable qbittorrent-<用户> \
  && rm -f /etc/systemd/system/qbittorrent-<用户>.service && systemctl daemon-reload
```

---

## frds3.3t_reseed.py — FRDS 批量辅种脚本

按配置好的任务列表，把一批种子（torrent id / URL）批量推送到指定 qBittorrent，用于 FRDS 大包的批量辅种/重做种，支持并发下载与缓存。

**使用前：填写配置**

编辑脚本顶部：

```python
QB_URL  = ""      # qBittorrent WebUI 地址，如 http://127.0.0.1:8080
QB_USER = ""      # WebUI 用户名
QB_PASS = ""      # WebUI 密码
PASSKEY = ""      # 站点 passkey
COMMON_SAVE_PATH = "./..."   # 保存路径
FRDS_URL = ""     # 种子获取地址
TORRENT_JOBS = [ ... ]       # 任务列表（save_path + torrent_ids）
```

**运行**

```bash
python frds3.3t_reseed.py
```

> 脚本依赖 `requests`（`pip install requests`）。种子缓存目录、并发数等参数在文件顶部调整。

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

- 各配置文件的真实值（API Key、Token、密码、Cookie、服务器地址）已清空/占位，首次使用请自行填写。
- `multqbittorrent.sh` 不含真实用户名/密码，配置文件顶部均为占位符；运行前必须修改，脚本会校验。
- `vertex_cookie.py` / `vertex_config.py` / `frds3.3t_reseed.py` 不内置任何默认凭据，密码一律运行时提供。
- 请勿把含真实凭据的 `config.json`、`.env`、`vertex_cookie_cache.json`、填写后的脚本提交到仓库。
