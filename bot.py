import os
import json
import asyncio
import threading
from datetime import datetime
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)

TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID = 6388027054

DATA_FILE    = "data.json"
MSG_MAP_FILE = "msg_map.json"

# ── DB ──────────────────────────────────────────────────

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

db      = load_json(DATA_FILE,    {"users": {}, "messages": {}})
msg_map = load_json(MSG_MAP_FILE, {})

def save_db():      save_json(DATA_FILE,    db)
def save_msg_map(): save_json(MSG_MAP_FILE, msg_map)

def log_msg(uid, text, status):
    db["messages"].setdefault(uid, []).append({
        "text": text, "time": str(datetime.now()), "status": status
    })
    save_db()

# ── STATE ────────────────────────────────────────────────

waiting_name  = set()
WAITING_REPLY = 1
WAITING_BCAST = 2

# ── KEYBOARDS ────────────────────────────────────────────

def msg_kb(uid):
    """Two buttons under every user message."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ Reply", callback_data=f"reply:{uid}"),
        InlineKeyboardButton("☰ Menu",   callback_data=f"menu:{uid}"),
    ]])

def admin_home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users",     callback_data="adm:users"),
         InlineKeyboardButton("🚫 Banned",    callback_data="adm:banned")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm:broadcast")],
    ])

def user_kb(uid):
    banned = db["users"].get(uid, {}).get("banned", False)
    ban_btn = (
        InlineKeyboardButton("✅ Unban", callback_data=f"adm:unban:{uid}")
        if banned else
        InlineKeyboardButton("🚫 Ban",   callback_data=f"adm:ban:{uid}")
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Reply",   callback_data=f"reply:{uid}"),
         ban_btn],
        [InlineKeyboardButton("📋 History", callback_data=f"adm:history:{uid}"),
         InlineKeyboardButton("🔙 Back",    callback_data="adm:users")],
    ])

# ── /start ───────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if update.effective_user.id == ADMIN_ID:
        total  = len(db["users"])
        banned = sum(1 for u in db["users"].values() if u.get("banned"))
        await update.message.reply_text(
            f"👑 *Admin Panel*\n👥 Users: {total}  🚫 Banned: {banned}",
            parse_mode="Markdown",
            reply_markup=admin_home_kb()
        )
        return

    if uid in db["users"]:
        if db["users"][uid].get("banned"):
            await update.message.reply_text("You are banned.")
            return
        await update.message.reply_text("Send your message 👇")
        return

    waiting_name.add(uid)
    await update.message.reply_text("Welcome! What's your name?")

# ── USER MESSAGES ────────────────────────────────────────

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    text = update.message.text

    if user.id == ADMIN_ID:
        return   # admin free text ignored outside ConversationHandler

    if uid in db["users"] and db["users"][uid].get("banned"):
        return   # silent for banned users

    # Name registration
    if uid in waiting_name:
        name = text.strip()
        db["users"][uid] = {"name": name, "banned": False}
        waiting_name.discard(uid)
        save_db()
        await update.message.reply_text(
            f"Nice to meet you, {name}!\n\nSend your message anytime 👇"
        )
        return

    if uid not in db["users"]:
        waiting_name.add(uid)
        await update.message.reply_text("What's your name?")
        return

    name = db["users"][uid]["name"]

    try:
        sent = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"*{name}*\n\n{text}",
            parse_mode="Markdown",
            reply_markup=msg_kb(uid)
        )
        msg_map[str(sent.message_id)] = uid
        save_msg_map()
        log_msg(uid, text, "sent")
        # No confirmation sent to user — keep it clean

    except Exception as e:
        print("Forward error:", e)
        log_msg(uid, text, "failed")

# ── REPLY ConversationHandler ────────────────────────────

async def reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    uid  = query.data.split(":")[1]
    name = db["users"].get(uid, {}).get("name", "User")

    context.user_data["reply_uid"]  = uid
    context.user_data["reply_name"] = name

    await query.message.reply_text(
        f"↩️ *{name}* — type your reply:\n/cancel to abort",
        parse_mode="Markdown"
    )
    return WAITING_REPLY


async def reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    text = update.message.text.strip()
    uid  = context.user_data.get("reply_uid")
    name = context.user_data.get("reply_name", "User")

    if not uid:
        await update.message.reply_text("Session lost. Click ↩️ Reply again.")
        return ConversationHandler.END

    try:
        await context.bot.send_message(chat_id=int(uid), text=text)
        log_msg(uid, f"[→] {text}", "admin_reply")
        await update.message.reply_text(f"✅ Sent to {name}")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def reply_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ── BROADCAST ConversationHandler ────────────────────────

async def bcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    active = sum(1 for u in db["users"].values() if not u.get("banned"))
    await query.message.reply_text(
        f"📢 Broadcast to {active} users — type your message:\n/cancel to abort"
    )
    return WAITING_BCAST


async def bcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    text = update.message.text.strip()
    ok = failed = 0
    status = await update.message.reply_text("Sending...")

    for uid, u in db["users"].items():
        if u.get("banned"):
            continue
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            ok += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status.edit_text(f"📢 Done — ✅ {ok}  ❌ {failed}")
    return ConversationHandler.END


async def bcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ── INLINE BUTTONS ───────────────────────────────────────

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    data = query.data

    # Menu button on a user message → show user profile
    if data.startswith("menu:"):
        uid  = data.split(":")[1]
        u    = db["users"].get(uid, {})
        name = u.get("name", "Unknown")
        msgs = len(db["messages"].get(uid, []))
        flag = "🚫 Banned" if u.get("banned") else "✅ Active"
        await query.message.reply_text(
            f"👤 *{name}*\n{flag} · {msgs} messages\n`{uid}`",
            parse_mode="Markdown",
            reply_markup=user_kb(uid)
        )
        return

    if not data.startswith("adm:"):
        return

    parts = data.split(":")   # ["adm", sub, (uid)?]
    sub   = parts[1]

    if sub == "back":
        total  = len(db["users"])
        banned = sum(1 for u in db["users"].values() if u.get("banned"))
        await query.edit_message_text(
            f"👑 *Admin Panel*\n👥 Users: {total}  🚫 Banned: {banned}",
            parse_mode="Markdown",
            reply_markup=admin_home_kb()
        )

    elif sub == "users":
        active = {uid: u for uid, u in db["users"].items() if not u.get("banned")}
        if not active:
            await query.edit_message_text("No active users yet.", reply_markup=admin_home_kb())
            return
        btns = []
        for uid, u in active.items():
            msgs = len(db["messages"].get(uid, []))
            btns.append([InlineKeyboardButton(
                f"👤 {u['name']}  ({msgs})", callback_data=f"menu:{uid}"
            )])
        btns.append([InlineKeyboardButton("🔙 Back", callback_data="adm:back")])
        await query.edit_message_text(
            f"👥 *Users* ({len(active)})",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns)
        )

    elif sub == "banned":
        banned = {uid: u for uid, u in db["users"].items() if u.get("banned")}
        if not banned:
            await query.edit_message_text("No banned users 🎉", reply_markup=admin_home_kb())
            return
        btns = []
        for uid, u in banned.items():
            btns.append([InlineKeyboardButton(
                f"✅ Unban {u['name']}", callback_data=f"adm:unban:{uid}"
            )])
        btns.append([InlineKeyboardButton("🔙 Back", callback_data="adm:back")])
        await query.edit_message_text(
            f"🚫 *Banned* ({len(banned)})",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns)
        )

    elif sub == "ban" and len(parts) > 2:
        uid  = parts[2]
        db["users"][uid]["banned"] = True
        save_db()
        name = db["users"][uid]["name"]
        msgs = len(db["messages"].get(uid, []))
        await query.edit_message_text(
            f"👤 *{name}*\n🚫 Banned · {msgs} messages\n`{uid}`",
            parse_mode="Markdown",
            reply_markup=user_kb(uid)
        )

    elif sub == "unban" and len(parts) > 2:
        uid  = parts[2]
        db["users"][uid]["banned"] = False
        save_db()
        name = db["users"][uid]["name"]
        msgs = len(db["messages"].get(uid, []))
        await query.edit_message_text(
            f"👤 *{name}*\n✅ Active · {msgs} messages\n`{uid}`",
            parse_mode="Markdown",
            reply_markup=user_kb(uid)
        )

    elif sub == "history" and len(parts) > 2:
        uid  = parts[2]
        msgs = db["messages"].get(uid, [])
        if not msgs:
            await query.answer("No messages yet.", show_alert=True)
            return
        lines = []
        for m in msgs[-6:]:
            t = m["time"][5:16]
            lines.append(f"{t}  {m['text'][:60]}")
        await query.answer("\n".join(lines)[:200], show_alert=True)

# ── FLASK ────────────────────────────────────────────────

web = Flask(__name__)

@web.route("/")
def home():
    return "alive"

def run_web():
    web.run(host="0.0.0.0", port=10000)

# ── MAIN ─────────────────────────────────────────────────

def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reply_start, pattern=r"^reply:")],
        states={WAITING_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reply_send)]},
        fallbacks=[CommandHandler("cancel", reply_cancel)],
        per_user=True, per_chat=True,
    )

    bcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(bcast_start, pattern=r"^adm:broadcast$")],
        states={WAITING_BCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, bcast_send)]},
        fallbacks=[CommandHandler("cancel", bcast_cancel)],
        per_user=True, per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(reply_conv)
    app.add_handler(bcast_conv)
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user))

    print("Bot running...")
    app.run_polling(drop_pending_updates=True)


threading.Thread(target=run_web, daemon=True).start()
run_bot()
