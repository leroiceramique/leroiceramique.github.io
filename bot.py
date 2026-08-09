import os
import re
import json
import datetime
import logging
import asyncio
import unicodedata
import aiohttp
from aiohttp import web
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════
#  EXTRACTION PERMISSION & REQUEST FUNCTIONS
# ═══════════════════════════════════════════════════════════

def can_extract(telegram_id):
    """Check if user has extraction permission."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT can_extract FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return bool(row and row["can_extract"])
    except Exception as e:
        conn.rollback()
        logging.error("[DB] can_extract check failed: " + str(e))
        return False
    finally:
        release_connection(conn)


def set_extract_permission(telegram_id, allowed):
    """Grant or revoke extraction permission."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET can_extract = %s WHERE telegram_id = %s RETURNING id",
                (allowed, telegram_id),
            )
            updated = cur.fetchone() is not None
        conn.commit()
        return updated
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def create_extraction_request(product_id, quantity, requester_id):
    """Create a new extraction request (status: pending)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO extraction_requests (product_id, quantity, requester_id, status) "
                "VALUES (%s, %s, %s, 'pending') RETURNING *",
                (product_id, quantity, requester_id),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_extraction_requests():
    """Get all pending extraction requests."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT er.*, p.name AS product_name, u.username, u.telegram_id AS requester_tid "
                "FROM extraction_requests er "
                "JOIN products p ON p.id = er.product_id "
                "JOIN users u ON u.id = er.requester_id "
                "WHERE er.status = 'pending' "
                "ORDER BY er.created_at DESC"
            )
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def process_extraction_request(request_id, approved, admin_id):
    """Approve or reject an extraction request. Returns (success, message)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT er.*, p.name AS product_name, p.quantity AS current_qty "
                "FROM extraction_requests er "
                "JOIN products p ON p.id = er.product_id "
                "WHERE er.id = %s AND er.status = 'pending' FOR UPDATE",
                (request_id,),
            )
            req = cur.fetchone()
            if not req:
                return False, "الطلب غير موجود أو تم معالجته."
            req = dict(req)
            if approved:
                if req["current_qty"] < req["quantity"]:
                    return False, (
                        "الكمية المطلوبة ("
                        + str(req["quantity"]) + ") أكبر من المتوفر ("
                        + str(req["current_qty"]) + ")."
                    )
                cur.execute(
                    "UPDATE products SET quantity = %s WHERE id = %s",
                    (req["current_qty"] - req["quantity"], req["product_id"]),
                )
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id) VALUES (%s, 'remove', %s, %s)",
                    (req["product_id"], req["quantity"], admin_id),
                )
            cur.execute(
                "UPDATE extraction_requests SET status = %s, processed_at = NOW() WHERE id = %s",
                ("approved" if approved else "rejected", request_id),
            )
        conn.commit()
        pname = req["product_name"]
        qty = req["quantity"]
        if approved:
            return True, "✅ تم الموافقة على إخراج " + str(qty) + " من " + pname
        else:
            return True, "❌ تم رفض طلب الإخراج."
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
RENDER_EXTERNAL_URL: str = os.getenv("RENDER_EXTERNAL_URL", "")  # auto-set by Render

# Email backup (optional — for daily auto-backup via email)
SMTP_HOST = os.getenv("SMTP_HOST", "")         # e.g. smtp.gmail.com
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")       # sender email
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")   # app password
BACKUP_EMAIL = os.getenv("BACKUP_EMAIL", "")     # recipient email
IMAP_HOST = os.getenv("IMAP_HOST", "")  # e.g. imap.gmail.com

_last_backup_data = None  # keeps latest backup dict in memory


_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list = [
    int(uid.strip())
    for uid in _raw_admin_ids.split(",")
    if uid.strip().isdigit()
]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set.")

_previously_low_stock = {}  # {product_id: product_dict}

def _init_low_stock_ids():
    """Populate _previously_low_stock with current low-stock product IDs on startup.
    This prevents sending "new" alerts for products already low before restart."""
    global _previously_low_stock
    try:
        low_stock = get_low_stock_products()
        _previously_low_stock = {p["id"]: p for p in low_stock}
        logging.info("[INIT] Low-stock tracking initialized with " + str(len(_previously_low_stock)) + " products")
    except Exception as e:
        logging.warning("[INIT] Failed to init low-stock IDs: " + str(e))

# ═══════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════

_pool = None


def init_pool():
    global _pool
    _pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1, maxconn=5, dsn=DATABASE_URL,
    )
    return _pool


def get_connection():
    global _pool
    if _pool is None:
        init_pool()
    conn = None
    for attempt in range(2):
        try:
            conn = _pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception:
            # Connection is dead — close it properly
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    # All attempts failed — recreate the pool
    logging.warning("[DB] All pool connections dead, recreating pool...")
    try:
        _pool.closeall()
    except Exception:
        pass
    _pool = None
    init_pool()
    return _pool.getconn()


def release_connection(conn):
    if conn is None:
        return
    if conn.closed:
        return
    if _pool:
        try:
            _pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass


SCHEMA_SQL = """\nCREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username    VARCHAR(255),
    first_name  VARCHAR(255),
    role        VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (role IN ('admin', 'customer', 'pending')),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    can_extract   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 0,
    location       VARCHAR(255) DEFAULT '',
    minimum_stock  INTEGER NOT NULL DEFAULT 5,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id         SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    type       VARCHAR(10) NOT NULL CHECK (type IN ('add', 'remove')),
    quantity   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    date       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_name ON products (name);
CREATE INDEX IF NOT EXISTS idx_transactions_product ON transactions (product_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);

