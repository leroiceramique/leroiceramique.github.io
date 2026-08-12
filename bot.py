import os
import re
import json
import traceback
import inspect
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
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# ═══════════════════════════════════════════════════════════════
#  SECTION 1: EXTRACTION PERMISSION & REQUEST (Database Layer)
#  Functions: can_extract, set_extract_permission,
#              create_extraction_request, get_pending_extraction_requests,
#              process_extraction_request
# ═══════════════════════════════════════════════════════════════

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
        logging.error("[DB] can_extract check failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════════
#  SECTION 2: ENVIRONMENT CONFIGURATION & GLOBAL VARIABLES
#  Bot token, database URL, admin IDs, email settings
# ═══════════════════════════════════════════════════════════════

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET:
    import hashlib
    WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]
    logging.warning("[SEC] WEBHOOK_SECRET not set — derived from BOT_TOKEN.")
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

# ═══════════════════════════════════════════════════════════════
#  SECTION 3: DATABASE CONNECTION POOL & SCHEMA
#  Functions: init_pool, get_connection, release_connection,
#              init_db, seed_sample_products
#  Constants: SCHEMA_SQL (table definitions)
# ═══════════════════════════════════════════════════════════════

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
                cur.execute("SET TIME ZONE 'Africa/Algiers'")
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
    conn = _pool.getconn()
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Africa/Algiers'")
    return conn


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
    date       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason     VARCHAR(200)
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


CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bot_settings (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id              SERIAL PRIMARY KEY,
    customer_name   VARCHAR(500) NOT NULL,
    worker_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    admin_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    status          VARCHAR(15) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id              SERIAL PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE CASCADE,
    product_name    VARCHAR(500) NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL DEFAULT 0,
    subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice ON invoice_items(invoice_id);
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
                -- Migration: add reason column to transactions table
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'transactions' AND column_name = 'reason') THEN
                        ALTER TABLE transactions ADD COLUMN reason VARCHAR(200);
                    END IF;
                END $$;
                -- Migration: create bot_settings table
            
CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
            -- Seed: insert bot updates if table is empty
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة طلبات التوريد مع خاصية تصوير الوصل' || chr(10) || chr(10) || '📦 يمكن للمستخدمين الآن إرسال طلبات توريد جديدة عن طريق تصوير وصل التوريد' || chr(10) || '📋 الطلب يظهر تلقائياً للمدير مع صورة الوصل' || chr(10) || '✅ المدير يضغط جاهز أو غير جاهز ويوصلك إشعار')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة زر الحركة بالتاريخ' || chr(10) || chr(10) || '📋 الآن زر الحركة يعرض آخر 7 أيام' || chr(10) || '📅 اضغط على أي تاريخ لعرض كل عمليات الإضافة والإخراج في ذلك اليوم')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة نظام طلبات التوريد' || chr(10) || chr(10) || '📦 زر جديد: طلب توريد للمستخدمين' || chr(10) || '📋 الطلب يظهر للمدير مع تفاصيل كاملة')
            ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS bot_settings (
                key         VARCHAR(100) PRIMARY KEY,
                value       TEXT
            );
            -- Migration: add selling_price to products
            DO $$ BEGIN
                ALTER TABLE products ADD COLUMN IF NOT EXISTS selling_price NUMERIC(12,2) NOT NULL DEFAULT 0;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: allow invoice_items.product_id to be NULL (for manual products)
            DO $$ BEGIN
                ALTER TABLE invoice_items ALTER COLUMN product_id DROP NOT NULL;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Seed: add invoice update notification
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة نظام الفواتير' || chr(10) || chr(10) || '🧾 زر جديد: إنشاء فاتورة' || chr(10) || '💰 يمكن للمدير إنشاء فاتورة عن بُعد' || chr(10) || '📎 توليد PDF احترافي تلقائياً' || chr(10) || '📨 إشعار تلقائي للعامل المختار')
            ON CONFLICT DO NOTHING;
            -- Migration: add normalized_name column to products for duplicate prevention
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'products' AND column_name = 'normalized_name') THEN
                    ALTER TABLE products ADD COLUMN normalized_name VARCHAR(500);
                END IF;
            END $$;
            -- Backfill normalized_name from existing names
            DO $$ BEGIN
                UPDATE products SET normalized_name = LOWER(TRIM(REGEXP_REPLACE(name, '\\s+', ' ', 'g')))
                WHERE normalized_name IS NULL OR normalized_name = '';
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Create unique index on normalized_name (skip NULLs) to prevent duplicates
            DO $$ BEGIN
                CREATE UNIQUE INDEX IF NOT EXISTS idx_products_normalized_name
                    ON products (normalized_name) WHERE normalized_name IS NOT NULL AND normalized_name != '';
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING '[DB] Could not create unique index on normalized_name (duplicates may exist). Run /dedup first.';
            END $$;
            -- Clean up NULL normalized_name values (should not happen after seed fix)
            DO $$ BEGIN
                UPDATE products SET normalized_name = LOWER(TRIM(REGEXP_REPLACE(name, '\\s+', ' ', 'g')))
                WHERE normalized_name IS NULL OR normalized_name = '';
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
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
    """Insert sample products if they don't exist (uses db_add_product for dedup)."""
    samples = [
        ("plante pcs", 0, "", 5),
        ("120/60pcs", 0, "", 5),
    ]
    for name, qty, loc, min_stk in samples:
        try:
            product, is_new = db_add_product(name=name, quantity=qty, location=loc, minimum_stock=min_stk)
            if is_new:
                print("[DB] Seeded sample product: " + name)
        except Exception as e:
            print("[DB] Sample seed failed for " + name + ": " + str(e))


# ═══════════════════════════════════════════════════════════════
#  SECTION 4: USER MANAGEMENT (Database Layer)
#  Functions: register_user, sync_user_info, get_user,
#              is_admin, is_approved, get_pending_users,
#              approve_user, reject_user
# ═══════════════════════════════════════════════════════════════

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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


def is_admin(telegram_id):
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    return user is not None and user["role"] == "admin"


def is_approved(telegram_id):
    """Check if user is approved (admin or customer, not pending)."""
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    return user is not None and user["role"] in ("admin", "customer")


# ═══════════════════════════════════════════════════════════════
#  SECTION 5: INCOMING ORDERS - SUPPLY REQUESTS (Database Layer)
#  Functions: create_incoming_order, get_pending_incoming_orders,
#              get_user_pending_orders, complete_incoming_order,
#              receive_incoming_order, complete_all_pending_orders
# ═══════════════════════════════════════════════════════════════

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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════════
#  SECTION 6: BACKUP & RESTORE (Database Layer)
#  Functions: db_export_backup, db_import_backup
# ═══════════════════════════════════════════════════════════════

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
            "date": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=1))).isoformat(),
            "products": products,
            "transactions": transactions,
            "users": users,
        }
    except Exception as e:
        conn.rollback()
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
                pname = p["name"]
                norm_name = _normalize_product_name(pname) if pname else ""
                cur.execute(
                    "INSERT INTO products (id, name, quantity, location, minimum_stock, created_at, normalized_name) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (p["id"], pname, p["quantity"], p.get("location", ""),
                     p.get("minimum_stock", 5), p.get("created_at"), norm_name))
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)

# ═══════════════════════════════════════════════════════════════
#  SECTION 7: PRODUCT CRUD & SEARCH ENGINE
#  Functions: db_add_product, db_update_product, db_update_quantity,
#              db_delete_product, get_low_stock_products, get_all_products
#  Search:    _normalize_text, _levenshtein_distance, _fuzzy_score,
#              search_products (ILIKE fast path + fuzzy slow path)
# ═══════════════════════════════════════════════════════════════

def _normalize_product_name(name):
    """Normalize product name for exact duplicate detection.
    - Strip leading/trailing whitespace
    - Collapse multiple spaces into one
    - Case-insensitive comparison (stored lowercase)
    Does NOT change the meaning — only used for duplicate checking."""
    if not name:
        return ""
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = name.lower()
    return name


def db_find_product_by_normalized(name):
    """Find a product by its normalized name. Returns product dict or None."""
    normalized = _normalize_product_name(name)
    if not normalized:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE normalized_name = %s LIMIT 1", (normalized,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def db_add_product(name, quantity=0, location="", minimum_stock=5, skip_duplicate_check=False):
    """Add a new product. If a product with the same normalized name exists, update its quantity instead.
    Returns (product_dict, is_new)."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("اسم المنتج فارغ.")
    normalized = _normalize_product_name(clean_name)

    # Check for existing product (unless explicitly skipped, e.g. for backup import)
    if not skip_duplicate_check:
        existing = db_find_product_by_normalized(clean_name)
        if existing:
            # Product already exists — update quantity instead of creating new
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "UPDATE products SET quantity = quantity + %s WHERE id = %s RETURNING *",
                        (quantity, existing["id"]))
                    updated = cur.fetchone()
                conn.commit()
                return dict(updated), False
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO products (name, quantity, location, minimum_stock, normalized_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """, (clean_name, quantity, location.strip(), minimum_stock, normalized))
            product = cur.fetchone()
        conn.commit()
        return dict(product), True
    except Exception as e:
        conn.rollback()
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
    """Smart search: ILIKE fast path, then fuzzy matching with typo tolerance.
    Normalized text, slash/x interchangeability, and accent handling."""
    if not query or not query.strip():
        return []

    # ── FAST PATH: ILIKE exact/partial match in database (no full scan) ──
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM products WHERE name ILIKE %s ORDER BY id LIMIT 10",
                ("%" + query.strip() + "%",),
            )
            ilike_rows = cur.fetchall()
        conn.commit()
        # If ILIKE found 3+ results, return them directly (fast, accurate)
        ilike_results = [dict(r) for r in ilike_rows]
        if len(ilike_results) >= 3:
            return ilike_results
    except Exception as e:
        conn.rollback()
        logging.error("[SEARCH] ILIKE fast path failed: " + str(e), exc_info=True)
    finally:
        release_connection(conn)

    # ── SLOW PATH: Fuzzy matching (full scan, typo tolerance) ──
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products ORDER BY id")
            all_rows = cur.fetchall()
        conn.commit()
        all_products = [dict(r) for r in all_rows]
    except Exception as e:
        conn.rollback()
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)
    if not query or not query.strip():
        return []
    query_tokens = _normalize_text(query).split()
    if not query_tokens:
        return []
    # If ILIKE returned some results (1-2), include them as baseline
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
        # Keep results that are at least 70% of the best score (minimum 20)
        threshold = max(20, best_score * 0.7)
        scored = [(s, p) for s, p in scored if s >= threshold]
    # If the query is very short (1-2 chars), only return very good matches
    if len(query.strip()) <= 2:
        scored = [(s, p) for s, p in scored if s >= 30]
    # Limit to top 10 results max
    scored = scored[:10]
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


def db_update_quantity(product_id, change, transaction_type):
    if transaction_type not in ("add", "remove"):
        raise ValueError("transaction_type must be 'add' or 'remove'")
    if change <= 0:
        raise ValueError("الكمية يجب أن تكون أكبر من صفر.")
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  TRANSACTION FUNCTIONS
# ═══════════════════════════════════════════════════════════

