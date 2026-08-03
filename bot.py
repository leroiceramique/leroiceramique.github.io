import os
import json
import datetime
import logging
import asyncio
import aiohttp
from aiohttp import web
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
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


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username    VARCHAR(255),
    role        VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (role IN ('admin', 'customer', 'pending')),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
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
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);
"""


def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            # Migration: add 'pending' role for existing databases
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE users ADD CONSTRAINT users_role_check
                    CHECK (role IN ('admin', 'customer', 'pending'));
                ALTER TABLE users ALTER COLUMN role SET DEFAULT 'pending';
            """)
        conn.commit()
        print("[DB] Schema initialized successfully.")
    except Exception as e:
        conn.rollback()
        print("[DB] Schema init failed: " + str(e))
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  USER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def register_user(telegram_id, username=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            role = "admin" if telegram_id in ADMIN_IDS else "pending"
            cur.execute("""
                INSERT INTO users (telegram_id, username, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    active = TRUE
                -- NEVER downgrade role on re-register
                -- (admin stays admin, approved customer stays customer)
                RETURNING id, telegram_id, username, role, active, created_at
            """, (telegram_id, username, role))
            user = cur.fetchone()
        conn.commit()
        return dict(user)
    except Exception as e:
        conn.rollback()
        raise
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
            cur.execute("ALTER SEQUENCE products_id_seq RESTART WITH 1")
            cur.execute("ALTER SEQUENCE transactions_id_seq RESTART WITH 1")
            cur.execute("ALTER SEQUENCE users_id_seq RESTART WITH 1")

            user_count = 0
            for u in data.get("users", []):
                cur.execute(
                    "INSERT INTO users (telegram_id, username, role, active, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (u["telegram_id"], u.get("username"), u.get("role", "customer"),
                     u.get("active", True), u.get("created_at")))
                user_count += 1

            prod_count = 0
            for p in data.get("products", []):
                cur.execute(
                    "INSERT INTO products (name, quantity, location, minimum_stock, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (p["name"], p["quantity"], p.get("location", ""),
                     p.get("minimum_stock", 5), p.get("created_at")))
                prod_count += 1

            tx_count = 0
            for t in data.get("transactions", []):
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, date) VALUES (%s, %s, %s, %s, %s)",
                    (t["product_id"], t["type"], t["quantity"], t.get("user_id"), t.get("date")))
                tx_count += 1

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
            cur.execute("""
                INSERT INTO products (name, quantity, location, minimum_stock)
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


def search_products(query):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM products
                WHERE name ILIKE %s
                ORDER BY LOWER(name)
            """, ("%" + query.strip() + "%",))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


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
            if transaction_type == "remove":
                cur.execute("SELECT quantity FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
                if row and row["quantity"] < change:
                    raise ValueError(
                        "\u0627\u0644\u0639\u062f\u062f \u0627\u0644\u0645\u0637\u0644\u0648\u0628 ("
                        + str(change)
                        + " crt) \u0623\u0643\u0628\u0631 \u0645\u0646 \u0627\u0644\u0645\u062a\u0648\u0641\u0631 ("
                        + str(row["quantity"])
                        + " crt)"
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
        conn.commit()
        return dict(product)
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
            cur.execute("""
                INSERT INTO transactions (product_id, type, quantity, user_id)
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
            cur.execute("""
                SELECT
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

BTN_ADD = "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629"
BTN_SEARCH = "\U0001f50d \u0628\u062d\u062b"
BTN_ADD_QTY = "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629"
BTN_REMOVE_QTY = "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629"
BTN_STOCK = "\U0001f4ca \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0632\u0648\u0646"
BTN_LOG = "\U0001f4dc \u0633\u062c\u0644 \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a"
BTN_EDIT = "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629"
BTN_DELETE = "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629"
BTN_CANCEL = "\u274c \u0625\u0644\u063a\u0627\u0621"
BTN_USERS = "⚙️ المستخدمين"
BTN_BACKUP = "💾 نسخ احتياطي"


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════


def format_product_admin(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    status = "\U0001f534" if qty <= min_stock else "\U0001f7e2"
    return (
        "\U0001f4e6 " + product["name"] + "\n"
        "   \u0627\u0644\u0639\u0644\u0628: " + str(qty) + " crt"
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


async def send_low_stock_alerts(bot):
    try:
        low_stock = get_low_stock_products()
        if not low_stock:
            return
        lines = ["⚠️ تنبيه: مخزون منخفضن"]
        for p in low_stock:
            lines.append(
                "  • " + p["name"]
                + " — الكمية: " + str(p["quantity"])
                + " (الحد الأدنى: " + str(p["minimum_stock"]) + ")"
            )
        text = "\n".join(lines)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=text)
            except Exception as e:
                logging.warning("[ALERT] Send failed for " + str(admin_id) + ": " + str(e))
    except Exception as e:
        logging.warning("[ALERT] Low stock check failed: " + str(e))
def parse_crt_input(text):
    """Parse quantity input: accepts '10', '10crt', '10 crt'."""
    cleaned = text.strip().lower().replace(" ", "").replace("crt", "")
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
# ═══════════════════════════════════════════════════════════


def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            [BTN_ADD, BTN_SEARCH],
            [BTN_ADD_QTY, BTN_REMOVE_QTY],
            [BTN_STOCK, BTN_LOG],
            [BTN_EDIT, BTN_DELETE],
            [BTN_USERS, BTN_BACKUP],
            [BTN_CANCEL],
        ],
        resize_keyboard=True,
    )


def get_input_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


# ═══════════════════════════════════════════════════════════
#  REPORTS
# ═══════════════════════════════════════════════════════════


async def send_stock_report(bot, chat_id):
    products = get_all_products()
    if not products:
        await bot.send_message(chat_id=chat_id, text="\U0001f4ed \u0627\u0644\u0645\u062e\u0632\u0648\u0646 \u0641\u0627\u0631\u063a. \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639 \u0628\u0639\u062f.")
        return
    lines = ["\U0001f4ca \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0632\u0648\u0646 (" + str(len(products)) + " \u0633\u0644\u0639\u0629)\n"]
    for i, p in enumerate(products, 1):
        lines.append(str(i) + ". " + format_product_admin(p))
        lines.append("")
    full_text = "\n".join(lines)
    if len(full_text) <= 4096:
        await bot.send_message(chat_id=chat_id, text=full_text)
    else:
        chunks = []
        current = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > 4000 and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        for chunk in chunks:
            await bot.send_message(chat_id=chat_id, text=chunk)


async def send_transaction_log(bot, chat_id):
    transactions = get_transactions(limit=20)
    if not transactions:
        await bot.send_message(chat_id=chat_id, text="\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0639\u0645\u0644\u064a\u0627\u062a \u0645\u0633\u062c\u0644\u0629 \u0628\u0639\u062f.")
        return
    lines = ["\U0001f4dc \u0622\u062e\u0631 20 \u0639\u0645\u0644\u064a\u0629:\n"]
    for tx in transactions:
        icon = "\U0001f4e5" if tx["type"] == "add" else "\U0001f4e4"
        date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
        op = tx.get("operator_name") or "\u0645\u062c\u0647\u0648\u0644"
        sign = "+" if tx["type"] == "add" else "-"
        lines.append(icon + " " + tx["product_name"] + " | " + sign + str(tx["quantity"]) + " crt | " + op + " | " + date_str)
    await bot.send_message(chat_id=chat_id, text="\n".join(lines))


# ═══════════════════════════════════════════════════════════
#  STATE PROCESSORS
# ═══════════════════════════════════════════════════════════


# ── Add Product: Step-by-step ──────────────────────────

async def ask_add_name(update, context):
    """Step 1: Ask for product name."""
    context.user_data["state"] = STATE_ADD_NAME
    await update.message.reply_text(
        "\U0001f4e6 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
        reply_markup=get_input_keyboard(),
    )


async def ask_add_qty(update, context, product_name):
    """Step 2: Ask for quantity (cartons)."""
    context.user_data["state"] = STATE_ADD_QTY
    context.user_data["add_name"] = product_name
    await update.message.reply_text(
        "\U0001f4e6 " + product_name + "\n"
        "\U0001f4e6 \u0643\u0645 \u0639\u062f\u062f \u0627\u0644\u0639\u0644\u0628 (crt)?",
        reply_markup=get_input_keyboard(),
    )


async def confirm_add_product(update, context, product_name, qty):
    """Step 3: Show summary with inline confirm button."""
    context.user_data["add_qty"] = qty
    context.user_data["state"] = "waiting_add_confirm"
    text = (
        "\U0001f4cb \u062a\u0623\u0643\u064a\u062f \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629:\n\n"
        "\U0001f4e6 \u0627\u0644\u0627\u0633\u0645: " + product_name + "\n"
        "\U0001f4e6 \u0627\u0644\u0639\u0644\u0628: " + str(qty) + " crt"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2705 \u062a\u0623\u0643\u064a\u062f", callback_data="add_confirm"),
         InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="add_cancel")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


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
        msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 " + str(qty) + " crt \u0644\u0640 " + updated["name"] + "\n\n" + format_product_admin(updated)
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
        msg = "\u2705 \u062a\u0645 \u0625\u062e\u0631\u0627\u062c " + str(qty) + " crt \u0645\u0646 " + updated["name"] + "\n\n" + format_product_admin(updated)
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
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062c\u062f\u064a\u062f\n\u0645\u062b\u0627\u0644: 1, \u0632\u064a\u062a 5 \u0644\u062a\u0631",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id = int(parts[0])
    kwargs = {}
    if len(parts) > 1 and parts[1]:
        kwargs["name"] = parts[1]
    if len(parts) > 2 and parts[2]:
        kwargs["location"] = parts[2]
    if len(parts) > 3 and parts[3].isdigit():
        kwargs["minimum_stock"] = int(parts[3])
    try:
        updated = db_update_product(product_id, **kwargs)
        msg = "\u2705 \u062a\u0645 \u062a\u0639\u062f\u064a\u0644 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d!\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except ValueError as e:
        await update.message.reply_text("\u274c " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e), reply_markup=get_input_keyboard())
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
        await update.message.reply_text(
            "\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c \u0644\u0640 " + text,
            reply_markup=get_input_keyboard(),
        )
        return
    if admin_view:
        header = "\U0001f50d \u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0628\u062d\u062b \u0639\u0646 " + text + " (" + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629):\n"
        lines = [header]
        for p in results:
            lines.append("  #" + str(p["id"]) + " " + format_product_admin(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())
    else:
        lines = []
        for p in results:
            lines.append(format_product_customer(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines))
    context.user_data.clear()


# ═══════════════════════════════════════════════════════════
#  SHOW PRODUCTS LIST
# ═══════════════════════════════════════════════════════════


async def start_command(update, context):
    user = update.effective_user
    register_user(telegram_id=user.id, username=user.username)
    if is_admin(user.id):
        name = user.first_name or ""
        await update.message.reply_text(
            "\u0645\u0631\u062d\u0628\u0627\u064b " + name + "! \U0001f44b\n\n\u0623\u0646\u062a \u0645\u062f\u064a\u0631 \u0627\u0644\u0646\u0638\u0627\u0645.\n\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0644\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u062e\u0632\u0648\u0646.",
            reply_markup=get_admin_keyboard(),
        )
    else:
        name = user.first_name or ""
        await update.message.reply_text(
            "\u0645\u0631\u062d\u0628\u0627\u064b " + name + "! \U0001f44b\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u0625\u0630\u0627 \u0643\u0627\u0646\u062a \u0645\u062a\u0648\u0641\u0631\u0629.\n\u0645\u062b\u0627\u0644: \u0632\u064a\u062a 5 \u0644\u062a\u0631",
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
            "/stock - \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0632\u0648\u0646 \u0627\u0644\u0643\u0627\u0645\u0644\n"
            "/log - \u0633\u062c\u0644 \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a \u0627\u0644\u0623\u062e\u064a\u0631\u0629\n"
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


async def stock_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await send_stock_report(context.bot, update.effective_chat.id)


async def log_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await send_transaction_log(context.bot, update.effective_chat.id)


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


# ── Backup Command ─────────────────────────────────────

async def backup_command(update, context):
    """Export all data as JSON file and send to admin."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    try:
        import io
        backup = db_export_backup()
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        from telegram import InputFile
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M")
        await update.message.reply_text("💾 جارٍ إعداد النسخة الاحتياطية...")
        await update.message.reply_document(
            document=InputFile(io.BytesIO(file_bytes), filename="backup_" + now_str + ".json"),
            caption=(
                "💾 نسخة احتياطية كاملة\n"
                "📄 السلع: " + str(len(backup["products"])) + "\n"
                "📜 العمليات: " + str(len(backup["transactions"])) + "\n"
                "👥 المستخدمين: " + str(len(backup["users"])) + "\n"
                "💡 احفظ هذا الملف. \n"
                "لإستيراد البيانات أرسله للبوت."
            ),
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
            context.user_data.pop("restore_data", None)
        except Exception as e:
            await query.edit_message_text("\u274c \u0641\u0634\u0644 \u0627\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f: " + str(e))
            context.user_data.pop("restore_data", None)
    elif data == "restore_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0625\u0644\u063a\u0627\u0621 \u0627\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a.")
        context.user_data.pop("restore_data", None)


# ── Users Command ──

async def users_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
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
    msg_lines = ["\u2699\ufe0f \u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645\u064a\u0646\n"]
    if pending:
        msg_lines.append("\U0001f514 \u0645\u0646\u062a\u0638\u0631\u064a\u0646 \u0627\u0644\u0645\u0648\u0627\u0641\u0642\u0629 (" + str(len(pending)) + "):")
        for u in pending:
            name = u.get("username") or str(u["telegram_id"])
            date_str = u["created_at"].strftime("%m/%d %H:%M")
            msg_lines.append("  \u2022 " + name + " (" + date_str + ")")
        msg_lines.append("")
    else:
        msg_lines.append("\u2705 \u0644\u0627 \u064a\u0648\u062c\u062f \u0637\u0644\u0628\u0627\u062a \u0645\u0639\u0644\u0642\u0629.")
    msg_lines.append("")
    msg_lines.append("\u2705 \u0627\u0644\u0645\u0633\u062a\u062e\u062f\u0645\u0648\u0646 \u0627\u0644\u0645\u0641\u0639\u0644\u064a\u0646 (" + str(len(approved)) + "):")
    for u in approved:
        role_icon = "\U0001f451" if u["role"] == "admin" else "\U0001f464"
        name = u.get("username") or str(u["telegram_id"])
        msg_lines.append("  " + role_icon + " " + name)
    await update.message.reply_text("\n".join(msg_lines), reply_markup=get_admin_keyboard())


# ═══════════════════════════════════════════════════════════
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════

    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user
    chat_id = update.effective_chat.id
    bot = context.bot

    if state is None:
        if text == BTN_ADD:
            await ask_add_name(update, context)
            return
        if text == BTN_SEARCH:
            context.user_data["state"] = STATE_SEARCH_ADMIN
            await update.message.reply_text(
                "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
                reply_markup=get_input_keyboard())
            return
        if text == BTN_ADD_QTY:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639. \u0623\u0636\u0641 \u0633\u0644\u0639\u0627\u064b \u0623\u0648\u0644\u0627\u064b.", reply_markup=get_admin_keyboard())
                return
            ls = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                ls.append("  #" + str(p["id"]) + " " + p["name"] + " \u2014 " + str(p["quantity"]) + " crt")
            await update.message.reply_text("\n".join(ls))
            context.user_data["state"] = STATE_EXISTING_ADD_QTY
            await update.message.reply_text(
                "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0639\u0644\u0628\n\n\u0623\u0631\u0633\u0644: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\n\u0645\u062b\u0627\u0644: 1, 25",
                reply_markup=get_input_keyboard())
            return
        if text == BTN_REMOVE_QTY:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639.", reply_markup=get_admin_keyboard())
                return
            ls = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                ls.append("  #" + str(p["id"]) + " " + p["name"] + " \u2014 " + str(p["quantity"]) + " crt")
            await update.message.reply_text("\n".join(ls))
            context.user_data["state"] = STATE_REMOVE_QTY
            await update.message.reply_text(
                "\u2796 \u0625\u062e\u0631\u0627\u062c \u0639\u0644\u0628\n\n\u0623\u0631\u0633\u0644: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\n\u0645\u062b\u0627\u0644: 1, 10",
                reply_markup=get_input_keyboard())
            return
        if text == BTN_STOCK:
            await send_stock_report(bot, chat_id)
            return
        if text == BTN_LOG:
            await send_transaction_log(bot, chat_id)
            return
        if text == BTN_EDIT:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639.", reply_markup=get_admin_keyboard())
                return
            ls = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                ls.append("  #" + str(p["id"]) + " " + p["name"])
            await update.message.reply_text("\n".join(ls))
            context.user_data["state"] = STATE_EDIT_PRODUCT
            await update.message.reply_text(
                "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629\n\n"
                + "\u0623\u0631\u0633\u0644: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062c\u062f\u064a\u062f\n\n"
                + "\u0645\u062b\u0627\u0644: 1, \u0632\u064a\u062a 5 \u0644\u062a\u0631",
                reply_markup=get_input_keyboard())
            return
        if text == BTN_DELETE:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639.", reply_markup=get_admin_keyboard())
                return
            ls = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                ls.append("  #" + str(p["id"]) + " " + p["name"])
            await update.message.reply_text("\n".join(ls))
            context.user_data["state"] = STATE_DELETE_PRODUCT
            await update.message.reply_text(
                "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629\n\n"
                + "\u0623\u0631\u0633\u0644 \u0631\u0642\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0641\u0642\u0637:\n"
                + "\u0645\u062b\u0627\u0644: 5\n\n"
                + "\u26a0\ufe0f \u0633\u064a\u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0633\u0644\u0639\u0629 \u0648\u062c\u0645\u064a\u0639 \u0633\u062c\u0644\u0627\u062a\u0647\u0627 \u0646\u0647\u0627\u0626\u064a\u0627\u064b!",
                reply_markup=get_input_keyboard())
            return
        if text == BTN_CANCEL:
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
            return
        await update.message.reply_text(
            "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u0648 \u0627\u0644\u0623\u0648\u0627\u0645\u0631:\n/add, /stock, /log, /search",
            reply_markup=get_admin_keyboard())
        return

    # In a state
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
        return
    if state == STATE_ADD_NAME:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.", reply_markup=get_input_keyboard())
            return
        await ask_add_qty(update, context, product_name)
    elif state == STATE_ADD_QTY:
        qty = parse_crt_input(text)
        if qty is None:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0639\u062f\u062f \u0627\u0644\u0639\u0644\u0628.\n\u0645\u062b\u0627\u0644: 10 \u0623\u0648 10crt", reply_markup=get_input_keyboard())
            return
        product_name = context.user_data.get("add_name", "")
        await confirm_add_product(update, context, product_name, qty)
    elif state == STATE_EXISTING_ADD_QTY:
        await process_add_quantity(update, context, text, user.id)
    elif state == STATE_REMOVE_QTY:
        await process_remove_quantity(update, context, text, user.id)
    elif state == STATE_EDIT_PRODUCT:
        await process_edit_product(update, context, text, user.id)
    elif state == STATE_DELETE_PRODUCT:
        await process_delete_product(update, context, text, user.id)
    elif state == STATE_SEARCH_ADMIN:
        await process_search(update, context, text, admin_view=True)
    else:
        context.user_data.clear()
        await update.message.reply_text("\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647.", reply_markup=get_admin_keyboard())


async def handle_customer_message(update, context):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u0625\u0630\u0627 \u0643\u0627\u0646\u062a \u0645\u062a\u0648\u0641\u0631\u0629.")
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
    register_user(telegram_id=user.id, username=user.username)
    if is_admin(user.id):
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ═══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════

async def error_handler(update, context):
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logging.warning("[BOT] Conflict detected - another instance running. Will recover automatically.")
        return

    import traceback
    print("[ERROR] " + str(context.error))
    traceback.print_exc()
    if update and hasattr(update, "effective_message") and update.effective_message:
        try:
            await update.effective_message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623. \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649.")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#  BUILD APP
# ═══════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


def build_application():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(callback_add_handler, pattern="^add_"))
    app.add_handler(CallbackQueryHandler(callback_approval_handler, pattern="^(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(callback_restore_handler, pattern="^restore_"))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_backup_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)
    return app


# ═══════════════════════════════════════════════════════════
#  DAILY AUTO-BACKUP
# ═══════════════════════════════════════════════════════════



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
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    _last_backup_date = today
    try:
        import io
        from telegram import InputFile
        backup = db_export_backup()
        json_str = json.dumps(backup, ensure_ascii=False, indent=2, default=str)
        file_bytes = json_str.encode("utf-8")
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d_%H-%M")
        filename = "backup_" + now_str + ".json"
        caption = ("\U0001f4be \u0646\u0633\u062e\u0629 \u0627\u062d\u062a\u064a\u0627\u0637\u064a\u0629 (" + now_str + ")\n"
                  + "\U0001f4c4 \u0627\u0644\u0633\u0644\u0639: " + str(len(backup["products"])) + "\n"
                  + "\U0001f4dc \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a: " + str(len(backup["transactions"])))
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_document(
                    chat_id=admin_id,
                    document=InputFile(io.BytesIO(file_bytes), filename=filename),
                    caption=caption)
            except Exception as e:
                logging.error("[BACKUP] Telegram send failed for " + str(admin_id) + ": " + str(e))
        _send_backup_email(file_bytes, filename)
        logging.info("[BACKUP] Daily backup completed (" + today + ")")
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
    """Ping the external Render URL every 3 min to prevent spin-down."""
    external_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else ""
    if not external_url:
        port = int(os.getenv("PORT", 10000))
        external_url = "http://127.0.0.1:" + str(port) + "/"
    ping_url = external_url.rstrip("/") + "/"
    logging.info("[KEEP-ALIVE] Target: " + ping_url)
    # Reuse one session instead of creating new each time
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    try:
        while True:
            try:
                async with session.get(ping_url) as r:
                    if r.status != 200:
                        logging.warning("[KEEP-ALIVE] Status " + str(r.status))
            except Exception as e:
                logging.warning("[KEEP-ALIVE] Ping failed: " + str(e))
            await asyncio.sleep(180)
    finally:
        await session.close()



if __name__ == "__main__":
    init_db()
    init_pool()
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
            await tg_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)
            asyncio.create_task(_keep_alive(aio_app))
            asyncio.create_task(_daily_backup_loop(tg_app.bot))
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

