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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list = [
    int(uid.strip())
    for uid in _raw_admin_ids.split(",")
    if uid.strip().isdigit()
]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Add it in .env or Render environment variables.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Add it in .env or Render environment variables.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set. Add your Telegram ID in .env or Render environment variables.")

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
                    raise ValueError("الكمية المطلوبة (" + str(change) + ") أكبر من المتوفر (" + str(row['quantity']) + ")")
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


def format_product_admin(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    status_emoji = "\U0001f534" if qty <= min_stock else "\U0001f7e2"
    location = product.get("location", "") or "-"
    return (
        "\U0001f4e6 " + product["name"] + "\n"
        "   الكمية: " + str(qty) + "\n"
        "   الحد الأدنى: " + str(min_stock) + "\n"
        "   المكان: " + location + "\n"
        "   الحالة: " + status_emoji
    )


def format_product_customer(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f7e2 متوفر"
    elif qty > 0:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f7e1 كمية محدودة"
    else:
        return "\U0001f4e6 " + product["name"] + "\n\U0001f534 غير متوفر حالياً"


async def send_low_stock_alerts(bot):
    try:
        low_stock = get_low_stock_products()
        if not low_stock:
            return
        alert_lines = ["\u26a0\ufe0f تنبيه: مخزون منخفض\n"]
        for p in low_stock:
            alert_lines.append(
                "  \u2022 " + p["name"] + " — الكمية: " + str(p["quantity"]) + " (الحد الأدنى: " + str(p["minimum_stock"]) + ")"
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
#  ADMIN KEYBOARD
# ═══════════════════════════════════════════════════════════

def get_admin_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("\U0001f4e6 إضافة سلعة", callback_data="admin_add"),
            InlineKeyboardButton("\U0001f50d بحث", callback_data="admin_search"),
        ],
        [
            InlineKeyboardButton("\u2795 إضافة كمية", callback_data="admin_add_qty"),
            InlineKeyboardButton("\u2796 إخراج كمية", callback_data="admin_remove_qty"),
        ],
        [
            InlineKeyboardButton("\U0001f4ca تقرير المخزون", callback_data="admin_stock"),
            InlineKeyboardButton("\U0001f4dc سجل العمليات", callback_data="admin_log"),
        ],
        [
            InlineKeyboardButton("\u270f\ufe0f تعديل سلعة", callback_data="admin_edit"),
            InlineKeyboardButton("\U0001f5d1\ufe0f حذف سلعة", callback_data="admin_delete"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ═══════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(telegram_id=user.id, username=user.username)
    if is_admin(user.id):
        text = ("مرحباً " + (user.first_name or "") + "! \U0001f44b\n\n"
                "أنت مدير النظام.\n"
                "استخدم القائمة أدناه لإدارة المخزون.")
        reply_markup = get_admin_keyboard()
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        text = ("مرحباً " + (user.first_name or "") + "! \U0001f44b\n\n"
                "أرسل اسم السلعة لمعرفة إذا كانت متوفرة.\n"
                "مثال: زيت 5 لتر")
        await update.message.reply_text(text)
    context.user_data.clear()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_admin(user.id):
        text = (
            "\U0001f4cb أوامر الإدارة:\n\n"
            "/start - القائمة الرئيسية\n"
            "/help - عرض هذا المساعد\n"
            "/add - إضافة سلعة جديدة\n"
            "/stock - تقرير المخزون الكامل\n"
            "/log - سجل العمليات الأخيرة\n"
            "/search - بحث في المخزون\n\n"
            "\U0001f4a1 يمكنك أيضاً استخدام الأزرار أدناه."
        )
    else:
        text = (
            "\U0001f4cb المساعدة:\n\n"
            "أرسل اسم السلعة مباشرة لمعرفة توفرها.\n\n"
            "مثال:\n"
            "  زيت 5 لتر\n"
            "  زيت\n"
            "  حليب"
        )
    await update.message.reply_text(text)


# ═══════════════════════════════════════════════════════════
#  ADMIN COMMANDS
# ═══════════════════════════════════════════════════════════

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c هذا الأمر للمديرين فقط.")
        return
    context.user_data["state"] = STATE_ADD_PRODUCT
    text = (
        "\U0001f4e6 إضافة سلعة جديدة\n\n"
        "أرسل البيانات بهذا التنسيق:\n"
        "اسم السلعة, الكمية, المكان, الحد الأدنى\n\n"
        "مثال:\n"
        "زيت 5 لتر, 50, الرف A, 10\n\n"
        "أو أرسل اسم السلعة فقط (الكمية = 0):\n"
        "حليب"
    )
    await update.message.reply_text(text)


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c هذا الأمر للمديرين فقط.")
        return
    await send_stock_report(context.bot, update.effective_chat.id)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c هذا الأمر للمديرين فقط.")
        return
    await send_transaction_log(context.bot, update.effective_chat.id)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c هذا الأمر للمديرين فقط.")
        return
    context.user_data["state"] = STATE_SEARCH_ADMIN
    await update.message.reply_text(
        "\U0001f50d ابحث في المخزون\n\n"
        "أرسل اسم أو جزء من اسم السلعة:"
    )


# ═══════════════════════════════════════════════════════════
#  REPORT SENDERS (reusable from commands and callbacks)
# ═══════════════════════════════════════════════════════════

async def send_stock_report(bot, chat_id):
    products = get_all_products()
    if not products:
        await bot.send_message(chat_id=chat_id, text="\U0001f4ed المخزون فارغ. لا توجد سلع بعد.")
        return
    lines = ["\U0001f4ca تقرير المخزون (" + str(len(products)) + " سلعة)\n"]
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
    await bot.send_message(chat_id=chat_id, text="\U0001f519 العودة للقائمة:", reply_markup=get_admin_keyboard())


async def send_transaction_log(bot, chat_id):
    transactions = get_transactions(limit=20)
    if not transactions:
        await bot.send_message(chat_id=chat_id, text="\U0001f4ed لا توجد عمليات مسجلة بعد.")
        return
    lines = ["\U0001f4dc آخر 20 عملية:\n"]
    for tx in transactions:
        type_emoji = "\U0001f4e5" if tx["type"] == "add" else "\U0001f4e4"
        date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
        operator = tx.get("operator_name") or "مجهول"
        sign = "+" if tx["type"] == "add" else "-"
        lines.append(
            type_emoji + " " + tx["product_name"] + " | "
            + sign + str(tx["quantity"]) + " | "
            + operator + " | " + date_str
        )
    await bot.send_message(chat_id=chat_id, text="\n".join(lines))
    await bot.send_message(chat_id=chat_id, text="\U0001f519 العودة للقائمة:", reply_markup=get_admin_keyboard())


# ═══════════════════════════════════════════════════════════
#  STATE PROCESSORS
# ═══════════════════════════════════════════════════════════

async def process_add_product(update, context, text, telegram_id):
    parsed = parse_add_input(text)
    if not parsed:
        await update.message.reply_text("\u274c تنسيق خاطئ. حاول مرة أخرى.")
        return
    try:
        product = db_add_product(
            name=parsed["name"],
            quantity=parsed["quantity"],
            location=parsed["location"],
            minimum_stock=parsed["minimum_stock"],
        )
        msg = "\u2705 تمت إضافة السلعة بنجاح!\n\n" + format_product_admin(product)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except Exception as e:
        await update.message.reply_text("\u274c حدث خطأ: " + str(e))
    context.user_data.clear()


async def process_add_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text("\u274c التنسيق: رقم_السلعة, الكمية\nمثال: 1, 25")
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c لم يتم العثور على حسابك.")
        return
    try:
        updated = db_update_quantity(product_id, qty, "add")
        log_transaction(product_id, "add", qty, db_user["id"])
        name = updated["name"]
        msg = "\u2705 تمت إضافة " + str(qty) + " لـ " + name + "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text("\u274c " + str(e))
    except Exception as e:
        await update.message.reply_text("\u274c حدث خطأ: " + str(e))
    context.user_data.clear()


async def process_remove_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text("\u274c التنسيق: رقم_السلعة, الكمية\nمثال: 1, 10")
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c لم يتم العثور على حسابك.")
        return
    try:
        updated = db_update_quantity(product_id, qty, "remove")
        log_transaction(product_id, "remove", qty, db_user["id"])
        name = updated["name"]
        msg = "\u2705 تم إخراج " + str(qty) + " من " + name + "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text("\u274c " + str(e))
    except Exception as e:
        await update.message.reply_text("\u274c حدث خطأ: " + str(e))
    context.user_data.clear()


async def process_edit_product(update, context, text, telegram_id):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2 or not parts[0].isdigit():
        await update.message.reply_text(
            "\u274c التنسيق: رقم_السلعة, الاسم الجديد, المكان الجديد, الحد الأدنى\n"
            "مثال: 1, زيت 5 لتر, الرف B, 15\n\n"
            "(يمكنك ترك الحقول التي لا تريد تغييرها)"
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
        msg = "\u2705 تم تعديل السلعة بنجاح!\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except ValueError as e:
        await update.message.reply_text("\u274c " + str(e))
    except Exception as e:
        await update.message.reply_text("\u274c حدث خطأ: " + str(e))
    context.user_data.clear()


async def process_delete_product(update, context, text, telegram_id):
    text = text.strip()
    if not text.isdigit():
        await update.message.reply_text("\u274c أرسل رقم السلعة فقط.\nمثال: 5")
        return
    product_id = int(text)
    try:
        deleted = db_delete_product(product_id)
        if deleted:
            await update.message.reply_text("\u2705 تم حذف السلعة بنجاح.", reply_markup=get_admin_keyboard())
        else:
            await update.message.reply_text("\u274c لم يتم العثور على السلعة.")
    except Exception as e:
        await update.message.reply_text("\u274c حدث خطأ: " + str(e))
    context.user_data.clear()


async def process_search(update, context, text, admin_view=False):
    if not text:
        await update.message.reply_text("\u274c أرسل اسم أو جزء من اسم السلعة.")
        return
    results = search_products(text)
    if not results:
        await update.message.reply_text("\U0001f50d لم يتم العثور على نتائج لـ " + text)
        return
    if admin_view:
        header = "\U0001f50d نتائج البحث عن " + text + " (" + str(len(results)) + " نتيجة):\n"
        lines = [header]
        for p in results:
            lines.append("  #" + str(p["id"]) + " " + format_product_admin(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines))
        await update.message.reply_text("\U0001f519 العودة للقائمة:", reply_markup=get_admin_keyboard())
    else:
        lines = []
        for p in results:
            lines.append(format_product_customer(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines))
    context.user_data.clear()


# ═══════════════════════════════════════════════════════════
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user

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
            "استخدم الأزرار أدناه أو الأوامر:\n/add, /stock, /log, /search",
            reply_markup=get_admin_keyboard(),
        )


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("أرسل اسم السلعة لمعرفة إذا كانت متوفرة.")
        return
    await process_search(update, context, text, admin_view=False)


async def route_message(update, context):
    user = update.effective_user
    text = update.message.text.strip()
    if text == "/cancel":
        context.user_data.clear()
        if is_admin(user.id):
            await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=get_admin_keyboard())
        else:
            await update.message.reply_text("\u274c تم الإلغاء. أرسل اسم سلعة للبحث.")
        return
    register_user(telegram_id=user.id, username=user.username)
    if is_admin(user.id):
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ═══════════════════════════════════════════════════════════
#  CALLBACK HANDLER (inline buttons)
# ═══════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.effective_chat.id
    data = query.data

    if data.startswith("admin_") and not is_admin(user_id):
        await query.edit_message_text("\u274c ليس لديك صلاحية للوصول هنا.")
        return

    if data == "admin_add":
        context.user_data["state"] = STATE_ADD_PRODUCT
        await query.edit_message_text(
            "\U0001f4e6 إضافة سلعة جديدة\n\n"
            "أرسل البيانات بهذا التنسيق:\n"
            "اسم السلعة, الكمية, المكان, الحد الأدنى\n\n"
            "مثال:\n"
            "زيت 5 لتر, 50, الرف A, 10\n\n"
            "أو أرسل اسم السلعة فقط (الكمية = 0):\n"
            "حليب"
        )

    elif data == "admin_add_qty":
        products = search_products("")
        if products:
            lines = ["\U0001f4e6 قائمة السلع (أرسل: رقم_السلعة, الكمية)\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"] + " — الكمية: " + str(p["quantity"]))
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("\U0001f4ed لا توجد سلع. أضف سلعاً أولاً.")
            return
        context.user_data["state"] = STATE_ADD_QTY
        await context.bot.send_message(
            chat_id=chat_id,
            text="\u2795 إضافة كمية\n\nأرسل:\nرقم_السلعة, الكمية\n\nمثال:\n1, 25",
        )

    elif data == "admin_remove_qty":
        products = search_products("")
        if products:
            lines = ["\U0001f4e6 قائمة السلع (أرسل: رقم_السلعة, الكمية)\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"] + " — الكمية: " + str(p["quantity"]))
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("\U0001f4ed لا توجد سلع.")
            return
        context.user_data["state"] = STATE_REMOVE_QTY
        await context.bot.send_message(
            chat_id=chat_id,
            text="\u2796 إخراج كمية\n\nأرسل:\nرقم_السلعة, الكمية\n\nمثال:\n1, 10",
        )

    elif data == "admin_search":
        context.user_data["state"] = STATE_SEARCH_ADMIN
        await query.edit_message_text(
            "\U0001f50d ابحث في المخزون\n\nأرسل اسم أو جزء من اسم السلعة:"
        )

    elif data == "admin_stock":
        await query.edit_message_text("\U0001f4ca جاري تحضير التقرير...")
        await send_stock_report(context.bot, chat_id)

    elif data == "admin_log":
        await query.edit_message_text("\U0001f4dc جاري تحميل السجل...")
        await send_transaction_log(context.bot, chat_id)

    elif data == "admin_edit":
        products = search_products("")
        if products:
            lines = ["\U0001f4e6 قائمة السلع\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"] + " — المكان: " + (p.get("location", "") or "-"))
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("\U0001f4ed لا توجد سلع.")
            return
        context.user_data["state"] = STATE_EDIT_PRODUCT
        await context.bot.send_message(
            chat_id=chat_id,
            text="\u270f\ufe0f تعديل سلعة\n\nأرسل:\nرقم_السلعة, الاسم الجديد, المكان الجديد, الحد الأدنى\n\nمثال:\n1, زيت 5 لتر, الرف B, 15\n\nيمكنك تعديل حقل واحد أو أكثر.",
        )

    elif data == "admin_delete":
        products = search_products("")
        if products:
            lines = ["\U0001f4e6 قائمة السلع\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"])
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("\U0001f4ed لا توجد سلع.")
            return
        context.user_data["state"] = STATE_DELETE_PRODUCT
        await context.bot.send_message(
            chat_id=chat_id,
            text="\U0001f5d1\ufe0f حذف سلعة\n\nأرسل رقم السلعة فقط:\nمثال: 5\n\n\u26a0\ufe0f سيتم حذف السلعة وجميع سجلاتها نهائياً!",
        )
    else:
        await query.edit_message_text("\u274c زر غير معروف.")


# ═══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════

async def error_handler(update, context):
    import traceback
    print("[ERROR] " + str(context.error))
    traceback.print_exc()
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text("\u274c حدث خطأ غير متوقع. حاول مرة أخرى.")
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

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("search", search_command))

    app.add_handler(CallbackQueryHandler(button_callback))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    app.add_error_handler(error_handler)

    return app


# ═══════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    init_pool()

    app = build_application()

    if WEBHOOK_URL:
        import os as _os
        port = int(_os.getenv("PORT", 10000))
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
    else:
        print("[BOT] Starting in polling mode...")
        app.run_polling(drop_pending_updates=True)