def log_transaction(product_id, transaction_type, quantity, user_id, reason=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO transactions (product_id, type, quantity, user_id, reason)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, product_id, type, quantity, user_id, date, reason
            """, (product_id, transaction_type, quantity, user_id, reason))
            tx = cur.fetchone()
        conn.commit()
        return dict(tx)
    except Exception as e:
        conn.rollback()
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)

# ═══════════════════════════════════════════════════════════════
#  SECTION 9: FSM STATE CONSTANTS & TIMEOUT
#  States: STATE_ADD_NAME, STATE_ADD_QTY, STATE_SEARCH_ADMIN, etc.
#  Timeout: _is_state_expired(), _set_state() (10 min auto-clear)
# ═══════════════════════════════════════════════════════════════

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
STATE_INVOICE_CUSTOMER = "waiting_invoice_customer"
STATE_INVOICE_PRODUCT = "waiting_invoice_product"
STATE_INVOICE_QTY = "waiting_invoice_qty"
STATE_INVOICE_PRICE = "waiting_invoice_price_m2"
STATE_INVOICE_AREA = "waiting_invoice_area"
STATE_INVOICE_REVIEW = "waiting_invoice_review"
STATE_REMOVE_REASON = "waiting_remove_reason"

# ── State timeout: 10 minutes (600 seconds) ──
_STATE_TIMEOUT_SECONDS = 600

def _is_state_expired(context):
    """Check if current state has been active longer than timeout. Returns True if expired."""
    set_at = context.user_data.get("state_set_at")
    if not set_at:
        return False
    elapsed = datetime.datetime.now(datetime.timezone.utc).timestamp() - set_at
    return elapsed > _STATE_TIMEOUT_SECONDS

def _set_state(context, state):
    """Set state and record timestamp for timeout tracking."""
    context.user_data["state"] = state
    context.user_data["state_set_at"] = datetime.datetime.now(datetime.timezone.utc).timestamp()


# ═══════════════════════════════════════════════════════════════
#  SYNC DB HELPERS (called via asyncio.to_thread from async handlers)
#  These encapsulate direct get_connection() blocks so async handlers
#  never touch the DB synchronously.
# ═══════════════════════════════════════════════════════════════

def _db_fetch_latest_update():
    """Fetch the latest bot_update description."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT description FROM bot_updates ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_broadcast_users(active_only=False):
    """Fetch telegram_ids of admin+customer users. If active_only, filter active=TRUE."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if active_only:
                cur.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'customer') AND active = TRUE")
            else:
                cur.execute("SELECT telegram_id FROM users WHERE role IN ('admin', 'customer')")
            rows = cur.fetchall()
        conn.commit()
        return [r[0] for r in rows]
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def _db_fetch_product_quantity(product_id):
    """Fetch current quantity for a single product. Returns int or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT quantity FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return row["quantity"] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_weekly_report_data():
    """Fetch all stats needed for the weekly report. Returns dict or None."""
    conn = get_connection()
    try:
        with conn.cursor(psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM products")
            total = cur.fetchone()["cnt"]
            cur.execute("SELECT COALESCE(SUM(quantity), 0) as total_qty FROM products")
            total_qty = cur.fetchone()["total_qty"]
            cur.execute("SELECT COUNT(*) as cnt FROM products WHERE quantity <= minimum_stock")
            low_count = cur.fetchone()["cnt"]
            cur.execute("SELECT name, quantity FROM products WHERE quantity > 0 ORDER BY quantity DESC LIMIT 5")
            top_products = cur.fetchall()
            cur.execute("SELECT name, quantity FROM products ORDER BY quantity ASC LIMIT 5")
            bottom_products = cur.fetchall()
            cur.execute("SELECT COUNT(*) as cnt FROM transactions WHERE date >= NOW() - INTERVAL '7 days'")
            week_transactions = cur.fetchone()["cnt"]
            cur.execute("SELECT type AS tx_type, COUNT(*) as cnt FROM transactions WHERE date >= NOW() - INTERVAL '7 days' GROUP BY type")
            type_counts = {r["tx_type"]: r["cnt"] for r in cur.fetchall()}
        conn.commit()
        return {
            "total": total, "total_qty": total_qty, "low_count": low_count,
            "top_products": top_products, "bottom_products": bottom_products,
            "week_transactions": week_transactions,
            "adds": type_counts.get("add", 0), "removes": type_counts.get("remove", 0),
        }
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_product_by_id(product_id):
    """Fetch a product row by ID. Returns dict or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_product_name(product_id):
    """Fetch product name by ID. Returns string or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT name FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return row["name"] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_promote_demote_user(telegram_id, action):
    """Promote or demote a user. Returns updated user dict or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if action == "promote":
                cur.execute("UPDATE users SET role = 'admin' WHERE telegram_id = %s AND role = 'customer' RETURNING *", (telegram_id,))
            elif action == "demote":
                cur.execute("UPDATE users SET role = 'customer' WHERE telegram_id = %s AND role = 'admin' AND telegram_id NOT IN %s RETURNING *",
                    (telegram_id, tuple(ADMIN_IDS)))
            else:
                return None
            user = cur.fetchone()
        conn.commit()
        return dict(user) if user else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_approved_users():
    """Fetch all approved admin+customer users ordered by role, created_at."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE role IN ('admin', 'customer') ORDER BY role, created_at")
            approved = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return approved
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def _db_fetch_workers():
    """Fetch active workers (admin+customer) for invoice assignment."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, telegram_id, username, first_name FROM users "
                "WHERE role IN ('admin', 'customer') AND active = TRUE ORDER BY first_name"
            )
            workers = cur.fetchall()
        conn.commit()
        return workers
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def _db_update_tx_reason(tx_id, product_id, user_id, reason):
    """Update reason on a transaction row. Uses tx_id if available, else fuzzy match."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if tx_id:
                cur.execute("UPDATE transactions SET reason = %s WHERE id = %s", (reason, tx_id))
            else:
                cur.execute(
                    "UPDATE transactions SET reason = %s WHERE product_id = %s AND type = 'remove' AND user_id = %s AND reason IS NULL ORDER BY date DESC LIMIT 1",
                    (reason, product_id, user_id))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _db_finalize_invoice(customer, worker_id, admin_db_id, items):
    """Create invoice + items, deduct stock, log transactions. Returns invoice dict or raises."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            total = sum(item.get("subtotal", 0) for item in items)
            cur.execute(
                "INSERT INTO invoices (customer_name, worker_id, admin_id, total_amount, status, approved_at) "
                "VALUES (%s, %s, %s, %s, 'approved', NOW()) RETURNING id",
                (customer, worker_id, admin_db_id, total))
            invoice = cur.fetchone()
            invoice_id = invoice["id"]
            for item in items:
                cur.execute(
                    "INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, subtotal) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (invoice_id, item.get("product_id"), item.get("product_name", ""), item.get("quantity", 0), item.get("unit_price", 0), item.get("subtotal", 0)))
                if item.get("product_id"):
                    cur.execute("UPDATE products SET quantity = quantity - %s WHERE id = %s AND quantity >= %s",
                        (item["quantity"], item["product_id"], item["quantity"]))
                    cur.execute(
                        "INSERT INTO transactions (product_id, type, quantity, user_id) VALUES (%s, 'remove', %s, %s)",
                        (item["product_id"], item["quantity"], admin_db_id))
        conn.commit()
        return dict(invoice)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _db_fetch_extraction_request_info(request_id):
    """Fetch extraction request details with user and product info."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT er.requester_id, u.telegram_id, p.name AS product_name, er.quantity "
                "FROM extraction_requests er "
                "JOIN users u ON u.id = er.requester_id "
                "JOIN products p ON p.id = er.product_id "
                "WHERE er.id = %s",
                (request_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_incoming_orders():
    """Fetch all pending/received incoming orders with user info."""
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
        return all_orders
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def _db_fetch_incoming_order_with_user(order_id):
    """Fetch a single incoming order with user telegram_id."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT io.*, u.telegram_id FROM incoming_orders io "
                "LEFT JOIN users u ON u.id = io.requester_id WHERE io.id = %s",
                (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_invoice_product(product_id):
    """Fetch product id/name/quantity/price for invoice item selection."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name, quantity, selling_price FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _db_fetch_worker_name(worker_id):
    """Fetch worker display name by DB id."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT first_name, username FROM users WHERE id = %s", (worker_id,))
            w = cur.fetchone()
        conn.commit()
        return (w["first_name"] or w["username"] or str(worker_id)) if w else str(worker_id)
    except Exception:
        conn.rollback()
        return str(worker_id)
    finally:
        release_connection(conn)


def _db_ping():
    """Ping the database to keep the connection pool alive."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.commit()
    finally:
        release_connection(conn)


def _db_dedup_products():
    """Find and merge duplicate products (same normalized_name).
    Returns (merged_count, deleted_count, merge_details) or None if no duplicates.
    Raises on DB error."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT normalized_name, COUNT(*) as cnt
                FROM products
                WHERE normalized_name IS NOT NULL AND normalized_name != ''
                GROUP BY normalized_name
                HAVING COUNT(*) > 1
                ORDER BY cnt DESC
            """)
            dup_groups = cur.fetchall()

        if not dup_groups:
            return None

        total_merged = 0
        total_deleted = 0
        merge_details = []

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for group in dup_groups:
                norm_name = group["normalized_name"]
                cur.execute("""
                    SELECT * FROM products
                    WHERE normalized_name = %s
                    ORDER BY created_at ASC, id ASC
                """, (norm_name,))
                rows = cur.fetchall()

                keep = dict(rows[0])
                merge_ids = [r["id"] for r in rows[1:]]

                total_qty = sum(r["quantity"] for r in rows)
                if total_qty != keep["quantity"]:
                    cur.execute(
                        "UPDATE products SET quantity = %s WHERE id = %s",
                        (total_qty, keep["id"]))

                for del_id in merge_ids:
                    cur.execute(
                        "UPDATE transactions SET product_id = %s WHERE product_id = %s",
                        (keep["id"], del_id))
                    cur.execute(
                        "DELETE FROM extraction_requests WHERE product_id = %s",
                        (del_id,))

                for del_id in merge_ids:
                    cur.execute("DELETE FROM products WHERE id = %s", (del_id,))

                total_deleted += len(merge_ids)
                total_merged += 1
                merge_details.append(
                    keep["name"] + " (" + str(len(merge_ids)) + " \u0645\u0643\u0631\u0631 \u2192 \u0643\u0645\u064a\u0629: " + str(total_qty) + ")")

        conn.commit()
        return (total_merged, total_deleted, merge_details)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


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
BTN_INVOICE = "🧾 إنشاء فاتورة"

# Customer buttons
BTN_CUST_ALERT = "🔔 تنبيه المخزون"
BTN_CUST_ADD = "➕ إضافة"
BTN_CUST_REMOVE = "➖ إخراج"
BTN_CUST_SEARCH = "🔍 بحث"
BTN_CUST_MOVEMENTS = "📋 الحركة"
BTN_CUST_CANCEL = "❌ إلغاء"
BTN_CUST_ORDER = "📦 طلب توريد"
BTN_CUST_MY_ORDERS = "📋 طلباتي"


# ═══════════════════════════════════════════════════════════════
#  SECTION 10: FORMATTING HELPERS & PRODUCT UTILITIES
#  Functions: format_product_admin, format_product_customer,
#              _product_status_icon, _is_pcs_product, _invoice_product_type,
#              _get_unit_code, _get_unit_ar, _show_product_detail
# ═══════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════
#  SECTION 11: BROADCAST & NOTIFICATION SYSTEM
#  Functions: _broadcast_update_notification, _broadcast_to_all_users,
#              _broadcast_add_notification, send_low_stock_alerts,
#              send_low_stock_list
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  SECTION 11: BROADCAST & NOTIFICATION SYSTEM
#  Functions: _broadcast_update_notification, _broadcast_to_all_users,
#              _broadcast_add_notification, send_low_stock_alerts,
#              send_low_stock_list
# ═══════════════════════════════════════════════════════════════
async def _broadcast_update_notification(update, context):
    """إرسال إشعار آخر تحديث تلقائياً بدون كتابة من المدير."""
    row = await asyncio.to_thread(_db_fetch_latest_update)

    if not row:
        await update.message.reply_text(
            "📭 لا يوجد تحديثات مسجلة.",
            reply_markup=await asyncio.to_thread(get_admin_keyboard),
        )
        return

    update_desc = row["description"]

    user_rows = await asyncio.to_thread(_db_fetch_broadcast_users)

    if not user_rows:
        await update.message.reply_text(
            "📭 لا يوجد مستخدمين مفعلين.",
            reply_markup=await asyncio.to_thread(get_admin_keyboard),
        )
        return

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
    for uid in user_rows:
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
        "✅ تم إرسال إشعار التحديث إلى <b>" + str(sent) + "</b> مستخدم.",
        parse_mode=ParseMode.HTML,
        reply_markup=await asyncio.to_thread(get_admin_keyboard),
    )
    logging.info("[UPDATE] Auto-broadcast sent to " + str(sent) + " users")


async def _broadcast_to_all_users(bot, message):
    """Send a message to ALL active users (admin + customer)."""
    try:
        rows = await asyncio.to_thread(_db_fetch_broadcast_users, True)
        for uid in rows:
            try:
                await bot.send_message(chat_id=uid, text=message)
            except Exception:
                pass
    except Exception as e:
        logging.warning("[BROADCAST] Failed: " + str(e))


async def _broadcast_add_notification(bot, product, qty, actor, reason="truck"):
    """Broadcast add-notification to all users with reason (truck arrival or customer return)."""
    pname = product.get("name", "") if isinstance(product, dict) else str(product)
    if reason == "truck":
        msg = ("🚚 وصول بضاعة جديدة!" + chr(10) + chr(10)
               + "📦 " + pname + chr(10)
               + "➕ الكمية: " + str(qty))
    else:
        msg = ("↩️ إرجاع سلعة من زبون" + chr(10) + chr(10)
               + "📦 " + pname + chr(10)
               + "➕ الكمية: " + str(qty))
    await _broadcast_to_all_users(bot, msg)


async def send_low_stock_alerts(bot):
    """Send individual alerts to ALL users when products enter or leave low-stock list."""
    try:
        low_stock = await asyncio.to_thread(get_low_stock_products)
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
                # Fetch CURRENT quantity from database (not old low-stock snapshot)
                qty = prod.get("quantity", "?")
                new_qty = await asyncio.to_thread(_db_fetch_product_quantity, pid)
                if new_qty is not None:
                    qty = new_qty
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
        low_stock = await asyncio.to_thread(get_low_stock_products)
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


# ── Input Parsing Helpers ──

# ── Input Parsing Helpers ──
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
        data = await asyncio.to_thread(_db_fetch_weekly_report_data)
        if not data:
            return None
        total = data["total"]
        total_qty = data["total_qty"]
        low_count = data["low_count"]
        top_products = data["top_products"]
        bottom_products = data["bottom_products"]
        week_transactions = data["week_transactions"]
        adds = data["adds"]
        removes = data["removes"]

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
        recipient_id = await asyncio.to_thread(get_bot_setting, "weekly_report_recipient")
        if not recipient_id:
            logging.warning("[WEEKLY] No recipient configured")
            return report_text

        try:
            await bot.send_message(chat_id=int(recipient_id), text=report_text)
            logging.info("[WEEKLY] Report sent to " + str(recipient_id))
        except Exception as e:
            logging.error("[WEEKLY] Failed to send: " + str(e), exc_info=True)

        return report_text

    except Exception as e:
        logging.error("[WEEKLY] Report generation failed: " + str(e), exc_info=True)
        return None


async def _weekly_report_job(bot):
    """APScheduled job: send weekly report every Sunday at 23:00 Algeria time."""
    enabled = await asyncio.to_thread(get_bot_setting, "weekly_report_enabled", "false") == "true"
    if enabled:
        logging.info("[WEEKLY] Running scheduled weekly report...")
        await generate_weekly_report(bot)
    else:
        logging.info("[WEEKLY] Weekly report disabled in settings, skipping.")

# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
#  SECTION 13: KEYBOARD BUILDERS
#  Functions: get_admin_keyboard, get_input_keyboard, get_customer_keyboard
# ═══════════════════════════════════════════════════════════════
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
            [BTN_INVOICE, BTN_UPDATE, BTN_CANCEL],
        ],
        resize_keyboard=True,
    )


def get_input_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


def get_customer_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_CUST_SEARCH, BTN_CUST_REMOVE, BTN_CUST_ADD],
            [BTN_CUST_ORDER],
            [BTN_CUST_MY_ORDERS],
            [BTN_CUST_ALERT],
            [BTN_CUST_MOVEMENTS],
            [BTN_CUST_CANCEL],
        ],
        resize_keyboard=True,
    )

