import os
import json
import asyncio
import threading
from datetime import datetime
from flask import Flask

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6388027054  # ✅ Your Telegram ID

DATA_FILE = "data.json"

# ---------------- DATABASE ----------------

def load_db():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "messages": {}}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_db():
    with open(DATA_FILE, "w") as f:
        json.dump(db, f, indent=2)

db = load_db()

# Persist bot_data mapping across restarts
MSG_MAP_FILE = "msg_map.json"

def load_msg_map():
    if not os.path.exists(MSG_MAP_FILE):
        return {}
    with open(MSG_MAP_FILE, "r") as f:
        return json.load(f)

def save_msg_map(msg_map):
    with open(MSG_MAP_FILE, "w") as f:
        json.dump(msg_map, f)

msg_map = load_msg_map()  # { str(message_id): uid }

# ---------------- STATE ----------------

waiting_name = set()

# ---------------- UTIL ----------------

def add_message(uid, text, status):
    if uid not in db["messages"]:
        db["messages"][uid] = []
    db["messages"][uid].append({
        "text": text,
        "time": str(datetime.now()),
        "status": status
    })
    save_db()

# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if uid == str(ADMIN_ID):
        await update.message.reply_text("👑 Admin panel active.\n\n/users — list all users")
        return

    if uid in db["users"]:
        await update.message.reply_text(f"Welcome back {db['users'][uid]['name']} 👋\nSend your message:")
        return

    waiting_name.add(uid)
    await update.message.reply_text("👋 Welcome! Please enter your name:")

# ---------------- USER MESSAGE ----------------

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    text = update.message.text

    # ── ADMIN REPLY HANDLER (must be first) ──────────────────────────────
    if user.id == ADMIN_ID:
        msg = update.message

        if not msg.reply_to_message:
            # Not a reply — ignore (admin can type commands freely)
            return

        original_msg_id = str(msg.reply_to_message.message_id)

        # Look up uid from persistent map
        target_uid = msg_map.get(original_msg_id)

        # Fallback: parse from message text
        if not target_uid and msg.reply_to_message.text and "🆔" in msg.reply_to_message.text:
            try:
                target_uid = msg.reply_to_message.text.split("🆔")[1].strip().split()[0]
            except Exception:
                pass

        if not target_uid:
            await update.message.reply_text("⚠️ Could not identify user. Try again.")
            return

        target_name = db["users"].get(target_uid, {}).get("name", "Unknown")

        try:
            await context.bot.send_chat_action(chat_id=int(target_uid), action="typing")
            await asyncio.sleep(0.5)

            await context.bot.send_message(
                chat_id=int(target_uid),
                text=f"📨 *Reply from Admin:*\n\n{text}",
                parse_mode="Markdown"
            )

            add_message(target_uid, f"[ADMIN→USER] {text}", "admin_reply")

            await update.message.reply_text(f"✅ Sent to *{target_name}*", parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send: {e}")
        return
    # ─────────────────────────────────────────────────────────────────────

    # banned check
    if uid in db["users"] and db["users"][uid].get("banned"):
        await update.message.reply_text("🚫 You have been banned.")
        return

    # name setup
    if uid in waiting_name:
        db["users"][uid] = {"name": text, "banned": False}
        waiting_name.remove(uid)
        save_db()
        await update.message.reply_text(f"Hello {text} 👋\nYou can now send your message:")
        return

    if uid not in db["users"]:
        waiting_name.add(uid)
        await update.message.reply_text("Please enter your name first:")
        return

    name = db["users"][uid]["name"]

    keyboard = [
        [
            InlineKeyboardButton("🚫 Ban", callback_data=f"ban:{uid}"),
            InlineKeyboardButton("📋 History", callback_data=f"history:{uid}")
        ]
    ]

    try:
        await context.bot.send_chat_action(chat_id=ADMIN_ID, action="typing")

        sent = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📩 *New Message*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{text}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"👤 *{name}*\n"
                f"🆔 `{uid}`\n\n"
                f"↩️ _Swipe & reply to this message to respond_"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Save mapping (persistent)
        msg_map[str(sent.message_id)] = uid
        save_msg_map(msg_map)

        # Also store in bot_data (in-memory fallback)
        context.bot_data[sent.message_id] = uid

        add_message(uid, text, "sent")

        await update.message.reply_text("✅ Message sent!")

    except Exception as e:
        print("Forward error:", e)
        add_message(uid, text, "failed")
        asyncio.create_task(notify_fail(context, uid))
        await update.message.reply_text("⚠️ Delivery failed. We'll notify you.")

# ---------------- FAIL NOTIFY ----------------

async def notify_fail(context, uid):
    await asyncio.sleep(300)
    try:
        await context.bot.send_message(
            chat_id=int(uid),
            text="❌ Message not delivered.\nContact: aisignalbot@proton.me"
        )
    except Exception:
        pass

# ---------------- BAN / HISTORY BUTTONS ----------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    data = query.data
    parts = data.split(":")
    action = parts[0]
    uid = parts[1]

    if action == "ban":
        if uid not in db["users"]:
            await query.edit_message_text("⚠️ User not found.")
            return
        db["users"][uid]["banned"] = True
        save_db()
        name = db["users"][uid]["name"]
        await query.edit_message_text(f"🚫 *{name}* has been banned.", parse_mode="Markdown")

    elif action == "unban":
        if uid not in db["users"]:
            await query.edit_message_text("⚠️ User not found.")
            return
        db["users"][uid]["banned"] = False
        save_db()
        name = db["users"][uid]["name"]
        await query.edit_message_text(f"✅ *{name}* has been unbanned.", parse_mode="Markdown")

    elif action == "history":
        msgs = db["messages"].get(uid, [])
        if not msgs:
            await query.answer("No messages yet.", show_alert=True)
            return
        # Last 5 messages
        recent = msgs[-5:]
        lines = [f"📜 *Last {len(recent)} messages:*\n"]
        for m in recent:
            t = m["time"][:16]
            lines.append(f"`{t}` — {m['text'][:60]}")
        await query.answer("\n".join(lines)[:200], show_alert=True)

# ---------------- USERS LIST ----------------

async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not db["users"]:
        await update.message.reply_text("No users yet.")
        return

    lines = ["👥 *User List:*\n"]
    for uid, u in db["users"].items():
        status = "🚫" if u.get("banned") else "✅"
        msg_count = len(db["messages"].get(uid, []))
        lines.append(f"{status} *{u['name']}* — `{uid}` — {msg_count} msgs")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ---------------- BROADCAST ----------------

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return

    text = " ".join(context.args)
    sent = 0
    failed = 0

    for uid, u in db["users"].items():
        if u.get("banned"):
            continue
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=f"📢 *Broadcast:*\n\n{text}",
                parse_mode="Markdown"
            )
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(f"📢 Broadcast done.\n✅ Sent: {sent}\n❌ Failed: {failed}")

# ---------------- FLASK (KEEP ALIVE) ----------------

web = Flask(__name__)

@web.route("/")
def home():
    return "Bot Alive 🚀"

def run_web():
    web.run(host="0.0.0.0", port=10000)

# ---------------- MAIN ----------------

def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", users_list))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Single handler for ALL text — admin vs user logic is inside
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user))
    app.add_handler(CallbackQueryHandler(buttons))

    print("✅ Bot running...")
    app.run_polling()

# ---------------- START ----------------

threading.Thread(target=run_web, daemon=True).start()
run_bot()
