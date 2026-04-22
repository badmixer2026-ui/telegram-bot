import os
import json
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6388027054  # 🔴 replace with your Telegram ID

DATA_FILE = "users.json"

# ------------------ DATABASE ------------------
def load_users():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_users(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

users = load_users()

# ------------------ STATES ------------------
waiting_name = set()

# ------------------ START ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if user_id in users:
        await update.message.reply_text(f"Welcome back {users[user_id]['name']} 👋")
        return

    waiting_name.add(user_id)
    await update.message.reply_text("Enter your name:")

# ------------------ HANDLE MESSAGES ------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    text = update.message.text

    # banned check
    if user_id in users and users[user_id].get("banned"):
        return

    # name input
    if user_id in waiting_name:
        users[user_id] = {
            "name": text,
            "banned": False
        }
        save_users(users)
        waiting_name.remove(user_id)

        await update.message.reply_text(f"Hello {text} 👋\nNow send your message:")
        return

    # normal message → forward to admin
    if user_id in users:
        name = users[user_id]["name"]

        msg = f"👤 {name}\n🆔 {user_id}\n\n💬 {text}"

        await context.bot.send_message(chat_id=ADMIN_ID, text=msg)
        await update.message.reply_text("Message sent ✅")

# ------------------ ADMIN COMMANDS ------------------

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply user_id message")
        return

    user_id = context.args[0]
    message = " ".join(context.args[1:])

    await context.bot.send_message(chat_id=user_id, text=message)

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    user_id = context.args[0]

    if user_id in users:
        users[user_id]["banned"] = True
        save_users(users)
        await update.message.reply_text("User banned 🚫")

async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = "Users:\n"
    for uid, data in users.items():
        text += f"{data['name']} → {uid}\n"

    await update.message.reply_text(text)

async def message_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    user_id = context.args[0]
    message = " ".join(context.args[1:])

    await context.bot.send_message(chat_id=user_id, text=message)

# ------------------ FLASK SERVER ------------------

app_web = Flask(__name__)

@app_web.route("/")
def home():
    return "Bot running 🚀"

def run_web():
    app_web.run(host="0.0.0.0", port=10000)

# ------------------ MAIN ------------------

def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("users", users_list))
    app.add_handler(CommandHandler("msg", message_user))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

# ------------------ THREAD RUN ------------------

threading.Thread(target=run_web).start()
run_bot()