# ═══════════════════════════════════════════════════════════════
#  SECTION 14: ADMIN FSM - ADD PRODUCT FLOW
#  Functions: ask_add_name, ask_add_qty, confirm_add_product,
#              callback_addtype_handler, callback_add_handler
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  SECTION 14: ADMIN FSM - ADD PRODUCT FLOW
#  Functions: ask_add_name, ask_add_qty, confirm_add_product,
#              callback_addtype_handler, callback_add_handler
# ═══════════════════════════════════════════════════════════════
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
    user_id = update.effective_user.id if update.effective_user else 0
    if not await asyncio.to_thread(is_admin, user_id):
        await query.answer("⛔ هذا الإجراء متاح للأدمن فقط.", show_alert=True)
        return
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
                await context.bot.send_message(chat_id=query.message.chat_id, text="📦 القائمة الرئيسية:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
            except Exception:
                pass
    except Exception as e:
        logging.error("[ADDTYPE] Error: " + str(e), exc_info=True)
        try:
            await query.edit_message_text("❌ حدث خطأ داخلي.")
        except Exception:
            pass


async def callback_add_handler(update, context):
    """Handle inline button press for add confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0
    if not await asyncio.to_thread(is_admin, user_id):
        await query.answer("⛔ هذا الإجراء متاح للأدمن فقط.", show_alert=True)
        return
    data = query.data
    user_data = context.user_data

    if data == "add_confirm":
        name = user_data.get("add_name", "")
        qty = user_data.get("add_qty", 0)
        telegram_id = query.from_user.id
        try:
            product, is_new = await asyncio.to_thread(db_add_product, name=name, quantity=qty)
            if is_new:
                msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d!\n\n" + format_product_admin(product)
            else:
                msg = "\u2705 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0648\u062c\u0648\u062f\u0629 \u0645\u0633\u0628\u0642\u0627\u064b — \u062a\u0645 \u0625\u0636\u0627\u0641\u0629 " + str(qty) + " " + _get_unit_code(product["name"]) + " \u0644\u0644\u0643\u0645\u064a\u0629.\n\n" + format_product_admin(product)
                # Log the addition to existing product
                db_user = await asyncio.to_thread(get_user, telegram_id)
                if db_user:
                    await asyncio.to_thread(log_transaction, product["id"], "add", qty, db_user["id"])
            await query.edit_message_text(msg)
            await context.bot.send_message(chat_id=query.message.chat_id, text="\u2714\ufe0f", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception as e:
            await query.edit_message_text("❌ حدث خطأ داخلي.")
        user_data.clear()

    elif data == "add_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        user_data.clear()
        await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=await asyncio.to_thread(get_admin_keyboard))


# ═══════════════════════════════════════════════════════════════
#  SECTION 15: ADMIN FSM - QUANTITY, EDIT, DELETE, SEARCH
#  Functions: process_add_quantity, process_remove_quantity,
#              process_edit_product, process_delete_product, process_search
# ═══════════════════════════════════════════════════════════════
async def process_add_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 25",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = await asyncio.to_thread(get_user, telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    try:
        updated = await asyncio.to_thread(db_update_quantity, product_id, qty, "add")
        await asyncio.to_thread(log_transaction, product_id, "add", qty, db_user["id"])
        unit = _get_unit_code(updated["name"])
        msg = "✅ تمت إضافة " + str(qty) + " " + unit + " لـ " + updated["name"] + "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=await asyncio.to_thread(get_admin_keyboard))
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
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
    db_user = await asyncio.to_thread(get_user, telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    try:
        updated = await asyncio.to_thread(db_update_quantity, product_id, qty, "remove")
        tx = await asyncio.to_thread(log_transaction, product_id, "remove", qty, db_user["id"])
        unit = _get_unit_code(updated["name"])
        msg = "✅ تم إخراج " + str(qty) + " " + unit + " من " + updated["name"] + "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg)
        await send_low_stock_alerts(context.bot)
        # Ask for optional free-text reason — save tx ID for exact match
        context.user_data["pending_remove_reason"] = {"tx_id": tx["id"], "product_id": product_id, "user_id": db_user["id"]}
        skip_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ تخطي", callback_data="remskip")],
        ])
        context.user_data["state"] = STATE_REMOVE_REASON
        await update.message.reply_text("💬 سبب الإخراج (اختياري):\nاكتب السبب أو اضغط تخطي", reply_markup=skip_kb)
        return  # important: do NOT clear user_data — user needs to type reason
    except ValueError as e:
        await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
        context.user_data.clear()
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
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
        updated = await asyncio.to_thread(db_update_product, product_id, name=new_name)
        msg = "✅ تم تعديل الاسم بنجاح!"
        msg = msg + chr(10) + chr(10) + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=await asyncio.to_thread(get_admin_keyboard))
    except ValueError as e:
        await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
    context.user_data.clear()

async def process_delete_product(update, context, text, telegram_id):
    text = text.strip()

    # Parse input: supports "5", "1, 3, 5", "1-5", "1,3-5,7"
    ids_to_delete = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            # Range: "1-5"
            range_parts = part.split("-")
            if len(range_parts) == 2 and range_parts[0].strip().isdigit() and range_parts[1].strip().isdigit():
                start_id = int(range_parts[0].strip())
                end_id = int(range_parts[1].strip())
                for i in range(min(start_id, end_id), max(start_id, end_id) + 1):
                    ids_to_delete.add(i)
            else:
                await update.message.reply_text(
                    "❌ صيغة غير صالحة: " + part + chr(10) + "المدى يجب أن يكون رقم-رقم مثال: 1-5",
                    reply_markup=get_input_keyboard())
                return
        elif part.isdigit() and part:
            ids_to_delete.add(int(part))
        elif part:
            await update.message.reply_text(
                "❌ أرسل أرقام السلع فقط." + chr(10) + chr(10)
                + "صيغ مدعومة:" + chr(10)
                + "• رقم واحد: 5" + chr(10)
                + "• عدة أرقام: 1, 3, 5" + chr(10)
                + "• مدى: 1-5" + chr(10)
                + "• مزيج: 1, 3-5, 8",
                reply_markup=get_input_keyboard())
            return

    if not ids_to_delete:
        await update.message.reply_text(
            "❌ لم يتم تحديد أي رقم.",
            reply_markup=get_input_keyboard())
        return

    ids_list = sorted(ids_to_delete)
    deleted_count = 0
    not_found = []
    for product_id in ids_list:
        try:
            deleted = await asyncio.to_thread(db_delete_product, product_id)
            if deleted:
                deleted_count += 1
            else:
                not_found.append(str(product_id))
        except Exception as e:
            not_found.append(str(product_id) + " (خطأ)")

    msg = ""
    if deleted_count > 0:
        msg = "✅ تم حذف " + str(deleted_count) + " سلعة بنجاح."
    if not_found:
        if msg:
            msg += chr(10) + chr(10)
        msg += "⚠️ أرقام لم يتم العثور عليها: " + ", ".join(not_found)
    if deleted_count == 0:
        msg = "❌ لم يتم حذف أي سلعة. تأكد من الأرقام."

    await update.message.reply_text(msg, reply_markup=await asyncio.to_thread(get_admin_keyboard))
    context.user_data.clear()


async def process_search(update, context, text, admin_view=False):
    if not text:
        await update.message.reply_text(
            "\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.",
            reply_markup=get_input_keyboard(),
        )
        return
    results = await asyncio.to_thread(search_products, text)
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
        await update.message.reply_text("\n".join(lines), reply_markup=await asyncio.to_thread(get_admin_keyboard))
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


# ═══════════════════════════════════════════════════════════════
#  SECTION 16: PRODUCT DETAIL, MOVEMENTS & DATE PICKER
#  Functions: _show_product_detail, callback_product_select_handler,
#              callback_product_action_handler,
#              _build_movements_text, _fetch_today_movements,
#              _fetch_movements_by_date, _show_all_movements,
#              callback_mov_date_handler
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  SECTION 16: PRODUCT DETAIL, MOVEMENTS & DATE PICKER
#  Functions: _show_product_detail, callback_product_select_handler,
#              callback_product_action_handler,
#              _build_movements_text, _fetch_today_movements,
#              _fetch_movements_by_date, _show_all_movements,
#              callback_mov_date_handler
# ═══════════════════════════════════════════════════════════════
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


def _invoice_product_type(name):
    """Detect invoice product type from name. Returns:
       'm2'    -> sold by m² with box area (باقي السلع)
       'pcs'   -> sold by piece with unit price (motif, 120/60)
       'plant' -> sold by linear meter with length choice (plante)
    """
    if not name or not isinstance(name, str):
        return "m2"
    n = name.strip().lower()
    if n.startswith("motif") or n.startswith("120/60") or n.startswith("120x60") or n.startswith("120×60"):
        return "pcs"
    if n.startswith("plante"):
        return "plant"
    return "m2"


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
    admin_view = await asyncio.to_thread(is_admin, user_id)
    buttons = []
    if admin_view:
        buttons = [[
            InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
            InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid),
        ]]
    keyboard = InlineKeyboardMarkup(buttons)
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


WEEKDAY_AR = {
    0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
    3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
}


def _build_movements_text(rows, admin_view=False, start=0, per_page=8):
    """Build formatted movements text for one page with summary header.
    Returns (full_text, total_pages) tuple."""
    if not rows:
        return None, 0

    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page_rows = rows[start:start + per_page]
    page_num = (start // per_page) + 1

    add_count = sum(1 for r in rows if r["tx_type"] == "add")
    rem_count = sum(1 for r in rows if r["tx_type"] == "remove")

    lines = []
    # ── Summary header ──
    lines.append("📊 <b>الملخص:</b>  🟢➕ " + str(add_count) + " إضافة  |  🔴➖ " + str(rem_count) + " إخراج  |  المجموع: " + str(total))
    lines.append("")

    # ── Operations ──
    for r in page_rows:
        r = dict(r)
        if r["tx_type"] == "add":
            icon = "🟢➕"
        else:
            icon = "🔴➖"
        dt = r.get("date")
        if not dt:
            continue
        time_str = dt.strftime("%H:%M")
        prod = str(r["product_name"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        qty = r["quantity"]
        unit = _get_unit_code(str(r["product_name"] or ""))
        # Build clickable user link
        user_tid = r.get("user_tid")
        user_name = str(r.get("user_name") or "مجهول").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if user_tid and user_name != "مجهول":
            user_link = "<a href=\"tg://user?id=" + str(user_tid) + "\">" + user_name + "</a>"
        else:
            user_link = user_name
        line = icon + " <b>" + prod + "</b> " + str(qty) + " " + unit + "  —  " + user_link + "  " + time_str
        # Show reason only for admin, only for removals
        reason = r.get("reason")
        if admin_view and reason and r["tx_type"] == "remove":
            reason = str(reason).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            line = line + chr(10) + "   💬 " + reason
        lines.append(line)

    # ── Page indicator ──
    if total_pages > 1:
        lines.append("")
        lines.append("📄 صفحة " + str(page_num) + "/" + str(total_pages))

    return chr(10).join(lines), total_pages


def _fetch_movements_by_date(target_date):
    """Fetch movements for a specific date from DB."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.reason, p.name AS product_name, "
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
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


def _fetch_today_movements():
    """Fetch today's movements from DB."""
    return _fetch_movements_by_date(datetime.date.today())


def _fetch_recent_movements(limit=50):
    """Fetch recent movements from DB (all dates, ordered by time DESC)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.reason, p.name AS product_name, "
                "u.username AS user_name, u.telegram_id AS user_tid "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "ORDER BY t.date DESC"
                " LIMIT %s", (limit,))
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception as e:
        conn.rollback()
        logging.error("[DB_ERROR] Operation failed: " + str(e), exc_info=True)
        raise
    finally:
        release_connection(conn)


def _build_date_buttons():
    """Build list of date buttons for last 7 days (for inline keyboard)."""
    today = datetime.date.today()
    buttons = []
    for d in range(6, -1, -1):
        day = today - datetime.timedelta(days=d)
        day_str = day.strftime("%Y-%m-%d")
        wd = day.weekday()
        if d == 0:
            label = "📅 " + WEEKDAY_AR.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
        else:
            label = "📅 " + WEEKDAY_AR.get(wd, "") + " " + day.strftime("%d/%m")
        buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])
    return buttons


def _build_date_picker_markup():
    """Build full date picker markup (dates only)."""
    return InlineKeyboardMarkup(_build_date_buttons())


async def _safe_send_html(chat_id, text, bot, reply_markup=None, edit_message=None):
    """Send long HTML text safely: splits by complete lines, never cuts HTML tags.
    If edit_message is given, first chunk tries to edit it; on failure sends new message.
    Returns list of sent Message objects (chunk messages to delete later)."""
    if len(text) <= 4096:
        if edit_message:
            try:
                await edit_message(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                return []
            except Exception:
                msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                return [msg]
        msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        return [msg]
    # Split by lines
    all_lines = text.split(chr(10))
    chunks = []
    current_lines = []
    current_len = 0
    for ln in all_lines:
        line_len = len(ln) + 1
        if current_len + line_len > 3900 and current_lines:
            chunks.append(chr(10).join(current_lines))
            current_lines = []
            current_len = 0
        current_lines.append(ln)
        current_len += line_len
    if current_lines:
        chunks.append(chr(10).join(current_lines))
    extra_msgs = []
    total_chunks = len(chunks)
    for i, chunk in enumerate(chunks):
        is_last = (i == total_chunks - 1)
        # Attach reply_markup only to the LAST chunk so buttons appear at the bottom
        markup = reply_markup if is_last else None
        if i == 0 and edit_message:
            try:
                await edit_message(text=chunk, parse_mode=ParseMode.HTML, reply_markup=markup)
            except Exception:
                msg = await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML, reply_markup=markup)
                extra_msgs.append(msg)
        else:
            msg = await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.HTML, reply_markup=markup)
            extra_msgs.append(msg)
    return extra_msgs


async def _delete_extra_chunks(bot, chat_id, context):
    """Delete any extra chunk messages stored in context.user_data['_mov_chunks']."""
    chunks = context.user_data.pop("_mov_chunks", [])
    for msg in chunks:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception:
            pass


def _build_mov_pagination_buttons(date_str, page, total_pages):
    """Build pagination keyboard for movement views."""
    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ السابق", callback_data="movp_" + date_str + "_" + str(page - 1)))
    if date_str == "recent":
        nav_row.append(InlineKeyboardButton("🔙 رجوع", callback_data="movd_back"))
    else:
        nav_row.append(InlineKeyboardButton("🔙 رجوع", callback_data="movd_back"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("▶️ التالي", callback_data="movp_" + date_str + "_" + str(page + 1)))
    if nav_row:
        buttons.append(nav_row)
    return InlineKeyboardMarkup(buttons)


async def _show_all_movements(update, context, admin_view=True):
    """Show date picker for last 7 days — user picks a date to see movements."""
    try:
        text = "📋 اختار التاريخ لعرض الحركة:"
        markup = _build_date_picker_markup()
        await update.message.reply_text(text, reply_markup=markup)
    except Exception as e:
        logging.error("[MOVEMENTS_DATE] Error: " + str(e), exc_info=True)
        await report_diag(bot=context.bot, chat_id=update.effective_chat.id, error=e, context_tag="[MOVEMENTS-SHOW]")
        kb = await asyncio.to_thread(get_admin_keyboard) if admin_view else asyncio.to_thread(get_customer_keyboard)
        await update.message.reply_text("❌ حدث خطأ في عرض الحركات. تم إرسال التشخيص.", reply_markup=kb)


async def callback_mov_date_handler(update, context):
    """Handle movement callbacks: date selection, today shortcut, back."""
    query = update.callback_query
    await query.answer()
    data = query.data
    admin_view = await asyncio.to_thread(is_admin, query.from_user.id)
    chat_id = query.message.chat.id

    # --- Back to date picker ---
    if data == "movd_back":
        await _delete_extra_chunks(context.bot, chat_id, context)
        context.user_data.pop("_mov_rows", None)
        markup = _build_date_picker_markup()
        try:
            await query.edit_message_text(
                "📋 اختار التاريخ لعرض الحركة:",
                reply_markup=markup)
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text="📋 اختار التاريخ لعرض الحركة:",
                reply_markup=markup)
        return

    # --- Today shortcut ---
    if data == "movd_today":
        target_date = datetime.date.today()
    else:
        # --- Date selected: movd_YYYY-MM-DD ---
        date_str = data.replace("movd_", "")
        try:
            target_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            await query.edit_message_text("❌ تاريخ غير صالح.")
            return

    # Fetch movements for the selected date
    try:
        rows = await asyncio.to_thread(_fetch_movements_by_date, target_date)
    except Exception as e:
        logging.error("[MOV_DATE_HANDLER] DB error: " + str(e), exc_info=True)
        await report_diag(bot=context.bot, chat_id=chat_id, error=e, context_tag="[MOVEMENTS-DATE]")
        try:
            await query.edit_message_text("❌ حدث خطأ في الاتصال بقاعدة البيانات. تم إرسال التشخيص.")
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="❌ حدث خطأ في الاتصال بقاعدة البيانات. تم إرسال التشخيص.")
        return

    wd = target_date.weekday()
    date_display = WEEKDAY_AR.get(wd, "") + " " + target_date.strftime("%d/%m/%Y")
    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]
    ])

    if not rows:
        try:
            await query.edit_message_text(
                "📋 حركة " + date_display + ":" + chr(10) + chr(10) + "لا توجد عمليات في هذا اليوم.",
                reply_markup=back_btn)
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text="📋 حركة " + date_display + ":" + chr(10) + chr(10) + "لا توجد عمليات في هذا اليوم.",
                reply_markup=back_btn)
        return

    # Show ALL movements in one page (no pagination)
    body, _ = _build_movements_text(rows, admin_view=admin_view, start=0, per_page=999)
    header = "📋 حركة " + date_display + ":"
    full_text = header + chr(10) + chr(10) + body

    await _delete_extra_chunks(context.bot, chat_id, context)
    extra = await _safe_send_html(
        chat_id=chat_id, text=full_text, bot=context.bot,
        reply_markup=back_btn, edit_message=query.edit_message_text)
    if extra:
        context.user_data["_mov_chunks"] = extra


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
        row = await asyncio.to_thread(_db_fetch_product_by_id, product_id)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        await _show_product_detail(update, context, row)
    except Exception as e:
        logging.error("[PSEL] Error: " + str(e), exc_info=True)
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
    # Security: only admins can add/remove stock via callbacks
    if action in ("add", "rem"):
        if not await asyncio.to_thread(is_admin, user_id):
            await query.answer("⛔ هذا الإجراء متاح للأدمن فقط.", show_alert=True)
            return
    if action == "log":
        try:
            txns = await asyncio.to_thread(get_product_transactions, product_id, limit=15)
            product_name = await asyncio.to_thread(_db_fetch_product_name, product_id) or "?"
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
            logging.error("[PACT_LOG] Error: " + str(e), exc_info=True)
            await report_diag(bot=context.bot, chat_id=query.message.chat.id, error=e, context_tag="[PRODUCT-LOG]")
            try:
                await query.edit_message_text("❌ خطأ في تحميل الحركة. تم إرسال التشخيص.")
            except Exception:
                pass
        return
    try:
        row = await asyncio.to_thread(_db_fetch_product_by_id, product_id)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        product = row
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
        logging.error("[PACT] Error: " + str(e), exc_info=True)
        await report_diag(bot=context.bot, chat_id=query.message.chat.id, error=e, context_tag="[PRODUCT-ACTION]")
        try:
            await query.edit_message_text("❌ حدث خطأ. تم إرسال التشخيص.")
        except Exception:
            pass
#  SHOW PRODUCTS LIST
# ═══════════════════════════════════════════════════════════


async def start_command(update, context):
    user = update.effective_user
    await asyncio.to_thread(sync_user_info, user)
    # Notify admins about new pending user
    if not await asyncio.to_thread(is_admin, user.id):
        db_user = await asyncio.to_thread(get_user, user.id)
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
                    logging.error("[JOIN] Failed to notify admin " + str(admin_id) + ": " + str(notify_err), exc_info=True)
    if await asyncio.to_thread(is_admin, user.id):
        name = user.first_name or ""
        await update.message.reply_text(
            "\U0001f44b \u0645\u0631\u062d\u0628\u0627\u064b <b>" + name + "</b>\n\n\U0001f3e2 \u0623\u0646\u062a \u0645\u062f\u064a\u0631 \u0627\u0644\u0646\u0638\u0627\u0645.\n\U0001f4cb \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0644\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u062e\u0632\u0648\u0646.\n\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\u26a1 \u062a\u0645 \u0627\u0644\u062a\u0637\u0648\u064a\u0631 \u0628\u0648\u0627\u0633\u0637\u0629 <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
            reply_markup=await asyncio.to_thread(get_admin_keyboard),
            parse_mode=ParseMode.HTML,
        )
    else:
        name = user.first_name or ""
        db_user = await asyncio.to_thread(get_user, user.id)
        if db_user and db_user.get("role") == "pending":
            await update.message.reply_text(
                "👋 مرحباً <b>" + name + "</b>\n\n⏳ حسابك قيد المراجعة.\nيجب الانتظار حتى يفعلك المدير.\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "👋 مرحباً <b>" + name + "</b>\n\n⌛ بحث عن المنتجات المتاحة\n📁 استخدم الأزرار أدناه.\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                reply_markup=await asyncio.to_thread(get_customer_keyboard),
                parse_mode=ParseMode.HTML,
            )
    context.user_data.clear()


async def help_command(update, context):
    user = update.effective_user
    if await asyncio.to_thread(is_admin, user.id):
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
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await ask_add_name(update, context)


async def search_command(update, context):
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    context.user_data["state"] = STATE_SEARCH_ADMIN
    await update.message.reply_text(
        "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
        reply_markup=get_input_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
#  SECTION 18: USER APPROVAL & ROLE MANAGEMENT CALLBACKS
#  Functions: callback_approval_handler, callback_promote_handler
# ═══════════════════════════════════════════════════════════════

async def callback_approval_handler(update, context):
    """Handle approve/reject for pending users."""
    query = update.callback_query
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
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
        user = await asyncio.to_thread(approve_user, target_id)
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
        deleted = await asyncio.to_thread(reject_user, target_id)
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
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data  # "promote_123456" or "demote_123456"
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    action, tid_str = parts[0], parts[1]
    if not tid_str.isdigit():
        return
    target_id = int(tid_str)

    user = await asyncio.to_thread(_db_promote_demote_user, target_id, action)

    if user:
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


# ═══════════════════════════════════════════════════════════════
#  SECTION 19: BACKUP COMMAND & RESTORE CALLBACKS
#  Functions: backup_command, handle_backup_document,
#              callback_restore_handler, callback_restore_from_email_handler,
#              callback_restore_last_handler, callback_add_notify_handler,
#              callback_email_restore_handler, callback_restore_last_confirm_handler
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  SECTION 19: BACKUP COMMAND & RESTORE CALLBACKS
#  Functions: backup_command, handle_backup_document,
#              callback_restore_handler, callback_restore_from_email_handler,
#              callback_restore_last_handler, callback_add_notify_handler,
#              callback_email_restore_handler, callback_restore_last_confirm_handler
# ═══════════════════════════════════════════════════════════════
async def dedup_command(update, context):
    """Clean duplicate products: merge exact matches (same normalized name).
    Keeps the oldest record, merges quantities, deletes duplicates."""
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    await update.message.reply_text("🔍 جاري فحص التكرارات...")
    try:
        result = await asyncio.to_thread(_db_dedup_products)
        if result is None:
            await update.message.reply_text("✅ لا توجد تكرارات مطابقة في قاعدة البيانات.")
            return
        total_merged, total_deleted, merge_details = result
        msg = ("✅ تم تنظيف التكرارات:\n\n"
            + "📊 المجموعات المدمجة: " + str(total_merged) + "\n"
            + "🗑 السجلات المحذوفة: " + str(total_deleted) + "\n\n")
        # Show first 10 details
        for detail in merge_details[:10]:
            msg = msg + "  • " + detail + "\n"
        if len(merge_details) > 10:
            msg = msg + "  ... و " + str(len(merge_details) - 10) + " أخرى"
        await update.message.reply_text(msg)
    except Exception as e:
        logging.error("[DEDUP] Error: " + str(e), exc_info=True)
        await update.message.reply_text("❌ حدث خطأ داخلي.")

async def backup_command(update, context):
    global _last_backup_data
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    try:
        import io
        from telegram import InputFile
        backup = await asyncio.to_thread(db_export_backup)
        _last_backup_data = backup
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d_%H-%M")
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
        await update.message.reply_text("❌ حدث خطأ داخلي.")


async def handle_backup_document(update, context):
    """Admin sends a backup .json file to restore data."""
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
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
        await update.message.reply_text("❌ حدث خطأ داخلي.")


async def callback_restore_handler(update, context):
    query = update.callback_query
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data
    if data == "restore_confirm":
        restore_data = context.user_data.get("restore_data")
        if not restore_data:
            await query.edit_message_text("\u274c \u0644\u0627 \u064a\u0648\u062c\u062f \u0628\u064a\u0627\u0646\u0627\u062a \u0644\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f.")
            return
        try:
            result = await asyncio.to_thread(db_import_backup, restore_data)
            await query.edit_message_text(result)
        except Exception as e:
            await query.edit_message_text("❌ حدث خطأ داخلي.")
        context.user_data.pop("restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception:
            pass
    elif data == "restore_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0625\u0644\u063a\u0627\u0621 \u0627\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a.")
        context.user_data.pop("restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
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
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
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
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
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


async def callback_remskip_handler(update, context):
    """Handle skip button for optional removal reason."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("pending_remove_reason", None)
    is_adm = await asyncio.to_thread(is_admin, query.from_user.id)
    context.user_data.clear()
    kb = await asyncio.to_thread(get_admin_keyboard) if is_adm else asyncio.to_thread(get_customer_keyboard)
    try:
        await query.edit_message_text("✅ تم الإخراج بدون سبب.", reply_markup=kb)
    except Exception:
        pass


async def callback_add_notify_handler(update, context):
    """Handle the 'reason for add' choice: truck, customer return, or silent correction."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "addnotify_truck", "addnotify_return", or "addnotify_silent"
    reason = "truck" if data == "addnotify_truck" else ("return" if data == "addnotify_return" else "silent")
    pending = context.user_data.get("pending_add_notify")
    if not pending:
        await query.edit_message_text("❌ انتهت صلاحية هذا الاختيار.")
        context.user_data.clear()
        return
    product = pending["product"]
    qty = pending["qty"]
    actor = pending["user"]
    try:
        if reason == "silent":
            # تصحيح المخزون — بدون إشعار وبدون تسجيل في سجل الحركة
            confirm = "📝 تم تصحيح المخزون بنجاح (بدون إشعار وبدون تسجيل) ✅"
            await query.edit_message_text(confirm)
        else:
            # Log the transaction ONLY for real stock movements (truck/return)
            if actor:
                await asyncio.to_thread(log_transaction, product["id"], "add", qty, actor["id"])
            # Broadcast the notification with the chosen reason
            await _broadcast_add_notification(context.bot, product, qty, actor, reason=reason)
            # Confirm to the user
            if reason == "truck":
                confirm = "🚚 تم إرسال تنبيه وصول بضاعة من الشاحنة للجميع ✅"
            else:
                confirm = "↩️ تم إرسال تنبيه إرجاع سلعة من زبون للجميع ✅"
            await query.edit_message_text(confirm)
    except Exception as e:
        logging.error("[ADD_NOTIFY] Broadcast failed: " + str(e), exc_info=True)
        try:
            await query.edit_message_text("❌ حدث خطأ في إرسال التنبيه.")
        except Exception:
            pass
    context.user_data.pop("pending_add_notify", None)


async def callback_email_restore_handler(update, context):
    query = update.callback_query
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data
    if data == "email_restore_confirm":
        restore_data = context.user_data.get("email_restore_data")
        if not restore_data:
            await _safe_edit(query, "❌ لا يوجد بيانات.")
            return
        try:
            result = await asyncio.to_thread(db_import_backup, restore_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ حدث خطأ داخلي.")
        context.user_data.pop("email_restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception:
            pass
    elif data == "email_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        context.user_data.pop("email_restore_data", None)
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception:
            pass

async def callback_restore_last_confirm_handler(update, context):
    query = update.callback_query
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    global _last_backup_data
    data = query.data
    if data == "last_restore_confirm":
        if not _last_backup_data:
            await _safe_edit(query, "❌ لا توجد نسخة.")
            return
        try:
            result = await asyncio.to_thread(db_import_backup, _last_backup_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ حدث خطأ داخلي.")
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 القائمة الرئيسية:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception:
            pass
    elif data == "last_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        try:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 القائمة الرئيسية:", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
#  SECTION 20: USERS MANAGEMENT COMMAND
#  Functions: users_command
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  SECTION 20: USERS MANAGEMENT COMMAND
#  Functions: users_command
# ═══════════════════════════════════════════════════════════════

# ── Users Command ──

async def users_command(update, context):
    if not await asyncio.to_thread(is_admin, update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    pending = await asyncio.to_thread(get_pending_users)
    approved = await asyncio.to_thread(_db_fetch_approved_users)

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


# ═══════════════════════════════════════════════════════════════
#  SECTION 21: INVOICE SYSTEM (Full Workflow)
#  Functions: _invoice_add_item, _fmt_price, _invoice_show_review,
#              _invoice_show_workers, _generate_invoice_pdf,
#              _invoice_finalize, callback_invoice_handler
#  Flow: customer → product → qty → price → area → review → worker → PDF
# ═══════════════════════════════════════════════════════════════

async def _invoice_add_item(update, context, product_id, product_name, qty, unit_price, ptype, extra_info=None):
    """Add item to invoice draft. ptype: 'm2', 'pcs', 'plant'."""
    items = context.user_data.get("invoice_items", [])
    item = {
        "product_id": product_id,
        "product_name": product_name,
        "ptype": ptype,
        "quantity": qty,
        "unit_price": unit_price,
    }
    if ptype == "m2":
        item["qty_m2"] = extra_info.get("qty_m2", qty) if extra_info else qty
        item["box_area"] = extra_info.get("box_area", 1) if extra_info else 1
        item["num_boxes"] = extra_info.get("num_boxes", qty) if extra_info else qty
        item["subtotal"] = item["qty_m2"] * unit_price
    elif ptype == "pcs":
        item["subtotal"] = qty * unit_price
    elif ptype == "plant":
        item["length_m"] = extra_info.get("length_m", 0.45) if extra_info else 0.45
        item["qty_m"] = extra_info.get("qty_m", qty) if extra_info else qty
        item["num_pieces"] = extra_info.get("num_pieces", qty) if extra_info else qty
        item["subtotal"] = item["qty_m"] * unit_price
    items.append(item)
    context.user_data["invoice_items"] = items
    chat_id = update.effective_chat.id
    total = sum(i["subtotal"] for i in items)
    msg = "✅ تمت الإضافة (" + str(len(items)) + " عناصر)" + chr(10) + chr(10)
    if ptype == "m2":
        msg += "📦 " + product_name + " → " + str(item.get("num_boxes", qty)) + " علبة" + chr(10)
        msg += "📐 " + str(round(item.get("qty_m2", 0), 2)) + " م² × " + _fmt_price(item["unit_price"]) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    elif ptype == "pcs":
        msg += "📦 " + product_name + " → " + str(qty) + " قطعة = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    elif ptype == "plant":
        msg += "📦 " + product_name + " → " + str(item.get("num_pieces", qty)) + " قطعة (" + str(item.get("length_m", "")) + " م) = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    msg += chr(10) + "💰 الإجمالي الحالي: " + _fmt_price(total) + " د.ج"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة منتج جديد", callback_data="invoice_add_new")],
        [InlineKeyboardButton("📋 إنهاء الفاتورة", callback_data="invoice_review"),
         InlineKeyboardButton("🗑 حذف آخر عنصر", callback_data="invoice_remove_last")],
        [InlineKeyboardButton("❌ إلغاء الفاتورة", callback_data="invoice_cancel")],
    ])
    context.user_data["state"] = STATE_INVOICE_PRODUCT
    await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard)


def _fmt_price(val):
    """Format price with Algerian convention (comma as decimal separator)."""
    return "{:,.2f}".format(val).replace(",", "X").replace(".", ",").replace("X", ".")


async def _invoice_show_review(update_or_query, context, from_callback=False):
    """Show invoice draft for approval."""
    items = context.user_data.get("invoice_items", [])
    customer = context.user_data.get("invoice_customer", "")
    if not items:
        if from_callback:
            await update_or_query.edit_message_text("❌ لا توجد عناصر في الفاتورة.")
        else:
            await update_or_query.message.reply_text("❌ لا توجد عناصر.")
        return
    total = sum(i["subtotal"] for i in items)
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y/%m/%d %H:%M")
    msg = "🧾 مسودة فاتورة" + chr(10) + chr(10)
    msg += "👤 الزبون: " + customer + chr(10)
    msg += "📅 التاريخ: " + now_str + chr(10)
    msg += "━" * 20 + chr(10)
    for i, item in enumerate(items, 1):
        msg += str(i) + ". " + item["product_name"] + chr(10)
        pt = item.get("ptype", "m2")
        if pt == "m2":
            msg += "   📐 " + str(item.get("qty_m2", "")) + " m² → " + str(item["quantity"]) + " علبة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/m² × " + str(round(item.get("qty_m2", 0), 2)) + " m² = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
        elif pt == "pcs":
            msg += "   📦 " + str(item["quantity"]) + " قطعة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/قطعة × " + str(item["quantity"]) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
        elif pt == "plant":
            msg += "   📏 " + str(item.get("qty_m", "")) + " م.ط (" + str(item.get("length_m", "")) + " م) → " + str(item.get("num_pieces", item["quantity"])) + " قطعة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/م.ط × " + str(round(item.get("qty_m", 0), 2)) + " م.ط = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    msg += "━" * 20 + chr(10)
    msg += "💰 الإجمالي: " + _fmt_price(total) + " د.ج"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ اعتماد الفاتورة والخصم", callback_data="invoice_approve"),
            InlineKeyboardButton("❌ إلغاء وتعديل", callback_data="invoice_cancel"),
        ],
    ])
    if from_callback:
        await update_or_query.edit_message_text(msg, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(msg, reply_markup=keyboard)


async def _invoice_show_workers(update_or_query, context, from_callback=False):
    """Show worker selection list after invoice approval."""
    workers = await asyncio.to_thread(_db_fetch_workers)

    if not workers:
        msg = "❌ لا يوجد عمال مسجلين في النظام."
        if from_callback:
            await update_or_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    msg = "👷 اختر العامل المسؤول عن تجهيز الطلبية:" + chr(10) + chr(10)
    buttons = []
    for w in workers:
        name = w["first_name"] or w["username"] or str(w["telegram_id"])
        msg += "  👤 " + name + chr(10)
        buttons.append([InlineKeyboardButton("👤 " + name, callback_data="inv_worker_" + str(w["id"]) + "_" + str(w["telegram_id"]))])

    if from_callback:
        await update_or_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update_or_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))


def _generate_invoice_pdf(invoice_id, customer_name, items, total, worker_name=None):
    """Generate a professional PDF invoice file. Returns file path."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm, cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import tempfile
        import os

        # Register fonts - try multiple paths for compatibility across servers
        font_path = None
        _font_candidates = [
            "/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
            "/usr/share/fonts/truetype/noto-serif-sc/NotoSerifSC-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        for _fp in _font_candidates:
            if os.path.isfile(_fp):
                font_path = _fp
                break
        if not font_path:
            # Try to find any TTF font on the system
            import subprocess
            try:
                result = subprocess.run(["find", "/usr/share/fonts", "-name", "*.ttf"],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    font_path = result.stdout.strip().split("\n")[0]
            except Exception:
                pass
        if not font_path:
            logging.error("[INVOICE] No TTF font found on system for PDF generation")
            return None
        pdfmetrics.registerFont(TTFont("NotoSans", font_path))

        # Create temp file
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="invoice_")
        os.close(fd)

        doc = SimpleDocTemplate(
            pdf_path, pagesize=A4,
            rightMargin=15*mm, leftMargin=15*mm,
            topMargin=15*mm, bottomMargin=15*mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("ArabicTitle", parent=styles["Title"], fontName="NotoSans", fontSize=22, alignment=1, spaceAfter=5*mm)
        subtitle_style = ParagraphStyle("ArabicSubtitle", parent=styles["Normal"], fontName="NotoSans", fontSize=11, alignment=1, spaceAfter=3*mm)
        body_style = ParagraphStyle("ArabicBody", parent=styles["Normal"], fontName="NotoSans", fontSize=10, leading=14)
        small_style = ParagraphStyle("ArabicSmall", parent=styles["Normal"], fontName="NotoSans", fontSize=8, alignment=1)
        detail_style = ParagraphStyle("DetailSmall", parent=styles["Normal"], fontName="NotoSans", fontSize=8, leading=10, textColor=colors.Color(0.3, 0.3, 0.3))

        elements = []
        elements.append(Paragraph("Produit Le Roi", title_style))
        elements.append(Paragraph("Facture de Vente", subtitle_style))

        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))
        date_str = now.strftime("%Y/%m/%d %H:%M")

        info_data = [
            [Paragraph("N\u00b0 Facture", body_style), Paragraph("#" + str(invoice_id), body_style)],
            [Paragraph("Date", body_style), Paragraph(date_str, body_style)],
            [Paragraph("Client", body_style), Paragraph(customer_name, body_style)],
        ]
        if worker_name:
            info_data.append([Paragraph("Livreur", body_style), Paragraph(worker_name, body_style)])

        info_table = Table(info_data, colWidths=[60*mm, 80*mm])
        info_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3*mm),
            ("LINEBELOW", (0, -1), (-1, -1), 1, colors.grey),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 5*mm))

        # Items table
        header = [
            Paragraph("N\u00b0", body_style),
            Paragraph("Produit", body_style),
            Paragraph("D\u00e9tail", body_style),
            Paragraph("Prix", body_style),
            Paragraph("Total (DA)", body_style),
        ]
        table_data = [header]
        for idx, item in enumerate(items, 1):
            pt = item.get("ptype", "m2")
            num_label = Paragraph(str(idx), body_style)
            name_label = Paragraph(item["product_name"], body_style)
            if pt == "pcs":
                detail_text = (str(item["quantity"]) + " pi\u00e8ce(s)")
                price_text = "{:,.2f}".format(item["unit_price"]) + " DA/pcs"
                detail_label = Paragraph(detail_text, detail_style)
                price_label = Paragraph(price_text, detail_style)
                total_label = Paragraph("{:,.2f}".format(item["subtotal"]), body_style)
            elif pt == "plant":
                n_pieces = item.get("num_pieces", item["quantity"])
                length_m = item.get("length_m", "")
                qty_m = item.get("qty_m", 0)
                detail_text = str(n_pieces) + " pi\u00e8ce(s) x " + str(length_m) + "m = " + str(round(qty_m, 2)) + " m.lin"
                price_text = "{:,.2f}".format(item["unit_price"]) + " DA/m.lin"
                detail_label = Paragraph(detail_text, detail_style)
                price_label = Paragraph(price_text, detail_style)
                total_label = Paragraph("{:,.2f}".format(item["subtotal"]), body_style)
            else:
                # m2 products
                qty_m2 = item.get("qty_m2", 0)
                n_boxes = item.get("num_boxes", item["quantity"])
                box_area = item.get("box_area", 0)
                detail_text = str(round(qty_m2, 2)) + " m\u00b2 (" + str(n_boxes) + " carton" + ("s" if n_boxes > 1 else "") + " x " + str(box_area) + " m\u00b2)"
                price_text = "{:,.2f}".format(item["unit_price"]) + " DA/m\u00b2"
                detail_label = Paragraph(detail_text, detail_style)
                price_label = Paragraph(price_text, detail_style)
                total_label = Paragraph("{:,.2f}".format(item["subtotal"]), body_style)
            table_data.append([num_label, name_label, detail_label, price_label, total_label])

        table_data.append([
            Paragraph("", body_style),
            Paragraph("TOTAL", body_style), "", "",
            Paragraph("{:,.2f}".format(total), body_style),
        ])

        items_table = Table(table_data, colWidths=[8*mm, 42*mm, 42*mm, 28*mm, 28*mm])
        items_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (1, 1), (1, -2), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.5)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5*mm),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LINEBELOW", (0, -1), (-1, -1), 2, colors.Color(0.2, 0.3, 0.5)),
            ("BACKGROUND", (0, -1), (-1, -1), colors.Color(0.9, 0.9, 0.9)),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 10*mm))
        elements.append(Paragraph("Merci pour votre confiance !", small_style))

        doc.build(elements)
        return pdf_path
    except Exception as e:
        logging.error("[INVOICE] PDF generation failed: " + str(e), exc_info=True)
        return None


