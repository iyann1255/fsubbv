import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (same folder as this file)
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

def env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    return v if v is not None and v != "" else default

BOT_TOKEN = env("BOT_TOKEN", "")
DB_PATH = env("DB_PATH", "fsub.sqlite3")

# Dev IDs: comma separated
DEV_IDS = set()
_raw = env("DEV_IDS", "")
if _raw:
    for x in _raw.split(","):
        x = x.strip()
        if x.isdigit():
            DEV_IDS.add(int(x))

MAX_BOTS_PER_OWNER = int(env("MAX_BOTS_PER_OWNER", "3"))

# Token encryption key (Fernet base64 key)
TOKEN_KEY = env("TOKEN_KEY", "")

PAYWALL_ON = env("PAYWALL_ON", "0") == "1"

DEFAULT_FSUB_TEXT = env(
    "DEFAULT_FSUB_TEXT",
    "Kamu belum join channel wajib.\n\nJoin dulu, baru bisa chat di sini."
)

BTN_JOIN_TEXT = env("BTN_JOIN_TEXT", "Join")
BTN_CHECK_TEXT = env("BTN_CHECK_TEXT", "✅ Saya sudah join")

WARN_COOLDOWN_SEC = int(env("WARN_COOLDOWN_SEC", "12"))

DEFAULT_MODE = env("DEFAULT_MODE", "delete")     # delete | warn_only
DEFAULT_BYPASS = env("DEFAULT_BYPASS", "admin")  # admin | custom
