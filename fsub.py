import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.constants import ChatMemberStatus

from config import DEFAULT_FSUB_TEXT, BTN_JOIN_TEXT, BTN_CHECK_TEXT, WARN_COOLDOWN_SEC
from links import channel_link, normalize_channel
from storage import Storage

_last_warn = {}

def _cooldown_ok(chat_id: int, user_id: int) -> bool:
    now = time.time()
    key = (chat_id, user_id)
    last = _last_warn.get(key, 0)
    if now - last >= WARN_COOLDOWN_SEC:
        _last_warn[key] = now
        return True
    return False

async def is_admin_or_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_chat or not update.effective_user:
        return False
    try:
        cm = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return cm.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False

async def is_joined_all(context: ContextTypes.DEFAULT_TYPE, user_id: int, required: list[str]) -> tuple[bool, list[str]]:
    missing = []
    for ch in required:
        uname = normalize_channel(ch)
        try:
            member = await context.bot.get_chat_member(f"@{uname}", user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                missing.append(uname)
        except Exception:
            missing.append(uname)
    return (len(missing) == 0), missing

def build_fsub_keyboard(required: list[str], chat_id: int) -> InlineKeyboardMarkup:
    rows = []
    for ch in required[:10]:
        uname = normalize_channel(ch)
        rows.append([InlineKeyboardButton(f"{BTN_JOIN_TEXT} @{uname}", url=channel_link(uname))])
    rows.append([InlineKeyboardButton(BTN_CHECK_TEXT, callback_data=f"fsub_check:{chat_id}")])
    return InlineKeyboardMarkup(rows)

async def gate_message(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Storage, bot_key: str) -> None:
    if not update.effective_chat or not update.effective_user or not update.message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    cfg = db.get_group(bot_key, chat_id)
    if int(cfg.get("enabled", 0)) != 1:
        return

    required = db.list_channels(bot_key, chat_id)
    if not required:
        return

    bypass = (cfg.get("bypass") or "admin").lower()
    if bypass == "admin":
        if await is_admin_or_owner(update, context):
            return
    elif bypass == "custom":
        if db.is_bypass_user(bot_key, chat_id, user_id):
            return

    ok, missing = await is_joined_all(context, user_id, required)
    if ok:
        return

    mode = (cfg.get("mode") or "delete").lower()
    text = cfg.get("text") or DEFAULT_FSUB_TEXT

    if mode == "delete":
        try:
            await update.message.delete()
        except Exception:
            pass

    if not _cooldown_ok(chat_id, user_id):
        return

    kb = build_fsub_keyboard(required, chat_id)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{text}\n\nYang belum kamu join:\n" + "\n".join([f"• @{m}" for m in missing]),
            reply_markup=kb,
            disable_web_page_preview=True
        )
    except Exception:
        pass

async def on_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db: Storage, bot_key: str) -> None:
    if not update.callback_query or not update.effective_user:
        return
    q = update.callback_query
    await q.answer()

    data = q.data or ""
    try:
        chat_id = int(data.split(":", 1)[1])
    except Exception:
        return

    required = db.list_channels(bot_key, chat_id)
    if not required:
        await q.edit_message_text("FSUB belum diset channel wajibnya.")
        return

    ok, missing = await is_joined_all(context, update.effective_user.id, required)
    if ok:
        try:
            await q.edit_message_text("Sip. Kamu sudah join semua. Sekarang bebas chat.")
        except Exception:
            pass
    else:
        kb = build_fsub_keyboard(required, chat_id)
        try:
            await q.edit_message_text(
                "Masih ada yang belum kamu join:\n" + "\n".join([f"• @{m}" for m in missing]),
                reply_markup=kb,
                disable_web_page_preview=True
            )
        except Exception:
            pass