async def _invoice_finalize(update, context, worker_id, worker_tid, worker_name):
    """Finalize invoice: save to DB, deduct stock, generate PDF, notify worker."""
    items = context.user_data.get("invoice_items", [])
    customer = context.user_data.get("invoice_customer", "")
    admin = update.effective_user
    chat_id = update.effective_chat.id
    total = sum(i["subtotal"] for i in items)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))

    async def _send(text, markup=None):
        """Safe send helper — works for both Update and CallbackQuery."""
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)

    try:
        # Get admin DB id first (admin.id is telegram_id, not DB id)
        admin_user = await asyncio.to_thread(get_user, admin.id)
        admin_db_id = admin_user["id"] if admin_user else None
        if not admin_db_id:
            await _send("❌ حدث خطأ: لم يتم العثور على بيانات المدير.", await asyncio.to_thread(get_admin_keyboard))
            return

        invoice = await asyncio.to_thread(_db_finalize_invoice, customer, worker_id, admin_db_id, items)
        invoice_id = invoice["id"]
    except Exception as e:
        logging.error("[INVOICE] DB save failed: " + str(e), exc_info=True)
        await _send("❌ حدث خطأ داخلي.", await asyncio.to_thread(get_admin_keyboard))
        return

    # Generate PDF
    pdf_path = _generate_invoice_pdf(invoice_id, customer, items, total, worker_name)

    # Send PDF to admin
    if pdf_path:
        try:
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="invoice_" + str(invoice_id) + ".pdf",
                    caption="🧾 فاتورة #" + str(invoice_id) + " - " + customer + chr(10)
                    + "💰 الإجمالي: " + _fmt_price(total) + " د.ج" + chr(10)
                    + "👷 العامل: " + worker_name
                )
            os.remove(pdf_path)
        except Exception as e:
            logging.error("[INVOICE] PDF send failed: " + str(e), exc_info=True)

    # Notify worker
    worker_notified = False
    try:
        worker_msg = "🧾 فاتورة جديدة للتجهيز!" + chr(10) + chr(10)
        worker_msg += "👤 الزبون: " + customer + chr(10)
        worker_msg += "📅 " + now.strftime("%Y/%m/%d %H:%M") + chr(10)
        worker_msg += "━" * 15 + chr(10)
        for i, item in enumerate(items, 1):
            pt = item.get("ptype", "m2")
            if pt == "pcs":
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item["quantity"]) + " قطعة" + chr(10)
            elif pt == "plant":
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item.get("num_pieces", item["quantity"])) + " قطعة (" + str(item.get("qty_m", "")) + " م.ط)" + chr(10)
            else:
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item.get("qty_m2", "")) + "m² → " + str(item["quantity"]) + " علبة" + chr(10)
        worker_msg += "━" * 15 + chr(10)
        worker_msg += "💰 الإجمالي: " + _fmt_price(total) + " د.ج" + chr(10) + chr(10)
        worker_msg += "⚙️ يرجى تجهيز البضاعة فوراً"
        await context.bot.send_message(chat_id=worker_tid, text=worker_msg)
        worker_notified = True
    except Exception as e:
        logging.error("[INVOICE] Worker notification failed: " + str(e), exc_info=True)

    # Clear state and return to admin panel
    context.user_data.clear()
    result_msg = "✅ تم إنشاء الفاتورة بنجاح!" + chr(10) + chr(10)
    result_msg += "🧾 #" + str(invoice_id) + chr(10)
    result_msg += "👤 " + customer + chr(10)
    result_msg += "💰 " + _fmt_price(total) + " د.ج" + chr(10)
    result_msg += "👷 " + worker_name + chr(10) + chr(10)
    if pdf_path:
        result_msg += "📎 تم إرسال الفاتورة PDF" + chr(10)
    if worker_notified:
        result_msg += "📨 تم إشعار العامل" + chr(10)
    result_msg += "✅ تم الخصم من المخزون"
    await _send(result_msg, await asyncio.to_thread(get_admin_keyboard))


