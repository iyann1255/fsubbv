import sqlite3
import time
import hashlib
from contextlib import contextmanager
from typing import Optional

from cryptography.fernet import Fernet

class Storage:
    def __init__(self, path: str, token_key: str | None = None):
        self.path = path
        self._fernet = None
        if token_key:
            try:
                self._fernet = Fernet(token_key.encode())
            except Exception:
                self._fernet = None
        self._init()

    @staticmethod
    def generate_token_key() -> str:
        return Fernet.generate_key().decode()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self):
        with self._conn() as c:
            cur = c.cursor()

            # ----- clone bots registry -----
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bots(
                bot_key TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                token_enc TEXT NOT NULL,
                status TEXT NOT NULL,          -- pending | pending_payment | active | suspended | rejected
                created_at INTEGER NOT NULL,
                approved_by INTEGER,
                approved_at INTEGER
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                bot_key TEXT,
                actor_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT
            )
            """)

            # ----- FSUB settings per bot_key -----
            cur.execute("""
            CREATE TABLE IF NOT EXISTS groups_v2(
                bot_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'delete',
                bypass TEXT NOT NULL DEFAULT 'admin',
                text TEXT,
                PRIMARY KEY(bot_key, chat_id)
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS required_channels_v2(
                bot_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                PRIMARY KEY(bot_key, chat_id, channel)
            )
            """)

            cur.execute("""
            CREATE TABLE IF NOT EXISTS bypass_users_v2(
                bot_key TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY(bot_key, chat_id, user_id)
            )
            """)

    # ---------------- encryption helpers ----------------
    def _enc(self, token: str) -> str:
        if self._fernet:
            return self._fernet.encrypt(token.encode()).decode()
        # fallback (not secure) - still better than plain in logs
        return "plain:" + token

    def _dec(self, token_enc: str) -> str:
        if token_enc.startswith("plain:"):
            return token_enc.split("plain:", 1)[1]
        if self._fernet:
            return self._fernet.decrypt(token_enc.encode()).decode()
        # if no fernet but not plain, cannot decrypt
        raise ValueError("TOKEN_KEY missing/invalid, cannot decrypt stored token.")

    @staticmethod
    def make_bot_key(token: str) -> str:
        # stable id that doesn't reveal token
        return hashlib.sha256(token.encode()).hexdigest()[:16]

    # ---------------- logs ----------------
    def log(self, action: str, detail: str = "", actor_id: int | None = None, bot_key: str | None = None):
        with self._conn() as c:
            c.execute(
                "INSERT INTO bot_logs(ts, bot_key, actor_id, action, detail) VALUES(?,?,?,?,?)",
                (int(time.time()), bot_key, actor_id, action, detail[:800])
            )

    # ---------------- bots registry ----------------
    def count_bots_for_owner(self, owner_id: int) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM bots WHERE owner_id=? AND status IN ('pending','pending_payment','active','suspended')",
                (owner_id,)
            ).fetchone()
            return int(row["n"]) if row else 0

    def upsert_bot_pending(self, owner_id: int, token: str, status: str = "pending") -> str:
        bot_key = self.make_bot_key(token)
        token_enc = self._enc(token)
        now = int(time.time())
        with self._conn() as c:
            c.execute("""
            INSERT INTO bots(bot_key, owner_id, token_enc, status, created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(bot_key) DO UPDATE SET
              owner_id=excluded.owner_id,
              token_enc=excluded.token_enc,
              status=excluded.status
            """, (bot_key, owner_id, token_enc, status, now))
        return bot_key

    def set_bot_status(self, bot_key: str, status: str, actor_id: int | None = None):
        with self._conn() as c:
            c.execute("UPDATE bots SET status=? WHERE bot_key=?", (status, bot_key))
        self.log("SET_STATUS", f"{bot_key} -> {status}", actor_id=actor_id, bot_key=bot_key)

    def approve_bot(self, bot_key: str, approved_by: int):
        with self._conn() as c:
            c.execute(
                "UPDATE bots SET status='active', approved_by=?, approved_at=? WHERE bot_key=?",
                (approved_by, int(time.time()), bot_key)
            )
        self.log("APPROVE", "approved", actor_id=approved_by, bot_key=bot_key)

    def reject_bot(self, bot_key: str, rejected_by: int):
        with self._conn() as c:
            c.execute("UPDATE bots SET status='rejected' WHERE bot_key=?", (bot_key,))
        self.log("REJECT", "rejected", actor_id=rejected_by, bot_key=bot_key)

    def get_bot(self, bot_key: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM bots WHERE bot_key=?", (bot_key,)).fetchone()
            return dict(row) if row else None

    def list_bots_by_owner(self, owner_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT bot_key, status, created_at, approved_at FROM bots WHERE owner_id=? ORDER BY created_at DESC",
                (owner_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_active_bots(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM bots WHERE status='active'").fetchall()
            return [dict(r) for r in rows]

    def get_token_for_bot(self, bot_key: str) -> str:
        with self._conn() as c:
            row = c.execute("SELECT token_enc FROM bots WHERE bot_key=?", (bot_key,)).fetchone()
            if not row:
                raise KeyError("bot not found")
            return self._dec(row["token_enc"])

    # ---------------- FSUB per-bot methods ----------------
    def ensure_group(self, bot_key: str, chat_id: int):
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO groups_v2(bot_key, chat_id) VALUES(?,?)",
                (bot_key, chat_id)
            )

    def set_enabled(self, bot_key: str, chat_id: int, enabled: bool):
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            c.execute(
                "UPDATE groups_v2 SET enabled=? WHERE bot_key=? AND chat_id=?",
                (1 if enabled else 0, bot_key, chat_id)
            )

    def get_group(self, bot_key: str, chat_id: int) -> dict:
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM groups_v2 WHERE bot_key=? AND chat_id=?",
                (bot_key, chat_id)
            ).fetchone()
            return dict(row) if row else {"bot_key": bot_key, "chat_id": chat_id, "enabled": 0, "mode": "delete", "bypass": "admin", "text": None}

    def set_mode(self, bot_key: str, chat_id: int, mode: str):
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            c.execute(
                "UPDATE groups_v2 SET mode=? WHERE bot_key=? AND chat_id=?",
                (mode, bot_key, chat_id)
            )

    def set_bypass(self, bot_key: str, chat_id: int, bypass: str):
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            c.execute(
                "UPDATE groups_v2 SET bypass=? WHERE bot_key=? AND chat_id=?",
                (bypass, bot_key, chat_id)
            )

    def set_text(self, bot_key: str, chat_id: int, text: str | None):
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            c.execute(
                "UPDATE groups_v2 SET text=? WHERE bot_key=? AND chat_id=?",
                (text, bot_key, chat_id)
            )

    def add_channel(self, bot_key: str, chat_id: int, channel: str):
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO required_channels_v2(bot_key, chat_id, channel) VALUES(?,?,?)",
                (bot_key, chat_id, channel)
            )

    def del_channel(self, bot_key: str, chat_id: int, channel: str):
        with self._conn() as c:
            c.execute(
                "DELETE FROM required_channels_v2 WHERE bot_key=? AND chat_id=? AND channel=?",
                (bot_key, chat_id, channel)
            )

    def list_channels(self, bot_key: str, chat_id: int) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT channel FROM required_channels_v2 WHERE bot_key=? AND chat_id=? ORDER BY channel",
                (bot_key, chat_id)
            ).fetchall()
            return [r["channel"] for r in rows]

    def add_bypass_user(self, bot_key: str, chat_id: int, user_id: int):
        self.ensure_group(bot_key, chat_id)
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO bypass_users_v2(bot_key, chat_id, user_id) VALUES(?,?,?)",
                (bot_key, chat_id, user_id)
            )

    def del_bypass_user(self, bot_key: str, chat_id: int, user_id: int):
        with self._conn() as c:
            c.execute(
                "DELETE FROM bypass_users_v2 WHERE bot_key=? AND chat_id=? AND user_id=?",
                (bot_key, chat_id, user_id)
            )

    def is_bypass_user(self, bot_key: str, chat_id: int, user_id: int) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM bypass_users_v2 WHERE bot_key=? AND chat_id=? AND user_id=? LIMIT 1",
                (bot_key, chat_id, user_id)
            ).fetchone()
            return row is not None

    def list_bypass_users(self, bot_key: str, chat_id: int) -> list[int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT user_id FROM bypass_users_v2 WHERE bot_key=? AND chat_id=? ORDER BY user_id",
                (bot_key, chat_id)
            ).fetchall()
            return [int(r["user_id"]) for r in rows]
