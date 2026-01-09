import os

def env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    return v if v is not None and v != "" else default
from dotenv import load_dotenv
load_dotenv()

# MASTER bot token (bot utama yang menerima forward dari BotFather)
BOT_TOKEN = env("BOT_TOKEN", "")

DB_PATH = env("DB_PATH", "fsub.sqlite3")

# Dev yang bisa ACC/REJECT (pisahkan dengan koma)
# contoh: DEV_IDS=12345,67890
DEV_IDS = set()
_raw = env("DEV_IDS", "")
if _raw:
    for x in _raw.split(","):
        x = x.strip()
        if x.isdigit():
            DEV_IDS.add(int(x))

# Max bot clone per owner
MAX_BOTS_PER_OWNER = int(env("MAX_BOTS_PER_OWNER", "3"))

# Token encryption key (Fernet). Kalau kosong, app akan generate dan print di console
TOKEN_KEY = env("TOKEN_KEY", "")

# Optional paywall toggle (placeholder workflow)
PAYWALL_ON = env("PAYWALL_ON", "0") == "1"

# Default FSUB behavior (per-group bisa diubah)
DEFAULT_FSUB_TEXT = env(
    "DEFAULT_FSUB_TEXT",
    "Kamu belum join channel wajib.\n\nJoin dulu, baru bisa chat di sini."
)

BTN_JOIN_TEXT = env("BTN_JOIN_TEXT", "Join")
BTN_CHECK_TEXT = env("BTN_CHECK_TEXT", "✅ Saya sudah join")

WARN_COOLDOWN_SEC = int(env("WARN_COOLDOWN_SEC", "12"))

DEFAULT_MODE = env("DEFAULT_MODE", "delete")         # delete | warn_only
DEFAULT_BYPASS = env("DEFAULT_BYPASS", "admin")      # admin | custom
