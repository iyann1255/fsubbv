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

# --- DB init ---
if not TOKEN_KEY:
    gen = Storage.generate_token_key()
    print("\n[IMPORTANT] TOKEN_KEY kosong. Ini key baru (simpan di .env):")
    print(f"TOKEN_KEY={gen}\n")
    db = Storage(DB_PATH, token_key=gen)
else:
    db = Storage(DB_PATH, token_key=TOKEN_KEY)

# --- child apps runtime ---
child_apps: dict[str, Application] = {}

def _is_dev(user_id: int | None) -> bool:
    return bool(user_id is not None and user_id in DEV_IDS)

def _mask_token(token: str) -> str:
    if len(token) < 15:
        return "***"
    return token[:6] + "..." + token[-6:]

def _ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

def _clone_request_kb(bot_key: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("✅ ACC", callback_data=f"dev_acc:{bot_key}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"dev_reject:{bot_key}"),
        ]
    ]
    if PAYWALL_ON:
        buttons.append([InlineKeyboardButton("💰 Mark Paid", callback_data=f"dev_paid:{bot_key}")])
    buttons.append([
        InlineKeyboardButton("⛔ Suspend", callback_data=f"dev_suspend:{bot_key}"),
        InlineKeyboardButton("▶️ Resume", callback_data=f"dev_resume:{bot_key}")
    ])
    return InlineKeyboardMarkup(buttons)

def _master_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Buat Bot (BotFather)", url="https://t.me/BotFather")],
        [InlineKeyboardButton("📩 Cara Forward Token", callback_data="ui_how_forward")],
        [InlineKeyboardButton("👤 Panel Owner", callback_data="ui_owner_panel")],
        [InlineKeyboardButton("🧩 Fitur & Cara Pakai", callback_data="ui_features")],
    ])

async def _admin_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

# =========================
# MASTER BOT UI
# =========================
async def master_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "FSUB Master (Clone System)\n\n"
        "Alur cepat:\n"
        "1) Owner bikin bot di BotFather\n"
        "2) Forward pesan BotFather yang berisi token ke sini\n"
        "3) Dev ACC → bot kamu langsung aktif\n\n"
        f"Limit bot per owner: {MAX_BOTS_PER_OWNER}\n"
        "Catatan: token itu kunci bot kamu. Jangan share ke siapa pun."
    )
    await update.message.reply_text(text, reply_markup=_master_start_kb(), disable_web_page_preview=True)

async def master_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Menu Master:\n"
        "/start — menu tombol\n"
        "/mybots — panel owner (list status)\n"
        "/features — daftar fitur\n\n"
        "Dev:\n"
        "/bots — list bot terbaru\n"
    )

async def master_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fitur Sistem Clone:\n"
        "• Forward BotFather → Pending → ACC via button\n"
        "• Limit bot per owner\n"
        "• Suspend/Resume tanpa restart server\n\n"
        "Fitur FSUB di bot hasil clone:\n"
        "• Multi-channel wajib join\n"
        "• Tombol '✅ saya sudah join'\n"
        "• Mode delete / warn_only\n"
        "• Bypass admin / custom whitelist\n"
        "• Custom teks per grup\n"
        "• Anti-spam cooldown\n"
    )

async def mybots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    owner_id = update.effective_user.id
    items = db.list_bots_by_owner(owner_id)
    used = db.count_bots_for_owner(owner_id)

    if not items:
        return await update.message.reply_text(
            f"Panel Owner\n\nSlot: {used}/{MAX_BOTS_PER_OWNER}\n"
            "Kamu belum punya bot terdaftar.\n\n"
            "Buat bot di @BotFather lalu forward pesan token ke sini."
        )

    lines = []
    for it in items:
        lines.append(f"• {it['bot_key']} — {it['status']} — created {_ts(it['created_at'])}")
    await update.message.reply_text(
        f"Panel Owner\n\nSlot: {used}/{MAX_BOTS_PER_OWNER}\n\n" + "\n".join(lines) +
        "\n\nKalau status ACTIVE: buka bot kamu → /start"
    )

