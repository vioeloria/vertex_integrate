#!/bin/bash
# ════════════════════════════════════════════════════════════════════════
#  文件名 : multqbittorrent.sh
#  功能   : 在 Linux 上批量创建 qBittorrent 多实例（多用户 + systemd 服务）
#  作者   : (按需填写)
#  版本   : 2.0
#  适用   : Debian / Ubuntu / CentOS / 其他使用 systemd 的发行版
#
#  【脚本做了什么】
#    1. 读取一个“基础用户”已配置好的 qBittorrent.conf
#    2. 为每个新实例创建一个 Linux 系统用户（如 qbuser1、qbuser2 ...）
#    3. 复制基础用户的配置到新用户目录，并自动改端口/路径
#    4. 生成对应的 systemd 服务，实现开机自启
#
#  【运行前提】
#    - 必须是 root（脚本内部会检查，用 sudo 运行）
#    - 已安装 qbittorrent-nox（脚本会自动查找可执行文件）
#    - 已有一个“基础用户”跑过 qBittorrent，即存在配置文件：
#         /home/<基础用户名>/.config/qBittorrent/qBittorrent.conf
#    - 系统使用 systemd（否则只能改配置，无法注册服务）
#
#  【快速开始】
#    chmod +x multqbittorrent.sh
#    sudo ./multqbittorrent.sh              # 交互模式，跟着提示填即可（推荐）
#    sudo ./multqbittorrent.sh -h           # 查看帮助
#
#  【重要】使用前请先看下面的“用户配置区”，把占位符改成你自己的值！
#         脚本已做脱敏处理，不含任何真实用户名/密码。
# ════════════════════════════════════════════════════════════════════════


# ┌──────────────────────────────────────────────────────────────────────┐
# │  ★★★  用户配置区：使用前请修改这里（已脱敏，均为占位符）  ★★★        │
# ├──────────────────────────────────────────────────────────────────────┤
# │  说明：以下 4 个变量是脚本的默认值。                                   │
# │        * 交互模式 / 命令行传参 时会覆盖这里的默认值。                   │
# │        * 若你只想“改一次以后一直用”，直接改这里即可。                   │
# │        * 带“← 改成”标记的行是必须按你的情况修改的。                    │
# └──────────────────────────────────────────────────────────────────────┘

# 基础配置用户名：已存在且有 qBittorrent 配置的 Linux 用户。
# 脚本会复制它的配置作为模板。此用户必须真实存在！
BASE_USER_DEFAULT="your_base_user"        # ← 改成你的用户名，例如 alice

# 新建实例的用户名前缀：最终用户名为 <前缀>+<序号>，如 qbuser1、qbuser2。
# 规则：小写字母开头，仅含小写字母和数字，长度 ≤ 20。
USER_PREFIX_DEFAULT="qbuser"              # ← 改成你喜欢的英文前缀

# 新建实例的系统登录密码：所有新实例用户共用该密码。
# 安全提示：不要用弱密码；脚本运行后可自行用 passwd 逐个修改。
DEFAULT_PASSWORD="ChangeMe_Strong123"     # ← 改成你自己的强密码

# WebUI 起始端口：第 1 个实例用该端口，之后依次 +1。
# 范围 1024-65535，且需未被占用。
START_PORT_DEFAULT=8081

# BT 端口步长：第 i 个实例 BT 端口 = 基础BT端口 + i * BT_PORT_STEP。
# 保留间隔是为了避免不同实例的 BT 监听端口互相冲突，一般无需修改。
BT_PORT_STEP=2


# ── 颜色定义（用于终端信息高亮，无需修改） ──────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'                              # NC = No Color，恢复默认颜色


# ── 日志输出函数（统一格式，方便阅读） ──────────────────────────────────
info()       { echo -e "${BLUE}[INFO]${NC} $1"; }      # 普通信息
warn()       { echo -e "${YELLOW}[WARN]${NC} $1"; }    # 警告（可继续）
error()      { echo -e "${RED}[ERROR]${NC} $1"; }      # 错误（通常退出）
success()    { echo -e "${GREEN}[SUCCESS]${NC} $1"; }  # 成功提示
need_input() { echo -e "${YELLOW}[INPUT]${NC} $1"; }   # 请求用户输入