CREATE TABLE IF NOT EXISTS extraction_requests (
    id            SERIAL PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity      INTEGER NOT NULL,
    requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extraction_requests_status ON extraction_requests (status);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

CREATE TABLE IF NOT EXISTS incoming_orders (
    id             SERIAL PRIMARY KEY,
    requester_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    product_name   VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 1,
    status         VARCHAR(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'received', 'completed')),
    photo_file_id  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_incoming_orders_status ON incoming_orders (status);

CREATE TABLE IF NOT EXISTS bot_settings (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT
);
"""


def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            # Migration: add 'pending' role for existing databases
            cur.execute("""\n                DO $$ BEGIN
                    ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE users ADD CONSTRAINT users_role_check
                    CHECK (role IN ('admin', 'customer', 'pending'));
                ALTER TABLE users ALTER COLUMN role SET DEFAULT 'pending';
                -- Migration: add can_extract column
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS can_extract BOOLEAN NOT NULL DEFAULT FALSE;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: add first_name column for auto-update display names
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(255);
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: update incoming_orders status to include 'received'
                DO $$ BEGIN
                    ALTER TABLE incoming_orders DROP CONSTRAINT IF EXISTS incoming_orders_status_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE incoming_orders ADD CONSTRAINT incoming_orders_status_check
                    CHECK (status IN ('pending', 'received', 'completed'));
                -- Migration: add photo_file_id column for receipt photos
                DO $$ BEGIN
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: create extraction_requests table
                CREATE TABLE IF NOT EXISTS extraction_requests (
                    id            SERIAL PRIMARY KEY,
                    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    quantity      INTEGER NOT NULL,
                    requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
                    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    processed_at  TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_extraction_requests_status ON extraction_requests (status);
            -- Migration: create bot_settings table
            CREATE TABLE IF NOT EXISTS bot_settings (
                key         VARCHAR(100) PRIMARY KEY,
                value       TEXT
            );
            """)
        conn.commit()
        print("[DB] Schema initialized successfully.")
    except Exception as e:
        conn.rollback()
        print("[DB] Schema init failed: " + str(e))
        raise
    finally:
        release_connection(conn)


def seed_sample_products():
    """Insert sample products if they don't exist."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            samples = [
                ("plante pcs", 0, "", 5),
                ("120/60pcs", 0, "", 5),
            ]
            for name, qty, loc, min_stk in samples:
                cur.execute(
                    "INSERT INTO products (name, quantity, location, minimum_stock)"
                    " VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT DO NOTHING",
                    (name, qty, loc, min_stk),
                )
        conn.commit()
        print("[DB] Sample products seeded.")
    except Exception as e:
        conn.rollback()
        print("[DB] Sample seed failed: " + str(e))
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  USER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def register_user(telegram_id, username=None, first_name=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            role = "admin" if telegram_id in ADMIN_IDS else "pending"
            cur.execute("""\n                INSERT INTO users (telegram_id, username, first_name, role)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    active = TRUE
                -- NEVER downgrade role on re-register
                -- (admin stays admin, approved customer stays customer)
                RETURNING id, telegram_id, username, first_name, role, active, created_at
            """, (telegram_id, username, first_name, role))
            user = cur.fetchone()
        conn.commit()
        return dict(user)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def sync_user_info(user):
    """تحديث اسم المستخدم تلقائياً عند أي تفاعل.
    يُنشئ السجل إذا لم يكن موجود، أو يُحدّث username و first_name إذا تغيرت."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""\n                INSERT INTO users (telegram_id, username, first_name, role)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    active = TRUE
                """, (user.id, user.username, user.first_name, "admin" if user.id in ADMIN_IDS else "pending"))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def get_user(telegram_id):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def is_admin(telegram_id):
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    return user is not None and user["role"] == "admin"


# ═══════════════════════════════════════════════════════════


def is_approved(telegram_id):
    """Check if user is approved (admin or customer, not pending)."""
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    return user is not None and user["role"] in ("admin", "customer")


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS (طلبات التوريد)
# ═══════════════════════════════════════════════════════════

def create_incoming_order(requester_id, product_name, quantity, photo_file_id=None):
    """إنشاء طلب توريد جديد من صاحب المحل."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO incoming_orders (requester_id, product_name, quantity, photo_file_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, requester_id, product_name, quantity, status, created_at, photo_file_id
                """, (requester_id, product_name, quantity, photo_file_id))
            order = cur.fetchone()
        conn.commit()
        return dict(order)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_incoming_orders():
    """جلب كل طلبات التوريد المعلّقة."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT io.*, u.first_name, u.username, u.telegram_id
                FROM incoming_orders io
                LEFT JOIN users u ON u.id = io.requester_id
                WHERE io.status = 'pending'
                ORDER BY io.created_at ASC
                """)
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_user_pending_orders(user_db_id):
    """جلب طلبات التوريد المعلّقة + قيد التجهيز لمستخدم محدد."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT * FROM incoming_orders
                WHERE requester_id = %s AND status IN ('pending', 'received')
                ORDER BY created_at ASC
                """, (user_db_id,))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def complete_incoming_order(order_id):
    """تحديث طلب توريد إلى 'مكتمل' (من pending أو received)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'completed', completed_at = NOW()
                WHERE id = %s AND status IN ('pending', 'received')
                RETURNING *
                """, (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def receive_incoming_order(order_id):
    """تحديث طلب توريد إلى 'تم الاستلام' (جاري التجهيز)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'received'
                WHERE id = %s AND status = 'pending'
                RETURNING *
                """, (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def complete_all_pending_orders():
    """تحديث كل طلبات التوريد المعلّقة إلى 'مكتمل'."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'completed', completed_at = NOW()
                WHERE status = 'pending'
                RETURNING *
                """)
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_users():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE role = 'pending' ORDER BY created_at DESC")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def approve_user(telegram_id):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE users SET role = 'customer' WHERE telegram_id = %s AND role = 'pending' RETURNING *",
                (telegram_id,),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def reject_user(telegram_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE telegram_id = %s AND role = 'pending'", (telegram_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_export_backup():
    """Export all data as a dict (products + transactions + users)."""
    import datetime as _dt
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products ORDER BY id")
            products = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM transactions ORDER BY id")
            transactions = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, telegram_id, username, role, active, created_at FROM users ORDER BY id")
            users = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return {
            "version": 2,
            "date": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "products": products,
            "transactions": transactions,
            "users": users,
        }
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_import_backup(data):
    """Restore data from a backup dict. Returns summary string."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transactions")
            cur.execute("DELETE FROM products")
            cur.execute("DELETE FROM users")

            # ── Import users with their ORIGINAL ids ──
            user_count = 0
            for u in data.get("users", []):
                cur.execute(
                    "INSERT INTO users (id, telegram_id, username, role, active, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (u["id"], u["telegram_id"], u.get("username"), u.get("role", "customer"),
                     u.get("active", True), u.get("created_at")))
                user_count += 1
            if user_count > 0:
                max_uid = max(u["id"] for u in data.get("users", []))
                cur.execute("SELECT setval('users_id_seq', %s)", (max_uid,))

            # ── Import products with their ORIGINAL ids ──
            prod_count = 0
            for p in data.get("products", []):
                cur.execute(
                    "INSERT INTO products (id, name, quantity, location, minimum_stock, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    (p["id"], p["name"], p["quantity"], p.get("location", ""),
                     p.get("minimum_stock", 5), p.get("created_at")))
                prod_count += 1
            if prod_count > 0:
                max_pid = max(p["id"] for p in data.get("products", []))
                cur.execute("SELECT setval('products_id_seq', %s)", (max_pid,))

            # ── Import transactions (users & products already exist with correct ids) ──
            tx_count = 0
            for t in data.get("transactions", []):
                cur.execute(
                    "INSERT INTO transactions (id, product_id, type, quantity, user_id, date) VALUES (%s, %s, %s, %s, %s, %s)",
                    (t["id"], t["product_id"], t["type"], t["quantity"], t.get("user_id"), t.get("date")))
                tx_count += 1
            if tx_count > 0:
                max_tid = max(t["id"] for t in data.get("transactions", []))
                cur.execute("SELECT setval('transactions_id_seq', %s)", (max_tid,))

        conn.commit()
        return ("✅ تم استيراد البيانات:\n"
            "  المستخدمين: " + str(user_count) + "\n"
            "  السلع: " + str(prod_count) + "\n"
            "  العمليات: " + str(tx_count))
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

# ═══════════════════════════════════════════════════════════
#  PRODUCT FUNCTIONS
# ═══════════════════════════════════════════════════════════

def db_add_product(name, quantity=0, location="", minimum_stock=5):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO products (name, quantity, location, minimum_stock)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """, (name.strip(), quantity, location.strip(), minimum_stock))
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _normalize_text(text):
    """Normalize text for fuzzy matching: strip accents, lowercase, remove extra spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    # Remove diacritical marks (accents)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    # Replace multiple spaces with single
    text = re.sub(r"\s+", " ", text)
    # Normalize slashes: 60/60 -> 60x60
    text = text.replace("/", "x")
    text = text.replace("×", "x")
    return text


def _levenshtein_distance(s1, s2):
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _fuzzy_score(name, query_tokens):
    """Score how well a product name matches the query tokens.
    Returns a score (higher = better match)."""
    norm_name = _normalize_text(name)
    name_words = set(norm_name.split())
    score = 0
    # Exact substring match in normalized name (strongest signal)
    norm_query = " ".join(query_tokens)
    if norm_query in norm_name:
        score += 100
    # Each token that appears exactly as a word
    for token in query_tokens:
        if token in name_words:
            score += 30
        elif token in norm_name:
            score += 15
        else:
            # Fuzzy: check Levenshtein distance for each word in name
            for word in name_words:
                dist = _levenshtein_distance(token, word)
                max_len = max(len(token), len(word), 1)
                similarity = 1 - (dist / max_len)
                if similarity >= 0.6:
                    score += int(similarity * 20)
                    break
    return score


def search_products(query):
    """Smart search: fuzzy matching with typo tolerance, normalized text,
    slash/x interchangeability, and accent handling."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch all products (fuzzy matching needs full scan)
            cur.execute("SELECT * FROM products ORDER BY id")
            all_rows = cur.fetchall()
        conn.commit()
        all_products = [dict(r) for r in all_rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    if not query or not query.strip():
        return []
    query_tokens = _normalize_text(query).split()
    if not query_tokens:
        return []
    # Score every product
    scored = []
    for p in all_products:
        s = _fuzzy_score(p["name"], query_tokens)
        if s > 0:
            scored.append((s, p))
    # Sort by score descending, then by id ascending
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    # Determine the best score to filter low-quality matches
    if scored:
        best_score = scored[0][0]
        # Keep results that are at least 50% of the best score (minimum 15)
        threshold = max(15, best_score * 0.5)
        scored = [(s, p) for s, p in scored if s >= threshold]
    # If the query is very short (1-2 chars), only return very good matches
    if len(query.strip()) <= 2:
        scored = [(s, p) for s, p in scored if s >= 30]
    # Limit to top 50 results max
    scored = scored[:50]
    return [p for s, p in scored]


def db_update_product(product_id, name=None, location=None, minimum_stock=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            fields = []
            values = []
            if name is not None:
                fields.append("name = %s")
                values.append(name.strip())
            if location is not None:
                fields.append("location = %s")
                values.append(location.strip())
            if minimum_stock is not None:
                fields.append("minimum_stock = %s")
                values.append(minimum_stock)
            if not fields:
                raise ValueError("No fields to update")
            values.append(product_id)
            query_sql = "UPDATE products SET " + ", ".join(fields) + " WHERE id = %s RETURNING id, name, quantity, location, minimum_stock, created_at"
            cur.execute(query_sql, values)
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_update_quantity(product_id, change, transaction_type):
    if transaction_type not in ("add", "remove"):
        raise ValueError("transaction_type must be 'add' or 'remove'")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if transaction_type == "remove" and change < 0:
                change = abs(change)
            # Always fetch old quantity
            cur.execute("SELECT quantity FROM products WHERE id = %s", (product_id,))
            old_row = cur.fetchone()
            old_qty = old_row["quantity"] if old_row else 0
            if transaction_type == "remove":
                if old_row and old_qty < change:
                    raise ValueError(
                        "العدد المطلوب ("
                        + str(change)
                        + ") أكبر من المتوفر ("
                        + str(old_qty)
                        + ")"
                    )
                new_qty_expr = "GREATEST(quantity - %s, 0)"
            else:
                new_qty_expr = "quantity + %s"
            cur.execute(
                "UPDATE products SET quantity = "
                + new_qty_expr
                + " WHERE id = %s RETURNING id, name, quantity, location, minimum_stock, created_at",
                (change, product_id),
            )
            product = cur.fetchone()
        product = dict(product)
        product["_old_qty"] = old_qty
        conn.commit()
        return product
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_delete_product(product_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE id = %s RETURNING id", (product_id,))
            deleted = cur.fetchone() is not None
        conn.commit()
        return deleted
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_low_stock_products():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE quantity <= minimum_stock ORDER BY quantity ASC")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_all_products():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products ORDER BY LOWER(name)")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  TRANSACTION FUNCTIONS
# ═══════════════════════════════════════════════════════════

def log_transaction(product_id, transaction_type, quantity, user_id):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO transactions (product_id, type, quantity, user_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, product_id, type, quantity, user_id, date
            """, (product_id, transaction_type, quantity, user_id))
            tx = cur.fetchone()
        conn.commit()
        return dict(tx)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_transactions(limit=20, offset=0):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT
                    t.id, t.type, t.quantity, t.date,
                    p.name AS product_name,
                    u.username AS operator_name
                FROM transactions t
                JOIN products p ON p.id = t.product_id
                LEFT JOIN users u ON u.id = t.user_id
                ORDER BY t.date DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)



def get_product_transactions(product_id, limit=15):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    t.id, t.type AS tx_type, t.quantity, t.date,
                    u.username AS operator_name
                FROM transactions t
                LEFT JOIN users u ON u.id = t.user_id
                WHERE t.product_id = %s
                ORDER BY t.date DESC
                LIMIT %s
            """, (product_id, limit))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

# ═══════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════

STATE_ADD_NAME = "waiting_add_name"
STATE_ADD_QTY = "waiting_add_qty"
STATE_EXISTING_ADD_QTY = "waiting_existing_add_qty"
STATE_REMOVE_QTY = "waiting_remove_qty"
STATE_EDIT_PRODUCT = "waiting_edit_product"
STATE_DELETE_PRODUCT = "waiting_delete_product"
STATE_SEARCH_ADMIN = "waiting_search_admin"
STATE_CUST_ADD_QTY = "waiting_cust_add_qty"
STATE_CUST_REMOVE_QTY = "waiting_cust_remove_qty"
STATE_ADMIN_ADD_SEL = "waiting_admin_add_sel"
STATE_ADMIN_REM_SEL = "waiting_admin_rem_sel"
STATE_ADD_TYPE = "waiting_add_type"
STATE_ADD_NAME_MOTIF = "waiting_add_name_motif"
STATE_ADD_NAME_12060 = "waiting_add_name_12060"
STATE_ADD_NAME_PLANTE = "waiting_add_name_plante"
STATE_ALERT_MSG = "waiting_alert_msg"

BTN_ADD = "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629"
BTN_SEARCH = "\U0001f50d \u0628\u062d\u062b"
BTN_ADD_QTY = "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629"
BTN_REMOVE_QTY = "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629"
BTN_EDIT = "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629"
BTN_DELETE = "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629"
BTN_CANCEL = "\u274c \u0625\u0644\u063a\u0627\u0621"
BTN_USERS = "⚙️ المستخدمين"
BTN_BACKUP = "💾 نسخ احتياطي"
BTN_ALERT = "🔔 تنبيه"
BTN_UPDATE = "📢 إشعار تحديث"
BTN_LOW_STOCK = "📊 المخزون المنخفض"
BTN_MOVEMENTS = "📋 الحركة"
BTN_INCOMING = "🚚 طلبات التوريد"

# Customer buttons
BTN_CUST_ALERT = "🔔 تنبيه المخزون"
BTN_CUST_ADD = "➕ إضافة"
BTN_CUST_REMOVE = "➖ إخراج"
BTN_CUST_SEARCH = "🔍 بحث"
BTN_CUST_MOVEMENTS = "📋 الحركة"
BTN_CUST_CANCEL = "❌ إلغاء"
BTN_CUST_ORDER = "📦 طلب توريد"
BTN_CUST_MY_ORDERS = "📋 طلباتي"


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════


def format_product_admin(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    status = "🔴" if qty <= min_stock else "🟢"
    unit = _get_unit_code(product["name"])
    return (
        "\U0001f4e6 " + product["name"] + "\n"
        "   \u0627\u0644\u0643\u0645\u064a\u0629: " + str(qty) + " " + unit
    )


def format_product_customer(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f7e2 \u0645\u062a\u0648\u0641\u0631"
    elif qty > 0:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f7e1 \u0643\u0645\u064a\u0629 \u0645\u062d\u062f\u0648\u062f\u0629"
    else:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f534 \u063a\u064a\u0631 \u0645\u062a\u0648\u0641\u0631 \u062d\u0627\u0644\u064a\u0627\u064b"


async def _broadcast_update_notification(update, context):
    """إرسال إشعار التحديث تلقائياً - يجلب آخر عملية ويبثها مباشرة."""
    # Fetch the most recent transaction
    conn = get_connection()
    recent = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, p.name AS product_name "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "ORDER BY t.date DESC LIMIT 1")
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
            if rows:
                recent = rows[0]
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)

    if not recent:
        await update.message.reply_text(
            "📭 لا توجد عمليات حديثة للإشعار عنها.",
            reply_markup=get_admin_keyboard(),
        )
        return

    # Build update description from last transaction
    r = recent
    if r["tx_type"] == "add":
        icon = "✅"
        action = "تمت إضافة"
    else:
        icon = "➖"
        action = "تم إخراج"
    time_str = r["date"].strftime("%H:%M %d/%m")
    update_desc = icon + " " + action + " " + str(r["quantity"]) + " من " + r["product_name"] + " (" + time_str + ")"

    # Fetch all active users
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'customer')")
            user_rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        user_rows = []
    finally:
        release_connection(conn)

    if not user_rows:
        await update.message.reply_text(
            "📭 لا يوجد مستخدمين مفعلين.",
            reply_markup=get_admin_keyboard(),
        )
        return

    # Broadcast
    broadcast_text = (
        "🆕 <b>تحديث جديد!</b>\n\n"
        + update_desc + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👉 اضغط الزر أدناه لتحديث الواجهة\n"
        "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الواجهة", callback_data="go_start")]
    ])
    sent = 0
    for row in user_rows:
        uid = row[0]
        try:
            await context.bot.send_message(
                chat_id=uid, text=broadcast_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            pass

    await update.message.reply_text(
        "✅ تم إرسال إشعار التحديث إلى <b>" + str(sent) + "</b> مستخدم.\n\n"
        "📋 " + update_desc,
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(),
    )
    logging.info("[UPDATE] Auto-broadcast sent to " + str(sent) + " users: " + update_desc)


async def send_low_stock_alerts(bot):
    """Send individual alerts to ALL users when products enter or leave low-stock list."""
    try:
        low_stock = get_low_stock_products()
        if not low_stock:
            current = {}
        else:
            current = {p["id"]: p for p in low_stock}

        # Products that ENTERED low stock (new)
        new_ids = set(current.keys()) - set(_previously_low_stock.keys())
        if new_ids:
            for pid in new_ids:
                prod = current[pid]
                name = prod["name"]
                lower_name = name.lower().strip()
                if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                    continue
                qty = prod.get("quantity", "?")
                unit = _get_unit_ar(name)
                msg = ("🔴 تنبيه: مخزون منخفض جديد"
                    + chr(10) + chr(10)
                    + "📦 " + name + chr(10)
                    + "📏 الكمية: " + str(qty) + " " + unit)
                await _broadcast_to_all_users(bot, msg)

        # Products that LEFT low stock (restocked)
        left_ids = set(_previously_low_stock.keys()) - set(current.keys())
        if left_ids:
            for pid in left_ids:
                prod = _previously_low_stock[pid]
                name = prod["name"]
                lower_name = name.lower().strip()
                if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                    continue
                qty = prod.get("quantity", "?")
                unit = _get_unit_ar(name)
                msg = ("✅ تنبيه: توفرت سلعة"
                    + chr(10) + chr(10)
                    + "📦 " + name + chr(10)
                    + "📏 الكمية: " + str(qty) + " " + unit)
                await _broadcast_to_all_users(bot, msg)

        # Update tracked dict
        _previously_low_stock.clear()
        _previously_low_stock.update(current)
    except Exception as e:
        logging.warning("[ALERT] Low stock check failed: " + str(e))


async def send_low_stock_list(update=None, context=None, bot=None):
    """Send full low-stock list. Works for admin or customer button."""
    try:
        low_stock = get_low_stock_products()
        valid_items = []
        for p in low_stock:
            lower_name = p["name"].lower().strip()
            if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                continue
            valid_items.append(p)
        if not valid_items:
            msg = "✅ لا توجد سلع منخفضة المخزون."
            if update:
                await update.message.reply_text(msg)
            return
        txt_lines = ["⚠️ قائمة المخزون المنخفض"]
        for p in valid_items:
            txt_lines.append("  • " + p["name"])
        text = chr(10).join(txt_lines)
        if len(text) > 4096:
            chunks = []
            cur = ["⚠️ قائمة المخزون المنخفض"]
            for ln in txt_lines[1:]:
                if len(chr(10).join(cur)) + len(chr(10) + ln) > 4000:
                    chunks.append(chr(10).join(cur))
                    cur = ["⚠️ مخزون منخفض (تابع)"]
                cur.append(ln)
            if cur:
                chunks.append(chr(10).join(cur))
            for chunk in chunks:
                if update:
                    await update.message.reply_text(chunk)
        else:
            if update:
                await update.message.reply_text(text)
    except Exception as e:
        logging.warning("[LOW_STOCK_LIST] Error: " + str(e))


def parse_crt_input(text):
    """Parse quantity input: accepts '10', '10crt', '10 crt'."""
    cleaned = text.strip().lower().replace(" ", "").replace("crt", "").replace("pcs", "")
    if cleaned.isdigit() and int(cleaned) >= 0:
        return int(cleaned)
    return None


def parse_qty_input(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        pid, qty = int(parts[0]), int(parts[1])
        if pid <= 0 or qty <= 0:
            return None
        return pid, qty
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════
#  KEYBOARDS

# ═══════════════════════════════════════════════
#  BOT SETTINGS HELPERS
# ═══════════════════════════════════════════════


def get_bot_setting(key, default=None):
    """Get a bot setting value from bot_settings table."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
            row = cur.fetchone()
        conn.commit()
        release_connection(conn)
        if row:
            return row[0]
        return default
    except Exception as e:
        logging.warning("[SETTINGS] Get failed: " + str(e))
        return default


def set_bot_setting(key, value):
    """Set a bot setting value."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = %s",
                (key, value, value))
        conn.commit()
        release_connection(conn)
    except Exception as e:
        logging.warning("[SETTINGS] Set failed: " + str(e))


async def generate_weekly_report(bot):
    """Generate and send the weekly inventory report. Returns report text."""
    try:
        conn = get_connection()
        try:
            with conn.cursor(psycopg2.extras.RealDictCursor) as cur:
                # Total products
                cur.execute("SELECT COUNT(*) as cnt FROM products")
                total = cur.fetchone()["cnt"]

                # Total quantity
                cur.execute("SELECT COALESCE(SUM(quantity), 0) as total_qty FROM products")
                total_qty = cur.fetchone()["total_qty"]

                # Low stock count
                cur.execute("SELECT COUNT(*) as cnt FROM products WHERE quantity <= minimum_stock")
                low_count = cur.fetchone()["cnt"]

                # Top 5 highest quantity
                cur.execute(
                    "SELECT name, quantity FROM products"
                    " WHERE quantity > 0"
                    " ORDER BY quantity DESC LIMIT 5")
                top_products = cur.fetchall()

                # Bottom 5 (lowest / zero)
                cur.execute(
                    "SELECT name, quantity FROM products"
                    " ORDER BY quantity ASC LIMIT 5")
                bottom_products = cur.fetchall()

                # Transactions this week
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM transactions"
                    " WHERE date >= NOW() - INTERVAL '7 days'")
                week_transactions = cur.fetchone()["cnt"]

                # Adds vs removes this week
                cur.execute(
                    "SELECT type AS tx_type, COUNT(*) as cnt FROM transactions"
                    " WHERE date >= NOW() - INTERVAL '7 days'"
                    " GROUP BY type")
                type_counts = {r["tx_type"]: r["cnt"] for r in cur.fetchall()}
                adds = type_counts.get("add", 0)
                removes = type_counts.get("remove", 0)
        finally:
            release_connection(conn)

        # Build report message
        lines = []
        lines.append("\U0001f4cb \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0632\u0648\u0646 \u0627\u0644\u0623\u0633\u0628\u0648\u0639\u064a")
        algeria_tz = datetime.timezone(datetime.timedelta(hours=1))
        lines.append("\U0001f4c5 " + datetime.datetime.now(algeria_tz).strftime("%Y-%m-%d"))
        lines.append("")
        lines.append("\U0001f4e6 \u0625\u062c\u0645\u0627\u0644\u064a \u0627\u0644\u0645\u0646\u062a\u062c\u0627\u062a: " + str(total))
        lines.append("\U0001f4cf \u0625\u062c\u0645\u0627\u0644\u064a \u0627\u0644\u0643\u0645\u064a\u0627\u062a: " + str(total_qty))
        lines.append("\U0001f534 \u0645\u0646\u062a\u062c\u0627\u062a \u0645\u0646\u062e\u0641\u0636\u0629: " + str(low_count))
        lines.append("")
        lines.append("\U0001f504 \u062d\u0631\u0643\u0629 \u0647\u0630\u0627 \u0627\u0644\u0623\u0633\u0628\u0648\u0639:")
        lines.append("   \u2795 \u0625\u0636\u0627\u0641\u0629: " + str(adds) + " \u0639\u0645\u0644\u064a\u0629")
        lines.append("   \u2796 \u0625\u062e\u0631\u0627\u062c: " + str(removes) + " \u0639\u0645\u0644\u064a\u0629")
        lines.append("")

        if top_products:
            lines.append("\U0001f4c8 \u0623\u0643\u062b\u0631 5 \u0645\u0646\u062a\u062c\u0627\u062a \u0643\u0645\u064a\u0629:")
            for p in top_products:
                lines.append("   \U0001f4e6 " + str(p["name"] or "") + " (" + str(p["quantity"]) + ")")
            lines.append("")

        if bottom_products:
            lines.append("\U0001f4c9 \u0623\u0642\u0644 5 \u0645\u0646\u062a\u062c\u0627\u062a \u0643\u0645\u064a\u0629:")
            for p in bottom_products:
                icon = "\U0001f534" if p["quantity"] == 0 else "\U0001f7e1"
                lines.append("   " + icon + " " + str(p["name"] or "") + " (" + str(p["quantity"]) + ")")

        report_text = chr(10).join(lines)

        # Send to configured recipient
        recipient_id = get_bot_setting("weekly_report_recipient")
        if not recipient_id:
            logging.warning("[WEEKLY] No recipient configured")
            return report_text

        try:
            await bot.send_message(chat_id=int(recipient_id), text=report_text)
            logging.info("[WEEKLY] Report sent to " + str(recipient_id))
        except Exception as e:
            logging.error("[WEEKLY] Failed to send: " + str(e))

        return report_text

    except Exception as e:
        logging.error("[WEEKLY] Report generation failed: " + str(e))
        return None


