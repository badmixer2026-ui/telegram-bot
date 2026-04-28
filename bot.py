import os
import json
import asyncio
import threading
from datetime import datetime, timedelta
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
MSG_EXPIRE_HOURS = 6  # Messages expire after 6 hours

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

def add_to_cleanup_queue(uid, message_id, chat_id):
    """Track messages for future cleanup"""
    cleanup_data = load_json("cleanup.json", {})
    expire_time = (datetime.now() + timedelta(hours=MSG_EXPIRE_HOURS)).timestamp()
    
    if uid not in cleanup_data:
        cleanup_data[uid] = []
    
    cleanup_data[uid].append({
        "message_id": message_id,
        "chat_id": chat_id,
        "expire_time": expire_time
    })
    
    save_json("cleanup.json", cleanup_data)

async def cleanup_old_messages(bot):
    """Delete expired messages from user chats every 30 minutes"""
    while True:
        try:
            cleanup_data = load_json("cleanup.json", {})
            current_time = datetime.now().timestamp()
            modified = False
            
            for uid in list(cleanup_data.keys()):
                if uid in cleanup_data:
                    expired = []
                    active = []
                    
                    for msg in cleanup_data[uid]:
                        if msg["expire_time"] <= current_time:
                            expired.append(msg)
                        else:
                            active.append(msg)
                    
                    for msg in expired:
                        try:
                            await bot.delete_message(
                                chat_id=msg["chat_id"],
                                message_id=msg["message_id"]
                            )
                            print(f"Deleted message {msg['message_id']} from {uid}")
                        except Exception as e:
                            print(f"Failed to delete message {msg['message_id']}: {e}")
                    
                    if active:
                        cleanup_data[uid] = active
                        modified = True
                    else:
                        del cleanup_data[uid]
                        modified = True
            
            if modified:
                save_json("cleanup.json", cleanup_data)
            
        except Exception as e:
            print(f"Cleanup error: {e}")
        
        await asyncio.sleep(30 * 60)  # Check every 30 minutes

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

# ── SEND ANY MEDIA TO A CHAT ─────────────────────────────

async def forward_media(bot, msg, chat_id, caption=None):
    """
    Forward whatever media type is in msg to chat_id.
    Returns the sent Message object.
    """
    if msg.photo:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=msg.photo[-1].file_id,
            caption=caption
        )
    elif msg.video:
        return await bot.send_video(
            chat_id=chat_id,
            video=msg.video.file_id,
            caption=caption
        )
    elif msg.audio:
        return await bot.send_audio(
            chat_id=chat_id,
            audio=msg.audio.file_id,
            caption=caption
        )
    elif msg.voice:
        return await bot.send_voice(
            chat_id=chat_id,
            voice=msg.voice.file_id,
            caption=caption
        )
    elif msg.document:
        return await bot.send_document(
            chat_id=chat_id,
            document=msg.document.file_id,
            caption=caption
        )
    elif msg.sticker:
        return await bot.send_sticker(
            chat_id=chat_id,
            sticker=msg.sticker.file_id
        )
    elif msg.text:
        return await bot.send_message(
            chat_id=chat_id,
            text=msg.text
        )
    return None

def media_label(msg):
    if msg.photo:    return "📷 Photo"
    if msg.video:    return "🎬 Video"
    if msg.audio:    return "🎵 Audio"
    if msg.voice:    return "🎤 Voice"
    if msg.document: return "📎 File"
    if msg.sticker:  return "🎭 Sticker"
    return "💬 Text"

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

# ── USER → ADMIN (text) ──────────────────────────────────

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    text = update.message.text

    if user.id == ADMIN_ID:
        return

    if uid in db["users"] and db["users"][uid].get("banned"):
        return

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
        
        # Track user's message for cleanup
        add_to_cleanup_queue(uid, update.message.message_id, update.effective_chat.id)

    except Exception as e:
        print("Forward error:", e)
        log_msg(uid, text, "failed")

# ── USER → ADMIN (media) ─────────────────────────────────

