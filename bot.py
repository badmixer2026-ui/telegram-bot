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
ADMIN_ID = 6388027054  # 🔴 PUT YOUR TELEGRAM ID

DATA_FILE = "data.json"

# ---------------- DATABASE ----------------

def load_db():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "messages": {}}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_db():
    with open(DATA_FILE, "w") as f:
        json.dump(db, f)

db = load_db()

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

    if uid in db["users"]:
        await update.message.reply_text(f"Welcome back {db['users'][uid]['name']} 👋")
        return

    waiting_name.add(uid)
    await update.message.reply_text("Enter your name:")

# ---------------- USER MESSAGE ----------------

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    text = update.message.text

    # banned check
    if uid in db["users"] and db["users"][uid].get("banned"):
        return

    # name setup
    if uid in waiting_name:
        db["users"][uid] = {"name": text, "banned": False}
        waiting_name.remove(uid)
        save_db()

        await update.message.reply_text(f"Hello {text} 👋\nSend your message:")
        return

    # typing indicator (admin side)
    await context.bot.send_chat_action(chat_id=ADMIN_ID, action="typing")

    name = db["users"][uid]["name"]

    keyboard = [
        [InlineKeyboardButton("🚫 Ban", callback_data=f"ban:{uid}")]
    ]

    try:
        sent = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"💬 {text}\n\n👤 {name}\n🆔 {uid}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # map message → user
        context.bot_data[sent.message_id] = uid

        add_message(uid, text, "sent")

    except Exception:
        add_message(uid, text, "failed")
        asyncio.create_task(notify_fail(context, uid))

# ---------------- FAIL NOTIFY ----------------

async def notify_fail(context, uid):
    await asyncio.sleep(300)

    try:
        await context.bot.send_message(
            chat_id=int(uid),
            text="❌ Message not delivered.\nContact: aisignalbot@proton.me"
        )
    except:
        pass

# ---------------- ADMIN REPLY (FIXED) ----------------

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    msg = update.message

    # must reply to a message
    if not msg.reply_to_message:
        return

    original = msg.reply_to_message

    # get mapped user id
    uid = context.bot_data.get(original.message_id)

    # fallback (if mapping lost)
    if not uid and "🆔" in original.text:
        try:
            uid = original.text.split("🆔")[1].strip().split("\n")[0]
        except:
            return

    if uid:
        try:
            await context.bot.send_chat_action(chat_id=int(uid), action="typing")

            await context.bot.send_message(
                chat_id=int(uid),
                text=msg.text
            )

            add_message(uid, msg.text, "admin_reply")

        except Exception as e:
            print("Reply error:", e)

# ---------------- BAN BUTTON ----------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    data = query.data
    uid = data.split(":")[1]

    if data.startswith("ban"):
        db["users"][uid]["banned"] = True
        save_db()
        await query.edit_message_text("🚫 User banned")

# ---------------- USERS LIST ----------------

async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = "Users:\n"
    for uid, u in db["users"].items():
        text += f"{u['name']} → {uid}\n"

    await update.message.reply_text(text)

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

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user))
    app.add_handler(MessageHandler(filters.REPLY, admin_reply))
    app.add_handler(CallbackQueryHandler(buttons))

    app.run_polling()

# ---------------- START ----------------

threading.Thread(target=run_web).start()
run_bot()