async def _weekly_report_loop(bot):
    """Send weekly report every Sunday at 23:00 Algeria time (22:00 UTC)."""
    last_report = ""
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        # Sunday = weekday 6, send at 22:00 UTC = 23:00 Algeria
        today = now.strftime("%Y-%m-%d")
        is_sunday = now.weekday() == 6
        hour_ok = 22 <= now.hour < 23
        enabled = get_bot_setting("weekly_report_enabled", "false") == "true"
        if is_sunday and hour_ok and today != last_report and enabled:
            await generate_weekly_report(bot)
            last_report = today
        await asyncio.sleep(60)

# ═══════════════════════════════════════════════════════════


def get_admin_keyboard():
    """لوحة المفاتيح الديناميكية - تعرض عدادات الطلبات والمستخدمين المعلّقين."""
    # عداد طلبات التوريد المعلّقة
    incoming_count = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'pending'")
            incoming_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'received'")
            incoming_count += cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE role = 'pending'")
            pending_users_count = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        pending_users_count = 0
    finally:
        release_connection(conn)

    # نص الأزرار مع العدادات
    users_btn = ("⚙️ المستخدمين (" + str(pending_users_count) + ")") if pending_users_count > 0 else BTN_USERS
    incoming_btn = ("🚚 طلبات التوريد (" + str(incoming_count) + ")") if incoming_count > 0 else BTN_INCOMING

    return ReplyKeyboardMarkup(
        [
            [BTN_ADD, BTN_SEARCH],
            [BTN_ADD_QTY, BTN_REMOVE_QTY],
            [BTN_EDIT, BTN_DELETE],
            [users_btn, BTN_BACKUP, BTN_ALERT],
            [BTN_LOW_STOCK, BTN_MOVEMENTS, incoming_btn],
            [BTN_UPDATE, BTN_CANCEL],
        ],
        resize_keyboard=True,
    )


def get_input_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)





def get_customer_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_CUST_ORDER],
            [BTN_CUST_MY_ORDERS],
            [BTN_CUST_ALERT],
            [BTN_CUST_ADD, BTN_CUST_REMOVE],
            [BTN_CUST_SEARCH],
            [BTN_CUST_MOVEMENTS],
            [BTN_CUST_CANCEL],
        ],
        resize_keyboard=True,
    )
async def ask_add_name(update, context):
    """Step 1: Ask product type (motif or other)."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 موتيف (قطع pcs)", callback_data="addtype_motif")],
        [InlineKeyboardButton("🎨 120/60 (قطع pcs)", callback_data="addtype_12060")],
        [InlineKeyboardButton("🌿 plante (قطع pcs)", callback_data="addtype_plante")],
        [InlineKeyboardButton("📦 باقي السلع (كرتون crt)", callback_data="addtype_other")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="addtype_cancel")],
    ])
    await update.message.reply_text(
        "📦 إضافة سلعة جديدة\n\nاختر نوع السلعة:",
        reply_markup=keyboard,
    )


async def ask_add_qty(update, context, product_name, is_motif=False):
    """Step 2: Ask for quantity."""
    context.user_data["state"] = STATE_ADD_QTY
    context.user_data["add_name"] = product_name
    context.user_data["add_is_motif"] = is_motif
    unit = "pcs" if is_motif else "crt"
    await update.message.reply_text(
        "\U0001f4e6 " + product_name + "\n"
        "\U0001f4e6 \u0643\u0645 \u0627\u0644\u0639\u062f\u062f (" + unit + ")?",
        reply_markup=get_input_keyboard(),
    )


async def confirm_add_product(update, context, product_name, qty, is_motif=False):
    """Step 3: Show summary with inline confirm button."""
    context.user_data["add_qty"] = qty
    context.user_data["add_is_motif"] = is_motif
    context.user_data["state"] = "waiting_add_confirm"
    unit = "pcs" if is_motif else "crt"
    text = (
        "\U0001f4cb \u062a\u0623\u0643\u064a\u062f \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629:\n\n"
        "\U0001f4e6 \u0627\u0644\u0627\u0633\u0645: " + product_name + "\n"
        "\U0001f4e6 \u0627\u0644\u0639\u062f\u062f: " + str(qty) + " " + unit
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="add_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="add_cancel")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def callback_addtype_handler(update, context):
    """Handle product type selection (motif vs other)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        if data == "addtype_motif":
            context.user_data["state"] = STATE_ADD_NAME_MOTIF
            await query.edit_message_text(
                "🎨 موتيف (قطع pcs)\n\nأرسل اسم السلعة:",
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 أرسل اسم السلعة (موتيف):",
                reply_markup=get_input_keyboard(),
            )
        if data == "addtype_12060":
            context.user_data["state"] = STATE_ADD_NAME_12060
            await query.edit_message_text("🎨 120/60 (قطع pcs)\n\nأرسل اسم السلعة:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 أرسل اسم السلعة (120/60):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_plante":
            context.user_data["state"] = STATE_ADD_NAME_PLANTE
            await query.edit_message_text("🌿 plante (قطع pcs)\n\nأرسل اسم السلعة:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🌿 أرسل اسم السلعة (plante):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_other":
            context.user_data["state"] = STATE_ADD_NAME
            await query.edit_message_text(
                "📦 باقي السلع (كرتون crt)\n\nأرسل اسم السلعة:",
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📦 أرسل اسم السلعة (باقي السلع):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_cancel":
            await query.edit_message_text("❌ تم الإلغاء.")
            try:
                await context.bot.send_message(chat_id=query.message.chat_id, text="📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
            except Exception:
                pass
    except Exception as e:
        logging.error("[ADDTYPE] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ: " + str(e))
        except Exception:
            pass


async def callback_add_handler(update, context):
    """Handle inline button press for add confirmation."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_data = context.user_data

    if data == "add_confirm":
        name = user_data.get("add_name", "")
        qty = user_data.get("add_qty", 0)
        telegram_id = query.from_user.id
        try:
            product = db_add_product(name=name, quantity=qty)
            msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d!\n\n" + format_product_admin(product)
            await query.edit_message_text(msg)
            await context.bot.send_message(chat_id=query.message.chat_id, text="\u2714\ufe0f", reply_markup=get_admin_keyboard())
        except Exception as e:
            await query.edit_message_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e))
        user_data.clear()

    elif data == "add_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        user_data.clear()
        await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())


async def process_add_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 25",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_quantity(product_id, qty, "add")
        log_transaction(product_id, "add", qty, db_user["id"])
        unit = _get_unit_code(updated["name"])
        msg = "✅ تمت إضافة " + str(qty) + " " + unit + " لـ " + updated["name"] + "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text("\u274c " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()


async def process_remove_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 10",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_quantity(product_id, qty, "remove")
        log_transaction(product_id, "remove", qty, db_user["id"])
        unit = _get_unit_code(updated["name"])
        msg = "✅ تم إخراج " + str(qty) + " " + unit + " من " + updated["name"] + "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text("\u274c " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()


async def process_edit_product(update, context, text, telegram_id):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2 or not parts[0].isdigit():
        err_msg = ("❌ التنسيق: رقم_السلعة, الاسم الجديد"
            + chr(10) + chr(10)
            + "مثال: 1, زيت 5 لتر")
        await update.message.reply_text(err_msg, reply_markup=get_input_keyboard())
        return
    product_id = int(parts[0])
    new_name = parts[1]
    if not new_name:
        await update.message.reply_text("❌ أرسل الاسم الجديد.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_product(product_id, name=new_name)
        msg = "✅ تم تعديل الاسم بنجاح!"
        msg = msg + chr(10) + chr(10) + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()

async def process_delete_product(update, context, text, telegram_id):
    text = text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "\u274c \u0623\u0631\u0633\u0644 \u0631\u0642\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0641\u0642\u0637.\n\u0645\u062b\u0627\u0644: 5",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id = int(text)
    try:
        deleted = db_delete_product(product_id)
        if deleted:
            await update.message.reply_text("\u2705 \u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d.", reply_markup=get_admin_keyboard())
        else:
            await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0627\u0644\u0633\u0644\u0639\u0629.", reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()


async def process_search(update, context, text, admin_view=False):
    if not text:
        await update.message.reply_text(
            "\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.",
            reply_markup=get_input_keyboard(),
        )
        return
    results = search_products(text)
    if not results:
        if admin_view:
            msg = ("\U0001f50d لم يتم العثور على نتائج لـ " + text)
        else:
            msg = ("🔍 العنصر غير موجود." + chr(10) + chr(10)
                + "💡 البحث الذكي يفهم الأخطاء الإملائية ويعامل / و × بشكل متساوٍ." + chr(10)
                + "جرّب البحث بكلمة واحدة أقصر (مثلاً: ceram أو 60x60 أو اسم المصنع).")
        reply_kb = get_input_keyboard() if admin_view else None
        await update.message.reply_text(msg, reply_markup=reply_kb)
        return
    if admin_view:
        header = "\U0001f50d \u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0628\u062d\u062b \u0639\u0646 " + text + " (" + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629):\n"
        lines = [header]
        for p in results:
            lines.append("  #" + str(p["id"]) + " " + format_product_admin(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())
    else:
        if len(results) == 1:
            p = results[0]
            await _show_product_detail(update, context, p)
        else:
            header = "🔍 وجدت " + str(len(results)) + " منتجات:\n"
            text_lines = [header]
            buttons = []
            for p in results:
                qty = p["quantity"]
                unit_ar = _get_unit_ar(p["name"])
                text_lines.append(
                    str(p["id"]) + ". " + p["name"]
                    + "\n   \U0001f4e6 " + str(qty) + " " + unit_ar
                )
                text_lines.append("")
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                buttons.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            text_lines.append("👆 اختر المنتج المطلوب.")
            await update.message.reply_text(
                "\n".join(text_lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
    context.user_data.clear()


# ═══════════════════════════════════════════════════════════

def _product_status_icon(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return "🟢"
    elif qty > 0:
        return "🟡"
    else:
        return "🔴"

def _is_pcs_product(name):
    """Check if product should use pcs unit."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return (n.startswith("motif") or n.startswith("plante")
            or n.startswith("120/60") or n.startswith("120x60")
            or n.startswith("120×60"))


def _get_unit_code(product_name):
    """Return unit code: 'pcs' for motif/plante/120x60 products, 'crt' for others."""
    return "pcs" if _is_pcs_product(product_name) else "crt"

def _get_unit_ar(product_name):
    """Return Arabic unit label."""
    return "قطعة" if _is_pcs_product(product_name) else "كرتون"

async def _show_product_detail(update, context, product):
    p = product
    icon = _product_status_icon(p)
    qty = p["quantity"]
    unit_ar = _get_unit_ar(p["name"])
    text = p["name"] + "\n\n" + "📦 الكمية: " + str(qty) + " " + unit_ar + " " + icon
    pid = str(p["id"])
    user_id = update.effective_user.id if update.effective_user else 0
    admin_view = is_admin(user_id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
            InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid),
        ],
    ])
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


