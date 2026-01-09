import asyncio
import re
import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from config import (
    BOT_TOKEN, DB_PATH, DEV_IDS, MAX_BOTS_PER_OWNER,
    TOKEN_KEY, PAYWALL_ON, DEFAULT_MODE, DEFAULT_BYPASS
)
from storage import Storage
from fsub import gate_message, on_check_callback

BOTFATHER_ID = 93372553
TOKEN_RE = re.compile(r"\b(\d{5,}:[A-Za-z0-9_-]{30,})\b")

# ---------- DB ----------
if not TOKEN_KEY:
    gen = Storage.generate_token_key()
    print("\n[IMPORTANT] TOKEN_KEY kosong. Ini key baru (simpan di .env):")
    print(f"TOKEN_KEY={gen}\n")
    db = Storage(DB_PATH, token_key=gen)
else:
    db = Storage(DB_PATH, token_key=TOKEN_KEY)

# ---------- child apps runtime ----------
child_apps: dict[str, Application] = {}   # bot_key -> Application

def _is_dev(user_id: int | None) -> bool:
    return bool(user_id is not None and user_id in DEV_IDS)

def _mask_token(token: str) -> str:
    if len(token) < 15:
        return "***"
    return token[:6] + "..." + token[-6:]

def _clone_request_kb(bot_key: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("✅ ACC", callback_data=f"dev_acc:{bot_key}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"dev_reject:{bot_key}"),
        ]
    ]
    if PAYWALL_ON:
        buttons.append([InlineKeyboardButton("💰 Mark Paid", callback_data=f"dev_paid:{bot_key}")])
    buttons.append([InlineKeyboardButton("⛔ Suspend", callback_data=f"dev_suspend:{bot_key}"),
                    InlineKeyboardButton("▶️ Resume", callback_data=f"dev_resume:{bot_key}")])
    return InlineKeyboardMarkup(buttons)

async def _admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

# ---------- build child bot app ----------
def build_child_app(token: str, bot_key: str) -> Application:
    app = Application.builder().token(token).build()

    async def child_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "FSUBv2 aktif.\n\nPakai di grup:\n"
            "/fsub on\n"
            "/fsub add @channelwajib\n"
            "/fsub list\n"
        )

    async def child_fsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        # command panel in group only
        if not (update.effective_chat and update.effective_chat.type in ("group", "supergroup")):
            return await update.message.reply_text("Pakai command ini di grup ya.")

        if not await _admin_only(update, context):
            return await update.message.reply_text("Admin aja yang boleh ngatur FSUB.")

        chat_id = update.effective_chat.id
        db.ensure_group(bot_key, chat_id)

        args = context.args or []
        if not args:
            cfg = db.get_group(bot_key, chat_id)
            chans = db.list_channels(bot_key, chat_id)
            text = cfg.get("text")
            await update.message.reply_text(
                "FSUB Status:\n"
                f"- enabled: {bool(cfg.get('enabled'))}\n"
                f"- mode: {cfg.get('mode')}\n"
                f"- bypass: {cfg.get('bypass')}\n"
                f"- channels: {', '.join(chans) if chans else '-'}\n"
                f"- custom_text: {'yes' if text else 'no'}\n\n"
                "Panel:\n"
                "/fsub on|off\n"
                "/fsub add @channel\n"
                "/fsub del @channel\n"
                "/fsub list\n"
                "/fsub mode delete|warn_only\n"
                "/fsub bypass admin|custom\n"
                "/fsub text <pesan>\n"
                "/fsub resettext\n"
                "/fsub bypassadd <user_id>\n"
                "/fsub bypassdel <user_id>\n"
                "/fsub bypasslist\n"
            )
            return

        sub = args[0].lower()

        if sub in ("on", "enable"):
            db.set_enabled(bot_key, chat_id, True)
            cfg = db.get_group(bot_key, chat_id)
            if not cfg.get("mode"):
                db.set_mode(bot_key, chat_id, DEFAULT_MODE)
            if not cfg.get("bypass"):
                db.set_bypass(bot_key, chat_id, DEFAULT_BYPASS)
            return await update.message.reply_text("FSUB: ON. Yang belum join bakal ketahan.")

        if sub in ("off", "disable"):
            db.set_enabled(bot_key, chat_id, False)
            return await update.message.reply_text("FSUB: OFF.")

        if sub == "add" and len(args) >= 2:
            ch = args[1]
            db.add_channel(bot_key, chat_id, ch)
            return await update.message.reply_text(f"Ditambahin: {ch}")

        if sub == "del" and len(args) >= 2:
            ch = args[1]
            db.del_channel(bot_key, chat_id, ch)
            return await update.message.reply_text(f"Dihapus: {ch}")

        if sub == "list":
            chans = db.list_channels(bot_key, chat_id)
            return await update.message.reply_text("Channel wajib:\n" + ("\n".join([f"• {c}" for c in chans]) if chans else "-"))

        if sub == "mode" and len(args) >= 2:
            mode = args[1].lower()
            if mode not in ("delete", "warn_only"):
                return await update.message.reply_text("Mode valid: delete | warn_only")
            db.set_mode(bot_key, chat_id, mode)
            return await update.message.reply_text(f"Mode diset: {mode}")

        if sub == "bypass" and len(args) >= 2:
            bp = args[1].lower()
            if bp not in ("admin", "custom"):
                return await update.message.reply_text("Bypass valid: admin | custom")
            db.set_bypass(bot_key, chat_id, bp)
            return await update.message.reply_text(f"Bypass diset: {bp}")

        if sub == "text" and len(args) >= 2:
            text = update.message.text.split(None, 2)[2]
            db.set_text(bot_key, chat_id, text)
            return await update.message.reply_text("Oke, pesan FSUB custom diset.")

        if sub == "resettext":
            db.set_text(bot_key, chat_id, None)
            return await update.message.reply_text("Pesan FSUB balik default.")

        if sub == "bypassadd" and len(args) >= 2:
            try:
                uid = int(args[1])
            except Exception:
                return await update.message.reply_text("Format: /fsub bypassadd <user_id>")
            db.add_bypass_user(bot_key, chat_id, uid)
            return await update.message.reply_text(f"Bypass user ditambah: {uid}")

        if sub == "bypassdel" and len(args) >= 2:
            try:
                uid = int(args[1])
            except Exception:
                return await update.message.reply_text("Format: /fsub bypassdel <user_id>")
            db.del_bypass_user(bot_key, chat_id, uid)
            return await update.message.reply_text(f"Bypass user dihapus: {uid}")

        if sub == "bypasslist":
            users = db.list_bypass_users(bot_key, chat_id)
            return await update.message.reply_text("Bypass users:\n" + ("\n".join([str(u) for u in users]) if users else "-"))

        return await update.message.reply_text("Subcommand nggak dikenal. Ketik /fsub buat panel.")

    async def child_on_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await gate_message(update, context, db, bot_key)

    async def child_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query and (update.callback_query.data or "").startswith("fsub_check:"):
            await on_check_callback(update, context, db, bot_key)

    app.add_handler(CommandHandler("start", child_start))
    app.add_handler(CommandHandler("fsub", child_fsub))
    app.add_handler(CallbackQueryHandler(child_cb))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, child_on_msg))
    return app

