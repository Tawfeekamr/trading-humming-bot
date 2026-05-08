#!/usr/bin/env python3
"""
Telegram diagnostic script — run inside the bot container to debug command issues.
Usage: docker exec -it trading-humming-bot-bot-1 python /home/hummingbot/scripts/diagnose_telegram.py
"""
import os
import sys
import json
import urllib.request

# Load .env
from pathlib import Path
for p in [Path("/home/hummingbot/.env"), Path(".env")]:
    if p.exists():
        from dotenv import load_dotenv
        load_dotenv(p, override=True)
        print(f"Loaded .env from {p}")
        break

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

print("=" * 60)
print("TELEGRAM DIAGNOSTIC")
print("=" * 60)

# 1. Check env vars
print(f"\n1. ENV VARS:")
print(f"   TELEGRAM_BOT_TOKEN: {'SET (%s...)' % token[:8] if token else 'NOT SET'}")
print(f"   TELEGRAM_CHAT_ID:   {repr(chat_id)}")
print(f"   Chat ID type:       {type(chat_id).__name__}")
print(f"   Chat ID is digit:   {chat_id.isdigit() if chat_id else 'N/A'}")
print(f"   Chat ID as int:     {int(chat_id) if chat_id and chat_id.lstrip('-').isdigit() else 'INVALID'}")

if not token or not chat_id:
    print("\n   FAIL: Token or chat_id missing. Check .env file.")
    sys.exit(1)

# 2. Test bot API access
print(f"\n2. BOT API ACCESS:")
try:
    url = f"https://api.telegram.org/bot{token}/getMe"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    print(f"   Bot username: @{data['result']['username']}")
    print(f"   Bot name: {data['result']['first_name']}")
except Exception as e:
    print(f"   FAIL: {e}")
    sys.exit(1)

# 3. Check for webhook (polling won't work if webhook is set)
print(f"\n3. WEBHOOK STATUS:")
try:
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    info = data['result']
    print(f"   Webhook URL: {info.get('url', 'none') or 'none'}")
    print(f"   Pending updates: {info.get('pending_update_count', 0)}")
    if info.get('url'):
        print(f"   WARNING: Webhook is set! This will conflict with polling.")
        print(f"   Fix: Run deleteWebhook first")
except Exception as e:
    print(f"   Error: {e}")

# 4. Get recent updates (see what Telegram has)
print(f"\n4. RECENT UPDATES (last 5):")
try:
    url = f"https://api.telegram.org/bot{token}/getUpdates?limit=5"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    updates = data.get('result', [])
    if not updates:
        print("   No pending updates")
    for u in updates:
        msg = u.get('message', {})
        chat = msg.get('chat', {})
        text = msg.get('text', '')
        from_user = msg.get('from', {})
        print(f"   Update {u['update_id']}: "
              f"chat_id={chat.get('id')} type={chat.get('type')} "
              f"from=@{from_user.get('username', '?')} "
              f"text={repr(text)}")
except Exception as e:
    print(f"   Error: {e}")

# 5. Send test message
print(f"\n5. SEND TEST MESSAGE:")
try:
    msg = "🔧 Telegram diagnostic — commands test. If you see this, outgoing messages work."
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={urllib.request.quote(msg)}"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    print(f"   Sent to chat_id={chat_id}, message_id={data['result']['message_id']}")
    print(f"   Outgoing messages: OK")
except Exception as e:
    print(f"   FAIL: {e}")

# 6. Check python-telegram-bot version
print(f"\n6. LIBRARY CHECK:")
try:
    import telegram
    print(f"   python-telegram-bot version: {telegram.__version__}")
except Exception as e:
    print(f"   Not installed: {e}")

# 7. Test command handler registration
print(f"\n7. COMMAND HANDLER TEST:")
try:
    from telegram.ext import Application, CommandHandler, filters
    app = Application.builder().token(token).build()
    cf = filters.Chat(int(chat_id))
    print(f"   filters.Chat({int(chat_id)}): created OK")
    print(f"   Application builder: OK")
    print(f"   python-telegram-bot is functional")
except Exception as e:
    print(f"   FAIL: {e}")

# 8. Check if getUpdates returns messages from the correct chat
print(f"\n8. CHAT ID VERIFICATION:")
try:
    url = f"https://api.telegram.org/bot{token}/getUpdates?limit=10&offset=-10"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    chats_seen = set()
    for u in data.get('result', []):
        chat = u.get('message', {}).get('chat', {})
        chats_seen.add((chat.get('id'), chat.get('type'), chat.get('username', '')))
    if chats_seen:
        print(f"   Recent chats seen:")
        for cid, ctype, cname in chats_seen:
            match = "MATCH" if str(cid) == chat_id else "MISMATCH"
            print(f"     id={cid} type={ctype} @{cname} [{match}]")
    else:
        print(f"   No recent updates to verify")
    print(f"\n   Expected chat_id: {chat_id}")
except Exception as e:
    print(f"   Error: {e}")

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
print("\nIf outgoing messages work but commands don't respond, likely causes:")
print("1. Chat ID mismatch — check section 8 above")
print("2. Webhook conflict — check section 3 above")
print("3. getUpdates conflict — polling AND manual getUpdates compete for updates")
print("4. Thread crash — check bot logs for 'Telegram polling thread crashed'")