def _build_movements_text(rows):
    """Build formatted movements text from transaction rows. Usernames are clickable Telegram links."""
    if not rows:
        return None
    lines = ["📋 حركة اليوم (" + str(len(rows)) + "):", ""]
    for r in rows:
        r = dict(r)
        if r["tx_type"] == "add":
            icon = "➕"
            action = "إضافة"
        else:
            icon = "➖"
            action = "إخراج"
        time_str = r["date"].strftime("%H:%M")
        prod = str(r["product_name"] or "")
        qty = r["quantity"]
        unit = _get_unit_code(prod)
        # Build clickable user link
        user_tid = r.get("user_tid")
        user_name = str(r.get("user_name") or "مجهول")
        if user_tid and user_name != "مجهول":
            user_link = "<a href=\"tg://user?id=" + str(user_tid) + "\">" + user_name + "</a>"
        else:
            user_link = user_name
        lines.append(icon + " " + prod + " " + str(qty) + " " + unit + " — " + user_link + " " + time_str)

    add_count = sum(1 for r in rows if r["tx_type"] == "add")
    rem_count = sum(1 for r in rows if r["tx_type"] == "remove")
    lines.append(chr(10) + "➕ " + str(add_count) + " إضافة  |  ➖ " + str(rem_count) + " إخراج")
    return chr(10).join(lines)


def _fetch_today_movements():
    """Fetch today's movements from DB. Uses regular cursor to avoid RealDictCursor + 'type' column conflict."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, p.name AS product_name, "
                "u.username AS user_name, u.telegram_id AS user_tid "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= CURRENT_DATE "
                "ORDER BY t.date DESC")
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)




def _fetch_movements_by_date(target_date):
    """Fetch movements for a specific date from DB."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, p.name AS product_name, "
                "u.username AS user_name, u.telegram_id AS user_tid "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= %s AND t.date < %s "
                "ORDER BY t.date DESC",
                (target_date, target_date + datetime.timedelta(days=1)))
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

async def _show_all_movements(update, context, admin_view=True):
    """Show date picker for last 7 days - user picks a date to see movements."""
    try:
        today = datetime.date.today()
        buttons = []
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        for d in range(6, -1, -1):
            day = today - datetime.timedelta(days=d)
            day_str = day.strftime("%Y-%m-%d")
            wd = day.weekday()
            if d == 0:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
            else:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m")
            buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])

        text = "📋 اختار التاريخ لعرض الحركة:"
        markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(text, reply_markup=markup)
    except Exception as e:
        logging.error("[MOVEMENTS_DATE] Error: " + str(e))
        kb = get_admin_keyboard() if admin_view else get_customer_keyboard()
        await update.message.reply_text("❌ حدث خطأ في عرض الحركات.", reply_markup=kb)


async def callback_mov_date_handler(update, context):
    """Handle date selection from movements date picker."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "movd_2025-01-15"

    # Back button
    if data == "movd_back":
        today = datetime.date.today()
        buttons = []
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        for d in range(6, -1, -1):
            day = today - datetime.timedelta(days=d)
            day_str = day.strftime("%Y-%m-%d")
            wd = day.weekday()
            if d == 0:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
            else:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m")
            buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])
        await query.edit_message_text(
            "📋 اختار التاريخ لعرض الحركة:",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    date_str = data.replace("movd_", "")
    try:
        target_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text("❌ تاريخ غير صالح.")
        return

    try:
        rows = _fetch_movements_by_date(target_date)
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        wd = target_date.weekday()
        date_display = weekday_ar.get(wd, "") + " " + target_date.strftime("%d/%m/%Y")

        if not rows:
            await query.edit_message_text(
                "📋 حركة " + date_display + ":\n\nلا توجد عمليات في هذا اليوم.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]
                ]))
            return

        text = _build_movements_text(rows)
        # Replace header to show date
        header = "📋 حركة " + date_display + " (" + str(len(rows)) + "):"
        body_lines = text.split("\n")[2:]  # skip old header and blank line
        text = header + "\n" + "\n".join(body_lines)

        back_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]
        ])

        if len(text) <= 4096:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button)
        else:
            await query.edit_message_text(text[:3900], parse_mode=ParseMode.HTML, reply_markup=back_button)
            remaining = text[3900:]
            while remaining:
                chunk = remaining[:4096]
                remaining = remaining[4096:]
                await context.bot.send_message(
                    chat_id=query.effective_chat.id,
                    text=chunk,
                    parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error("[MOV_DATE_HANDLER] Error: " + str(e))
        await query.edit_message_text("❌ حدث خطأ في عرض الحركات.")


async def callback_product_select_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        product_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        await _show_product_detail(update, context, dict(row))
    except Exception as e:
        logging.error("[PSEL] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass


async def callback_product_action_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split("_", 2)
    if len(parts) != 3:
        return
    action = parts[1]
    try:
        product_id = int(parts[2])
    except ValueError:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    if action == "log":
        try:
            txns = get_product_transactions(product_id, limit=15)
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT name FROM products WHERE id = %s", (product_id,))
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)
            product_name = dict(row)["name"] if row else "?"
            if not txns:
                text = "📜 لا توجد حركات مسجلة لـ\n" + product_name
            else:
                tx_lines = ["📜 آخر حركات - " + product_name + "\n"]
                for tx in txns:
                    icon = "📥" if tx["tx_type"] == "add" else "📤"
                    date_str = tx["date"].strftime("%m/%d %H:%M")
                    op = tx.get("operator_name") or "مجهول"
                    sign = "+" if tx["tx_type"] == "add" else "-"
                    unit = _get_unit_code(product_name)
                    tx_lines.append(
                        icon + " " + sign + str(tx["quantity"]) + " " + unit + " | "
                        + op + " | " + date_str
                    )
                text = "\n".join(tx_lines)
            await query.edit_message_text(text)
        except Exception as e:
            logging.error("[PACT_LOG] Error: " + str(e))
            try:
                await query.edit_message_text("❌ خطأ في تحميل الحركة.")
            except Exception:
                pass
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, name, quantity FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        product = dict(row)
        context.user_data["cust_product"] = product
        if action == "add":
            unit = _get_unit_code(product["name"])
            unit_ar = _get_unit_ar(product["name"])
            text = "\U0001f4e6 " + product["name"] + "\n\n" + "\u2795 \u0625\u0636\u0627\u0641\u0629 " + unit_ar + "\n\n" + "\u0623\u0631\u0633\u0644 \u0627\u0644\u0639\u062f\u062f (" + unit + "):"
        else:
            unit = _get_unit_code(product["name"])
            unit_ar = _get_unit_ar(product["name"])
            text = ("\U0001f4e6 " + product["name"]
                + " (\u0627\u0644\u0645\u062a\u0648\u0641\u0631: " + str(product["quantity"]) + " " + unit + ")\n\n"
                + "\u2796 \u0625\u062e\u0631\u0627\u062c " + unit_ar + "\n\n"
                + "\u0623\u0631\u0633\u0644 \u0627\u0644\u0639\u062f\u062f (" + unit + "):")
        context.user_data["pact_action"] = "remove" if action == "rem" else action
        context.user_data["state"] = "waiting_pact_qty"
        await query.edit_message_text(text)
    except Exception as e:
        logging.error("[PACT] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass
#  SHOW PRODUCTS LIST
# ═══════════════════════════════════════════════════════════


async def start_command(update, context):
    user = update.effective_user
    sync_user_info(user)
    # Notify admins about new pending user
    if not is_admin(user.id):
        db_user = get_user(user.id)
        if db_user and db_user.get("role") == "pending":
            uname = user.first_name or user.username or str(user.id)
            for admin_id in ADMIN_IDS:
                try:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ تفعيل", callback_data="approve_" + str(user.id)),
                         InlineKeyboardButton("❌ رفض", callback_data="reject_" + str(user.id))],
                    ])
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text="🔔 طلب انضمام جديد:\n\n👤 " + uname + "\n📧 " + str(user.id),
                        reply_markup=kb)
                    logging.info("[JOIN] Notification sent to admin " + str(admin_id) + " for user " + str(user.id))
                except Exception as notify_err:
                    logging.error("[JOIN] Failed to notify admin " + str(admin_id) + ": " + str(notify_err))
    if is_admin(user.id):
        name = user.first_name or ""
        await update.message.reply_text(
            "\U0001f44b \u0645\u0631\u062d\u0628\u0627\u064b <b>" + name + "</b>\n\n\U0001f3e2 \u0623\u0646\u062a \u0645\u062f\u064a\u0631 \u0627\u0644\u0646\u0638\u0627\u0645.\n\U0001f4cb \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0644\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u062e\u0632\u0648\u0646.\n\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\u26a1 \u062a\u0645 \u0627\u0644\u062a\u0637\u0648\u064a\u0631 \u0628\u0648\u0627\u0633\u0637\u0629 <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
            reply_markup=get_admin_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        name = user.first_name or ""
        db_user = get_user(user.id)
        if db_user and db_user.get("role") == "pending":
            await update.message.reply_text(
                "👋 مرحباً <b>" + name + "</b>\n\n⏳ حسابك قيد المراجعة.\nيجب الانتظار حتى يفعلك المدير.\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "👋 مرحباً <b>" + name + "</b>\n\n⌛ بحث عن المنتجات المتاحة\n📁 استخدم الأزرار أدناه.\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                reply_markup=get_customer_keyboard(),
                parse_mode=ParseMode.HTML,
            )
    context.user_data.clear()


async def help_command(update, context):
    user = update.effective_user
    if is_admin(user.id):
        text = (
            "\U0001f4cb \u0623\u0648\u0627\u0645\u0631 \u0627\u0644\u0625\u062f\u0627\u0631\u0629:\n\n"
            "/start - \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629\n"
            "/help - \u0639\u0631\u0636 \u0647\u0630\u0627 \u0627\u0644\u0645\u0633\u0627\u0639\u062f\n"
            "/add - \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629\n"
            "/search - \u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n"
            "\U0001f4a1 \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u064a\u0636\u0627\u064b."
        )
    else:
        text = (
            "\U0001f4cb \u0627\u0644\u0645\u0633\u0627\u0639\u062f\u0629:\n\n"
            "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u062a\u0648\u0641\u0631\u0647\u0627.\n\n"
            "\u0645\u062b\u0627\u0644:\n  \u0632\u064a\u062a 5 \u0644\u062a\u0631\n  \u0632\u064a\u062a\n  \u062d\u0644\u064a\u0628"
        )
    await update.message.reply_text(text)


async def add_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await ask_add_name(update, context)


async def search_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    context.user_data["state"] = STATE_SEARCH_ADMIN
    await update.message.reply_text(
        "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
        reply_markup=get_input_keyboard(),
    )


# ═══════════════════════════════════════════════════════════

# ── Whitelist: Approval Handler ───────────────────────

async def callback_approval_handler(update, context):
    """Handle approve/reject for pending users."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "approve_123456" or "reject_123456"
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    action, tid_str = parts[0], parts[1]
    if not tid_str.isdigit():
        return
    target_id = int(tid_str)

    if action == "approve":
        user = approve_user(target_id)
        if user:
            await query.edit_message_text(
                "\u2705 \u062a\u0645 \u062a\u0641\u0639\u064a\u0644 \u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645:\n"
                "👤 " + (user.get("username") or str(user["telegram_id"]))
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="\u2705 \u062a\u0645 \u062a\u0641\u0639\u064a\u0644 \u062d\u0633\u0627\u0628\u0643!\n"
                    "\u0627\u0644\u0622\u0646 \u064a\u0645\u0643\u0646\u0643 \u0627\u0644\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646.\n"
                    "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u062a\u0648\u0641\u0631\u0647\u0627.",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود أو مفعل بالفعل.")

    elif action == "reject":
        deleted = reject_user(target_id)
        if deleted:
            await query.edit_message_text("❌ تم رفض و حذف المستخدم.")
            try:
                await context.bot.send_message(chat_id=target_id, text="❌ تم رفض طلبك.")
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود.")


async def callback_promote_handler(update, context):
    """ترقية مستخدم إلى مدير أو إزالة صلاحية الإدارة."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "promote_123456" or "demote_123456"
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    action, tid_str = parts[0], parts[1]
    if not tid_str.isdigit():
        return
    target_id = int(tid_str)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if action == "promote":
                cur.execute("UPDATE users SET role = 'admin' WHERE telegram_id = %s AND role = 'customer' RETURNING *", (target_id,))
                user = cur.fetchone()
            elif action == "demote":
                cur.execute("UPDATE users SET role = 'customer' WHERE telegram_id = %s AND role = 'admin' AND telegram_id NOT IN %s RETURNING *",
                    (target_id, tuple(ADMIN_IDS)))
                user = cur.fetchone()
            else:
                return
        conn.commit()
    except Exception:
        conn.rollback()
        user = None
    finally:
        release_connection(conn)

    if user:
        user = dict(user)
        name = user.get("first_name") or user.get("username") or str(user["telegram_id"])
        if action == "promote":
            await query.edit_message_text("👑 تم ترقية <b>" + name + "</b> إلى مدير!", parse_mode=ParseMode.HTML)
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="👑 تم ترقيتك إلى مدير!\nالآن يمكنك إدارة المخزون.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("👤 تم إزالة صلاحية الإدارة من <b>" + name + "</b>.", parse_mode=ParseMode.HTML)
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="👤 تم إزالة صلاحية الإدارة من حسابك.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        logging.info("[USERS] " + action + " user " + str(target_id))
    else:
        await query.edit_message_text("⚠️ لم يتم تطبيق التغيير.")


# ── Backup Command ─────────────────────────────────────

async def backup_command(update, context):
    global _last_backup_data
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    try:
        import io
        from telegram import InputFile
        backup = db_export_backup()
        _last_backup_data = backup
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 استيراد هذه النسخة", callback_data="restore_last")],
            [InlineKeyboardButton("✉️ استيراد من البريد", callback_data="restore_from_email")],
        ])
        await update.message.reply_document(
            document=InputFile(io.BytesIO(file_bytes), filename="backup_" + now_str + ".json"),
            caption="💾 نسخة احتياطية كاملة\n" + "📄 السلع: " + str(len(backup["products"])) + "\n" + "📜 العمليات: " + str(len(backup["transactions"])) + "\n" + "👥 المستخدمين: " + str(len(backup["users"])) + "\n" + "💡 حفظ الملف. لإستيراد البيانات أرسله للبوت.",
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.message.reply_text("❌ فشل النسخ الاحتياطي: " + str(e))


async def handle_backup_document(update, context):
    """Admin sends a backup .json file to restore data."""
    if not is_admin(update.effective_user.id):
        return
    if not update.message.document:
        return
    file_name = update.message.document.file_name or ""
    if not file_name.endswith(".json"):
        return
    try:
        file = await update.message.document.get_file()
        raw = await file.download_as_bytearray()
        data = json.loads(bytes(raw).decode("utf-8"))
        if "products" not in data:
            await update.message.reply_text("❌ ملف غير صالح.")
            return
        context.user_data["restore_data"] = data
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ تأكيد استيراد", callback_data="restore_confirm"),
             InlineKeyboardButton("❌ إلغاء", callback_data="restore_cancel")],
        ])
        prod_count = len(data.get("products", []))
        tx_count = len(data.get("transactions", []))
        await update.message.reply_text(
            "⚠️ تأكيد استيراد البيانات:\n\n"
            "📦 السلع: " + str(prod_count) + "\n"
            "📜 العمليات: " + str(tx_count) + "\n\n"

            "\U0001f534 \u062a\u0646\u0628\u064a\u0647: \u0633\u064a\u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a \u0627\u0644\u062d\u0627\u0644\u064a\u0629 \u0648\u0627\u0633\u062a\u0628\u062f\u0627\u0644\u0647\u0627!",
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.message.reply_text("\u274c \u0641\u0634\u0644 \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0645\u0644\u0641: " + str(e))


async def callback_restore_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "restore_confirm":
        restore_data = context.user_data.get("restore_data")
        if not restore_data:
            await query.edit_message_text("\u274c \u0644\u0627 \u064a\u0648\u062c\u062f \u0628\u064a\u0627\u0646\u0627\u062a \u0644\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f.")
            return
        try:
            result = db_import_backup(restore_data)
            await query.edit_message_text(result)
        except Exception as e:
            await query.edit_message_text("\u274c \u0641\u0634\u0644 \u0627\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f: " + str(e))
        context.user_data.pop("restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "restore_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0625\u0644\u063a\u0627\u0621 \u0627\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a.")
        context.user_data.pop("restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass


async def _safe_edit(query, text, reply_markup=None):
    """Edit message safely — works for both text messages and document captions."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.edit_message_caption(text, reply_markup=reply_markup)
        except Exception:
            pass


async def callback_restore_from_email_handler(update, context):
    query = update.callback_query
    await query.answer()
    await _safe_edit(query, "⏳ جارٍ البحث في البريد الإلكتروني...")
    data = await asyncio.get_running_loop().run_in_executor(None, _fetch_latest_backup_from_email)
    if not data:
        await _safe_edit(query, "❌ لم يتم العثور على نسخة احتياطية في البريد.\n\nتأكد من:\n1. إعداد IMAP_HOST\n2. وجود نسخ مرسلة")
        return
    if "products" not in data:
        await _safe_edit(query, "❌ الملف غير صالح.")
        return
    backup_date = data.get("date", "?")[:16]
    prod_count = len(data.get("products", []))
    tx_count = len(data.get("transactions", []))
    context.user_data["email_restore_data"] = data
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ تأكيد", callback_data="email_restore_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="email_restore_cancel")],
    ])
    text = ("✉️ نسخة احتياطية من البريد:\n\n"
            + "📅 التاريخ: " + backup_date + "\n"
            + "📦 السلع: " + str(prod_count) + "\n"
            + "📜 العمليات: " + str(tx_count) + "\n\n"
            + "🔴 تنبيه: سيتم حذف البيانات الحالية واستبدالها!")
    await _safe_edit(query, text, reply_markup=keyboard)

