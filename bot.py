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
    ContextTypes, ConversationHandler, filters
)

TOKEN    = os.getenv("BOT_TOKEN")
ADMIN_ID = 6388027054

DATA_FILE    = "data.json"
MSG_MAP_FILE = "msg_map.json"

# ═══════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

db      = load_json(DATA_FILE,    {"users": {}, "messages": {}})
msg_map = load_json(MSG_MAP_FILE, {})   # { str(msg_id): uid }

def save_db():      save_json(DATA_FILE,    db)
def save_msg_map(): save_json(MSG_MAP_FILE, msg_map)

def add_message(uid, text, status):
    db["messages"].setdefault(uid, []).append({
        "text":   text,
        "time":   str(datetime.now()),
        "status": status
    })
    save_db()

# ═══════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════

waiting_name    = set()
WAITING_REPLY   = 1
WAITING_BCAST   = 2

# ═══════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users",     callback_data="menu:users"),
         InlineKeyboardButton("🚫 Banned",    callback_data="menu:banned")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="menu:broadcast")],
    ])

def message_kb(uid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Reply",   callback_data=f"reply:{uid}"),
         InlineKeyboardButton("🚫 Ban",     callback_data=f"ban:{uid}")],
        [InlineKeyboardButton("📋 History", callback_data=f"history:{uid}")],
    ])

def user_manage_kb(uid):
    banned  = db["users"].get(uid, {}).get("banned", False)
    ban_btn = (
        InlineKeyboardButton("✅ Unban", callback_data=f"unban:{uid}")
        if banned else
        InlineKeyboardButton("🚫 Ban",   callback_data=f"ban:{uid}")
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Reply",   callback_data=f"reply:{uid}"),
         ban_btn],
        [InlineKeyboardButton("📋 History", callback_data=f"history:{uid}"),
         InlineKeyboardButton("🔙 Back",    callback_data="menu:users")],
    ])

# ═══════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if update.effective_user.id == ADMIN_ID:
        total  = len(db["users"])
        banned = sum(1 for u in db["users"].values() if u.get("banned"))
        await update.message.reply_text(
            f"👑 *Admin Panel*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👥 Total users : {total}\n"
            f"🚫 Banned      : {banned}\n"
            f"━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
            reply_markup=admin_menu_kb()
        )
        return

    if uid in db["users"]:
        if db["users"][uid].get("banned"):
            await update.message.reply_text("🚫 You have been banned.")
            return
        await update.message.reply_text(
            f"👋 Welcome back, *{db['users'][uid]['name']}*!\nSend your message below 👇",
            parse_mode="Markdown"
        )
        return

    waiting_name.add(uid)
    await update.message.reply_text(
        "👋 Welcome!\n\nPlease enter your *name* to get started:",
        parse_mode="Markdown"
    )

# ═══════════════════════════════════════════
#  USER → ADMIN (forward messages)
# ═══════════════════════════════════════════

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    text = update.message.text

    # Admin free text — show menu
    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "Use /start to open the admin panel,\nor click ↩️ Reply on a user message.",
            reply_markup=admin_menu_kb()
        )
        return

    # Banned
    if uid in db["users"] and db["users"][uid].get("banned"):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    # Name registration
    if uid in waiting_name:
        name = text.strip()
        db["users"][uid] = {"name": name, "banned": False}
        waiting_name.discard(uid)
        save_db()
        await update.message.reply_text(
            f"✅ Hello, *{name}*!\nYou can now send messages. We'll reply as soon as possible 🙏",
            parse_mode="Markdown"
        )
        return

    if uid not in db["users"]:
        waiting_name.add(uid)
        await update.message.reply_text("Please enter your *name* first:", parse_mode="Markdown")
        return

    name = db["users"][uid]["name"]

    try:
        sent = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📩 *New Message*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💬  {text}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤  *{name}*\n"
                f"🆔  `{uid}`"
            ),
            parse_mode="Markdown",
            reply_markup=message_kb(uid)
        )
        msg_map[str(sent.message_id)] = uid
        save_msg_map()
        add_message(uid, text, "sent")
        await update.message.reply_text("✅ Message delivered!")

    except Exception as e:
        print("Forward error:", e)
        add_message(uid, text, "failed")
        await update.message.reply_text("⚠️ Delivery failed. Please try again later.")