async def dev_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not _is_dev(update.effective_user.id):
        return await update.message.reply_text("Dev only.")
    rows = db.list_recent_bots(limit=40)
    if not rows:
        return await update.message.reply_text("Belum ada data bot.")
    lines = ["Bot list (latest):"]
    for r in rows:
        lines.append(
            f"- {r['bot_key']} | owner {r['owner_id']} | {r['status']} | created {_ts(r['created_at'])} | approved {_ts(r.get('approved_at'))}"
        )
    await update.message.reply_text("\n".join(lines))

async def master_ui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.effective_user:
        return
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "ui_how_forward":
        return await q.edit_message_text(
            "Cara forward token (aman):\n\n"
            "1) Buka @BotFather\n"
            "2) Buat bot: /newbot\n"
            "3) Setelah BotFather kasih token, *FORWARD* pesan itu ke sini.\n\n"
            "Pastikan pesan yang kamu forward ada token seperti:\n"
            "123456:ABCDEF...\n\n"
            "Setelah itu tinggal tunggu ACC dari dev."
        )

    if data == "ui_owner_panel":
        owner_id = update.effective_user.id
        items = db.list_bots_by_owner(owner_id)
        used = db.count_bots_for_owner(owner_id)

        if not items:
            return await q.edit_message_text(
                "Panel Owner\n\n"
                f"Slot: {used}/{MAX_BOTS_PER_OWNER}\n"
                "Status: belum ada bot terdaftar.\n\n"
                "Buat bot di @BotFather lalu forward token ke sini."
            )

        lines = []
        for it in items:
            lines.append(f"• {it['bot_key']} — {it['status']}")
        return await q.edit_message_text(
            "Panel Owner\n\n"
            f"Slot: {used}/{MAX_BOTS_PER_OWNER}\n\n"
            "Daftar bot kamu:\n" + "\n".join(lines) +
            "\n\nTip: kalau status ACTIVE, buka bot kamu lalu /start."
        )

    if data == "ui_features":
        return await q.edit_message_text(
            "Fitur FSUB di bot hasil clone:\n\n"
            "• Multi-channel wajib join\n"
            "• Tombol '✅ Saya sudah join' (cek ulang tanpa spam)\n"
            "• Mode: delete / warn_only\n"
            "• Bypass: admin / custom whitelist\n"
            "• Pesan FSUB custom per grup\n"
            "• Anti-spam cooldown\n\n"
            "Command di bot hasil clone:\n"
            "/fsub — panel admin\n"
            "/help — panduan\n"
            "/features — daftar fitur"
        )