async def handle_user_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = str(user.id)
    msg  = update.message

    if user.id == ADMIN_ID:
        return

    if uid in db["users"] and db["users"][uid].get("banned"):
        return

    if uid not in db["users"]:
        waiting_name.add(uid)
        await msg.reply_text("What's your name?")
        return

    name  = db["users"][uid]["name"]
    label = media_label(msg)

    try:
        # Send label header first
        header = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"*{name}* · {label}",
            parse_mode="Markdown",
            reply_markup=msg_kb(uid)
        )
        msg_map[str(header.message_id)] = uid

        # Forward the actual media
        await forward_media(context.bot, msg, ADMIN_ID,
                            caption=msg.caption or None)

        save_msg_map()
        log_msg(uid, label, "sent")
        
        # Track user's media message for cleanup
        add_to_cleanup_queue(uid, msg.message_id, update.effective_chat.id)

    except Exception as e:
        print("Media forward error:", e)

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
        f"↩️ *{name}* — send your reply (text, image, video, audio, file):\n/cancel to abort",
        parse_mode="Markdown"
    )
    return WAITING_REPLY


async def reply_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text reply from admin."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    uid  = context.user_data.get("reply_uid")
    name = context.user_data.get("reply_name", "User")

    if not uid:
        await update.message.reply_text("Session lost. Click ↩️ Reply again.")
        return ConversationHandler.END

    try:
        sent = await context.bot.send_message(chat_id=int(uid), text=update.message.text)
        log_msg(uid, f"[→] {update.message.text}", "admin_reply")
        await update.message.reply_text(f"✅ Sent to {name}")
        
        # Track admin's reply for cleanup in user's chat
        add_to_cleanup_queue(uid, sent.message_id, int(uid))
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def reply_send_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle media reply from admin."""
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    uid  = context.user_data.get("reply_uid")
    name = context.user_data.get("reply_name", "User")

    if not uid:
        await update.message.reply_text("Session lost. Click ↩️ Reply again.")
        return ConversationHandler.END

    msg   = update.message
    label = media_label(msg)

    try:
        sent = await forward_media(context.bot, msg, int(uid),
                            caption=msg.caption or None)
        log_msg(uid, f"[→] {label}", "admin_reply")
        await update.message.reply_text(f"✅ {label} sent to {name}")
        
        # Track admin's media reply for cleanup in user's chat
        if sent:
            add_to_cleanup_queue(uid, sent.message_id, int(uid))
        
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
        f"📢 Broadcast to {active} users — send message (text or media):\n/cancel to abort"
    )
    return WAITING_BCAST


async def bcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    msg = update.message
    ok = failed = 0
    status = await msg.reply_text("Sending...")

    for uid, u in db["users"].items():
        if u.get("banned"):
            continue
        try:
            if msg.text:
                sent = await context.bot.send_message(chat_id=int(uid), text=msg.text)
            else:
                sent = await forward_media(context.bot, msg, int(uid),
                                    caption=msg.caption or None)
            ok += 1
            
            # Track broadcast message for cleanup in user's chat
            if sent:
                add_to_cleanup_queue(uid, sent.message_id, int(uid))
                
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

    parts = data.split(":")
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

async def post_init(app):
    """Start background cleanup task"""
    asyncio.create_task(cleanup_old_messages(app.bot))

def run_bot():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    # Media filter for all non-text content
    MEDIA_FILTER = (
        filters.PHOTO | filters.VIDEO | filters.AUDIO |
        filters.VOICE | filters.Document.ALL | filters.Sticker.ALL
    )

    # Reply conversation — accepts BOTH text and media
    reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reply_start, pattern=r"^reply:")],
        states={
            WAITING_REPLY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reply_send),
                MessageHandler(MEDIA_FILTER,                     reply_send_media),
            ]
        },
        fallbacks=[CommandHandler("cancel", reply_cancel)],
        per_user=True, per_chat=True,
    )

    # Broadcast conversation — accepts BOTH text and media
    bcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(bcast_start, pattern=r"^adm:broadcast$")],
        states={
            WAITING_BCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bcast_send),
                MessageHandler(MEDIA_FILTER,                     bcast_send),
            ]
        },
        fallbacks=[CommandHandler("cancel", bcast_cancel)],
        per_user=True, per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(reply_conv)
    app.add_handler(bcast_conv)
    app.add_handler(CallbackQueryHandler(buttons))

    # User text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user))
    # User media messages (forwarded to admin with Reply + Menu buttons)
    app.add_handler(MessageHandler(MEDIA_FILTER, handle_user_media))

    print("Bot running with auto-delete (6 hour expiry)...")
    app.run_polling(drop_pending_updates=True)


threading.Thread(target=run_web, daemon=True).start()
run_bot()