# ═══════════════════════════════════════════
#  ADMIN REPLY — ConversationHandler
# ═══════════════════════════════════════════

async def reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    uid  = query.data.split(":")[1]
    name = db["users"].get(uid, {}).get("name", "Unknown")

    context.user_data["reply_uid"]  = uid
    context.user_data["reply_name"] = name

    await query.message.reply_text(
        f"↩️ *Replying to {name}*\n\n"
        f"Type your reply below 👇\n"
        f"_/cancel to cancel_",
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
        await update.message.reply_text("⚠️ Session lost. Click ↩️ Reply again.")
        return ConversationHandler.END

    try:
        await context.bot.send_chat_action(chat_id=int(uid), action="typing")
        await asyncio.sleep(0.5)

        await context.bot.send_message(
            chat_id=int(uid),
            text=f"📨 *Reply from Admin:*\n\n{text}",
            parse_mode="Markdown"
        )
        add_message(uid, f"[ADMIN→USER] {text}", "admin_reply")
        await update.message.reply_text(
            f"✅ Reply sent to *{name}*!",
            parse_mode="Markdown",
            reply_markup=admin_menu_kb()
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Failed:\n`{e}`", parse_mode="Markdown")

    context.user_data.clear()
    return ConversationHandler.END


async def reply_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Reply cancelled.", reply_markup=admin_menu_kb())
    return ConversationHandler.END

# ═══════════════════════════════════════════
#  BROADCAST — ConversationHandler
# ═══════════════════════════════════════════

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    active = sum(1 for u in db["users"].values() if not u.get("banned"))
    await query.message.reply_text(
        f"📢 *Broadcast*\n\n"
        f"Will be sent to *{active}* active users.\n\n"
        f"Type your message 👇\n_/cancel to cancel_",
        parse_mode="Markdown"
    )
    return WAITING_BCAST


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    text   = update.message.text.strip()
    ok     = 0
    failed = 0

    status = await update.message.reply_text("📤 Sending...")

    for uid, u in db["users"].items():
        if u.get("banned"):
            continue
        try:
            await context.bot.send_message(
                chat_id=int(uid),
                text=f"📢 *Broadcast*\n━━━━━━━━━━━━━━━\n{text}",
                parse_mode="Markdown"
            )
            ok += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"📢 *Broadcast Complete*\n\n✅ Sent: {ok}\n❌ Failed: {failed}",
        parse_mode="Markdown",
        reply_markup=admin_menu_kb()
    )
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Broadcast cancelled.", reply_markup=admin_menu_kb())
    return ConversationHandler.END

# ═══════════════════════════════════════════
#  INLINE BUTTONS
# ═══════════════════════════════════════════

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    parts  = query.data.split(":")
    action = parts[0]
    uid    = parts[1] if len(parts) > 1 else None

    # ── Menu ─────────────────────────────────

    if action == "menu":
        if uid == "users":
            await _show_users(query)
        elif uid == "banned":
            await _show_banned(query)
        elif uid == "back":
            total  = len(db["users"])
            banned = sum(1 for u in db["users"].values() if u.get("banned"))
            await query.edit_message_text(
                f"👑 *Admin Panel*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"👥 Total users : {total}\n"
                f"🚫 Banned      : {banned}\n"
                f"━━━━━━━━━━━━━━━",
                parse_mode="Markdown",
                reply_markup=admin_menu_kb()
            )
        return

    # ── User actions ─────────────────────────

    if not uid:
        return

    if action == "ban":
        if uid not in db["users"]:
            await query.answer("User not found.", show_alert=True)
            return
        db["users"][uid]["banned"] = True
        save_db()
        name = db["users"][uid]["name"]
        try:
            await query.edit_message_reply_markup(reply_markup=message_kb(uid))
        except Exception:
            pass
        await query.answer(f"🚫 {name} banned.", show_alert=True)

    elif action == "unban":
        if uid not in db["users"]:
            await query.answer("User not found.", show_alert=True)
            return
        db["users"][uid]["banned"] = False
        save_db()
        name = db["users"][uid]["name"]
        # Refresh the manage panel
        u    = db["users"][uid]
        msgs = len(db["messages"].get(uid, []))
        try:
            await query.edit_message_text(
                f"👤 *User Profile*\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Name   : {name}\n"
                f"ID     : `{uid}`\n"
                f"Status : ✅ Active\n"
                f"Msgs   : {msgs}",
                parse_mode="Markdown",
                reply_markup=user_manage_kb(uid)
            )
        except Exception:
            pass
        await query.answer(f"✅ {name} unbanned.", show_alert=True)

    elif action == "history":
        msgs = db["messages"].get(uid, [])
        if not msgs:
            await query.answer("No messages yet.", show_alert=True)
            return
        recent = msgs[-5:]
        lines  = []
        for m in recent:
            t    = m["time"][:16]
            snip = m["text"][:70]
            lines.append(f"{t}\n{snip}")
        await query.answer("\n\n".join(lines)[:200], show_alert=True)

    elif action == "manage":
        if uid not in db["users"]:
            await query.answer("User not found.", show_alert=True)
            return
        u    = db["users"][uid]
        name = u["name"]
        msgs = len(db["messages"].get(uid, []))
        status_str = "🚫 Banned" if u.get("banned") else "✅ Active"
        await query.edit_message_text(
            f"👤 *User Profile*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Name   : {name}\n"
            f"ID     : `{uid}`\n"
            f"Status : {status_str}\n"
            f"Msgs   : {msgs}",
            parse_mode="Markdown",
            reply_markup=user_manage_kb(uid)
        )


async def _show_users(query):
    active = {uid: u for uid, u in db["users"].items() if not u.get("banned")}
    if not active:
        await query.edit_message_text("No active users yet.", reply_markup=admin_menu_kb())
        return

    lines = [f"👥 *Active Users* ({len(active)})\n━━━━━━━━━━━━━━━"]
    btns  = []
    for uid, u in active.items():
        msgs = len(db["messages"].get(uid, []))
        lines.append(f"• *{u['name']}* — {msgs} msg(s)")
        btns.append([InlineKeyboardButton(
            f"👤 {u['name']}  ({msgs} msgs)", callback_data=f"manage:{uid}"
        )])

    btns.append([InlineKeyboardButton("🔙 Back", callback_data="menu:back")])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )


async def _show_banned(query):
    banned = {uid: u for uid, u in db["users"].items() if u.get("banned")}
    if not banned:
        await query.edit_message_text("No banned users 🎉", reply_markup=admin_menu_kb())
        return

    lines = [f"🚫 *Banned Users* ({len(banned)})\n━━━━━━━━━━━━━━━"]
    btns  = []
    for uid, u in banned.items():
        lines.append(f"• {u['name']} — `{uid}`")
        btns.append([InlineKeyboardButton(
            f"✅ Unban {u['name']}", callback_data=f"unban:{uid}"
        )])

    btns.append([InlineKeyboardButton("🔙 Back", callback_data="menu:back")])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )

# ═══════════════════════════════════════════
#  FLASK KEEP-ALIVE
# ═══════════════════════════════════════════

web = Flask(__name__)

@web.route("/")
def home():
    return "Bot Alive 🚀"

def run_web():
    web.run(host="0.0.0.0", port=10000)

# ═══════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════

def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()

    # Reply conversation
    reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reply_start, pattern=r"^reply:")],
        states={
            WAITING_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reply_send)
            ]
        },
        fallbacks=[CommandHandler("cancel", reply_cancel)],
        per_user=True,
        per_chat=True,
    )

    # Broadcast conversation
    bcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_start, pattern=r"^menu:broadcast$")],
        states={
            WAITING_BCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)
            ]
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
        per_user=True,
        per_chat=True,
    )

    # Order matters — ConversationHandlers first
    app.add_handler(CommandHandler("start", start))
    app.add_handler(reply_conv)
    app.add_handler(bcast_conv)
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user))

    print("✅ Bot running...")
    app.run_polling(drop_pending_updates=True)


threading.Thread(target=run_web, daemon=True).start()
run_bot()