async def start_child_bot(bot_key: str):
    if bot_key in child_apps:
        return

    token = db.get_token_for_bot(bot_key)
    app = build_child_app(token, bot_key)
    child_apps[bot_key] = app

    await app.initialize()
    await app.start()
    # polling
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    db.log("CHILD_STARTED", "polling started", bot_key=bot_key)

async def stop_child_bot(bot_key: str):
    app = child_apps.get(bot_key)
    if not app:
        return
    try:
        await app.updater.stop()
    except Exception:
        pass
    try:
        await app.stop()
        await app.shutdown()
    except Exception:
        pass
    child_apps.pop(bot_key, None)
    db.log("CHILD_STOPPED", "stopped", bot_key=bot_key)

# ---------- MASTER handlers ----------
async def master_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "FSUB Master aktif.\n\n"
        "Owner flow:\n"
        "1) Buat bot di BotFather\n"
        "2) Forward pesan BotFather (yang berisi token) ke sini\n"
        "3) Tunggu ACC dari dev\n\n"
        "Owner command:\n"
        "/mybots - cek status bot kamu\n\n"
        "Dev command:\n"
        "/bots - list bot aktif/pending\n"
    )

async def mybots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    items = db.list_bots_by_owner(update.effective_user.id)
    if not items:
        return await update.message.reply_text("Belum ada bot kamu yang terdaftar. Buat bot di BotFather lalu forward token ke sini.")
    lines = []
    for it in items:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(it["created_at"]))
        lines.append(f"- {it['bot_key']} | {it['status']} | created {ts}")
    await update.message.reply_text("Bot kamu:\n" + "\n".join(lines))

async def dev_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not _is_dev(update.effective_user.id):
        return await update.message.reply_text("Dev only.")
    # Show active and pending
    actives = db.list_active_bots()
    msg = ["Active bots:"]
    msg += [f"- {b['bot_key']} | owner {b['owner_id']} | active" for b in actives] or ["- (none)"]
    await update.message.reply_text("\n".join(msg))

def _is_forwarded_from_botfather(update: Update) -> bool:
    m = update.effective_message
    if not m:
        return False
    # PTB v21 uses forward_origin
    try:
        fo = getattr(m, "forward_origin", None)
        if fo and getattr(fo, "sender_user", None):
            return fo.sender_user.id == BOTFATHER_ID
    except Exception:
        pass
    # fallback (older)
    try:
        f = getattr(m, "forward_from", None)
        if f and getattr(f, "id", None):
            return f.id == BOTFATHER_ID
    except Exception:
        pass
    return False