# =========================
# FORWARD BOTFATHER DETECTOR
# =========================
def _is_forwarded_from_botfather(update: Update) -> bool:
    m = update.effective_message
    if not m:
        return False
    # PTB v21 forward_origin
    try:
        fo = getattr(m, "forward_origin", None)
        if fo and getattr(fo, "sender_user", None):
            return fo.sender_user.id == BOTFATHER_ID
    except Exception:
        pass
    # fallback
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
        return await update.message.reply_text(
            "Forward BotFather kebaca, tapi token-nya nggak ketemu.\n"
            "Forward pesan BotFather yang ada baris token ya."
        )

    token = m.group(1)
    owner_id = update.effective_user.id

    if db.count_bots_for_owner(owner_id) >= MAX_BOTS_PER_OWNER:
        return await update.message.reply_text(
            f"Limit bot kamu sudah mentok ({MAX_BOTS_PER_OWNER}). Hubungi dev kalau mau nambah slot."
        )

    status = "pending_payment" if PAYWALL_ON else "pending"
    bot_key = db.upsert_bot_pending(owner_id, token, status=status)
    db.log("CLONE_REQUEST", f"owner {owner_id} forward token {_mask_token(token)}", actor_id=owner_id, bot_key=bot_key)

    await update.message.reply_text(
        f"Request clone masuk.\nBotKey: {bot_key}\nStatus: {status}\nTunggu ACC dari dev."
    )

    if DEV_IDS:
        msg = (
            "Clone Request\n\n"
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

# =========================
# CHILD BOT FACTORY
# =========================
def build_child_app(token: str, bot_key: str) -> Application:
    app = Application.builder().token(token).build()

    async def child_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧩 Fitur", callback_data="child_ui_features")],
            [InlineKeyboardButton("📌 Cara Setup", callback_data="child_ui_setup")],
        ])
        await update.message.reply_text(
            "FSUBv2 aktif.\n\n"
            "Ketik /fsub untuk panel admin.\n"
            "Ketik /help untuk panduan.",
            reply_markup=kb
        )

    async def child_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Panduan FSUB:\n\n"
            "1) Tambahkan bot ini ke grup (disarankan admin kalau mau delete pesan)\n"
            "2) Set channel wajib:\n"
            "   /fsub add @channelwajib\n"
            "3) Aktifkan:\n"
            "   /fsub on\n\n"
            "Perintah:\n"
            "/fsub — panel admin\n"
            "/features — daftar fitur\n"
        )

    async def child_features(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Fitur FSUB:\n"
            "• Multi-channel wajib join\n"
            "• Tombol cek join\n"
            "• Mode delete / warn_only\n"
            "• Bypass admin / custom\n"
            "• Custom teks per grup\n"
            "• Anti-spam cooldown\n\n"
            "Panel: /fsub"
        )

    async def child_ui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.callback_query:
            return
        q = update.callback_query
        await q.answer()
        if (q.data or "") == "child_ui_features":
            return await q.edit_message_text(
                "Fitur FSUB:\n"
                "• Multi-channel wajib join\n"
                "• Tombol cek join\n"
                "• Mode delete / warn_only\n"
                "• Bypass admin / custom\n"
                "• Custom teks per grup\n"
                "• Anti-spam cooldown\n\n"
                "Panel: /fsub"
            )
        if (q.data or "") == "child_ui_setup":
            return await q.edit_message_text(
                "Cara setup cepat:\n"
                "1) Jadikan bot admin di grup (opsional tapi disarankan)\n"
                "2) /fsub add @channelwajib\n"
                "3) /fsub on\n\n"
                "Selesai."
            )

    async def child_fsub(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

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
                "FSUB Panel:\n"
                f"- enabled: {bool(cfg.get('enabled'))}\n"
                f"- mode: {cfg.get('mode')}\n"
                f"- bypass: {cfg.get('bypass')}\n"
                f"- channels: {', '.join(chans) if chans else '-'}\n"
                f"- custom_text: {'yes' if text else 'no'}\n\n"
                "Command:\n"
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
    app.add_handler(CommandHandler("help", child_help))
    app.add_handler(CommandHandler("features", child_features))
    app.add_handler(CommandHandler("fsub", child_fsub))
    app.add_handler(CallbackQueryHandler(child_ui_callback, pattern="^child_ui_"))
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

# =========================
# DEV CALLBACKS (ACC/REJECT/SUSPEND/RESUME)
# =========================
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
        if PAYWALL_ON and bot["status"] == "pending_payment":
            return await q.edit_message_text(f"{bot_key}: masih pending_payment. Mark Paid dulu.")
        db.approve_bot(bot_key, update.effective_user.id)
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

# =========================
# MAIN
# =========================
async def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN kosong. Isi di .env dulu.")

    master = Application.builder().token(BOT_TOKEN).build()

    master.add_handler(CommandHandler("start", master_start))
    master.add_handler(CommandHandler("help", master_help))
    master.add_handler(CommandHandler("features", master_features))
    master.add_handler(CommandHandler("mybots", mybots))
    master.add_handler(CommandHandler("bots", dev_bots))

    master.add_handler(CallbackQueryHandler(master_ui_callback, pattern="^ui_"))
    master.add_handler(CallbackQueryHandler(dev_callback, pattern="^dev_"))
    master.add_handler(MessageHandler(filters.FORWARDED, on_forward_botfather))

    await master.initialize()
    await master.start()
    await master.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Boot: start all active bots
    actives = db.list_active_bots()
    for b in actives:
        try:
            await start_child_bot(b["bot_key"])
        except Exception as e:
            db.log("BOOT_CHILD_START_FAIL", str(e), bot_key=b["bot_key"])

    print("[OK] Master + active child bots running (polling).")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