# ── 帮助信息 ────────────────────────────────────────────────────────────
# 用法: show_help
show_help() {
    cat << EOF
qBittorrent 多开配置脚本（支持 4.x / 5.x）

用法:
    $0 <实例数量> [起始WebUI端口] [用户名前缀] [基础用户名]
    $0                   # 进入交互模式（推荐，跟着提示填即可）
    $0 -h / --help       # 显示本帮助

参数:
    实例数量        1-20 之间的整数
    起始WebUI端口   第 1 个实例使用的端口，默认 $START_PORT_DEFAULT，之后依次 +1
    用户名前缀      新用户的用户名前缀，默认 $USER_PREFIX_DEFAULT
    基础用户名      复制哪份配置作为模板，默认 $BASE_USER_DEFAULT

示例:
    $0 3                          # 创建3个实例，WebUI端口从 $START_PORT_DEFAULT 起
    $0 2 8033                     # 创建2个实例，WebUI端口 8033-8034
    $0 3 9000 qbuser alice        # 完整参数：3个实例，端口9000起，前缀qbuser，模板用户alice

版本差异说明（脚本会自动检测，无需手动输入版本号）:
    4.x: BT端口只有 [Preferences] Connection\\PortRangeMin
         下载路径在 [Preferences] Downloads\\SavePath

    5.x: BT端口有两处，两处都必须修改才能生效:
           [Preferences] Connection\\PortRangeMin
           [BitTorrent]  Session\\Port

    脚本通过判断配置里是否存在 [BitTorrent] 段的 Session\\Port 来识别版本。

EOF
}


# ════════════════════════════════════════════════════════════════════════
#  校验区：确保用户已填写真实配置，避免把占位符当成真实值运行
# ════════════════════════════════════════════════════════════════════════

# 检查“用户配置区”的占位符是否仍未修改。
# 用法: check_placeholders <是否检查基础用户: 1/0>
#   - 密码占位符始终检查（没有命令行密码参数，未修改即为风险）
#   - 基础用户仅在“使用默认值”时才检查（传参指定真实用户则无需检查）
check_placeholders() {
    local check_base="${1:-1}"
    local bad=0

    if [ "$DEFAULT_PASSWORD" = "ChangeMe_Strong123" ]; then
        error "尚未配置密码：请修改脚本顶部 DEFAULT_PASSWORD，不要使用默认占位密码"
        bad=1
    fi
    if [ "$check_base" = "1" ] && [ "$BASE_USER_DEFAULT" = "your_base_user" ]; then
        error "尚未配置基础用户名：请修改脚本顶部 BASE_USER_DEFAULT，或用第 4 个参数传入"
        bad=1
    fi

    if [ "$bad" -ne 0 ]; then
        echo ""
        info "提示：可用交互模式逐个输入，或直接编辑脚本顶部的“用户配置区”。"
        info "      查看帮助：$0 -h"
        exit 1
    fi
}


# ════════════════════════════════════════════════════════════════════════
#  工具函数区
# ════════════════════════════════════════════════════════════════════════

# 检测配置文件是 4.x 还是 5.x 格式。
# 判断依据：5.x 在 [BitTorrent] 段里有 "Session\Port" 字段。
# 用法: detect_config_version <配置文件路径>  -> 输出 "4x" 或 "5x"
detect_config_version() {
    local config_file="$1"
    if grep -q "^Session\\\\Port=" "$config_file"; then
        echo "5x"
    else
        echo "4x"
    fi
}

# 读取配置文件中指定字段的值（自动处理反斜杠转义与 Windows 换行符）。
# 用法: read_field <文件> <字段名>
# 字段名示例: "Connection\PortRangeMin" 或 "Session\Port"
read_field() {
    local config_file="$1"
    local field="$2"
    local escaped
    escaped=$(echo "$field" | sed 's/\\/\\\\/g')   # 反斜杠转义，供 grep 使用
    grep "^${escaped}=" "$config_file" | cut -d'=' -f2- | tr -d '\r'
}

# 设置配置文件中指定字段的值；字段不存在则追加到对应 section 末尾。
# 用法: set_field <文件> <字段名> <值> <所属section>
set_field() {
    local config_file="$1"
    local field="$2"
    local value="$3"
    local section="$4"
    local escaped
    escaped=$(echo "$field" | sed 's/\\/\\\\/g')

    if grep -q "^${escaped}=" "$config_file"; then
        # 字段已存在：直接就地替换整行
        sed -i "s|^${escaped}=.*|${escaped}=${value}|" "$config_file"
    else
        # 字段不存在：在指定 [section] 标题行后面插入
        warn "字段 $field 不存在，追加到 [$section] 段"
        sed -i "/^\[${section}\]/a ${escaped}=${value}" "$config_file"
    fi
}