async def callback_restore_last_handler(update, context):
    query = update.callback_query
    await query.answer()
    if not _last_backup_data:
        await _safe_edit(query, "❌ لا توجد نسخة احتياطية محفوضة.\nاستخدم /backup لإنشاء واحدة.")
        return
    data = _last_backup_data
    prod_count = len(data.get("products", []))
    tx_count = len(data.get("transactions", []))
    backup_date = data.get("date", "?")[:16]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ تأكيد استيراد", callback_data="last_restore_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="last_restore_cancel")],
    ])
    text = "💾 استيراد آخر نسخة احتياطية:\n\n" + "📅 التاريخ: " + backup_date + "\n" + "📦 السلع: " + str(prod_count) + "\n" + "📜 العمليات: " + str(tx_count) + "\n\n" + "🔴 تنبيه: سيتم حذف البيانات الحالية واستبدالها!"
    await _safe_edit(query, text, reply_markup=keyboard)


async def callback_add_notify_handler(update, context):
    """Handle the 'reason for add' choice: truck or customer return."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "addnotify_truck" or "addnotify_return"
    reason = "truck" if data == "addnotify_truck" else "return"
    pending = context.user_data.get("pending_add_notify")
    if not pending:
        await query.edit_message_text("❌ انتهت صلاحية هذا الاختيار.")
        context.user_data.clear()
        return
    product = pending["product"]
    qty = pending["qty"]
    actor = pending["user"]
    # Broadcast the notification with the chosen reason
    await _broadcast_add_notification(context.bot, product, qty, actor, reason=reason)
    # Confirm to the user
    if reason == "truck":
        confirm = "🚚 تم إرسال تنبيه وصول بضاعة من الشاحنة للجميع ✅"
    else:
        confirm = "↩️ تم إرسال تنبيه إرجاع سلعة من زبون للجميع ✅"
    await query.edit_message_text(confirm)
    context.user_data.pop("pending_add_notify", None)


async def callback_email_restore_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "email_restore_confirm":
        restore_data = context.user_data.get("email_restore_data")
        if not restore_data:
            await _safe_edit(query, "❌ لا يوجد بيانات.")
            return
        try:
            result = db_import_backup(restore_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ فشل الاستيراد: " + str(e))
        context.user_data.pop("email_restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "email_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        context.user_data.pop("email_restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass

async def callback_restore_last_confirm_handler(update, context):
    query = update.callback_query
    await query.answer()
    global _last_backup_data
    data = query.data
    if data == "last_restore_confirm":
        if not _last_backup_data:
            await _safe_edit(query, "❌ لا توجد نسخة.")
            return
        try:
            result = db_import_backup(_last_backup_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ فشل الاستيراد: " + str(e))
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "last_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
        except Exception:
            pass

# ── Users Command ──

async def users_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    pending = get_pending_users()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE role IN ('admin', 'customer') ORDER BY role, created_at")
            approved = [dict(r) for r in cur.fetchall()]
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[USERS] Failed to fetch approved users: " + str(e))
        approved = []
    finally:
        release_connection(conn)

    # أزرار الموافقة/الرفض للمعلّقين
    buttons = []
    if pending:
        for u in pending:
            name = u.get("first_name") or u.get("username") or str(u["telegram_id"])
            date_str = u["created_at"].strftime("%m/%d %H:%M") if u.get("created_at") else ""
            buttons.append([
                InlineKeyboardButton("✅ " + name + " (" + date_str + ")", callback_data="approve_" + str(u["telegram_id"])),
                InlineKeyboardButton("❌", callback_data="reject_" + str(u["telegram_id"])),
            ])

    # أزرار الترقية/الإزالة للمفعلين
    promote_buttons = []
    for u in approved:
        name = (u.get("first_name") or u.get("username") or str(u["telegram_id"]))[:18]
        if u["role"] == "customer":
            promote_buttons.append([InlineKeyboardButton(
                "👑 ترقية: " + name, callback_data="promote_" + str(u["telegram_id"])
            )])
        elif u["role"] == "admin" and u["telegram_id"] not in ADMIN_IDS:
            promote_buttons.append([InlineKeyboardButton(
                "👤 إزالة إدارة: " + name, callback_data="demote_" + str(u["telegram_id"])
            )])

    msg_lines = ["⚙️ إدارة المستخدمين\n"]
    if pending:
        msg_lines.append("🔔 منتظرين الموافقة (" + str(len(pending)) + "):")
        for u in pending:
            name = u.get("first_name") or u.get("username") or str(u["telegram_id"])
            date_str = u["created_at"].strftime("%m/%d %H:%M")
            msg_lines.append("  • " + name + " (" + date_str + ")")
        msg_lines.append("")
    else:
        msg_lines.append("✅ لا يوجد طلبات معلقة.")
    msg_lines.append("")
    msg_lines.append("✅ المستخدمون المفعلين (" + str(len(approved)) + "):")
    for u in approved:
        role_icon = "👑" if u["role"] == "admin" else "👤"
        name = u.get("first_name") or u.get("username") or str(u["telegram_id"])
        msg_lines.append("  " + role_icon + " " + name)

    # أزرار الترقية أسفل الرسالة
    if promote_buttons:
        buttons.extend(promote_buttons)

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text("\n".join(msg_lines), reply_markup=markup)

async def handle_admin_message(update, context):


# ═══════════════════════════════════════════════════════════
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════

    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user
    chat_id = update.effective_chat.id
    bot = context.bot

    # In a state - if user clicks an admin button, clear state first
    admin_buttons = [BTN_ADD, BTN_SEARCH, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT, BTN_DELETE, BTN_BACKUP, BTN_ALERT, BTN_UPDATE, BTN_LOW_STOCK, BTN_MOVEMENTS]
    # هذه الأزرار ديناميكية (قد تحتوي عدادات)
    dynamic_prefixes = [BTN_USERS, BTN_INCOMING]
    if text in admin_buttons or any(text.startswith(p) for p in dynamic_prefixes):
        context.user_data.clear()
        state = None

    if not state:
        if text == BTN_ADD:
            await ask_add_name(update, context)
            return
        if text == BTN_SEARCH:
            context.user_data["state"] = STATE_SEARCH_ADMIN
            await update.message.reply_text(
                "🔍 البحث الذكي" + chr(10) + chr(10) + "أرسل اسم أو جزء من اسم السلعة:" + chr(10) + chr(10) + "💡 يفهم الأخطاء الإملائية" + chr(10) + "🔤 / و × متساويان (60/60 = 60x60)" + chr(10) + "🏭 ابحث بالمصنع أو المقاس",
                reply_markup=get_input_keyboard())
            return
        if text == BTN_ADD_QTY:
            context.user_data["state"] = STATE_ADMIN_ADD_SEL
            msg = "➕ إضافة علب" + chr(10) + chr(10) + "أرسل اسم السلعة:"
            await update.message.reply_text(msg, reply_markup=get_input_keyboard())
            return
        if text == BTN_REMOVE_QTY:
            context.user_data["state"] = STATE_ADMIN_REM_SEL
            msg = "➖ إخراج علب" + chr(10) + chr(10) + "أرسل اسم السلعة:"
            await update.message.reply_text(msg, reply_markup=get_input_keyboard())
            return
        if text == BTN_EDIT:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=get_admin_keyboard())
                return
            lines = ["\U0001f4e6 قائمة السلع" + chr(10)]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"])
            full = chr(10).join(lines)
            if len(full) <= 4096:
                await update.message.reply_text(full)
            else:
                chunks = []
                cur = []
                cur_len = 0
                for ln in lines:
                    if cur_len + len(ln) + 1 > 4000 and cur:
                        chunks.append(chr(10).join(cur))
                        cur = []
                        cur_len = 0
                    cur.append(ln)
                    cur_len += len(ln) + 1
                if cur:
                    chunks.append(chr(10).join(cur))
                for chunk in chunks:
                    await update.message.reply_text(chunk)
            context.user_data["state"] = STATE_EDIT_PRODUCT
            edit_prompt = ("\u270f\ufe0f تعديل سلعة" + chr(10) + chr(10)
                + "أرسل: رقم_السلعة, الاسم الجديد" + chr(10) + chr(10)
                + "مثال: 1, زيت 5 لتر")
            await update.message.reply_text(edit_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_DELETE:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=get_admin_keyboard())
                return
            lines = ["\U0001f4e6 قائمة السلع" + chr(10)]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"])
            full = chr(10).join(lines)
            if len(full) <= 4096:
                await update.message.reply_text(full)
            else:
                chunks = []
                cur = []
                cur_len = 0
                for ln in lines:
                    if cur_len + len(ln) + 1 > 4000 and cur:
                        chunks.append(chr(10).join(cur))
                        cur = []
                        cur_len = 0
                    cur.append(ln)
                    cur_len += len(ln) + 1
                if cur:
                    chunks.append(chr(10).join(cur))
                for chunk in chunks:
                    await update.message.reply_text(chunk)
            context.user_data["state"] = STATE_DELETE_PRODUCT
            del_prompt = ("\U0001f5d1\ufe0f حذف سلعة" + chr(10) + chr(10)
                + "أرسل رقم السلعة فقط:" + chr(10)
                + "مثال: 5" + chr(10) + chr(10)
                + "\u26a0\ufe0f سيتم حذف السلعة وجميع سجلاتها نهائياً!")
            await update.message.reply_text(del_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_ALERT:
            context.user_data["state"] = STATE_ALERT_MSG
            alert_prompt = ("\U0001f514 أرسل نص التنبيه:" + chr(10) + chr(10)
                + "سيتم إرساله لجميع المستخدمين.")
            await update.message.reply_text(alert_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_UPDATE:
            await _broadcast_update_notification(update, context)
            return
        if text == BTN_LOW_STOCK:
            await send_low_stock_list(update, context)
            return
        if text == BTN_MOVEMENTS:
            await _show_all_movements(update, context, admin_view=True)
            return
        if text == BTN_INCOMING or text.startswith(BTN_INCOMING):
            await _show_admin_incoming_orders(update, context)
            return

        if text == BTN_USERS or text.startswith(BTN_USERS):
            await users_command(update, context)
            return
        if text == BTN_BACKUP:
            await backup_command(update, context)
            return
        if text == BTN_CANCEL:
            await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=get_admin_keyboard())
            return
        await update.message.reply_text(
            "استخدم الأزرار أدناه أو الأوامر:" + chr(10) + "/add, /search",
            reply_markup=get_admin_keyboard())
        return

    # State handlers
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=get_admin_keyboard())
        return
    if state == STATE_ADD_NAME:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.", reply_markup=get_input_keyboard())
            return
        await ask_add_qty(update, context, product_name, is_motif=False)
    elif state == STATE_ADD_NAME_MOTIF:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("motif"):
            product_name = "motif " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
    elif state == STATE_ADD_NAME_12060:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("120/60") and not product_name.lower().startswith("120x60"):
            product_name = "120/60 " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
    elif state == STATE_ADD_NAME_PLANTE:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("plante"):
            product_name = "plante " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
    elif state == STATE_ADD_QTY:
        qty = parse_crt_input(text)
        if qty is None:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0639\u062f\u062f \u0627\u0644\u0639\u0644\u0628.\n\u0645\u062b\u0627\u0644: 10 \u0623\u0648 10crt", reply_markup=get_input_keyboard())
            return
        product_name = context.user_data.get("add_name", "")
        is_motif = context.user_data.get("add_is_motif", False)
        await confirm_add_product(update, context, product_name, qty, is_motif=is_motif)
    elif state == STATE_EXISTING_ADD_QTY:
        await process_add_quantity(update, context, text, user.id)
    elif state == STATE_REMOVE_QTY:
        await process_remove_quantity(update, context, text, user.id)
    elif state == STATE_EDIT_PRODUCT:
        await process_edit_product(update, context, text, user.id)
    elif state == STATE_DELETE_PRODUCT:
        await process_delete_product(update, context, text, user.id)
    elif state == STATE_ADMIN_ADD_SEL:
        results = search_products(text)
        if not results:
            await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c.", reply_markup=get_input_keyboard())
            return
        if len(results) == 1:
            await _show_product_detail(update, context, results[0])
        else:
            header = "\U0001f50d " + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629:\n"
            btns = []
            txt = [header]
            for p in results:
                txt.append(str(p["id"]) + ". " + p["name"] + " — " + str(p["quantity"]) + " " + _get_unit_code(p["name"]))
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                btns.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            txt.append("\u2b06\ufe0f \u0627\u062e\u062a\u0631 \u0627\u0644\u0633\u0644\u0639\u0629.")
            await update.message.reply_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(btns))
        context.user_data["state"] = None
        return
    elif state == STATE_ADMIN_REM_SEL:
        results = search_products(text)
        if not results:
            await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c.", reply_markup=get_input_keyboard())
            return
        if len(results) == 1:
            await _show_product_detail(update, context, results[0])
        else:
            header = "\U0001f50d " + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629:\n"
            btns = []
            txt = [header]
            for p in results:
                txt.append(str(p["id"]) + ". " + p["name"] + " — " + str(p["quantity"]) + " " + _get_unit_code(p["name"]))
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                btns.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            txt.append("\u2b06\ufe0f \u0627\u062e\u062a\u0631 \u0627\u0644\u0633\u0644\u0639\u0629.")
            await update.message.reply_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(btns))
        context.user_data["state"] = None
        return
    elif state == "waiting_pact_qty":
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_admin_keyboard())
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا.\nمثال: 10 أو 10" + unit, reply_markup=get_input_keyboard())
            return
        qty = parsed
        try:
            updated = db_update_quantity(product["id"], qty, action)
            db_user = get_user(user.id)
            if db_user:
                log_transaction(product["id"], action, qty, db_user["id"])
            icon = _product_status_icon(updated)
            unit = _get_unit_code(updated["name"])
            unit_ar = _get_unit_ar(updated["name"])
            if action == "add":
                msg = "\u2705 \u062a\u0645\u062a \u0627\u0644\u0625\u0636\u0627\u0641\u0629!\n\n" + updated["name"] + "\n\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            else:
                msg = "\u2705 \u062a\u0645 \u0625\u062e\u0631\u0627\u062c!\n\n" + updated["name"] + "\n\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            pid = str(updated["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2795 \u0625\u0636\u0627\u0641\u0629", callback_data="pact_add_" + pid),
                 InlineKeyboardButton("\u2796 \u0625\u062e\u0631\u0627\u062c", callback_data="pact_rem_" + pid)],
                [InlineKeyboardButton("\U0001f4ca \u0627\u0644\u062d\u0631\u0643\u0629", callback_data="pact_log_" + pid)],
                [InlineKeyboardButton("\u2705 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="admin_done")],
            ])
            context.user_data["cust_product"] = dict(updated)
            await update.message.reply_text(msg, reply_markup=keyboard)
            await send_low_stock_alerts(context.bot)
            # After add: ask reason for notification
            if action == "add" and db_user:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f69a \u0648\u0635\u0648\u0644 \u0628\u0636\u0627\u0639\u0629 \u0645\u0646 \u0627\u0644\u0634\u0627\u062d\u0646\u0629", callback_data="addnotify_truck"),
                     InlineKeyboardButton("\u21a9\ufe0f \u0625\u0631\u062c\u0627\u0639 \u0633\u0644\u0639\u0629 \u0645\u0646 \u0632\u0628\u0648\u0646", callback_data="addnotify_return")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user}
                await update.message.reply_text("\U0001f4e8 \u0645\u0627 \u0633\u0628\u0628 \u0627\u0644\u0625\u0636\u0627\u0641\u0629\u061f", reply_markup=notify_kb)
        except ValueError as e:
            await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ: " + str(e), reply_markup=get_input_keyboard())
        return

    elif state == STATE_SEARCH_ADMIN:
        await process_search(update, context, text, admin_view=True)
    elif state == STATE_ALERT_MSG:
        alert_text = text.strip()
        if not alert_text:
            await update.message.reply_text("❌ أرسل نص التنبيه.", reply_markup=get_input_keyboard())
            return
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'customer')")
                    users = [r[0] for r in cur.fetchall()]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)
            sent = 0
            for uid in users:
                try:
                    alert_msg = "🔔 تنبيه من الإدارة:"
                    alert_msg = alert_msg + chr(10) + chr(10) + alert_text
                    await context.bot.send_message(chat_id=uid, text=alert_msg)
                    sent += 1
                except Exception as e:
                    logging.warning("[ALERT] Failed for " + str(uid) + ": " + str(e))
            result_msg = "✅ تم إرسال التنبيه لـ " + str(sent) + " مستخدم."
            await update.message.reply_text(result_msg, reply_markup=get_admin_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ خطأ: " + str(e), reply_markup=get_input_keyboard())
        context.user_data.clear()
        return

    elif state == "waiting_update_msg":
        update_text = text.strip()
        if not update_text:
            await update.message.reply_text("❌ أرسل وصف التحديث.", reply_markup=get_input_keyboard())
            return
        await _send_update_broadcast(update, context)
        return
    
    else:
        context.user_data.clear()
        await update.message.reply_text("\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647.", reply_markup=get_admin_keyboard())


async def handle_customer_message(update, context):
    text = update.message.text.strip()
    state = context.user_data.get("state")
    user = update.effective_user

    # Handle customer buttons (clear state first)
    cust_buttons = [BTN_CUST_ALERT, BTN_CUST_ADD, BTN_CUST_REMOVE, BTN_CUST_SEARCH, BTN_CUST_MOVEMENTS, BTN_CUST_CANCEL, BTN_CUST_ORDER, BTN_CUST_MY_ORDERS]
    if text in cust_buttons:
        context.user_data.clear()
        state = None

        if text == BTN_CUST_CANCEL:
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return

        if text == BTN_CUST_ALERT:
            await send_low_stock_list(update, context)
            return

        if text == BTN_CUST_ORDER:
            context.user_data["state"] = "waiting_order_photo"
            await update.message.reply_text(
                "📦 طلب توريد جديد\n\n"
                "📷 صوّر الوصل وأرسله مباشرة",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_CUST_MY_ORDERS:
            await _show_customer_orders(update, context)
            return

        if text in (BTN_CUST_ADD, BTN_CUST_REMOVE, BTN_CUST_SEARCH):
            context.user_data["state"] = "waiting_cust_search"
            await update.message.reply_text("🔍 اكتب اسم السلعة:")
            return

        if text == BTN_CUST_MOVEMENTS:
            await _show_all_movements(update, context, admin_view=False)
            return

    # State: waiting for product name from button
    if state == "waiting_cust_search":
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return
        await process_search(update, context, text, admin_view=False)
        context.user_data.clear()
        return

    # State: waiting_order_photo - صورة وصل فقط
    if state in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return
        await update.message.reply_text(
            "📷 يرجى إرسال صورة الوصل فقط.\n"
            "أو اضغط ❌ إلغاء.",
            reply_markup=get_input_keyboard(),
        )
        return

    # State: waiting_pact_qty
    if state == "waiting_pact_qty":
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا." + chr(10) + "مثال: 10 أو 10" + unit)
            return
        qty = parsed
        try:
            updated = db_update_quantity(product["id"], qty, action)
            db_user = get_user(user.id)
            if db_user:
                log_transaction(product["id"], action, qty, db_user["id"])
            icon = _product_status_icon(updated)
            unit = _get_unit_code(updated["name"])
            unit_ar = _get_unit_ar(updated["name"])
            if action == "add":
                msg = "✅ تمت الإضافة!" + chr(10) + chr(10) + updated["name"] + chr(10) + "📦 الكمية: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            else:
                msg = "✅ تم إخراج!" + chr(10) + chr(10) + updated["name"] + chr(10) + "📦 الكمية: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            pid = str(updated["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
                 InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid)],
            ])
            context.user_data["cust_product"] = dict(updated)
            await update.message.reply_text(msg, reply_markup=keyboard)
            await send_low_stock_alerts(context.bot)
            # After add: ask reason for notification
            if action == "add" and db_user:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚚 وصول بضاعة من الشاحنة", callback_data="addnotify_truck"),
                     InlineKeyboardButton("↩️ إرجاع سلعة من زبون", callback_data="addnotify_return")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user}
                await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
                return  # Don't clear user_data yet — callback needs pending_add_notify
        except ValueError as e:
            await update.message.reply_text("❌ " + str(e))
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ: " + str(e))
        context.user_data.clear()
        return

    # State: waiting_extract_qty
    if state == "waiting_extract_qty":
        product = context.user_data.get("cust_product")
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            await update.message.reply_text("❌ أرسل عددا صحيحا.")
            return
        qty = parsed
        if qty <= 0:
            await update.message.reply_text("❌ الكمية يجب أن تكون أكبر من 0.")
            return
        db_user = get_user(user.id)
        if not db_user:
            await update.message.reply_text("❌ حسابك غير مسجل.")
            return
        try:
            req = create_extraction_request(product["id"], qty, db_user["id"])
            unit_ar = _get_unit_ar(product["name"])
            msg = "📨 تم إرسال طلب الإخراج!" + chr(10) + chr(10) + "📦 " + product["name"] + chr(10) + "📦 الكمية: " + str(qty) + " " + unit_ar + chr(10) + chr(10) + "⏳ بانتظار موافقة المدير."
            await update.message.reply_text(msg)
            for admin_id in ADMIN_IDS:
                try:
                    requester_name = user.first_name or user.username or str(user.id)
                    unit = _get_unit_code(product["name"])
                    admin_msg = "🔔 طلب إخراج جديد:" + chr(10) + chr(10) + "👤 " + requester_name + chr(10) + "📦 " + product["name"] + chr(10) + "📦 الكمية: " + str(qty) + " " + unit + chr(10) + chr(10) + "موافقة على إلاخراج هذا الطلب؟"
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ موافقة", callback_data="ext_approve_" + str(req["id"])),
                         InlineKeyboardButton("❌ رفض", callback_data="ext_reject_" + str(req["id"]))],
                    ])
                    await context.bot.send_message(chat_id=admin_id, text=admin_msg, reply_markup=kb)
                except Exception as e:
                    logging.error("[EXTRACT] Notify admin failed: " + str(e))
        except Exception as e:
            logging.error("[EXTRACT] Request failed: " + str(e))
            await update.message.reply_text("❌ حدث خطأ: " + str(e))
        context.user_data.clear()
        return

    # Default: treat as search query
    if not text:
        await update.message.reply_text("أرسل اسم السلعة لمعرفة إذا كانت متوفرة.")
        return
    await process_search(update, context, text, admin_view=False)


async def route_message(update, context):
    user = update.effective_user
    text = update.message.text.strip()
    if not is_admin(user.id) and not is_approved(user.id):
        if text == "/cancel":
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        elif text != "/start":
            await update.message.reply_text("\u23f3 \u062d\u0633\u0627\u0628\u0643 \u0642\u064a\u062f \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629.\n\u064a\u062c\u0628 \u0627\u0644\u0627\u0646\u062a\u0638\u0627\u0631 \u062d\u062a\u0649 \u064a\u0641\u0639\u0644\u0643 \u0627\u0644\u0645\u062f\u064a\u0631.")
        return
    if text == "/cancel":
        context.user_data.clear()
        if is_admin(user.id):
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
        else:
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        return
    register_user(telegram_id=user.id, username=user.username, first_name=user.first_name)
    if is_admin(user.id):
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ═══════════════════════════════════════════════════════════
#  EXTRACTION PERMISSION & REQUEST HANDLERS
# ═══════════════════════════════════════════════════════════

async def callback_extract_perm_handler(update, context):
    """Handle grant/revoke extraction permission from users list."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data
    try:
        if data.startswith("ext_grant_"):
            tid = int(data.split("_")[2])
            set_extract_permission(tid, True)
            db_u = get_user(tid)
            uname = db_u.get("username") or str(tid) if db_u else str(tid)
            await query.edit_message_text("✅ تم منح صلاحية الإخراج لـ " + uname)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="\🛑 تم منحك صلاحية طلب إخراج السلع.\nابحث عن سلعة واضغط طلب إخراج.",
                )
            except Exception as e:
                logging.error("[EXTRACT] Notify user failed: " + str(e))
        elif data.startswith("ext_revoke_"):
            tid = int(data.split("_")[2])
            set_extract_permission(tid, False)
            db_u = get_user(tid)
            uname = db_u.get("username") or str(tid) if db_u else str(tid)
            await query.edit_message_text("❌ تم سحب صلاحية الإخراج من " + uname)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="❌ تم سحب صلاحية طلب إخراج السلع.",
                )
            except Exception as e:
                logging.error("[EXTRACT] Notify user failed: " + str(e))
        elif data == "ext_pending_list":
            await _show_pending_extractions(update, context)
    except Exception as e:
        logging.error("[EXTRACT_PERM] Error: " + str(e))
        try:
            await query.edit_message_text("❌ خطأ: " + str(e))
        except Exception:
            pass