async def on_forward_botfather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if not _is_forwarded_from_botfather(update):
        return

    text = update.message.text or update.message.caption or ""
    m = TOKEN_RE.search(text)
    if not m:
        return await update.message.reply_text("Forward BotFather kebaca, tapi token-nya nggak ketemu. Pastikan yang kamu forward itu pesan yang berisi token.")

    token = m.group(1)
    owner_id = update.effective_user.id

    # limit per owner
    if db.count_bots_for_owner(owner_id) >= MAX_BOTS_PER_OWNER:
        return await update.message.reply_text(f"Limit bot kamu sudah mentok ({MAX_BOTS_PER_OWNER}). Hubungi dev kalau mau nambah slot.")

    status = "pending_payment" if PAYWALL_ON else "pending"
    bot_key = db.upsert_bot_pending(owner_id, token, status=status)
    db.log("CLONE_REQUEST", f"owner {owner_id} forward token {_mask_token(token)}", actor_id=owner_id, bot_key=bot_key)

    await update.message.reply_text(
        f"Request clone masuk.\nBotKey: {bot_key}\nStatus: {status}\nTunggu ACC dari dev."
    )

    # notify dev(s)
    if DEV_IDS:
        msg = (
            "📦 Clone Request\n\n"
            f"BotKey: {bot_key}\n"
            f"Owner: {owner_id}\n"
            f"Token: {_mask_token(token)}\n"
            f"Status: {status}"
        )
        for dev_id in DEV_IDS:
            try:
                await context.bot.send_message(dev_id, msg, reply_markup=_clone_request_kb(bot_key))
            except Exception:
                pass

async def dev_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.effective_user:
        return
    q = update.callback_query
    await q.answer()

    if not _is_dev(update.effective_user.id):
        return

    data = q.data or ""
    if ":" not in data:
        return
    action, bot_key = data.split(":", 1)

    bot = db.get_bot(bot_key)
    if not bot:
        return await q.edit_message_text("Bot tidak ditemukan di DB.")

    if action == "dev_paid":
        db.set_bot_status(bot_key, "pending", actor_id=update.effective_user.id)
        return await q.edit_message_text(f"{bot_key}: marked paid -> pending. Sekarang bisa ACC.")

    if action == "dev_acc":
        # if paywall on, only acc if not pending_payment
        if PAYWALL_ON and bot["status"] == "pending_payment":
            return await q.edit_message_text(f"{bot_key}: masih pending_payment. Mark Paid dulu.")
        db.approve_bot(bot_key, update.effective_user.id)
        # start child bot immediately
        try:
            await start_child_bot(bot_key)
        except Exception as e:
            db.log("CHILD_START_FAIL", str(e), actor_id=update.effective_user.id, bot_key=bot_key)
            return await q.edit_message_text(f"{bot_key}: approved, tapi start gagal: {e}")
        return await q.edit_message_text(f"{bot_key}: APPROVED + STARTED.")

    if action == "dev_reject":
        db.reject_bot(bot_key, update.effective_user.id)
        await stop_child_bot(bot_key)
        return await q.edit_message_text(f"{bot_key}: REJECTED.")

    if action == "dev_suspend":
        db.set_bot_status(bot_key, "suspended", actor_id=update.effective_user.id)
        await stop_child_bot(bot_key)
        return await q.edit_message_text(f"{bot_key}: SUSPENDED + STOPPED.")

    if action == "dev_resume":
        db.set_bot_status(bot_key, "active", actor_id=update.effective_user.id)
        try:
            await start_child_bot(bot_key)
        except Exception as e:
            return await q.edit_message_text(f"{bot_key}: resume gagal: {e}")
        return await q.edit_message_text(f"{bot_key}: RESUMED + STARTED.")

# ---------- main runner ----------
async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN kosong. Isi di .env dulu.")

    master = Application.builder().token(BOT_TOKEN).build()

    master.add_handler(CommandHandler("start", master_start))
    master.add_handler(CommandHandler("mybots", mybots))
    master.add_handler(CommandHandler("bots", dev_bots))
    master.add_handler(CallbackQueryHandler(dev_callback))
    # Only listen forwarded messages (private chat usually)
    master.add_handler(MessageHandler(filters.FORWARDED, on_forward_botfather))

    # start master
    await master.initialize()
    await master.start()
    await master.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # start all active bots on boot
    actives = db.list_active_bots()
    for b in actives:
        try:
            await start_child_bot(b["bot_key"])
        except Exception as e:
            db.log("BOOT_CHILD_START_FAIL", str(e), bot_key=b["bot_key"])

    print("[OK] Master + active child bots running (polling).")
    # idle forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