# 检查端口是否空闲（未被监听）。
# 返回值: 0=空闲, 1=被占用
# 用法: check_port_free <端口号>
check_port_free() {
    local port="$1"
    # 优先用 ss，退回 netstat；两个都不可用时视为空闲（后续启动阶段才会真正暴露冲突）
    if ss -tulpn 2>/dev/null | grep -q ":${port}[[:space:]]" || \
       netstat -tulpn 2>/dev/null | grep -q ":${port}[[:space:]]"; then
        return 1
    fi
    return 0
}

# 创建 Linux 系统用户并设置密码；用户已存在则跳过。
# 用法: create_system_user <用户名> <密码>
create_system_user() {
    local username="$1"
    local password="$2"

    if id -u "$username" > /dev/null 2>&1; then
        warn "用户 $username 已存在，跳过创建"
        return 0
    fi

    # -m 创建家目录，-s 指定登录 shell
    useradd -m -s /bin/bash "$username" || { error "创建用户 $username 失败"; return 1; }
    echo "$username:$password" | chpasswd || { error "设置 $username 密码失败"; return 1; }
    chown -R "$username:$username" "/home/$username"
    success "用户 $username 创建成功（密码: $password）"
    info  "安全建议：如需修改密码，执行 sudo passwd $username"
}

# 获取本机对外 IP，失败则回退为 localhost。
# 用法: get_host_ip
get_host_ip() {
    local ip
    ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -1)
    [ -z "$ip" ] && ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -z "$ip" ] && ip="localhost"
    echo "$ip"
}


# ════════════════════════════════════════════════════════════════════════
#  交互模式：逐个引导用户输入，适合不熟悉命令行的场景
# ════════════════════════════════════════════════════════════════════════
interactive_input() {
    echo "========================================="
    echo " qBittorrent 多开配置 - 交互模式"
    echo "========================================="
    echo "（直接回车即使用括号内默认值）"
    echo ""

    # 1) 实例数量
    while true; do
        need_input "创建实例数量 (1-20): "
        read -r NUM_INSTANCES
        [[ "$NUM_INSTANCES" =~ ^[0-9]+$ ]] && \
            [ "$NUM_INSTANCES" -ge 1 ] && [ "$NUM_INSTANCES" -le 20 ] && break
        error "请输入 1-20 之间的整数"
    done

    # 2) 起始 WebUI 端口
    while true; do
        need_input "WebUI 起始端口 (默认 $START_PORT_DEFAULT): "
        read -r START_PORT
        [ -z "$START_PORT" ] && START_PORT=$START_PORT_DEFAULT && break
        [[ "$START_PORT" =~ ^[0-9]+$ ]] && \
            [ "$START_PORT" -ge 1024 ] && [ "$START_PORT" -le 65535 ] && break
        error "请输入 1024-65535 之间的端口号"
    done

    # 3) 用户名前缀
    while true; do
        need_input "用户名前缀 (默认 $USER_PREFIX_DEFAULT): "
        read -r USER_PREFIX
        [ -z "$USER_PREFIX" ] && USER_PREFIX="$USER_PREFIX_DEFAULT" && break
        [[ "$USER_PREFIX" =~ ^[a-z][a-z0-9]*$ ]] && \
            [ "${#USER_PREFIX}" -le 20 ] && break
        error "只能包含小写字母和数字，以字母开头，长度 ≤ 20"
    done

    # 4) 基础用户（必须是已存在且已配置 qBittorrent 的用户）
    while true; do
        need_input "基础配置用户名 (默认 $BASE_USER_DEFAULT): "
        read -r BASE_USER
        [ -z "$BASE_USER" ] && BASE_USER="$BASE_USER_DEFAULT"
        if id -u "$BASE_USER" > /dev/null 2>&1; then
            break
        fi
        error "用户 $BASE_USER 不存在，请输入已存在的用户名"
    done

    # 5) 密码
    while true; do
        need_input "新用户统一密码 (默认 $DEFAULT_PASSWORD): "
        read -r input_pwd
        [ -n "$input_pwd" ] && DEFAULT_PASSWORD="$input_pwd"
        [ -n "$DEFAULT_PASSWORD" ] && break
        error "密码不能为空"
    done

    # 6) 确认
    echo ""
    info "配置确认："
    info "  实例数量 : $NUM_INSTANCES"
    info "  起始端口 : $START_PORT"
    info "  用户前缀 : $USER_PREFIX"
    info "  基础用户 : $BASE_USER"
    info "  统一密码 : $DEFAULT_PASSWORD"
    echo ""
    while true; do
        need_input "确认以上配置? (y/n): "
        read -r confirm
        case $confirm in
            [Yy]*) break ;;
            [Nn]*) exit 0 ;;
            *) echo "请输入 y 或 n" ;;
        esac
    done
}