async def _show_pending_extractions(update, context):
    """Show list of pending extraction requests."""
    query = update.callback_query
    try:
        pending = get_pending_extraction_requests()
    except Exception as e:
        logging.error("[EXTRACT] Get pending failed: " + str(e))
        await query.edit_message_text("❌ خطأ في تحميل الطلبات.")
        return
    if not pending:
        await query.edit_message_text("✅ لا توجد طلبات إخراج معلقة.")
        return
    txt_lines = ["🔔 طلبات الإخراج المعلقة (" + str(len(pending)) + "):"]
    buttons = []
    for req in pending:
        req_name = req.get("username") or str(req.get("requester_tid", "?"))
        pname = req.get("product_name", "?")
        unit = _get_unit_code(pname)
        date_str = req["created_at"].strftime("%m/%d %H:%M")
        txt_lines.append(
            "👤 " + req_name
            + " | 📦 " + pname
            + " | " + str(req["quantity"]) + " " + unit
            + " | " + date_str
        )
        label = req_name + ": " + pname + " (" + str(req["quantity"]) + " " + unit + ")"
        if len(label) > 35:
            label = label[:32] + "..."
        buttons.append([
            InlineKeyboardButton("✅ " + label, callback_data="ext_approve_" + str(req["id"])),
            InlineKeyboardButton("❌", callback_data="ext_reject_" + str(req["id"])),
        ])
    txt_lines.append("")
    txt_lines.append("✅ موافقة | ❌ رفض")
    try:
        await query.edit_message_text("\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logging.error("[EXTRACT] Edit failed: " + str(e))
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\n".join(txt_lines), reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            pass

async def callback_extract_request_handler(update, context):
    """Handle member click on request extract button."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not can_extract(user_id):
        await query.answer("❌ ليس لديك صلاحية الإخراج.", show_alert=True)
        return
    data = query.data
    try:
        product_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, quantity FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    if not row:
        await query.edit_message_text("❌ المنتج غير موجود.")
        return
    product = dict(row)
    context.user_data["cust_product"] = product
    context.user_data["state"] = "waiting_extract_qty"
    unit = _get_unit_code(product["name"])
    unit_ar = _get_unit_ar(product["name"])
    text = (
        "📦 " + product["name"]
        + " (المتوفر: " + str(product["quantity"]) + " " + unit + ")"
        + "\n➖ طلب إخراج " + unit_ar
        + "\nأرسل العدد (" + unit + "):"
    )
    try:
        await query.edit_message_text(text)
    except Exception:
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)

async def callback_extract_approve_handler(update, context):
    """Handle admin approve/reject extraction request."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data
    parts = data.split("_")
    action = parts[1]
    try:
        request_id = int(parts[2])
    except (IndexError, ValueError):
        return
    admin_db = get_user(query.from_user.id)
    admin_uid = admin_db["id"] if admin_db else 0
    try:
        success, msg = process_extraction_request(request_id, action == "approve", admin_uid)
        await query.edit_message_text(msg)
        if success:
            try:
                conn = get_connection()
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT er.requester_id, u.telegram_id, p.name AS product_name, er.quantity "
                        "FROM extraction_requests er "
                        "JOIN users u ON u.id = er.requester_id "
                        "JOIN products p ON p.id = er.product_id "
                        "WHERE er.id = %s",
                        (request_id,),
                    )
                    req_info = cur.fetchone()
                conn.commit()
                release_connection(conn)
                if req_info:
                    unit_ar = _get_unit_ar(req_info["product_name"])
                    if action == "approve":
                        notify_text = (
                            "\✅ تم الموافقة على طلبك!\n\n"
                            + "📦 " + req_info["product_name"] + "\n"
                            + "📦 الكمية: " + str(req_info["quantity"]) + " " + unit_ar
                        )
                    else:
                        notify_text = "❌ تم رفض طلب الإخراج."
                    await context.bot.send_message(chat_id=req_info["telegram_id"], text=notify_text)
            except Exception as e:
                logging.error("[EXTRACT] Notify requester failed: " + str(e))
            if action == "approve":
                await send_low_stock_alerts(context.bot)
    except Exception as e:
        logging.error("[EXTRACT_APPROVE] Error: " + str(e))
        try:
            await query.edit_message_text("❌ خطأ: " + str(e))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════

