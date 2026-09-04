#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Netcup 配置文件构建工具
支持多账号批量获取 token 并生成完整的 config.json
"""

import time
import json
import requests

AUTH_URL = "https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/auth/device"
TOKEN_URL = "https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/token"

# 这里放多个账号，每个账号只需要 client_id = "scp"  只改 "name": "325292"
ACCOUNTS = [
    {"name": "325292", "client_id": "scp"}
]


def request_device_code(client_id):
    """请求 device_code、user_code、验证链接"""
    data = {
        "client_id": client_id,
        "scope": "offline_access openid"
    }
    r = requests.post(AUTH_URL, data=data)
    r.raise_for_status()
    return r.json()


def poll_token(client_id, device_code, interval):
    """轮询 token endpoint，直到授权成功"""
    print(f"[{client_id}] 开始轮询，每 {interval}s 查询一次…")
    while True:
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id
        }
        r = requests.post(TOKEN_URL, data=data)
        resp = r.json()
        
        # 授权未完成
        if resp.get("error") == "authorization_pending":
            print(f"[{client_id}] 等待授权...")
            time.sleep(interval)
            continue
        
        # 授权成功
        if "access_token" in resp:
            print(f"[{client_id}] ✅ 授权成功，获取到 access_token！")
            return resp
        
        # 授权过期
        if resp.get("error") == "expired_token":
            print(f"[{client_id}] ❌ device_code 已过期，请重新运行脚本。")
            return None
        
        # 其他错误
        print(f"[{client_id}] ❌ 遇到错误：{resp}")
        return None


def load_existing_config():
    """尝试加载现有的配置文件"""
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("📝 未找到现有配置文件，将创建新配置")
        return None
    except json.JSONDecodeError:
        print("⚠️  现有配置文件格式错误，将创建新配置")
        return None


def merge_config(existing_config, rest_accounts):
    """合并现有配置和新获取的账号信息"""
    if existing_config:
        # 保留现有的其他配置
        config = existing_config.copy()
        config["rest_accounts"] = rest_accounts
        print("✅ 已保留现有配置中的其他设置")
        return config
    else:
        # 创建新配置（带完整的注释模板）
        return {
            "webhook_path": "/webhook/secret-xxxxxxxxx",
            "port": 56578,
            "rest_accounts": rest_accounts,
            "vertex": {
                "base_url": "https://vertex.example.com",
                "cookie": "YOUR_VERTEX_COOKIE_HERE"
            },
            "telegram": {
                "bot_token": "YOUR_TELEGRAM_BOT_TOKEN_HERE",
                "chat_id": "YOUR_TELEGRAM_CHAT_ID_HERE"
            }
        }


def main():
    print("=" * 70)
    print("🚀 Netcup 配置文件构建工具")
    print("=" * 70)
    print()
    
    # 尝试加载现有配置
    existing_config = load_existing_config()
    
    rest_accounts = []
    
    for acc in ACCOUNTS:
        name = acc["name"]
        client_id = acc["client_id"]
        
        print(f"\n{'=' * 60}")
        print(f"📋 账号 {name} - 获取 device_code")
        print(f"{'=' * 60}")
        
        try:
            dev = request_device_code(client_id)
            device_code = dev["device_code"]
            user_code = dev["user_code"]
            verify_url = dev["verification_uri_complete"]
            interval = dev["interval"]
            
            print(f"[{name}] 🔗 请在浏览器打开以下链接完成授权：")
            print(f"    {verify_url}")
            print(f"[{name}] 🔑 用户代码：{user_code}")
            print()
            
            # 开始轮询 token
            token_data = poll_token(client_id, device_code, interval)
            
            if token_data:
                account_entry = {
                    "account_id": name,
                    "access_token": token_data.get("access_token", ""),
                    "refresh_token": token_data.get("refresh_token", "")
                }
                rest_accounts.append(account_entry)
                print(f"[{name}] ✅ Token 已保存\n")
            else:
                print(f"[{name}] ❌ Token 获取失败\n")
                
        except Exception as e:
            print(f"[{name}] ❌ 发生错误: {e}\n")
            continue
    
    if not rest_accounts:
        print("\n❌ 未成功获取任何账号的 token，配置文件未生成。")
        return
    
    # 合并配置
    config = merge_config(existing_config, rest_accounts)
    
    # 保存配置文件
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print("\n" + "=" * 70)
    print("✅ 配置文件生成完成！")
    print("=" * 70)
    print(f"📄 文件名: config.json")
    print(f"📊 成功获取 {len(rest_accounts)} 个账号的 token")
    print()
    
    # 检查需要用户手动配置的项
    needs_config = []
    
    vertex_config = config.get("vertex", {})
    if "example.com" in vertex_config.get("base_url", ""):
        needs_config.append("  • vertex.base_url - Vertex 服务地址")
    if "YOUR_VERTEX_COOKIE_HERE" in vertex_config.get("cookie", ""):
        needs_config.append("  • vertex.cookie - Vertex 认证 Cookie")
    
    telegram_config = config.get("telegram", {})
    if "YOUR_TELEGRAM_BOT_TOKEN_HERE" in telegram_config.get("bot_token", ""):
        needs_config.append("  • telegram.bot_token - Telegram Bot Token")
    if "YOUR_TELEGRAM_CHAT_ID_HERE" in telegram_config.get("chat_id", ""):
        needs_config.append("  • telegram.chat_id - Telegram Chat ID")
    
    if needs_config:
        print("⚠️  以下配置项需要手动修改：")
        for item in needs_config:
            print(item)
        print()
        print("📝 请编辑 config.json 文件，填入正确的配置信息")
    else:
        print("✅ 所有配置项已就绪，可以直接使用！")
    
    print()
    print("=" * 70)
    print("配置说明:")
    print("  • webhook_path: Webhook 路径（建议修改为随机字符串）")
    print("  • port: Web 服务端口")
    print("  • rest_accounts: Netcup 账户信息（已自动填充）")
    print("  • vertex.base_url: Vertex 服务器地址")
    print("  • vertex.cookie: Vertex 登录 Cookie")
    print("  • telegram.bot_token: Telegram Bot Token")
    print("  • telegram.chat_id: Telegram 接收消息的 Chat ID")
    print("=" * 70)
    print()
    
    # 显示配置文件预览
    print("📋 配置文件预览：")
    print("-" * 70)
    preview_config = config.copy()
    
    # 隐藏敏感信息
    for account in preview_config.get("rest_accounts", []):
        if account.get("access_token"):
            account["access_token"] = account["access_token"][:20] + "..." + account["access_token"][-10:]
        if account.get("refresh_token"):
            account["refresh_token"] = account["refresh_token"][:20] + "..." + account["refresh_token"][-10:]
    
    if preview_config.get("vertex", {}).get("cookie"):
        cookie = preview_config["vertex"]["cookie"]
        if len(cookie) > 50 and "YOUR_VERTEX_COOKIE_HERE" not in cookie:
            preview_config["vertex"]["cookie"] = cookie[:30] + "..." + cookie[-10:]
    
    if preview_config.get("telegram", {}).get("bot_token"):
        token = preview_config["telegram"]["bot_token"]
        if len(token) > 30 and "YOUR_TELEGRAM_BOT_TOKEN_HERE" not in token:
            preview_config["telegram"]["bot_token"] = token[:20] + "..." + token[-10:]
    
    print(json.dumps(preview_config, indent=2, ensure_ascii=False))
    print("-" * 70)


if __name__ == "__main__":
    main()