# ════════════════════════════════════════════════════════════════════════
#  主程序开始
# ════════════════════════════════════════════════════════════════════════

# 1) 权限检查：必须 root
[ "$EUID" -ne 0 ] && { error "需要 root 权限，请使用 sudo 运行"; exit 1; }

# 2) 查找 qbittorrent-nox 可执行文件
QB_NOX_PATH=$(which qbittorrent-nox 2>/dev/null)
[ -z "$QB_NOX_PATH" ] && { error "未找到 qbittorrent-nox，请先安装"; exit 1; }

# 3) 解析参数或进入交互模式
if [ $# -eq 0 ]; then
    # 无参数 -> 交互模式
    interactive_input
elif [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    show_help; exit 0
else
    # 命令行参数模式：未提供的使用“用户配置区”的默认值
    NUM_INSTANCES="$1"
    START_PORT="${2:-$START_PORT_DEFAULT}"
    USER_PREFIX="${3:-$USER_PREFIX_DEFAULT}"
    BASE_USER="${4:-$BASE_USER_DEFAULT}"

    [[ "$NUM_INSTANCES" =~ ^[0-9]+$ ]] && \
        [ "$NUM_INSTANCES" -ge 1 ] && [ "$NUM_INSTANCES" -le 20 ] || \
        { error "实例数量必须是 1-20 之间的整数"; exit 1; }

    [[ "$START_PORT" =~ ^[0-9]+$ ]] && \
        [ "$START_PORT" -ge 1024 ] && [ "$START_PORT" -le 65535 ] || \
        { error "端口号必须在 1024-65535 之间"; exit 1; }

    [[ "$USER_PREFIX" =~ ^[a-z][a-z0-9]*$ ]] && \
        [ "${#USER_PREFIX}" -le 20 ] || \
        { error "用户名前缀只能包含小写字母和数字，以字母开头，长度 ≤ 20"; exit 1; }

    # 检查占位符：基础用户仅在本次使用默认值（未传第 4 个参数）时检查
    if [ "$BASE_USER" = "$BASE_USER_DEFAULT" ]; then
        check_placeholders 1
    else
        check_placeholders 0
    fi
fi

# 4) 计算基础路径与配置文件（供后续复制）
BASE_HOME="/home/$BASE_USER"
BASE_CONFIG_DIR="$BASE_HOME/.config/qBittorrent"
BASE_CONFIG_FILE="$BASE_CONFIG_DIR/qBittorrent.conf"

# 5) 基础用户/配置存在性检查
id -u "$BASE_USER" > /dev/null 2>&1 || { error "基础用户不存在: $BASE_USER"; exit 1; }
[ -d "$BASE_CONFIG_DIR" ]  || { error "基础配置目录不存在: $BASE_CONFIG_DIR"; exit 1; }
[ -f "$BASE_CONFIG_FILE" ] || { error "配置文件不存在: $BASE_CONFIG_FILE"; exit 1; }

# 6) 自动检测配置格式（4.x / 5.x）并读取基础 BT 端口
CONFIG_VER=$(detect_config_version "$BASE_CONFIG_FILE")

if [ "$CONFIG_VER" = "5x" ]; then
    # 5.x：[BitTorrent] Session\Port 才是实际生效的监听端口
    BASE_BT_PORT=$(read_field "$BASE_CONFIG_FILE" "Session\\Port")
    [ -z "$BASE_BT_PORT" ] && BASE_BT_PORT=6881 && warn "未读到 Session\\Port，使用默认 6881"
    info "检测到 5.x 配置格式"
    info "BT端口来源: [BitTorrent] Session\\Port = $BASE_BT_PORT"
    info "（同时会同步更新 [Preferences] Connection\\PortRangeMin）"
else
    # 4.x：只有 [Preferences] Connection\PortRangeMin
    BASE_BT_PORT=$(read_field "$BASE_CONFIG_FILE" "Connection\\PortRangeMin")
    [ -z "$BASE_BT_PORT" ] && BASE_BT_PORT=6881 && warn "未读到 Connection\\PortRangeMin，使用默认 6881"
    info "检测到 4.x 配置格式"
    info "BT端口来源: [Preferences] Connection\\PortRangeMin = $BASE_BT_PORT"
fi

# 7) 端口冲突预检：提前发现问题，避免创建到一半失败
info "检查端口占用情况..."
CONFLICT=()
for i in $(seq 1 "$NUM_INSTANCES"); do
    WEBUI_PORT=$((START_PORT + i - 1))
    BT_PORT=$((BASE_BT_PORT + i * BT_PORT_STEP))
    check_port_free "$WEBUI_PORT" || CONFLICT+=("WebUI 端口 $WEBUI_PORT 已被占用")
    check_port_free "$BT_PORT"    || CONFLICT+=("BT 端口 $BT_PORT 已被占用")
done

if [ ${#CONFLICT[@]} -gt 0 ]; then
    error "发现端口冲突，请更换起始端口或释放占用："
    for msg in "${CONFLICT[@]}"; do echo "   ✗ $msg"; done
    exit 1
fi
success "端口检查通过"

# 8) 打印本次运行的总览
echo ""
echo "========================================="
echo " qBittorrent 多开配置"
echo "========================================="
info "配置格式   : $CONFIG_VER"
info "实例数量   : $NUM_INSTANCES"
info "起始端口   : $START_PORT"
info "用户前缀   : $USER_PREFIX"
info "基础用户   : $BASE_USER"
info "基础BT端口 : $BASE_BT_PORT  →  实例1=$((BASE_BT_PORT+BT_PORT_STEP))  实例2=$((BASE_BT_PORT+BT_PORT_STEP*2)) ..."
info "qb路径     : $QB_NOX_PATH"
info "统一密码   : $DEFAULT_PASSWORD"
echo ""


# ════════════════════════════════════════════════════════════════════════
#  逐实例创建：用户 -> 配置 -> 服务
# ════════════════════════════════════════════════════════════════════════
CREATED_USERS=()        # 记录成功创建的用户名
CREATED_SERVICES=()     # 记录成功创建的服务名
PORT_ASSIGNMENTS=()     # 记录 用户名|WebUI端口|BT端口

for i in $(seq 1 "$NUM_INSTANCES"); do
    # 当前实例的变量
    NEW_USER="${USER_PREFIX}${i}"
    NEW_HOME="/home/$NEW_USER"
    NEW_CONFIG_DIR="$NEW_HOME/.config/qBittorrent"
    NEW_CONFIG_FILE="$NEW_CONFIG_DIR/qBittorrent.conf"
    NEW_WEBUI_PORT=$((START_PORT + i - 1))
    NEW_BT_PORT=$((BASE_BT_PORT + i * BT_PORT_STEP))

    echo "━━━ 实例 $i / $NUM_INSTANCES : $NEW_USER ━━━"

    # (1) 创建系统用户
    create_system_user "$NEW_USER" "$DEFAULT_PASSWORD" || continue
    CREATED_USERS+=("$NEW_USER")

    # (2) 确保 .config 目录存在
    sudo -u "$NEW_USER" mkdir -p "$NEW_HOME/.config"

    # (3) 复制基础配置目录作为模板
    info "复制配置目录 -> $NEW_CONFIG_DIR"
    if command -v rsync > /dev/null 2>&1; then
        rsync -a "$BASE_CONFIG_DIR/" "$NEW_CONFIG_DIR/"
    else
        cp -r "$BASE_CONFIG_DIR" "$NEW_HOME/.config/"
    fi
    chown -R "$NEW_USER:$NEW_USER" "$NEW_CONFIG_DIR"
    success "配置目录复制完成"

    # (4) 创建下载目录
    sudo -u "$NEW_USER" mkdir -p "$NEW_HOME/qbittorrent/Downloads"
    info "下载目录: $NEW_HOME/qbittorrent/Downloads"

    # (5) 修改配置文件（端口 + 路径）
    if [ ! -f "$NEW_CONFIG_FILE" ]; then
        warn "配置文件不存在，跳过修改: $NEW_CONFIG_FILE"
    else
        info "修改配置文件（$CONFIG_VER 格式）..."

        # 5a. WebUI 端口（4.x / 5.x 字段名相同）
        set_field "$NEW_CONFIG_FILE" "WebUI\\Port" "$NEW_WEBUI_PORT" "Preferences"

        if [ "$CONFIG_VER" = "5x" ]; then
            # 5.x：两处 BT 端口都要改，缺一不可
            #   [BitTorrent] Session\Port          ← 实际控制监听端口
            #   [Preferences] Connection\PortRangeMin ← 同步，避免 WebUI 显示不一致
            set_field "$NEW_CONFIG_FILE" "Session\\Port" "$NEW_BT_PORT" "BitTorrent"
            set_field "$NEW_CONFIG_FILE" "Connection\\PortRangeMin" "$NEW_BT_PORT" "Preferences"
        else
            # 4.x：只有 [Preferences] Connection\PortRangeMin
            set_field "$NEW_CONFIG_FILE" "Connection\\PortRangeMin" "$NEW_BT_PORT" "Preferences"
        fi

        # 5b. 关闭随机端口（避免安装时默认开启，导致监听端口漂移）
        set_field "$NEW_CONFIG_FILE" "Connection\\UseRandomPort" "false" "Preferences"

        # 5c. 全文替换路径引用：把基础用户家目录改为新用户家目录
        sed -i "s|/home/${BASE_USER}/|/home/${NEW_USER}/|g" "$NEW_CONFIG_FILE"

        # 5d. 回读校验，确认修改真正生效
        VERIFY_WEBUI=$(read_field "$NEW_CONFIG_FILE" "WebUI\\Port")
        VERIFY_RAND=$(read_field  "$NEW_CONFIG_FILE" "Connection\\UseRandomPort")
        VERIFY_OK=true

        if [ "$CONFIG_VER" = "5x" ]; then
            VERIFY_SESSION=$(read_field "$NEW_CONFIG_FILE" "Session\\Port")
            VERIFY_PREF=$(read_field    "$NEW_CONFIG_FILE" "Connection\\PortRangeMin")
            [ "$VERIFY_WEBUI"   != "$NEW_WEBUI_PORT" ] && VERIFY_OK=false
            [ "$VERIFY_SESSION" != "$NEW_BT_PORT"    ] && VERIFY_OK=false
            [ "$VERIFY_PREF"    != "$NEW_BT_PORT"    ] && VERIFY_OK=false
            [ "$VERIFY_RAND"    != "false"           ] && VERIFY_OK=false
            if $VERIFY_OK; then
                success "验证通过: WebUI=$VERIFY_WEBUI | Session\\Port=$VERIFY_SESSION | PortRangeMin=$VERIFY_PREF | UseRandomPort=false"
            else
                warn "验证异常: WebUI='$VERIFY_WEBUI'(应$NEW_WEBUI_PORT) Session\\Port='$VERIFY_SESSION'(应$NEW_BT_PORT) PortRangeMin='$VERIFY_PREF'(应$NEW_BT_PORT) UseRandomPort='$VERIFY_RAND'(应false)"
            fi
        else
            VERIFY_BT=$(read_field "$NEW_CONFIG_FILE" "Connection\\PortRangeMin")
            [ "$VERIFY_WEBUI" != "$NEW_WEBUI_PORT" ] && VERIFY_OK=false
            [ "$VERIFY_BT"    != "$NEW_BT_PORT"    ] && VERIFY_OK=false
            [ "$VERIFY_RAND"  != "false"            ] && VERIFY_OK=false
            if $VERIFY_OK; then
                success "验证通过: WebUI=$VERIFY_WEBUI | PortRangeMin=$VERIFY_BT | UseRandomPort=false"
            else
                warn "验证异常: WebUI='$VERIFY_WEBUI'(应$NEW_WEBUI_PORT) PortRangeMin='$VERIFY_BT'(应$NEW_BT_PORT) UseRandomPort='$VERIFY_RAND'(应false)"
            fi
        fi
    fi

    # (6) 创建 systemd 服务，实现开机自启 / 后台运行
    SERVICE_NAME="qbittorrent-${NEW_USER}"
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    info "创建服务: $SERVICE_FILE"

    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=qBittorrent Daemon for $NEW_USER
After=network.target

[Service]
Type=forking
User=$NEW_USER
Group=$NEW_USER
UMask=0002
LimitNOFILE=infinity
ExecStart=$QB_NOX_PATH -d --webui-port=$NEW_WEBUI_PORT
ExecStop=/usr/bin/killall -w -s 9 $QB_NOX_PATH
Restart=on-failure
TimeoutStopSec=20
RestartSec=10
WorkingDirectory=$NEW_HOME

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    CREATED_SERVICES+=("$SERVICE_NAME")
    PORT_ASSIGNMENTS+=("${NEW_USER}|${NEW_WEBUI_PORT}|${NEW_BT_PORT}")

    success "实例 $NEW_USER 配置完成"
    echo ""
done


# ════════════════════════════════════════════════════════════════════════
#  汇总报告：端口分配、访问地址、管理命令
# ════════════════════════════════════════════════════════════════════════
HOST_IP=$(get_host_ip)

echo "========================================="
success "🎉 完成！共创建 ${#CREATED_USERS[@]} / $NUM_INSTANCES 个实例"
echo "========================================="
echo ""

if [ ${#CREATED_USERS[@]} -eq 0 ]; then
    warn "没有成功创建任何实例，请检查以上错误信息"
    exit 1
fi

if [ "$CONFIG_VER" = "5x" ]; then
    BT_LABEL="BT端口(Session\\Port & PortRangeMin)"
else
    BT_LABEL="BT端口(PortRangeMin)"
fi

# 端口分配表
info "📊 端口分配："
printf "   %-16s %-12s %-s\n" "用户名" "WebUI端口" "$BT_LABEL"
echo "   ──────────────────────────────────────────────────────"
for entry in "${PORT_ASSIGNMENTS[@]}"; do
    IFS='|' read -r uname wport bport <<< "$entry"
    printf "   %-16s %-12s %-s\n" "$uname" "$wport" "$bport"
done

echo ""
info "📋 BT 端口递增（基础 $BASE_BT_PORT，步长 $BT_PORT_STEP）："
for i in $(seq 1 "$NUM_INSTANCES"); do
    echo "   实例 $i (${USER_PREFIX}${i}): $((BASE_BT_PORT + i * BT_PORT_STEP))"
done

echo ""
info "👤 用户信息（密码均为: $DEFAULT_PASSWORD，建议尽快用 passwd 逐个修改）："
for uname in "${CREATED_USERS[@]}"; do
    echo "   $uname"
done

echo ""
info "🌐 Web 界面访问地址："
for entry in "${PORT_ASSIGNMENTS[@]}"; do
    IFS='|' read -r uname wport _ <<< "$entry"
    echo "   $uname  →  http://$HOST_IP:$wport"
done

echo ""
info "🔑 默认 WebUI 账号提示："
echo "   qBittorrent 默认用户名/密码通常为 admin / adminadmin"
echo "   首次登录后请立即在「选项 → Web UI」中修改，避免安全风险"

ALL_SERVICES="${CREATED_SERVICES[*]}"

echo ""
info "🚀 服务管理命令："
echo ""
echo "   # 启动全部"
echo "   systemctl start $ALL_SERVICES"
echo ""
echo "   # 停止全部"
echo "   systemctl stop $ALL_SERVICES"
echo ""
echo "   # 重启全部"
echo "   systemctl restart $ALL_SERVICES"
echo ""
echo "   # 查看全部状态"
echo "   systemctl status $ALL_SERVICES"
echo ""
echo "   # 实时日志（以第1个实例为例）"
echo "   journalctl -u ${CREATED_SERVICES[0]} -f"
echo ""
echo "   # 删除某个实例（停服 + 禁用 + 删服务文件）"
echo "   systemctl stop ${CREATED_SERVICES[0]} && systemctl disable ${CREATED_SERVICES[0]} && rm -f /etc/systemd/system/${CREATED_SERVICES[0]}.service && systemctl daemon-reload"
echo ""

info "📝 常见问题："
echo "   1) WebUI 打不开：确认服务已启动，且防火墙放行了对应端口"
echo "   2) BT 端口不生效（5.x）：检查 Session\\Port 与 Connection\\PortRangeMin 是否一致"
echo "   3) 需要更多/更少实例：重新运行脚本会跳过已存在用户，不会重复创建"
echo ""