async def error_handler(update, context):
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logging.warning("[BOT] Conflict detected - another instance running. Will recover automatically.")
        return
    logging.error("[BOT ERROR] %s: %s", type(context.error).__name__, context.error, exc_info=True)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ حدث خطأ حاول مرة أخرى.")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#  BUILD APP
# ═══════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


async def callback_admin_done_handler(update, context):
    query = update.callback_query
    await query.answer()
    try:
        await query.delete_message()
    except Exception:
        pass
    await context.bot.send_message(chat_id=query.message.chat_id, text="✅ القائمة الرئيسية:", reply_markup=get_admin_keyboard())


async def callback_go_start_handler(update, context):
    """ينفّذ /start عند ضغط المستخدم على زر التحديث."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    sync_user_info(user)

    db_user = get_user(user.id)
    name = user.first_name or ""

    if is_admin(user.id):
        try:
            await query.edit_message_text(
                "\U0001f44b مرحباً <b>" + name + "</b>\n\n"
                "\U0001f3e2 أنت مدير النظام.\n"
                "\U0001f4cb استخدم الأزرار أدناه لإدارة المخزون.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_keyboard(),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=get_admin_keyboard())
    elif db_user and db_user.get("role") == "pending":
        try:
            await query.edit_message_text(
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "⏳ حسابك قيد المراجعة.\n"
                "يجب الانتظار حتى يفعلك المدير.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        try:
            await query.edit_message_text(
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "⌛ بحث عن المنتجات المتاحة\n"
                "📁 استخدم الأزرار أدناه.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_customer_keyboard(),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=get_customer_keyboard())


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS DISPLAY & CALLBACKS
# ═══════════════════════════════════════════════════════════

async def _show_admin_incoming_orders(update, context):
    """عرض طلبات التوريد - كل طلب برسالة منفصلة: صورة + أزرار جاهز/غير جاهز."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\
                SELECT io.*, u.first_name, u.username, u.telegram_id
                FROM incoming_orders io
                LEFT JOIN users u ON u.id = io.requester_id
                WHERE io.status IN ('pending', 'received')
                ORDER BY io.created_at ASC
                """)
            all_orders = [dict(r) for r in cur.fetchall()]
        conn.commit()
    except Exception:
        conn.rollback()
        all_orders = []
    finally:
        release_connection(conn)

    if not all_orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=get_admin_keyboard())
        return

    bot = context.bot
    for o in all_orders:
        order_id = o["id"]
        req_name = o.get("first_name") or o.get("username") or "مستخدم"
        caption = (
            "🚚 طلب #" + str(order_id) +
            "  |  👤 " + req_name +
            "  |  📦 " + o["product_name"]
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ جاهز", callback_data="io_ready_" + str(order_id)),
                InlineKeyboardButton("⏳ غير جاهز", callback_data="io_notready_" + str(order_id)),
            ]
        ])
        if o.get("photo_file_id"):
            try:
                await bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=o["photo_file_id"],
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception:
                await update.message.reply_text(caption, reply_markup=keyboard)
        else:
            await update.message.reply_text(caption + "\n\n⚠️ بدون صورة وصل", reply_markup=keyboard)

    await update.message.reply_text("📤 عدد الطلبات: " + str(len(all_orders)), reply_markup=get_admin_keyboard())


async def _show_customer_orders(update, context):
    """عرض طلبات التوريد المعلّقة + قيد التجهيز للمستخدم."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        return
    orders = get_user_pending_orders(db_user["id"])
    if not orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=get_customer_keyboard())
        return

    lines = ["📋 طلباتك (" + str(len(orders)) + ")\n"]
    for o in orders:
        created = o["created_at"].strftime("%H:%M %d/%m") if o.get("created_at") else ""
        status_txt = "⏳ قيد التوريد" if o["status"] == "pending" else "🔧 جاري التجهيز"
        photo_icon = "  📷" if o.get("photo_file_id") else ""
        lines.append(
            "📋 #" + str(o["id"]) +
            "  |  📦 " + o["product_name"] +
            "  |  🔢 " + str(o["quantity"]) +
            "  |  " + status_txt +
            "  |  ⏰ " + created + photo_icon
        )
    lines.append("\n📡 سيتم إشعارك تلقائياً بأي تحديث.")
    await update.message.reply_text("\n".join(lines), reply_markup=get_customer_keyboard())


async def handle_receipt_photo(update, context):
    """معالجة صورة الوصل - إنشاء طلب جديد مباشرة."""
    user = update.effective_user
    state = context.user_data.get("state")
    photo = update.message.photo
    if not photo:
        return
    file_id = photo[-1].file_id
    req_name = user.first_name or user.username or str(user.id)

    if state not in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        return

    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        context.user_data.clear()
        return
    try:
        order = create_incoming_order(db_user["id"], "طلب بوصل صورة", 1, photo_file_id=file_id)
        order_id = order["id"]
        caption = (
            "🚚 طلب توريد جديد!\n\n"
            "👤 من: <b>" + req_name + "</b>\n"
            "📋 رقم الطلب: <b>#" + str(order_id) + "</b>"
        )
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await update.message.reply_text(
            "✅ تم تسجيل طلب التوريد!\n\n"
            "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
            "⏳ الحالة: <b>قيد التوريد</b>\n\n"
            "📡 سيتم إشعارك تلقائياً عند التحديث.\n"
            "تابع طلبك من 📋 طلباتي",
            parse_mode=ParseMode.HTML,
            reply_markup=get_customer_keyboard(),
        )
        logging.info("[INCOMING] Photo order #" + str(order_id) + " by user " + str(user.id))
    except Exception as e:
        logging.error("[INCOMING] Failed: " + str(e))
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
    context.user_data.clear()