async def callback_invoice_handler(update, context):
    """Handle invoice-related callback queries."""
    query = update.callback_query
    await query.answer()
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data

    if data == "invoice_review":
        await _invoice_show_review(query, context, from_callback=True)

    elif data == "invoice_approve":
        await _invoice_show_workers(query, context, from_callback=True)

    elif data == "invoice_cancel":
        context.user_data.clear()
        try:
            await query.edit_message_text(
                "❌ تم إلغاء الفاتورة.",
                reply_markup=None
            )
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📦 القائمة الرئيسية:",
            reply_markup=await asyncio.to_thread(get_admin_keyboard)
        )

    elif data == "invoice_back_customer":
        context.user_data["state"] = STATE_INVOICE_CUSTOMER
        context.user_data.pop("invoice_customer", None)
        try:
            await query.edit_message_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\U0001f464 \u0623\u0639\u062f \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646:")

    elif data == "invoice_back_product":
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        context.user_data.pop("invoice_tmp_product", None)
        context.user_data.pop("invoice_tmp_pid", None)
        try:
            await query.edit_message_text(
                "\U0001f4e6 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:" + chr(10) + chr(10)
                + "\U0001f50d \u0627\u0643\u062a\u0628 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0644\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646" + chr(10)
                + "\U0001f4cb \u0623\u0648 \u0627\u0643\u062a\u0628 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0625\u0630\u0627 \u0644\u0645 \u062a\u0643\u0646 \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f519 \u062a\u0631\u0627\u062c\u0639", callback_data="invoice_back_customer"),
                     InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\U0001f4e6 \u0623\u0639\u062f \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:")

    elif data == "invoice_back_qty":
        context.user_data["state"] = STATE_INVOICE_QTY
        context.user_data.pop("invoice_tmp_qty", None)
        product_name = context.user_data.get("invoice_tmp_product", "\u0627\u0644\u0645\u0646\u062a\u062c")
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")
        if ptype == "pcs":
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل عدد القطع:"
        elif ptype == "plant":
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل المساحة التي طلبها الزبون (بالمتر):"
        else:
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل الكمية المطلوبة (m²):"
        try:
            await query.edit_message_text(
                q_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=q_text)

    elif data == "invoice_remove_last":
        items = context.user_data.get("invoice_items", [])
        if items:
            removed = items.pop()
            context.user_data["invoice_items"] = items
            msg = "\U0001f5d1 \u062a\u0645 \u062d\u0630\u0641: " + removed["product_name"] + " \u00d7 " + str(removed["quantity"])
            if items:
                total = sum(i["subtotal"] for i in items)
                msg += chr(10) + chr(10) + "\U0001f4cb \u0627\u0644\u0639\u0646\u0627\u0635\u0631 \u0627\u0644\u0645\u062a\u0628\u0642\u064a\u0629 (" + str(len(items)) + "):" + chr(10)
                for i, item in enumerate(items, 1):
                    msg += "  " + str(i) + ". " + item["product_name"] + " \u00d7 " + str(item["quantity"]) + chr(10)
                msg += chr(10) + "\U0001f4b0 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a: " + _fmt_price(total) + " \u062f.\u062c" + chr(10)
                msg += chr(10) + "\U0001f4dd \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c \u0627\u0644\u062a\u0627\u0644\u064a"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f4cb \u0645\u0631\u0627\u062c\u0639\u0629 \u0627\u0644\u0641\u0627\u062a\u0648\u0631\u0629", callback_data="invoice_review"),
                     InlineKeyboardButton("\U0001f5d1 \u062d\u0630\u0641 \u0622\u062e\u0631 \u0639\u0646\u0635\u0631", callback_data="invoice_remove_last")],
                ])
            else:
                msg += chr(10) + chr(10) + "\U0001f4dd \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ])
            try:
                await query.edit_message_text(msg, reply_markup=keyboard)
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=keyboard)
        else:
            try:
                await query.edit_message_text("\u274c \u0644\u0627 \u062a\u0648\u062c\u062f \u0639\u0646\u0627\u0635\u0631 \u0644\u0644\u062d\u0630\u0641.")
            except Exception:
                pass

    elif data.startswith("invsel_"):
        # Product selection callback from invoice product search
        if data.startswith("invsel_manual_"):
            # Manual entry - go to qty step
            manual_name = data[len("invsel_manual_"):]
            ptype = _invoice_product_type(manual_name)
            context.user_data["invoice_tmp_product"] = manual_name
            context.user_data["invoice_tmp_pid"] = None
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            if ptype == "pcs":
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل المساحة التي طلبها الزبون (بالمتر):"
            else:
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل المساحة المطلوبة (m²):"
            try:
                await query.edit_message_text(
                    q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
        else:
            # Selected from DB - extract product id
            try:
                product_id = int(data[len("invsel_"):])
            except ValueError:
                return
            prod = await asyncio.to_thread(_db_fetch_invoice_product, product_id)
            if not prod:
                try:
                    await query.edit_message_text("❌ المنتج غير موجود.")
                except Exception:
                    pass
                return
            ptype = _invoice_product_type(prod["name"])
            context.user_data["invoice_tmp_product"] = prod["name"]
            context.user_data["invoice_tmp_pid"] = prod["id"]
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            stock_info = " (المتوفر: " + str(prod["quantity"]) + ")" if prod["quantity"] > 0 else " (غير متوفر! ⚠️)"
            if ptype == "pcs":
                q_text = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q_text = "🔢 أرسل المساحة التي طلبها الزبون (بالمتر):"
            else:
                q_text = "🔢 أرسل المساحة المطلوبة (m²):"
            try:
                await query.edit_message_text(
                    "📦 " + prod["name"] + stock_info + chr(10) + chr(10) + q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="📦 " + prod["name"] + stock_info + chr(10) + chr(10) + q_text)

    elif data == "invoice_add_new":
        # Add new product to invoice
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        try:
            await query.edit_message_text("📦 أرسل اسم المنتج:")
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 أرسل اسم المنتج:")

    elif data.startswith("inv_plen_"):
        # Plante length selection - calculate pieces and add item
        try:
            length_m = float(data[len("inv_plen_"):])
        except ValueError:
            return
        context.user_data["invoice_tmp_plant_len"] = length_m
        total_meters = context.user_data.get("invoice_tmp_qty", 0)
        price_m = context.user_data.get("invoice_tmp_price", 0)
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        import math
        num_pieces = math.ceil(total_meters / length_m) if length_m > 0 else 0
        actual_meters = num_pieces * length_m
        subtotal = actual_meters * price_m
        # Show summary
        summary = ("📦 " + product_name + chr(10) + chr(10)
                   + "📏 المساحة المطلوبة: " + str(total_meters) + " م.ط" + chr(10)
                   + "📏 طول القطعة: " + str(length_m) + " م" + chr(10)
                   + "📦 عدد القطع: " + str(num_pieces) + " (" + str(round(total_meters / length_m, 2)) + " → تقريب للأعلى)" + chr(10)
                   + "📐 المساحة الفعلية: " + str(round(actual_meters, 2)) + " م.ط" + chr(10)
                   + "💰 السعر/م.ط: " + _fmt_price(price_m) + " د.ج" + chr(10)
                   + "━" * 20 + chr(10)
                   + "💵 الإجمالي: " + _fmt_price(subtotal) + " د.ج")
        try:
            await query.edit_message_text(summary)
        except Exception:
            pass
        await _invoice_add_item(update, context, product_id, product_name, num_pieces, price_m, "plant",
                                 extra_info={"length_m": length_m, "qty_m": actual_meters, "num_pieces": num_pieces, "total_requested_m": total_meters})

    elif data.startswith("inv_area_"):
        # Box area selection callback - no longer used, but keep for compatibility
        try:
            box_area = float(data[len("inv_area_"):])
        except ValueError:
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty_m2 = context.user_data.get("invoice_tmp_qty", 0)
        price_m2 = context.user_data.get("invoice_tmp_price", 0)
        # Calculate number of boxes (round up to nearest whole box)
        import math
        raw_boxes = qty_m2 / box_area
        num_boxes = math.ceil(raw_boxes)
        # Actual area = num_boxes × box_area (customer pays for full boxes)
        actual_area = num_boxes * box_area
        total_price = actual_area * price_m2
        # Show summary and add item
        summary = "📦 " + product_name + chr(10) + chr(10)
        summary += "📐 المساحة المطلوبة: " + str(qty_m2) + " m²" + chr(10)
        summary += "📏 مساحة العلبة: " + str(box_area) + " m²" + chr(10)
        summary += "📦 عدد العلب: " + str(num_boxes) + " (" + str(raw_boxes) + " → تقريب للأعلى)" + chr(10)
        summary += "📐 المساحة الفعلية: " + str(round(actual_area, 2)) + " m²" + chr(10)
        summary += "💰 السعر/m²: " + _fmt_price(price_m2) + " د.ج" + chr(10)
        summary += "━" * 20 + chr(10)
        summary += "💵 الإجمالي: " + _fmt_price(total_price) + " د.ج"
        try:
            await query.edit_message_text(summary)
        except Exception:
            pass
        await _invoice_add_item(update, context, product_id, product_name, num_boxes, price_m2, "m2",
                                 extra_info={"qty_m2": actual_area, "box_area": box_area, "num_boxes": num_boxes})

    elif data.startswith("inv_worker_"):
        parts = data.split("_")
        worker_id = int(parts[2])
        worker_tid = int(parts[3])
        # Get worker name
        worker_name = await asyncio.to_thread(_db_fetch_worker_name, worker_id)
        # Show processing message
        try:
            await query.edit_message_text("⏳ جاري معالجة الفاتورة...")
        except Exception:
            pass
        # Finalize
        try:
            await _invoice_finalize(update, context, worker_id, worker_tid, worker_name)
        except Exception as e:
            logging.error("[INVOICE] Finalize unexpected error: " + str(e), exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ حدث خطأ أثناء إنشاء الفاتورة. تم تسجيل الخطأ.",
                    reply_markup=await asyncio.to_thread(get_admin_keyboard)
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
#  SECTION 22: MESSAGE ROUTER & FSM CONTROLLERS
#  Functions: handle_admin_message (~554 lines, central admin dispatcher),
#              handle_customer_message (~169 lines, customer dispatcher),
#              route_message (entry point: registers user → routes by role)
# ═══════════════════════════════════════════════════════════════

async def handle_admin_message(update, context):

    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user
    chat_id = update.effective_chat.id
    bot = context.bot

    # ── State timeout: expire stale states after 10 minutes ──
    if state and _is_state_expired(context):
        logging.info(f"[STATE_TIMEOUT] Admin {user.id} state '{state}' expired, clearing.")
        context.user_data.clear()
        state = None
        await update.message.reply_text("⏰ انتهت مهلة العملية. ابدأ من جديد.", reply_markup=await asyncio.to_thread(get_admin_keyboard))

    # In a state - if user clicks an admin button, clear state first
    admin_buttons = [BTN_ADD, BTN_SEARCH, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT, BTN_DELETE, BTN_BACKUP, BTN_ALERT, BTN_UPDATE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INVOICE]
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
            context.user_data["state_set_at"] = datetime.datetime.now(datetime.timezone.utc).timestamp()
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
            products = await asyncio.to_thread(get_all_products)
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
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
            products = await asyncio.to_thread(get_all_products)
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
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
            del_prompt = ("🗑 حذف سلعة (أو عدة سلع)" + chr(10) + chr(10)
                + "أرسل رقم أو أرقام السلع:" + chr(10)
                + "• رقم واحد: 5" + chr(10)
                + "• عدة أرقام: 1, 3, 5" + chr(10)
                + "• مدى: 1-5" + chr(10)
                + "• مزيج: 1, 3-5, 8" + chr(10) + chr(10)
                + "⚠️ سيتم حذف السلع وجميع سجلاتها نهائياً!")
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
        if text == BTN_INVOICE:
            context.user_data["state"] = STATE_INVOICE_CUSTOMER
            context.user_data["invoice_items"] = []
            await update.message.reply_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
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
            await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
            return
        await update.message.reply_text(
            "استخدم الأزرار أدناه أو الأوامر:" + chr(10) + "/add, /search",
            reply_markup=await asyncio.to_thread(get_admin_keyboard))
        return

    # State handlers
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        return

    # ── Invoice FSM States ──
    if state == STATE_INVOICE_CUSTOMER:
        customer_name = text.strip()
        if not customer_name:
            await update.message.reply_text("❌ أرسل اسم الزبون.", reply_markup=get_input_keyboard())
            return
        context.user_data["invoice_customer"] = customer_name
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        await update.message.reply_text(
            "📦 أرسل اسم المنتج:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_customer"),
                 InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
            ]))
        return

    if state == STATE_INVOICE_PRODUCT:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم المنتج.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_customer"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        results = await asyncio.to_thread(search_products, product_name)
        if not results:
            context.user_data["invoice_tmp_product"] = product_name
            context.user_data["invoice_tmp_pid"] = None
            ptype = _invoice_product_type(product_name)
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            if ptype == "pcs":
                q = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q = "🔢 أرسل المساحة التي طلبها الزبون (بالمتر):"
            else:
                q = "🔢 أرسل المساحة المطلوبة (m²):"
            await update.message.reply_text(
                "❓ \"" + product_name + "\" غير موجود في المخزون" + chr(10) + chr(10) + q,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        if len(results) == 1:
            found = results[0]
            context.user_data["invoice_tmp_product"] = found["name"]
            context.user_data["invoice_tmp_pid"] = found["id"]
            ptype = _invoice_product_type(found["name"])
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            stock_info = " (" + str(found["quantity"]) + ")" if found["quantity"] > 0 else " (غير متوفر! ⚠️)"
            if ptype == "pcs":
                q = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q = "🔢 أرسل المساحة التي طلبها الزبون (بالمتر):"
            else:
                q = "🔢 أرسل المساحة المطلوبة (m²):"
            await update.message.reply_text(
                "📦 " + found["name"] + stock_info + chr(10) + chr(10) + q,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        msg = "🔍 " + str(len(results)) + " نتائج:" + chr(10) + chr(10)
        buttons = []
        for p in results:
            stock = str(p["quantity"])
            label = p["name"] + " [" + stock + "]"
            if len(label) > 40:
                label = label[:37] + "..."
            buttons.append([InlineKeyboardButton(label, callback_data="invsel_" + str(p["id"]))])
        buttons.append([InlineKeyboardButton("✍️ كتابة يدوية", callback_data="invsel_manual_" + product_name)])
        buttons.append([InlineKeyboardButton("🔍 بحث آخر", callback_data="invoice_back_product")])
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if state == STATE_INVOICE_QTY:
        try:
            qty = float(text.strip())
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل رقم صحيح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")
        context.user_data["invoice_tmp_qty"] = qty

        if ptype == "pcs":
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر القطعة (د.ج):" + chr(10) + chr(10)
                + "📦 " + product_name + " × " + str(int(qty)) + " قطعة",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        elif ptype == "plant":
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر المتر (د.ج/م.ط):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📏 المساحة المطلوبة: " + str(qty) + " م.ط",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        else:
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر المتر المربع (د.ج/m²):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📐 المساحة: " + str(qty) + " m²",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return

    if state == "waiting_invoice_plant_length":
        # Accept manual length input for plante (e.g. 0.45 or 0.6)
        try:
            length_val = float(text.strip())
            if length_val <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل طول القطعة بالمتر (مثلاً: 0.45 أو 0.6):")
            return
        # Process the same as the inv_plen_ callback
        total_meters = context.user_data.get("invoice_tmp_qty", 0)
        price_m = context.user_data.get("invoice_tmp_price", 0)
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        import math
        num_pieces = math.ceil(total_meters / length_val) if length_val > 0 else 0
        actual_meters = num_pieces * length_val
        subtotal = actual_meters * price_m
        summary = ("📦 " + product_name + chr(10) + chr(10)
                   + "📏 المساحة المطلوبة: " + str(total_meters) + " م.ط" + chr(10)
                   + "📏 طول القطعة: " + str(length_val) + " م" + chr(10)
                   + "📦 عدد القطع: " + str(num_pieces) + " (" + str(round(total_meters / length_val, 2)) + " → تقريب للأعلى)" + chr(10)
                   + "📐 المساحة الفعلية: " + str(round(actual_meters, 2)) + " م.ط" + chr(10)
                   + "💰 السعر/م.ط: " + _fmt_price(price_m) + " د.ج" + chr(10)
                   + "━" * 20 + chr(10)
                   + "💵 الإجمالي: " + _fmt_price(subtotal) + " د.ج")
        await update.message.reply_text(summary)
        await _invoice_add_item(update, context, product_id, product_name, num_pieces, price_m, "plant",
                                 extra_info={"length_m": length_val, "qty_m": actual_meters, "num_pieces": num_pieces, "total_requested_m": total_meters})
        return

    # State: remove reason (optional free-text)
    if state == STATE_REMOVE_REASON:
        reason_text = text.strip()
        pending = context.user_data.get("pending_remove_reason")
        if pending and reason_text:
            await asyncio.to_thread(_db_update_tx_reason, pending.get("tx_id"), pending.get("product_id"), pending.get("user_id"), reason_text)
        context.user_data.pop("pending_remove_reason", None)
        context.user_data.clear()
        if reason_text:
            await update.message.reply_text("✅ تم تسجيل السبب: " + reason_text, reply_markup=await asyncio.to_thread(get_admin_keyboard))
        else:
            await update.message.reply_text("✅ تم الإخراج.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        return

    if state == STATE_INVOICE_PRICE:
        try:
            price = float(text.strip())
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل سعر صحيح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty = context.user_data.get("invoice_tmp_qty", 0)
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")

        if ptype == "pcs":
            await _invoice_add_item(update, context, product_id, product_name, int(qty), price, "pcs")
            return
        elif ptype == "plant":
            context.user_data["invoice_tmp_price"] = price
            context.user_data["state"] = "waiting_invoice_plant_length"
            qty_val = context.user_data.get("invoice_tmp_qty", 0)
            await update.message.reply_text(
                "📏 أرسل طول القطعة (بالمتر):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📏 المساحة: " + str(qty_val) + " م.ط" + chr(10)
                + "💰 السعر: " + _fmt_price(price) + " د.ج/م.ط" + chr(10) + chr(10)
                + "💡 اكتب 0.45 أو 0.6 أو اختر من الأزرار:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("0.45 م", callback_data="inv_plen_0.45"),
                     InlineKeyboardButton("0.60 م", callback_data="inv_plen_0.60")],
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        else:
            context.user_data["invoice_tmp_price"] = price
            context.user_data["state"] = STATE_INVOICE_AREA
            await update.message.reply_text(
                "📦 " + product_name + chr(10) + chr(10)
                + "📐 المساحة: " + str(qty) + " m²" + chr(10)
                + "💰 السعر: " + _fmt_price(price) + " د.ج/m²" + chr(10) + chr(10)
                + "📏 أرسل مساحة العلبة (m²):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return

    if state == STATE_INVOICE_AREA:
        try:
            box_area = float(text.strip())
            if box_area <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل مساحة العلبة صحيحة (مثلاً: 1.44).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty_m2 = context.user_data.get("invoice_tmp_qty", 0)
        price_m2 = context.user_data.get("invoice_tmp_price", 0)
        import math
        raw_boxes = qty_m2 / box_area
        num_boxes = math.ceil(raw_boxes)
        actual_area = num_boxes * box_area
        total_price = actual_area * price_m2
        await _invoice_add_item(update, context, product_id, product_name, num_boxes, price_m2, "m2",
                                 extra_info={"qty_m2": actual_area, "box_area": box_area, "num_boxes": num_boxes})
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
        # Remove prefix if user typed it again (avoid "motif motif X")
        if product_name.lower().startswith("motif "):
            product_name = product_name[6:].strip()
        product_name = "motif " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
    elif state == STATE_ADD_NAME_12060:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        # Remove prefix if user typed it again (avoid "120/60 120/60 X")
        if product_name.lower().startswith("120/60 ") or product_name.lower().startswith("120x60 "):
            product_name = product_name[7:].strip()
        product_name = "120/60 " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
    elif state == STATE_ADD_NAME_PLANTE:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        # Remove prefix if user typed it again (avoid "plante plante X")
        if product_name.lower().startswith("plante "):
            product_name = product_name[7:].strip()
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
        results = await asyncio.to_thread(search_products, text)
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
        results = await asyncio.to_thread(search_products, text)
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
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا.\nمثال: 10 أو 10" + unit, reply_markup=get_input_keyboard())
            return
        qty = parsed
        try:
            updated = await asyncio.to_thread(db_update_quantity, product["id"], qty, action)
            db_user = await asyncio.to_thread(get_user, user.id)
            # Transaction logging deferred to callback_add_notify_handler
            # (skip logging here — will log only if NOT silent correction)
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
                    [InlineKeyboardButton("\U0001f4dd \u062a\u0635\u062d\u064a\u062d \u0627\u0644\u0645\u062e\u0632\u0648\u0646 \u0641\u0642\u0637", callback_data="addnotify_silent")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user}
                await update.message.reply_text("\U0001f4e8 \u0645\u0627 \u0633\u0628\u0628 \u0627\u0644\u0625\u0636\u0627\u0641\u0629\u061f", reply_markup=notify_kb)
            # After remove: ask for optional free-text reason
            elif action == "remove" and db_user:
                context.user_data["pending_remove_reason"] = {"product_id": product["id"], "qty": qty, "user_id": db_user["id"]}
                context.user_data["state"] = STATE_REMOVE_REASON
                skip_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ تخطي", callback_data="remskip")],
                ])
                await update.message.reply_text("💬 سبب الإخراج (اختياري):\nاكتب السبب أو اضغط تخطي", reply_markup=skip_kb)
        except ValueError as e:
            await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
        return

    elif state == STATE_SEARCH_ADMIN:
        await process_search(update, context, text, admin_view=True)
    elif state == STATE_ALERT_MSG:
        alert_text = text.strip()
        if not alert_text:
            await update.message.reply_text("❌ أرسل نص التنبيه.", reply_markup=get_input_keyboard())
            return
        try:
            users = await asyncio.to_thread(_db_fetch_broadcast_users)
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
            await update.message.reply_text(result_msg, reply_markup=await asyncio.to_thread(get_admin_keyboard))
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ داخلي.", reply_markup=get_input_keyboard())
        context.user_data.clear()
        return


    else:
        context.user_data.clear()
        await update.message.reply_text("\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647.", reply_markup=await asyncio.to_thread(get_admin_keyboard))


async def handle_customer_message(update, context):
    text = update.message.text.strip()
    state = context.user_data.get("state")
    user = update.effective_user

    # ── State timeout: expire stale states after 10 minutes ──
    if state and _is_state_expired(context):
        logging.info(f"[STATE_TIMEOUT] Customer {user.id} state '{state}' expired, clearing.")
        context.user_data.clear()
        state = None
        await update.message.reply_text("⏰ انتهت مهلة العملية. ابدأ من جديد.", reply_markup=await asyncio.to_thread(get_customer_keyboard))

    # Handle customer buttons (clear state first)
    cust_buttons = [BTN_CUST_ALERT, BTN_CUST_ADD, BTN_CUST_REMOVE, BTN_CUST_SEARCH, BTN_CUST_MOVEMENTS, BTN_CUST_CANCEL, BTN_CUST_ORDER, BTN_CUST_MY_ORDERS]
    if text in cust_buttons:
        context.user_data.clear()
        state = None

        if text == BTN_CUST_CANCEL:
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
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
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
            return
        await process_search(update, context, text, admin_view=False)
        context.user_data.clear()
        return

    # State: waiting_order_photo - صورة وصل فقط
    if state in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
            return
        await update.message.reply_text(
            "📷 يرجى إرسال صورة الوصل فقط.\n"
            "أو اضغط ❌ إلغاء.",
            reply_markup=get_input_keyboard(),
        )
        return

    # State: waiting_pact_qty (admin-only stock operations)
    if state == "waiting_pact_qty":
        # Security: stock add/remove should only be done by admins
        if not await asyncio.to_thread(is_admin, user.id):
            context.user_data.clear()
            await update.message.reply_text("⛔ هذا الإجراء متاح للأدمن فقط.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
            return
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا." + chr(10) + "مثال: 10 أو 10" + unit)
            return
        qty = parsed
        try:
            updated = await asyncio.to_thread(db_update_quantity, product["id"], qty, action)
            db_user = await asyncio.to_thread(get_user, user.id)
            # Transaction logging deferred to callback_add_notify_handler
            # (skip logging here — will log only if NOT silent correction)
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
                    [InlineKeyboardButton("📝 تصحيح المخزون فقط", callback_data="addnotify_silent")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user}
                await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
                return  # Don't clear user_data yet — callback needs pending_add_notify
            # After remove: ask for optional free-text reason
            elif action == "remove" and db_user:
                context.user_data["pending_remove_reason"] = {"product_id": product["id"], "qty": qty, "user_id": db_user["id"]}
                context.user_data["state"] = STATE_REMOVE_REASON
                skip_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ تخطي", callback_data="remskip")],
                ])
                await update.message.reply_text("💬 سبب الإخراج (اختياري):\nاكتب السبب أو اضغط تخطي", reply_markup=skip_kb)
                return
        except ValueError as e:
            await update.message.reply_text("❌ حدث خطأ داخلي.")
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ داخلي.")
        context.user_data.clear()
        return

    # State: waiting_extract_qty
    if state == "waiting_extract_qty":
        product = context.user_data.get("cust_product")
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            await update.message.reply_text("❌ أرسل عددا صحيحا.")
            return
        qty = parsed
        if qty <= 0:
            await update.message.reply_text("❌ الكمية يجب أن تكون أكبر من 0.")
            return
        db_user = await asyncio.to_thread(get_user, user.id)
        if not db_user:
            await update.message.reply_text("❌ حسابك غير مسجل.")
            return
        try:
            req = await asyncio.to_thread(create_extraction_request, product["id"], qty, db_user["id"])
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
                    logging.error("[EXTRACT] Notify admin failed: " + str(e), exc_info=True)
        except Exception as e:
            logging.error("[EXTRACT] Request failed: " + str(e), exc_info=True)
            await update.message.reply_text("❌ حدث خطأ داخلي.")
        context.user_data.clear()
        return


    # State: remove reason (optional free-text)
    if state == STATE_REMOVE_REASON:
        reason_text = text.strip()
        pending = context.user_data.get("pending_remove_reason")
        if pending and reason_text:
            await asyncio.to_thread(_db_update_tx_reason, pending.get("tx_id"), pending.get("product_id"), pending.get("user_id"), reason_text)
        context.user_data.pop("pending_remove_reason", None)
        context.user_data.clear()
        if reason_text:
            await update.message.reply_text("✅ تم تسجيل السبب: " + reason_text, reply_markup=await asyncio.to_thread(get_customer_keyboard))
        else:
            await update.message.reply_text("✅ تم الإخراج.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
        return

    # Default: treat as search query
    if not text:
        await update.message.reply_text("أرسل اسم السلعة لمعرفة إذا كانت متوفرة.")
        return
    await process_search(update, context, text, admin_view=False)


async def route_message(update, context):
    user = update.effective_user
    text = update.message.text.strip()
    if not await asyncio.to_thread(is_admin, user.id) and not await asyncio.to_thread(is_approved, user.id):
        if text == "/cancel":
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        elif text != "/start":
            await update.message.reply_text("\u23f3 \u062d\u0633\u0627\u0628\u0643 \u0642\u064a\u062f \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629.\n\u064a\u062c\u0628 \u0627\u0644\u0627\u0646\u062a\u0638\u0627\u0631 \u062d\u062a\u0649 \u064a\u0641\u0639\u0644\u0643 \u0627\u0644\u0645\u062f\u064a\u0631.")
        return
    if text == "/cancel":
        context.user_data.clear()
        if await asyncio.to_thread(is_admin, user.id):
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
        else:
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        return
    await asyncio.to_thread(register_user, telegram_id=user.id, username=user.username, first_name=user.first_name)
    if await asyncio.to_thread(is_admin, user.id):
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ═══════════════════════════════════════════════════════════════
#  SECTION 23: EXTRACTION PERMISSION & REQUEST HANDLERS (Callbacks)
#  Functions: callback_extract_perm_handler, _show_pending_extractions,
#              callback_extract_request_handler, callback_extract_approve_handler
# ═══════════════════════════════════════════════════════════════

async def callback_extract_perm_handler(update, context):
    """Handle grant/revoke extraction permission from users list."""
    query = update.callback_query
    await query.answer()
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data
    try:
        if data.startswith("ext_grant_"):
            tid = int(data.split("_")[2])
            await asyncio.to_thread(set_extract_permission, tid, True)
            db_u = await asyncio.to_thread(get_user, tid)
            uname = db_u.get("username") or str(tid) if db_u else str(tid)
            await query.edit_message_text("✅ تم منح صلاحية الإخراج لـ " + uname)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="\🛑 تم منحك صلاحية طلب إخراج السلع.\nابحث عن سلعة واضغط طلب إخراج.",
                )
            except Exception as e:
                logging.error("[EXTRACT] Notify user failed: " + str(e), exc_info=True)
        elif data.startswith("ext_revoke_"):
            tid = int(data.split("_")[2])
            await asyncio.to_thread(set_extract_permission, tid, False)
            db_u = await asyncio.to_thread(get_user, tid)
            uname = db_u.get("username") or str(tid) if db_u else str(tid)
            await query.edit_message_text("❌ تم سحب صلاحية الإخراج من " + uname)
            try:
                await context.bot.send_message(
                    chat_id=tid,
                    text="❌ تم سحب صلاحية طلب إخراج السلع.",
                )
            except Exception as e:
                logging.error("[EXTRACT] Notify user failed: " + str(e), exc_info=True)
        elif data == "ext_pending_list":
            await _show_pending_extractions(update, context)
    except Exception as e:
        logging.error("[EXTRACT_PERM] Error: " + str(e), exc_info=True)
        try:
            await query.edit_message_text("❌ حدث خطأ داخلي.")
        except Exception:
            pass

async def _show_pending_extractions(update, context):
    """Show list of pending extraction requests."""
    query = update.callback_query
    try:
        pending = await asyncio.to_thread(get_pending_extraction_requests)
    except Exception as e:
        logging.error("[EXTRACT] Get pending failed: " + str(e), exc_info=True)
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
    if not await asyncio.to_thread(can_extract, user_id):
        await query.answer("❌ ليس لديك صلاحية الإخراج.", show_alert=True)
        return
    data = query.data
    try:
        product_id = int(data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    row = await asyncio.to_thread(_db_fetch_product_by_id, product_id)
    if not row:
        await query.edit_message_text("❌ المنتج غير موجود.")
        return
    product = row
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
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data
    parts = data.split("_")
    action = parts[1]
    try:
        request_id = int(parts[2])
    except (IndexError, ValueError):
        return
    admin_db = await asyncio.to_thread(get_user, query.from_user.id)
    admin_uid = admin_db["id"] if admin_db else 0
    try:
        success, msg = await asyncio.to_thread(process_extraction_request, request_id, action == "approve", admin_uid)
        await query.edit_message_text(msg)
        if success:
            try:
                req_info = await asyncio.to_thread(_db_fetch_extraction_request_info, request_id)
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
        logging.error("[EXTRACT_APPROVE] Error: " + str(e), exc_info=True)
        try:
            await query.edit_message_text("❌ حدث خطأ داخلي.")
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
#  SECTION 24: DIAGNOSTIC SYSTEM & GLOBAL ERROR HANDLER
#  Functions: _diag_analyze, _diag_find_source, report_diag,
#              error_handler (Conflict + full diagnostic to admin)
# ═══════════════════════════════════════════════════════════════

# --- Global reference filled during build_application() ---
_bot_app = None  # Application instance, needed by report_diag


def _diag_analyze(error):
    """Analyze an exception and return a human-readable diagnosis in Arabic.
    Returns: (title, explanation, likely_cause, suggestion)"""
    err_type = type(error).__name__
    err_msg = str(error)

    # ---- Telegram API errors ----
    if "Message is too long" in err_msg or "message is too long" in err_msg:
        return ("رسالة طويلة جداً",
                "النص المطلوب إرساله يتجاوز 4096 حرف (حد تيليجرام).",
                "دالة _safe_send_html لم تقسم النص بشكل صحيح، أو النص ذو سطر واحد طويل جداً.",
                "تأكد أن _safe_send_html يقسم السطور الطويلة أيضاً، وليس فقط عند حرف n.")

    if "Message to edit not found" in err_msg or "message can't be edited" in err_msg.lower():
        return ("تعديل رسالة غير موجودة",
                "محاولة تعديل رسالة تم حذفها أو انتهت صلاحيتها.",
                "المستخدم ضغط على زر بعد فترة طويلة، أو الرسالة حُذفت يدوياً، أو تم إرسال رسالة جديدة بدل التعديل ثم حُذفت الأصلية.",
                "كل edit_message_text يجب أن يكون داخل try/except، و fallback بـ send_message.")

    if "Bad Request" in err_msg and ("can't parse" in err_msg.lower() or "parse" in err_msg.lower()):
        return ("خطأ تحليل HTML/Markdown",
                "تيليجرام رفض النص لوجود HTML أو Markdown غير صالح.",
                "وجود حرف <> أو & غير مُهرب، أو وسم HTML مفتوح بدون إغلاق، أو محاولة إرسال HTML بدون تحديد parse_mode.",
                "تأكد من إضافة parse_mode=ParseMode.HTML، وأن كل <> و & مُهربة بـ &lt; &gt; &amp;.")

    if "QueryIdInvalid" in err_msg or "query_id invalid" in err_msg.lower():
        return ("Callback منتهي الصلاحية",
                "المستخدم ضغط على زر قديم expired callback query.",
                "الزُر ظهر منذ أكثر من 10 ثوانٍ بدون ضغط، أو اتصال بطيء.",
                "هذا طبيعي مع الأزرار القديمة. تأكد من query.answer() في البداية.")

    if "Timed out" in err_msg or "timeout" in err_msg.lower():
        return ("انتهاء وقت الانتظار",
                "العملية استغرقت وقتاً أطول من المسموح (غالباً استعلام DB بطيء أو اتصال بالشبكة).",
                "قاعدة البيانات بطيئة، أو Pool الاتصال فارغ/مُجمّد.",
                "1) تأكد من asyncio.to_thread لكل استعلام DB.\n2) تحقق من فتح/إغلاق الاتصالات.\n3) تأكد أن release_connection يُستدعى دائماً.")

    if "Conflict" in err_msg or "terminated by other getUpdates" in err_msg.lower():
        return ("تعارض بين نسختين",
                "يوجد أكثر من نسخة من البوت تعمل بنفس التوكن.",
                "أعد تشغيل البوت أو أوقف النسخة القديمة.",
                "لا حاجة لإصلاح — يتعافى تلقائياً.")

    if "Chat not found" in err_msg or "chat not found" in err_msg.lower():
        return ("محادثة غير موجودة",
                "محاولة إرسال رسالة لشخص حظر البوت أو لم يبدأ محادثة معه.",
                "المستخدم لم يبدأ conversation مع البوت بعد، أو حظره.",
                "تأكد أن المستخدم بدأ /start أولاً.")

    if "Forbidden" in err_msg or "bot was blocked by the user" in err_msg.lower():
        return ("ممنوع الوصول",
                "المستخدم حظر البوت.",
                "البوت لا يستطيع إرسال رسائل لهذا المستخدم.",
                "المستخدم يجب أن يُلغي حظر البوت.")

    # ---- psycopg2 / DB errors ----
    if "psycopg2" in str(type(error).__module__) or "PostgreSQL" in err_msg:
        if "connection" in err_msg.lower() and ("refused" in err_msg.lower() or "closed" in err_msg.lower() or "timeout" in err_msg.lower()):
            return ("فقدان اتصال قاعدة البيانات",
                    "الاتصال بقاعدة البيانات انقطع أو انتهى.",
                    "إما أن الخادم أغلق الاتصال بسبب idle timeout، أو تجاوز Pool حد الاتصالات.",
                    "1) تحقق من keep-alive (الخاصية pool_recycle).\n2) تأكد من release_connection.\n3) أعد تشغيل البوت لإعادة إنشاء Pool.")

        if "unique constraint" in err_msg.lower() or "duplicate key" in err_msg.lower():
            return ("تكرار في قاعدة البيانات",
                    "محاولة إدخال سجل موجود مسبقاً (مفتاح فريد مُكرّر).",
                    "منتج بنفس الاسم موجود بالفعل، أو مستخدم بنفس telegram_id.",
                    "أضف existence check قبل INSERT: SELECT أولاً، إذا وجدت → UPDATE بدل INSERT.")

        if "deadlock" in err_msg.lower():
            return ("حالة انتظار متبادل (Deadlock)",
                    "عمليتان تتعارضان على نفس الصف في قاعدة البيانات.",
                    "عمليتان متزامنتان تحاولان تعديل نفس المنتج/المستخدم.",
                    "استخدم RETRY مع sleep قصير. أضف UNIQUE constraint في DB.")

    # ---- Python built-in errors ----
    if err_type == "KeyError":
        return ("مفتاح غير موجود (KeyError)",
                "محاولة الوصول لعنصر في قاموس/ديكشنري بمفتاح غير موجود.",
                "الكود يفترض وجود حقل في DB أو user_data لكنه غير موجود.\nالمفتاح المفقود: " + repr(err_msg),
                "أضف .get() بدل [] للوصول الآمن، أو تحقق بـ 'if key in dict'.")

    if err_type == "IndexError":
        return ("فهرس خارج المدى (IndexError)",
                "محاولة الوصول لعنصر في قائمة بفهرس أكبر من حجمها.",
                "قائمة فارغة أو أقصر مما يُفترض.\nالفهرس: " + repr(err_msg),
                "تحقق من len() قبل الوصول، أو أضف if واقي.")

    if err_type == "TypeError":
        return ("نوع خاطئ (TypeError)",
                "استخدام دالة أو عملية على نوع بيانات خاطئ.",
                repr(err_msg),
                "تحقق من أن القيمة من النوع الصحيح قبل استخدامها.")

    if err_type == "AttributeError":
        if "NoneType" in err_msg and "has no attribute" in err_msg:
            return ("استدعاء على None (AttributeError)",
                    "محاولة استدعاء دالة على قيمة None.",
                    err_msg,
                    "أضف فحص if x is not None قبل الاستدعاء.")
        return ("صفة غير موجودة (AttributeError)",
                "محاولة الوصول لصفة/دالة غير موجودة.",
                err_msg,
                "تأكد من الاسم الصحيح. مثلاً update.get_bot() → context.bot في PTB v21.")

    if err_type == "ValueError":
        return ("قيمة غير صالحة (ValueError)",
                "قيمة غير صالحة مُمرّرة لدالة.",
                repr(err_msg),
                "تأكد من أن القيمة بالصيغة الصحيحة.")

    if err_type == "RuntimeError":
        return ("خطأ تشغيلي (RuntimeError)",
                repr(err_msg),
                "",
                "تحقق من شروط التشغيل.")

    # ---- Generic fallback ----
    return ("خطأ غير معروف",
            err_type + ": " + err_msg[:200],
            "لم يتم التعرف على نوع الخطأ.",
            "افحص التفاصيل التقنية أدناه.")


def _diag_find_source(error):
    """Extract the most relevant source line from traceback.
    Returns: (filename, lineno, function_name, code_line)"""
    tb = traceback.extract_tb(error.__traceback__)
    # Walk from bottom (most recent) to top
    for frame in reversed(tb):
        # Skip library frames, focus on bot.py
        if frame.filename and "bot.py" in frame.filename:
            return (os.path.basename(frame.filename), frame.lineno, frame.name, frame.line or "")
    # If no bot.py frame, return the innermost frame
    if tb:
        frame = tb[-1]
        return (os.path.basename(frame.filename), frame.lineno, frame.name, frame.line or "")
    return ("?", 0, "?", "?")


async def report_diag(bot, chat_id, error, context_tag=""):
    """Send a full diagnostic report to the FIRST ADMIN (not to the requesting user).
    context_tag: short label like [MOVEMENTS] or [ADD_PRODUCT]."""
    # Security: always send diagnostic to first admin, never to customer
    if ADMIN_IDS:
        chat_id = ADMIN_IDS[0]
    title, explanation, cause, suggestion = _diag_analyze(error)
    fname, lineno, func, code_line = _diag_find_source(error)
    err_type = type(error).__name__
    err_msg = str(error)

    # Build report
    report = "🔧 تقرير تشخيص الخطأ" + ((" " + context_tag) if context_tag else "")
    report += chr(10) + "━" * 30
    report += chr(10) + chr(10)
    report += "❌ المشكلة: " + title
    report += chr(10) + chr(10)
    report += "📝 الشرح: " + explanation
    report += chr(10) + chr(10)
    report += "🔍 السبب المحتمل: " + (cause or "غير محدد")
    report += chr(10) + chr(10)
    report += "💡 الاقتراح: " + (suggestion or "افحص الكود.")
    report += chr(10) + chr(10)
    report += "━" * 30
    report += chr(10) + "🔬 التفاصيل التقنية:"
    report += chr(10) + "نوع الخطأ: " + err_type
    report += chr(10) + "الرسالة: " + err_msg[:300]
    report += chr(10) + "الموقع: " + fname + " سطر " + str(lineno) + " في " + func + "()"
    if code_line:
        report += chr(10) + "الكود: " + code_line.strip()[:200]

    # Send — split if too long
    if len(report) > 3900:
        # Keep main report, show truncated traceback separately
        tb_text = traceback.format_exc()
        main_report = report
        report = main_report[:3800] + chr(10) + chr(10) + "(تم اقتطاع — يوجد stack trace كامل في السجلات)"

    try:
        await bot.send_message(chat_id=chat_id, text=report)
    except Exception:
        # Last resort: log it
        logging.error("[DIAG] Failed to send diagnostic report: " + report[:200])


async def error_handler(update, context):
    """Global error handler: sends diagnostic report to admin."""
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logging.warning("[BOT] Conflict detected - another instance running. Will recover automatically.")
        return

    logging.error("[BOT ERROR] %s: %s", type(context.error).__name__, context.error, exc_info=True)

    # Determine admin chat_id to send diagnostic report
    diag_chat_id = None
    if update and update.effective_user:
        diag_chat_id = update.effective_user.id
    elif ADMIN_IDS:
        diag_chat_id = ADMIN_IDS[0]

    if diag_chat_id and _bot_app:
        try:
            await report_diag(
                bot=_bot_app.bot,
                chat_id=diag_chat_id,
                error=context.error,
                context_tag="[GLOBAL]")
        except Exception:
            pass

    # Also notify the user who triggered the error
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ حدث خطأ. تم إرسال تقرير تشخيصي للأدمن.",
                parse_mode=ParseMode.HTML)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
#  SECTION 25: APPLICATION BUILDER & HANDLER REGISTRATION
#  Functions: build_application, callback_admin_done_handler,
#              callback_go_start_handler
#  Registers: 7 commands, 30 callbacks, 3 message handlers, 1 error handler
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


async def callback_admin_done_handler(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0
    if not await asyncio.to_thread(is_admin, user_id):
        await query.answer("⛔ هذا الإجراء متاح للأدمن فقط.", show_alert=True)
        return
    try:
        await query.delete_message()
    except Exception:
        pass
    await context.bot.send_message(chat_id=query.message.chat_id, text="✅ القائمة الرئيسية:", reply_markup=await asyncio.to_thread(get_admin_keyboard))


async def callback_go_start_handler(update, context):
    """ينفّذ /start عند ضغط المستخدم على زر التحديث."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    await asyncio.to_thread(sync_user_info, user)

    db_user = await asyncio.to_thread(get_user, user.id)
    name = user.first_name or ""

    if await asyncio.to_thread(is_admin, user.id):
        try:
            await query.edit_message_text(
                "\U0001f44b مرحباً <b>" + name + "</b>\n\n"
                "\U0001f3e2 أنت مدير النظام.\n"
                "\U0001f4cb استخدم الأزرار أدناه لإدارة المخزون.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=await asyncio.to_thread(get_admin_keyboard),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=await asyncio.to_thread(get_admin_keyboard))
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
                reply_markup=await asyncio.to_thread(get_customer_keyboard),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=await asyncio.to_thread(get_customer_keyboard))


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS DISPLAY & CALLBACKS
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
#  SECTION 26: INCOMING ORDER HANDLERS & RECEIPT PHOTO
#  Functions: _show_admin_incoming_orders, _show_customer_orders,
#              handle_receipt_photo, callback_incoming_order_handler,
#              _get_telegram_id
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  SECTION 26: INCOMING ORDER HANDLERS & RECEIPT PHOTO
#  Functions: _show_admin_incoming_orders, _show_customer_orders,
#              handle_receipt_photo, callback_incoming_order_handler,
#              _get_telegram_id
# ═══════════════════════════════════════════════════════════════
async def _show_admin_incoming_orders(update, context):
    """عرض طلبات التوريد - كل طلب برسالة منفصلة: صورة + أزرار جاهز/غير جاهز."""
    all_orders = await asyncio.to_thread(_db_fetch_incoming_orders)

    if not all_orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=await asyncio.to_thread(get_admin_keyboard))
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

    await update.message.reply_text("📤 عدد الطلبات: " + str(len(all_orders)), reply_markup=await asyncio.to_thread(get_admin_keyboard))


async def _show_customer_orders(update, context):
    """عرض طلبات التوريد المعلّقة + قيد التجهيز للمستخدم."""
    user = update.effective_user
    db_user = await asyncio.to_thread(get_user, user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
        return
    orders = await asyncio.to_thread(get_user_pending_orders, db_user["id"])
    if not orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
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
    await update.message.reply_text("\n".join(lines), reply_markup=await asyncio.to_thread(get_customer_keyboard))


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

    db_user = await asyncio.to_thread(get_user, user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
        context.user_data.clear()
        return
    try:
        order = await asyncio.to_thread(create_incoming_order, db_user["id"], "طلب بوصل صورة", 1, photo_file_id=file_id)
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
            reply_markup=await asyncio.to_thread(get_customer_keyboard),
        )
        logging.info("[INCOMING] Photo order #" + str(order_id) + " by user " + str(user.id))
    except Exception as e:
        logging.error("[INCOMING] Failed: " + str(e), exc_info=True)
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=await asyncio.to_thread(get_customer_keyboard))
    context.user_data.clear()


async def callback_incoming_order_handler(update, context):
    """معالجة أزرار المدير: جاهز / غير جاهز."""
    query = update.callback_query
    if not await asyncio.to_thread(is_admin, query.from_user.id):
        await query.answer("❌ هذا الأمر للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data
    bot = context.bot

    # ── 1. طلب جاهز في المخزن ──
    if data.startswith("io_ready_"):
        order_id = int(data.split("_")[-1])
        order = await asyncio.to_thread(complete_incoming_order, order_id)
        if not order:
            try:
                await query.edit_message_caption(caption="❌ الطلب غير موجود.")
            except Exception:
                try:
                    await query.edit_message_text("❌ الطلب غير موجود.")
                except Exception:
                    pass
            return

        tg_id = await asyncio.to_thread(_get_telegram_id, order["requester_id"])
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
        order = await asyncio.to_thread(_db_fetch_incoming_order_with_user, order_id)
        if order:
            tg_id = order.get("telegram_id")

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
    global _bot_app
    app = Application.builder().token(BOT_TOKEN).build()
    _bot_app = app  # Reference for diagnostic system

    # ── Middleware: تحديث بيانات المستخدم تلقائياً عند أي تفاعل ──
    async def auto_sync_user(update, context):
        user = update.effective_user
        if user:
            await asyncio.to_thread(sync_user_info, user)

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
    app.add_handler(CallbackQueryHandler(callback_remskip_handler, pattern="^remskip$"))
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
    app.add_handler(CallbackQueryHandler(callback_mov_date_handler, pattern="^mov[d,p]_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_worker_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_area_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_back_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_remove_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invsel_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_plen_"))
    app.add_handler(CommandHandler("dedup", dedup_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_backup_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)
    return app


# ═══════════════════════════════════════════════════════════════
#  SECTION 27: EMAIL BACKUP, DAILY AUTO-BACKUP & BACKGROUND LOOPS
#  Functions: _fetch_latest_backup_from_email, _send_backup_email,
#              _daily_backup, _daily_backup_loop, _keep_alive
# ═══════════════════════════════════════════════════════════════


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
        logging.error("[IMAP] Failed to fetch backup: " + str(e), exc_info=True)
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
        msg["Subject"] = "\U0001f4be \u0646\u0633\u062e\u0629 \u0627\u062d\u062a\u064a\u0627\u0637\u064a\u0629 - \u0627\u0644\u0645\u062e\u0632\u0646 " + datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
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
        logging.error("[BACKUP] Email failed: " + str(e), exc_info=True)
        return False


async def _daily_backup(bot):
    global _last_backup_data
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
    try:
        backup = await asyncio.to_thread(db_export_backup)
        _last_backup_data = backup
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d_%H-%M")
        filename = "backup_" + now_str + ".json"
        sent = _send_backup_email(file_bytes, filename)
        if sent:
            logging.info("[BACKUP] Daily backup sent to email (" + today + ")")
        else:
            logging.warning("[BACKUP] Email not configured, backup saved in memory only")
    except Exception as e:
        logging.error("[BACKUP] Daily backup failed: " + str(e), exc_info=True)


async def _daily_backup_job(bot):
    """APScheduled job: run daily backup at midnight Algeria time."""
    logging.info("[BACKUP] Running scheduled daily backup...")
    await _daily_backup(bot)

_scheduler = None

def _start_scheduler(bot):
    """Create and start APScheduler with all periodic jobs."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone="Africa/Algiers")
    # Daily backup at 00:00
    _scheduler.add_job(_daily_backup_job, CronTrigger(hour=0, minute=0), args=[bot], id="daily_backup", replace_existing=True)
    # Weekly report Sunday at 23:00
    _scheduler.add_job(_weekly_report_job, CronTrigger(day_of_week="sun", hour=23, minute=0), args=[bot], id="weekly_report", replace_existing=True)
    # Keep-alive every 3 minutes
    _scheduler.add_job(_keep_alive_ping, IntervalTrigger(minutes=3), id="keep_alive", replace_existing=True)
    _scheduler.start()
    logging.info("[SCHEDULER] APScheduler started: daily_backup(00:00), weekly_report(Sun 23:00), keep_alive(3min)")

async def _keep_alive_ping():
    """APScheduled job: ping Render URL + Neon DB to prevent spin-down."""
    external_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else ""
    if not external_url:
        port = int(os.getenv("PORT", 10000))
        external_url = "http://127.0.0.1:" + str(port) + "/"
    ping_url = external_url.rstrip("/") + "/"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(ping_url) as r:
                if r.status != 200:
                    logging.warning("[KEEP-ALIVE] Render ping status " + str(r.status))
        await asyncio.to_thread(_db_ping)
    except Exception as e:
        logging.warning("[KEEP-ALIVE] Ping failed: " + str(e))





if __name__ == "__main__":
    import time
    # ── Initialize database with retry (Neon cold-start can be slow) ──
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            print("[BOT] Initializing database (attempt " + str(attempt) + "/" + str(max_retries) + ")...")
            init_db()
            print("[DB] Schema initialized successfully.")
            break
        except Exception as e:
            print("[DB] Schema init failed (attempt " + str(attempt) + "): " + str(e))
            if attempt < max_retries:
                wait = attempt * 3  # 3, 6, 9, 12 seconds
                print("[DB] Retrying in " + str(wait) + "s...")
                time.sleep(wait)
            else:
                print("[DB] FATAL: Could not initialize database after " + str(max_retries) + " attempts.")
                raise

    try:
        seed_sample_products()
    except Exception as e:
        print("[DB] Seed failed (non-fatal): " + str(e))

    # init_db() already creates the pool via get_connection(), but ensure it's ready
    try:
        init_pool()
    except Exception as e:
        print("[DB] Pool init failed: " + str(e))
        raise

    try:
        _init_low_stock_ids()
    except Exception as e:
        print("[INIT] Low-stock init failed (non-fatal): " + str(e))

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
        # Block non-GET requests to avoid probe attempts
        async def block_non_get(request):
            return web.Response(status=405, text='{"status":"method_not_allowed"}', content_type="application/json")
        for route_path in ["/health", "/"]:
            webhook_app.router.add_route("POST", route_path, block_non_get)
        # Start scheduler for periodic tasks in webhook mode
        _start_scheduler(tg_app.bot)
        tg_app.run_webhook(
            webhook_path=webhook_path, url=url, port=port,
            drop_pending_updates=True, webhook_app=webhook_app,
            secret_token=WEBHOOK_SECRET)
    elif render_port:
        health_app = web.Application()
        async def health(request):
            return web.Response(text='{"status":"ok"}', content_type="application/json")
        health_app.router.add_get("/health", health)
        health_app.router.add_get("/", health)
        async def block_non_get(request):
            return web.Response(status=405, text='{"status":"method_not_allowed"}', content_type="application/json")
        for route_path in ["/health", "/"]:
            health_app.router.add_route("POST", route_path, block_non_get)
        async def on_startup(aio_app):
            await tg_app.initialize()
            await tg_app.start()
            try:
                await tg_app.bot.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass
            await tg_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)
            _start_scheduler(tg_app.bot)
            print("[BOT] APScheduler enabled: daily_backup(00:00), weekly_report(Sun 23:00), keep_alive(3min)")
            print("[BOT] Polling + health + scheduler on port " + str(render_port))
        async def on_cleanup(aio_app):
            global _scheduler
            if _scheduler:
                _scheduler.shutdown(wait=False)
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        health_app.on_startup.append(on_startup)
        health_app.on_cleanup.append(on_cleanup)
        web.run_app(health_app, host="0.0.0.0", port=render_port, print=None, access_log=None)
    else:
        print("[BOT] Local polling mode")
        tg_app.run_polling(drop_pending_updates=True)