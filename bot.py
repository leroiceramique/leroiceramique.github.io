"""
Telegram Inventory Bot - Single File Version
ملف واحد يحتوي على كل شيء بدون مشاكل استيراد
"""

import os
import sys
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
import psycopg2.pool

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
    if _pool is None:
        init_pool()
    return _pool.getconn()


def release_connection(conn):
    if _pool:
        _pool.putconn(conn)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username    VARCHAR(255),
    role        VARCHAR(20) NOT NULL DEFAULT 'customer' CHECK (role IN ('admin', 'customer')),
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
            role = "admin" if telegram_id in ADMIN_IDS else "customer"
            cur.execute("""
                INSERT INTO users (telegram_id, username, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    role = EXCLUDED.role,
                    active = TRUE
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
                ORDER BY name
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
                    raise ValueError("\u0627\u0644\u0643\u0645\u064a\u0629 \u0627\u0644\u0645\u0637\u0644\u0648\u0628\u0629 (" + str(change) + ") \u0623\u0643\u0628\u0631 \u0645\u0646 \u0627\u0644\u0645\u062a\u0648\u0641\u0631 (" + str(row['quantity']) + ")")
                new_qty_expr = "GREATEST(quantity - %s, 0)"
            else:
                new_qty_expr = "quantity + %s"
            cur.execute(
                "UPDATE products SET quantity = " + new_qty_expr + " WHERE id = %s RETURNING id, name, quantity, location, minimum_stock, created_at",
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
            cur.execute("SELECT * FROM products ORDER BY name")
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
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

# Conversation states
STATE_ADD_PRODUCT = "waiting_add_product"
STATE_ADD_QTY = "waiting_add_qty"
STATE_REMOVE_QTY = "waiting_remove_qty"
STATE_EDIT_PRODUCT = "waiting_edit_product"
STATE_DELETE_PRODUCT = "waiting_delete_product"
STATE_SEARCH_ADMIN = "waiting_search_admin"

# Button labels (must match exactly)
BTN_ADD = "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629"
BTN_SEARCH = "\U0001f50d \u0628\u062d\u062b"
BTN_ADD_QTY = "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629"
BTN_REMOVE_QTY = "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629"
BTN_STOCK = "\U0001f4ca \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0632\u0648\u0646"
BTN_LOG = "\U0001f4dc \u0633\u062c\u0644 \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a"
BTN_EDIT = "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629"
BTN_DELETE = "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629"
BTN_CANCEL = "\u274c \u0625\u0644\u063a\u0627\u0621"

ALL_BUTTONS = [BTN_ADD, BTN_SEARCH, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_STOCK, BTN_LOG, BTN_EDIT, BTN_DELETE, BTN_CANCEL]


def format_product_admin(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    status_emoji = "\U0001f534" if qty <= min_stock else "\U0001f7e2"
    location = product.get("location", "") or "-"
    return (
        "\U0001f4e6 " + product["name"] + "\n"
        "   \u0627\u0644\u0643\u0645\u064a\u0629: " + str(qty) + "\n"
        "   \u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649: " + str(min_stock) + "\n"
        "   \u0627\u0644\u0645\u0643\u0627\u0646: " + location + "\n"
        "   \u0627\u0644\u062d\u0627\u0644\u0629: " + status_emoji
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
        alert_lines = ["\u26a0\ufe0f \u062a\u0646\u0628\u064a\u0647: \u0645\u062e\u0632\u0648\u0646 \u0645\u0646\u062e\u0641\u0636\u0646"]
        for p in low_stock:
            alert_lines.append(
                "  \u2022 " + p["name"] + " \u2014 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(p["quantity"]) + " (\u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649: " + str(p["minimum_stock"]) + ")"
            )
        alert_text = "\n".join(alert_lines)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=alert_text)
            except Exception:
                pass
    except Exception:
        pass


def parse_add_input(text):
    parts = [p.strip() for p in text.split(",")]
    if not parts or not parts[0]:
        return None
    result = {"name": parts[0], "quantity": 0, "location": "", "minimum_stock": 5}
    if len(parts) > 1 and parts[1].isdigit():
        result["quantity"] = int(parts[1])
    if len(parts) > 2 and parts[2]:
        result["location"] = parts[2]
    if len(parts) > 3 and parts[3].isdigit():
        result["minimum_stock"] = int(parts[3])
    return result


def parse_qty_input(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════
#  ADMIN KEYBOARD (ReplyKeyboard - appears at bottom of screen)
# ═══════════════════════════════════════════════════════════

def get_admin_keyboard():
    keyboard = [
        [BTN_ADD, BTN_SEARCH],
        [BTN_ADD_QTY, BTN_REMOVE_QTY],
        [BTN_STOCK, BTN_LOG],
        [BTN_EDIT, BTN_DELETE],
        [BTN_CANCEL],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_input_keyboard():
    """Simplified keyboard shown during multi-step input."""
    keyboard = [[BTN_CANCEL]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(telegram_id=user.id, username=user.username)
    if is_admin(user.id):
        text = ("\u0645\u0631\u062d\u0628\u0627\u064b " + (user.first_name or "") + "! \U0001f44b\n\n"
                "\u0623\u0646\u062a \u0645\u062f\u064a\u0631 \u0627\u0644\u0646\u0638\u0627\u0645.\n"
                "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0644\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u062e\u0632\u0648\u0646.")
        await update.message.reply_text(text, reply_markup=get_admin_keyboard())
    else:
        text = ("\u0645\u0631\u062d\u0628\u0627\u064b " + (user.first_name or "") + "! \U0001f44b\n\n"
                "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u0625\u0630\u0627 \u0643\u0627\u0646\u062a \u0645\u062a\u0648\u0641\u0631\u0629.\n"
                "\u0645\u062b\u0627\u0644: \u0632\u064a\u062a 5 \u0644\u062a\u0631")
        await update.message.reply_text(text)
    context.user_data.clear()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "\u0645\u062b\u0627\u0644:\n"
            "  \u0632\u064a\u062a 5 \u0644\u062a\u0631\n"
            "  \u0632\u064a\u062a\n"
            "  \u062d\u0644\u064a\u0628"
        )
    await update.message.reply_text(text)


# ═══════════════════════════════════════════════════════════
#  ADMIN COMMANDS
# ═══════════════════════════════════════════════════════════

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    context.user_data["state"] = STATE_ADD_PRODUCT
    text = (
        "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629\n\n"
        "\u0623\u0631\u0633\u0644 \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a \u0628\u0647\u0630\u0627 \u0627\u0644\u062a\u0646\u0633\u064a\u0642:\n"
        "\u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0643\u0645\u064a\u0629, \u0627\u0644\u0645\u0643\u0627\u0646, \u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649\n\n"
        "\u0645\u062b\u0627\u0644:\n"
        "\u0632\u064a\u062a 5 \u0644\u062a\u0631, 50, \u0627\u0644\u0631\u0641 A, 10\n\n"
        "\u0623\u0648 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0641\u0642\u0637 (\u0627\u0644\u0643\u0645\u064a\u0629 = 0):\n"
        "\u062d\u0644\u064a\u0628"
    )
    await update.message.reply_text(text, reply_markup=get_input_keyboard())


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await send_stock_report(context.bot, update.effective_chat.id)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await send_transaction_log(context.bot, update.effective_chat.id)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    context.user_data["state"] = STATE_SEARCH_ADMIN
    await update.message.reply_text(
        "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n"
        "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
        reply_markup=get_input_keyboard(),
    )


# ═══════════════════════════════════════════════════════════
#  REPORT SENDERS
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
        type_emoji = "\U0001f4e5" if tx["type"] == "add" else "\U0001f4e4"
        date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
        operator = tx.get("operator_name") or "\u0645\u062c\u0647\u0648\u0644"
        sign = "+" if tx["type"] == "add" else "-"
        lines.append(
            type_emoji + " " + tx["product_name"] + " | "
            + sign + str(tx["quantity"]) + " | "
            + operator + " | " + date_str
        )
    await bot.send_message(chat_id=chat_id, text="\n".join(lines))


# ═══════════════════════════════════════════════════════════
#  STATE PROCESSORS
# ═══════════════════════════════════════════════════════════

async def process_add_product(update, context, text, telegram_id):
    parsed = parse_add_input(text)
    if not parsed:
        await update.message.reply_text("\u274c \u062a\u0646\u0633\u064a\u0642 \u062e\u0627\u0637\u0626. \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649.", reply_markup=get_input_keyboard())
        return
    try:
        product = db_add_product(
            name=parsed["name"],
            quantity=parsed["quantity"],
            location=parsed["location"],
            minimum_stock=parsed["minimum_stock"],
        )
        msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d!\n\n" + format_product_admin(product)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except Exception as e:
        await update.message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()


async def process_add_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text("\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0643\u0645\u064a\u0629\n\u0645\u062b\u0627\u0644: 1, 25", reply_markup=get_input_keyboard())
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_quantity(product_id, qty, "add")
        log_transaction(product_id, "add", qty, db_user["id"])
        name = updated["name"]
        msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 " + str(qty) + " \u0644\u0640 " + name + "\n\n" + format_product_admin(updated)
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
        await update.message.reply_text("\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0643\u0645\u064a\u0629\n\u0645\u062b\u0627\u0644: 1, 10", reply_markup=get_input_keyboard())
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_quantity(product_id, qty, "remove")
        log_transaction(product_id, "remove", qty, db_user["id"])
        name = updated["name"]
        msg = "\u2705 \u062a\u0645 \u0625\u062e\u0631\u0627\u062c " + str(qty) + " \u0645\u0646 " + name + "\n\n" + format_product_admin(updated)
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
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062c\u062f\u064a\u062f, \u0627\u0644\u0645\u0643\u0627\u0646 \u0627\u0644\u062c\u062f\u064a\u062f, \u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649\n"
            "\u0645\u062b\u0627\u0644: 1, \u0632\u064a\u062a 5 \u0644\u062a\u0631, \u0627\u0644\u0631\u0641 B, 15",
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
        await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0631\u0642\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0641\u0642\u0637.\n\u0645\u062b\u0627\u0644: 5", reply_markup=get_input_keyboard())
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
        await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.", reply_markup=get_input_keyboard())
        return
    results = search_products(text)
    if not results:
        await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c \u0644\u0640 " + text, reply_markup=get_input_keyboard())
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
#  SHOW PRODUCTS LIST (for add/remove qty)
# ═══════════════════════════════════════════════════════════

async def show_products_list(bot, chat_id, next_state, prompt_text):
    products = search_products("")
    if not products:
        await bot.send_message(chat_id=chat_id, text="\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639. \u0623\u0636\u0641 \u0633\u0644\u0639\u0627\u064b \u0623\u0648\u0644\u0627\u064b.", reply_markup=get_admin_keyboard())
        return
    lines = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
    for p in products:
        lines.append("  #" + str(p["id"]) + " " + p["name"] + " \u2014 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(p["quantity"]))
    await bot.send_message(chat_id=chat_id, text="\n".join(lines))
    await bot.send_message(chat_id=chat_id, text=prompt_text, reply_markup=get_input_keyboard())


# ═══════════════════════════════════════════════════════════
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user

    # Check if user pressed a menu button (not in a state)
    if state is None:
        if text == BTN_ADD:
            context.user_data["state"] = STATE_ADD_PRODUCT
            await update.message.reply_text(
                "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629\n\n"
                "\u0623\u0631\u0633\u0644 \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a \u0628\u0647\u0630\u0627 \u0627\u0644\u062a\u0646\u0633\u064a\u0642:\n"
                "\u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0643\u0645\u064a\u0629, \u0627\u0644\u0645\u0643\u0627\u0646, \u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649\n\n"
                "\u0645\u062b\u0627\u0644:\n"
                "\u0632\u064a\u062a 5 \u0644\u062a\u0631, 50, \u0627\u0644\u0631\u0641 A, 10\n\n"
                "\u0623\u0648 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0641\u0642\u0637 (\u0627\u0644\u0643\u0645\u064a\u0629 = 0):\n"
                "\u062d\u0644\u064a\u0628",
                reply_markup=get_input_keyboard(),
            )
            return

        elif text == BTN_ADD_QTY:
            await show_products_list(
                context.bot, update.effective_chat.id, STATE_ADD_QTY,
                "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629\n\n\u0623\u0631\u0633\u0644:\n\u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0643\u0645\u064a\u0629\n\n\u0645\u062b\u0627\u0644:\n1, 25",
            )
            return

        elif text == BTN_REMOVE_QTY:
            await show_products_list(
                context.bot, update.effective_chat.id, STATE_REMOVE_QTY,
                "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629\n\n\u0623\u0631\u0633\u0644:\n\u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0643\u0645\u064a\u0629\n\n\u0645\u062b\u0627\u0644:\n1, 10",
            )
            return

        elif text == BTN_SEARCH:
            context.user_data["state"] = STATE_SEARCH_ADMIN
            await update.message.reply_text(
                "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n"
                "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
                reply_markup=get_input_keyboard(),
            )
            return

        elif text == BTN_STOCK:
            await send_stock_report(context.bot, update.effective_chat.id)
            return

        elif text == BTN_LOG:
            await send_transaction_log(context.bot, update.effective_chat.id)
            return

        elif text == BTN_EDIT:
            products = search_products("")
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639.", reply_markup=get_admin_keyboard())
                return
            lines = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"])
            await update.message.reply_text("\n".join(lines))
            context.user_data["state"] = STATE_EDIT_PRODUCT
            await update.message.reply_text(
                "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629\n\n"
                "\u0623\u0631\u0633\u0644:\n"
                "\u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062c\u062f\u064a\u062f, \u0627\u0644\u0645\u0643\u0627\u0646 \u0627\u0644\u062c\u062f\u064a\u062f, \u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649\n\n"
                "\u0645\u062b\u0627\u0644:\n1, \u0632\u064a\u062a 5 \u0644\u062a\u0631, \u0627\u0644\u0631\u0641 B, 15",
                reply_markup=get_input_keyboard(),
            )
            return

        elif text == BTN_DELETE:
            products = search_products("")
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639.", reply_markup=get_admin_keyboard())
                return
            lines = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"])
            await update.message.reply_text("\n".join(lines))
            context.user_data["state"] = STATE_DELETE_PRODUCT
            await update.message.reply_text(
                "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629\n\n"
                "\u0623\u0631\u0633\u0644 \u0631\u0642\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0641\u0642\u0637:\n"
                "\u0645\u062b\u0627\u0644: 5\n\n"
                "\u26a0\ufe0f \u0633\u064a\u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0633\u0644\u0639\u0629 \u0648\u062c\u0645\u064a\u0639 \u0633\u062c\u0644\u0627\u062a\u0647\u0627 \u0646\u0647\u0627\u0626\u064a\u0627\u064b!",
                reply_markup=get_input_keyboard(),
            )
            return

        elif text == BTN_CANCEL:
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
            return

    # Process based on current state
    if state == STATE_ADD_PRODUCT:
        await process_add_product(update, context, text, user.id)
    elif state == STATE_ADD_QTY:
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
        await update.message.reply_text(
            "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u0648 \u0627\u0644\u0623\u0648\u0627\u0645\u0631:\n/add, /stock, /log, /search",
            reply_markup=get_admin_keyboard(),
        )


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u0625\u0630\u0627 \u0643\u0627\u0646\u062a \u0645\u062a\u0648\u0641\u0631\u0629.")
        return
    await process_search(update, context, text, admin_view=False)


async def route_message(update, context):
    user = update.effective_user
    text = update.message.text.strip()

    # Handle cancel command
    if text == "/cancel":
        context.user_data.clear()
        if is_admin(user.id):
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
        else:
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621. \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0633\u0644\u0639\u0629 \u0644\u0644\u0628\u062d\u062b.")
        return

    # Auto-register
    register_user(telegram_id=user.id, username=user.username)

    # Route based on role
    if is_admin(user.id):
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)



# ═══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════

async def error_handler(update, context):
    import traceback
    print("[ERROR] " + str(context.error))
    traceback.print_exc()
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623 \u063a\u064a\u0631 \u0645\u062a\u0648\u0642\u0639. \u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649.")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
#  APPLICATION BUILDER
# ═══════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_application():
    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("search", search_command))

    # Text messages (no more CallbackQueryHandler needed)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    # Error handler
    app.add_error_handler(error_handler)

    return app


# ═══════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import asyncio

    init_db()
    init_pool()

    app = build_application()
    render_port = int(os.getenv("PORT", 0))

    if WEBHOOK_URL:
        port = render_port or 10000
        webhook_path = "/webhook"
        url = WEBHOOK_URL + webhook_path

        print("[BOT] Starting in webhook mode on port " + str(port) + "...")
        print("[BOT] Webhook URL: " + url)

        from aiohttp import web

        webhook_app = web.Application()

        async def health_check(request):
            return web.Response(text='{"status":"ok"}', content_type='application/json')

        webhook_app.router.add_get('/health', health_check)

        app.run_webhook(
            webhook_path=webhook_path,
            url=url,
            port=port,
            drop_pending_updates=True,
            webhook_app=webhook_app,
        )
    elif render_port:
        # Render Web Service with polling: start health server + polling in background
        from aiohttp import web

        health_app = web.Application()

        async def health_check(request):
            return web.Response(text='{"status":"ok"}', content_type='application/json')

        health_app.router.add_get('/health', health_check)

        async def run_bot(app):
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            print("[BOT] Polling started on Render with health server on port " + str(render_port))

        async def stop_bot(app):
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

        health_app.on_startup.append(run_bot)
        health_app.on_cleanup.append(stop_bot)

        web.run_app(health_app, host="0.0.0.0", port=render_port)
    else:
        print("[BOT] Starting in polling mode...")
        app.run_polling(drop_pending_updates=True)