async def callback_upd_handler(update, context):
    """Handle update notification choice: predefined option or custom text."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # Custom text option → ask for input
    if data == "upd_custom":
        context.user_data["state"] = "waiting_update_msg"
        await query.edit_message_text(
            "\U0001f4e2 أرسل وصف التحديث:\n\n"
            "أو أرسل /cancel للإلغاء.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Predefined option → broadcast directly
    if data.startswith("upd_q_"):
        idx = int(data.split("_")[-1])
        options = context.user_data.get("update_options", [])
        if idx < len(options):
            update_text = options[idx]
        else:
            await query.edit_message_text("\u274c \u062e\u064a\u0627\u0631 \u063a\u064a\u0631 \u0645\u0648\u062c\u0648\u062f.")
            return
    else:
        return

    # Broadcast the chosen text
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'customer')")
            rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        rows = []
    finally:
        release_connection(conn)

    if not rows:
        await query.edit_message_text("\U0001f4ed \u0644\u0627 \u064a\u0648\u062c\u062f \u0645\u0633\u062a\u062e\u062f\u0645\u064a\u0646 \u0645\u0641\u0639\u0644\u064a\u0646.")
        return

    broadcast_text = (
        "\U0001f195 <b>\u062a\u062d\u062f\u064a\u062b \u062c\u062f\u064a\u062f!</b>\n\n"
        + update_text + "\n\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        "\n\u26a1 \u062a\u0645 \u0627\u0644\u062a\u0637\u0648\u064a\u0631 \u0628\u0648\u0627\u0633\u0637\u0629 <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001f504 \u062a\u062d\u062f\u064a\u062b \u0627\u0644\u0648\u0627\u062c\u0647\u0629", callback_data="go_start")]
    ])
    sent = 0
    for row in rows:
        uid = row[0]
        try:
            await context.bot.send_message(
                chat_id=uid, text=broadcast_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            pass

    await query.edit_message_text(
        "\u2705 \u062a\u0645 \u0625\u0631\u0633\u0627\u0644 \u0625\u0634\u0639\u0627\u0631 \u0627\u0644\u062a\u062d\u062f\u064a\u062b \u0625\u0644\u0649 <b>" + str(sent) + "</b> \u0645\u0633\u062a\u062e\u062f\u0645.",
        parse_mode=ParseMode.HTML,
    )
    # Auto-return to admin keyboard
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\U0001f3e2 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:",
        reply_markup=get_admin_keyboard(),
    )
    logging.info("[UPDATE] Broadcast sent to " + str(sent) + " users: " + update_text[:80])
    context.user_data.pop("update_options", None)


async def callback_incoming_order_handler(update, context):
    """معالجة أزرار المدير: جاهز / غير جاهز."""
    query = update.callback_query
    await query.answer()
    data = query.data
    bot = context.bot

    # ── 1. طلب جاهز في المخزن ──
    if data.startswith("io_ready_"):
        order_id = int(data.split("_")[-1])
        order = complete_incoming_order(order_id)
        if not order:
            try:
                await query.edit_message_caption(caption="❌ الطلب غير موجود.")
            except Exception:
                try:
                    await query.edit_message_text("❌ الطلب غير موجود.")
                except Exception:
                    pass
            return

        tg_id = _get_telegram_id(order["requester_id"])
        if tg_id:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="✅ طلبك جاهز في المخزن الآن!\n\n"
                         "📦 السلعة: <b>" + order["product_name"] + "</b>\n"
                         "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
                         "🏃 تقدر تيجان تأخذه!",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        # Edit caption (photo) or text depending on message type
        try:
            await query.edit_message_caption(
                caption="✅ تم تأكيد - الطلب #" + str(order_id) + " جاهز في المخزن\n"
                "📦 " + order["product_name"] +
                "\n\n📱 تم إشعار صاحب الطلب.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            try:
                await query.edit_message_text(
                    "✅ تم تأكيد - الطلب #" + str(order_id) + " جاهز في المخزن\n"
                    "📦 " + order["product_name"] +
                    "\n\n📱 تم إشعار صاحب الطلب."
                )
            except Exception:
                pass
        logging.info("[INCOMING] Ready order #" + str(order_id))
        return

    # ── 2. طلب ليس جاهز بعد ──
    if data.startswith("io_notready_"):
        order_id = int(data.split("_")[-1])

        tg_id = None
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT io.*, u.telegram_id FROM incoming_orders io LEFT JOIN users u ON u.id = io.requester_id WHERE io.id = %s", (order_id,))
                order = cur.fetchone()
                if order:
                    tg_id = order.get("telegram_id")
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)

        if tg_id:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="⏳ طلبك ليس جاهزاً بعد.\n\n"
                         "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
                         "سيتم إشعارك فور الجاهزية.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        if order:
            try:
                await query.edit_message_caption(
                    caption="⏳ الطلب #" + str(order_id) + " ليس جاهزاً بعد.\n"
                    "📦 " + dict(order).get("product_name", "") +
                    "\n\n📱 تم إشعار المستخدم.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                try:
                    await query.edit_message_text(
                        "⏳ الطلب #" + str(order_id) + " ليس جاهزاً بعد.\n"
                        "📦 " + dict(order).get("product_name", "") +
                        "\n\n📱 تم إشعار المستخدم."
                    )
                except Exception:
                    pass
        else:
            try:
                await query.edit_message_caption(caption="❌ الطلب غير موجود.")
            except Exception:
                try:
                    await query.edit_message_text("❌ الطلب غير موجود.")
                except Exception:
                    pass
        logging.info("[INCOMING] Not ready - order #" + str(order_id))
        return

def _get_telegram_id(user_db_id):
    """جلب telegram_id من user_db_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE id = %s", (user_db_id,))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def build_application():
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Middleware: تحديث بيانات المستخدم تلقائياً عند أي تفاعل ──
    async def auto_sync_user(update, context):
        user = update.effective_user
        if user:
            sync_user_info(user)

    # يشغل sync_user_info قبل أي handler آخر
    app.add_handler(MessageHandler(filters.ALL, auto_sync_user), group=-1)
    app.add_handler(CallbackQueryHandler(auto_sync_user, block=False), group=-1)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(callback_addtype_handler, pattern="^addtype_"))
    app.add_handler(CallbackQueryHandler(callback_add_handler, pattern="^add_"))
    app.add_handler(CallbackQueryHandler(callback_approval_handler, pattern="^(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(callback_promote_handler, pattern="^promote_"))
    app.add_handler(CallbackQueryHandler(callback_promote_handler, pattern="^demote_"))
    # Restore handlers - specific patterns first, then generic
    app.add_handler(CallbackQueryHandler(callback_restore_from_email_handler, pattern="^restore_from_email$"))
    app.add_handler(CallbackQueryHandler(callback_add_notify_handler, pattern="^addnotify_"))
    app.add_handler(CallbackQueryHandler(callback_restore_last_handler, pattern="^restore_last$"))
    app.add_handler(CallbackQueryHandler(callback_restore_last_confirm_handler, pattern="^last_restore_"))
    app.add_handler(CallbackQueryHandler(callback_email_restore_handler, pattern="^email_restore_"))
    app.add_handler(CallbackQueryHandler(callback_restore_handler, pattern="^restore_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(callback_admin_done_handler, pattern="^admin_done$"))
    app.add_handler(CallbackQueryHandler(callback_product_select_handler, pattern="^psel_"))
    app.add_handler(CallbackQueryHandler(callback_product_action_handler, pattern="^pact_"))
    app.add_handler(CallbackQueryHandler(callback_extract_perm_handler, pattern="^ext_grant_"))
    app.add_handler(CallbackQueryHandler(callback_extract_perm_handler, pattern="^ext_revoke_"))
    app.add_handler(CallbackQueryHandler(callback_extract_perm_handler, pattern="^ext_pending_list$"))
    app.add_handler(CallbackQueryHandler(callback_extract_request_handler, pattern="^req_ext_"))
    app.add_handler(CallbackQueryHandler(callback_extract_approve_handler, pattern="^ext_approve_"))
    app.add_handler(CallbackQueryHandler(callback_extract_approve_handler, pattern="^ext_reject_"))
    app.add_handler(CallbackQueryHandler(callback_incoming_order_handler, pattern="^io_ready_"))
    app.add_handler(CallbackQueryHandler(callback_incoming_order_handler, pattern="^io_notready_"))
    app.add_handler(CallbackQueryHandler(callback_go_start_handler, pattern="^go_start$"))
    app.add_handler(CallbackQueryHandler(callback_mov_date_handler, pattern="^movd_"))
    app.add_handler(CallbackQueryHandler(callback_upd_handler, pattern="^upd_"))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_backup_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)
    return app


# ═══════════════════════════════════════════════════════════
#  DAILY AUTO-BACKUP
# ═══════════════════════════════════════════════════════════



def _fetch_latest_backup_from_email():
    """Fetch latest backup JSON from email via IMAP. Returns dict or None."""
    if not IMAP_HOST or not SMTP_EMAIL or not SMTP_PASSWORD:
        return None
    import imaplib
    import email as email_lib
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(SMTP_EMAIL, SMTP_PASSWORD)
        mail.select("INBOX")
        status, msg_ids = mail.search(None, '(SUBJECT "نسخة احتياطية")')
        if status != "OK" or not msg_ids[0]:
            mail.logout()
            return None
        id_list = msg_ids[0].split()
        if not id_list:
            mail.logout()
            return None
        latest_id = id_list[-1]
        status, msg_data = mail.fetch(latest_id, "(RFC822)")
        if status != "OK":
            mail.logout()
            return None
        raw_email = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw_email)
        json_data = None
        for part in msg.walk():
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                fname = part.get_filename()
                if fname and fname.endswith(".json"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        json_data = json.loads(payload.decode("utf-8"))
                        break
        mail.logout()
        return json_data
    except Exception as e:
        logging.error("[IMAP] Failed to fetch backup: " + str(e))
        return None

def _send_backup_email(backup_bytes, filename):
    if not SMTP_EMAIL or not SMTP_PASSWORD or not BACKUP_EMAIL:
        return False
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = BACKUP_EMAIL
        msg["Subject"] = "\U0001f4be \u0646\u0633\u062e\u0629 \u0627\u062d\u062a\u064a\u0627\u0637\u064a\u0629 - \u0627\u0644\u0645\u062e\u0632\u0646 " + datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        body = ("\u0645\u0631\u062d\u0628\u0627\n"
                + "\n"
                + "\u0647\u0630\u0627 \u0645\u0644\u0641 \u0627\u0644\u0646\u0633\u062e \u0627\u0644\u0627\u062d\u062a\u064a\u0627\u0637\u064a \u0627\u0644\u064a\u0648\u0645\u064a \u0644\u0644\u0645\u062e\u0632\u0646.\n"
                + "\u0627\u062d\u0641\u0638\u0647 \u0641\u064a \u0645\u0643\u0627\u0646 \u0622\u0645\u0646.\n"
                + "\n"
                + "\u0644\u0625\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a: \u0623\u0631\u0633\u0644 \u0647\u0630\u0627 \u0627\u0644\u0645\u0644\u0641 \u0644\u0644\u0628\u0648\u062a \u0641\u064a \u062a\u064a\u0644\u064a\u063a\u0631\u0627\u0645.")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        part = MIMEBase("application", "json")
        part.set_payload(backup_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, BACKUP_EMAIL, msg.as_string())
        logging.info("[BACKUP] Email sent to " + BACKUP_EMAIL)
        return True
    except Exception as e:
        logging.error("[BACKUP] Email failed: " + str(e))
        return False


async def _daily_backup(bot):
    global _last_backup_data
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    try:
        backup = db_export_backup()
        _last_backup_data = backup
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M")
        filename = "backup_" + now_str + ".json"
        sent = _send_backup_email(file_bytes, filename)
        if sent:
            logging.info("[BACKUP] Daily backup sent to email (" + today + ")")
        else:
            logging.warning("[BACKUP] Email not configured, backup saved in memory only")
    except Exception as e:
        logging.error("[BACKUP] Daily backup failed: " + str(e))


async def _daily_backup_loop(bot):
    last_backup = ""
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        today = now.strftime("%Y-%m-%d")
        # 5-minute window (23:00-23:04 UTC = midnight Algeria)
        if today != last_backup and 23 <= now.hour <= 23 and now.minute < 5:
            await _daily_backup(bot)
            last_backup = today
        await asyncio.sleep(60)


async def _keep_alive(app):
    """Ping the external Render URL + Neon DB every 3 min to prevent spin-down."""
    external_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else ""
    if not external_url:
        port = int(os.getenv("PORT", 10000))
        external_url = "http://127.0.0.1:" + str(port) + "/"
    ping_url = external_url.rstrip("/") + "/"
    logging.info("[KEEP-ALIVE] Target: " + ping_url + " + Neon DB ping")
    # Reuse one session instead of creating new each time
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    try:
        while True:
            try:
                # Ping Render health endpoint
                async with session.get(ping_url) as r:
                    if r.status != 200:
                        logging.warning("[KEEP-ALIVE] Render ping status " + str(r.status))
                # Ping Neon database to prevent auto-suspend (cold start)
                try:
                    conn = get_connection()
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1")
                    finally:
                        release_connection(conn)
                except Exception as db_err:
                    logging.warning("[KEEP-ALIVE] DB ping failed: " + str(db_err))
            except Exception as e:
                logging.warning("[KEEP-ALIVE] Ping failed: " + str(e))
            await asyncio.sleep(180)
    finally:
        await session.close()



if __name__ == "__main__":
    init_db()
    seed_sample_products()
    init_pool()
    _init_low_stock_ids()
    tg_app = build_application()
    render_port = int(os.getenv("PORT", 0))
    if WEBHOOK_URL:
        port = render_port or 10000
        webhook_path = "/webhook"
        url = WEBHOOK_URL + webhook_path
        print("[BOT] Webhook mode, port " + str(port) + ", url: " + url)
        webhook_app = web.Application()
        async def health(request):
            return web.Response(text='{"status":"ok"}', content_type="application/json")
        webhook_app.router.add_get("/health", health)
        webhook_app.router.add_get("/", health)
        tg_app.run_webhook(
            webhook_path=webhook_path, url=url, port=port,
            drop_pending_updates=True, webhook_app=webhook_app)
    elif render_port:
        health_app = web.Application()
        async def health(request):
            return web.Response(text='{"status":"ok"}', content_type="application/json")
        health_app.router.add_get("/health", health)
        health_app.router.add_get("/", health)
        async def on_startup(aio_app):
            await tg_app.initialize()
            await tg_app.start()
            try:
                await tg_app.bot.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass
            await tg_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)
            asyncio.create_task(_keep_alive(aio_app))
            asyncio.create_task(_daily_backup_loop(tg_app.bot))
            asyncio.create_task(_weekly_report_loop(tg_app.bot))
            print("[BOT] Weekly report scheduler enabled")
            print("[BOT] Daily auto-backup enabled (midnight Algeria time)")
            print("[BOT] Polling + health + keep-alive on port " + str(render_port))
        async def on_cleanup(aio_app):
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        health_app.on_startup.append(on_startup)
        health_app.on_cleanup.append(on_cleanup)
        web.run_app(health_app, host="0.0.0.0", port=render_port, print=None, access_log=None)
    else:
        print("[BOT] Local polling mode")
        tg_app.run_polling(drop_pending_updates=True)

