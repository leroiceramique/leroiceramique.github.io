import os
import json
import logging
import base64
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
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

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
#  CONSTANTS
# ═══════════════════════════════════════════════════════════

STATE_ADD_NAME = "waiting_add_name"
STATE_ADD_QTY = "waiting_add_qty"
STATE_EXISTING_ADD_QTY = "waiting_existing_add_qty"
STATE_REMOVE_QTY = "waiting_remove_qty"
STATE_EDIT_PRODUCT = "waiting_edit_product"
STATE_DELETE_PRODUCT = "waiting_delete_product"
STATE_SEARCH_ADMIN = "waiting_search_admin"
STATE_RECEIPT_PHOTO = "waiting_receipt_photo"

BTN_ADD = "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629"
BTN_SEARCH = "\U0001f50d \u0628\u062d\u062b"
BTN_ADD_QTY = "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629"
BTN_REMOVE_QTY = "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629"
BTN_STOCK = "\U0001f4ca \u062a\u0642\u0631\u064a\u0631 \u0627\u0644\u0645\u062e\u0632\u0648\u0646"
BTN_LOG = "\U0001f4dc \u0633\u062c\u0644 \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a"
BTN_EDIT = "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629"
BTN_DELETE = "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629"
BTN_RECEIPT = "\U0001f4f7 \u0648\u0635\u0644\u0629 \u0634\u062d\u0646"
BTN_CANCEL = "\u274c \u0625\u0644\u063a\u0627\u0621"


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
        lines = ["\u26a0\ufe0f \u062a\u0646\u0628\u064a\u0647: \u0645\u062e\u0632\u0648\u0646 \u0645\u0646\u062e\u0641\u0636\u0646"]
        for p in low_stock:
            lines.append(
                "  \u2022 " + p["name"]
                + " \u2014 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(p["quantity"])
                + " (\u0627\u0644\u062d\u062f \u0627\u0644\u0623\u062f\u0646\u0649: " + str(p["minimum_stock"]) + ")"
            )
        text = "\n".join(lines)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=text)
            except Exception:
                pass
    except Exception:
        pass


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
        return int(parts[0]), int(parts[1])
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
            [BTN_RECEIPT],
            [BTN_STOCK, BTN_LOG],
            [BTN_EDIT, BTN_DELETE],
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
        "\U0001f4e6 " + product_name + "\n\n"
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


# ── Receipt / Shipping Invoice by AI ─────────────────

RECEIPT_PROMPT = """\
أنت مساعد مخزن. هذه صورة وصلة شحن أو فاتورة.

مهمتك: استخرج كل السلع وعدد العلب (cartons/crt) من الصورة.

IMPORTANT: أجب فقط بـ JSON بهذا التنسيق بالضبط، بدون أي نص آخر:
{"items": [{"name": "اسم السلعة", "qty": 5}]}

قواعد:
- "name": اسم السلعة كما هو مكتوب
- "qty": عدد العلب (إذا مكتوب كرتون/علب/صندوق/كرت أو carton/crt/box خذ الرقم)
- إذا لم يوجد عدد، ضع 1
- إذا الصورة لا تحتوي على سلع، أرجع {"items": []}
- أجب بـ JSON فقط
"""


def _find_product_by_name(name):
    """Find a product in DB by fuzzy name match (ILIKE)."""
    results = search_products(name)
    if results:
        return results[0]
    # Try individual words
    words = name.split()
    for w in words:
        if len(w) < 2:
            continue
        results = search_products(w)
        if results:
            return results[0]
    return None


async def _analyze_receipt_image(image_bytes):
    """Send image to Google Gemini 2.0 Flash (free) and extract items."""
    import aiohttp
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": RECEIPT_PROMPT},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError("Gemini error " + str(resp.status) + ": " + body[:200])
            data = await resp.json()
    content = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    # Strip markdown code blocks if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    return json.loads(content)


async def handle_receipt_button(update, context):
    """Admin pressed 📷 وصلة شحن — ask for photo."""
    if not GEMINI_API_KEY:
        await update.message.reply_text(
            "\u274c \u0645\u064a\u0632\u0629 \u0648\u0635\u0644\u0629 \u0627\u0644\u0634\u062d\u0646 \u0645\u0639\u0637\u0644\u0629.\n\n"
            "\u0644\u062a\u0641\u0639\u064a\u0644\u0647\u0627 \u0623\u0636\u0641 \u0641\u064a Render:\n"
            "GEMINI_API_KEY = AIza...",
            reply_markup=get_admin_keyboard(),
        )
        return
    context.user_data["state"] = STATE_RECEIPT_PHOTO
    await update.message.reply_text(
        "\U0001f4f7 \u0623\u0631\u0633\u0644 \u0635\u0648\u0631\u0629 \u0648\u0635\u0644\u0629 \u0627\u0644\u0634\u062d\u0646:\n\n"
        "\u0633\u064a\u062a\u0645 \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0633\u0644\u0639 \u0648\u0627\u0644\u0639\u062f\u062f \u062a\u0644\u0642\u0627\u0626\u064a\u0627\u064b \u062b\u0645 \u062a\u0623\u0643\u064a\u062f \u0642\u0628\u0644 \u0627\u0644\u062e\u0635\u0645.",
        reply_markup=get_input_keyboard(),
    )


async def handle_receipt_photo(update, context):
    """Process the uploaded receipt photo."""
    state = context.user_data.get("state")
    if state != STATE_RECEIPT_PHOTO:
        # Not in receipt mode — ignore photo
        return

    chat_id = update.effective_chat.id

    # Download photo
    photo = update.message.photo[-1]
    status_msg = await update.message.reply_text("\u23f3 \u062c\u0627\u0631\u064d \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0648\u0635\u0644\u0629...")

    try:
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()
    except Exception as e:
        await status_msg.edit_text("\u274c \u0641\u0634\u0644 \u062a\u062d\u0645\u064a\u0644 \u0627\u0644\u0635\u0648\u0631\u0629: " + str(e))
        context.user_data.clear()
        return

    try:
        result = await _analyze_receipt_image(bytes(image_bytes))
    except Exception as e:
        await status_msg.edit_text("\u274c \u0641\u0634\u0644\u062a \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0648\u0635\u0644\u0629: " + str(e))
        context.user_data.clear()
        return

    items = result.get("items", [])
    if not items:
        await status_msg.edit_text(
            "\u2753 \u0644\u0645 \u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0633\u0644\u0639 \u0641\u064a \u0627\u0644\u0648\u0635\u0644\u0629.\n"
            "\u062a\u0623\u0643\u062f \u0623\u0646 \u0627\u0644\u0635\u0648\u0631\u0629 \u0648\u0627\u0636\u062d\u0629 \u0648\u0623\u0639\u062f \u0627\u0644\u0645\u062d\u0627\u0648\u0644\u0629.",
            reply_markup=get_admin_keyboard(),
        )
        context.user_data.clear()
        return

    # Match items with DB products
    matched = []
    unmatched = []
    for item in items:
        product = _find_product_by_name(item["name"])
        if product:
            matched.append({
                "product_id": product["id"],
                "name": product["name"],
                "qty": item["qty"],
                "current_stock": product["quantity"],
                "receipt_name": item["name"],
            })
        else:
            unmatched.append({"name": item["name"], "qty": item["qty"]})

    # Build confirmation message
    lines = ["\U0001f4cb \u0646\u062a\u064a\u062c\u0629 \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0648\u0635\u0644\u0629:\n"]
    lines.append("\u2705 \u0633\u0644\u0639 \u0645\u0637\u0627\u0628\u0642\u0629 \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646:")
    for m in matched:
        lines.append(
            "  \U0001f4e6 " + m["name"]
            + " \u2014 \u062e\u0635\u0645 " + str(m["qty"]) + " crt"
            + " (\u0627\u0644\u0645\u062a\u0648\u0641\u0631: " + str(m["current_stock"]) + " crt)"
        )
    if unmatched:
        lines.append("")
        lines.append("\u26a0\ufe0f \u0633\u0644\u0639 \u063a\u064a\u0631 \u0645\u0648\u062c\u0648\u062f\u0629 \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646:")
        for u in unmatched:
            lines.append("  \u274c " + u["name"] + " \u2014 " + str(u["qty"]) + " crt")
    lines.append("")
    lines.append("\u26a0\ufe0f \u0633\u064a\u062a\u0645 \u062e\u0635\u0645 \u0627\u0644\u0643\u0645\u064a\u0627\u062a \u0645\u0646 \u0627\u0644\u0645\u062e\u0632\u0648\u0646!")

    # Store data for callback
    context.user_data["receipt_matched"] = matched
    context.user_data["state"] = "waiting_receipt_confirm"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2705 \u062a\u0623\u0643\u064a\u062f \u062e\u0635\u0645", callback_data="receipt_confirm"),
            InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="receipt_cancel"),
        ],
    ])
    await status_msg.edit_text("\n".join(lines), reply_markup=keyboard)


async def callback_receipt_handler(update, context):
    """Handle confirm/cancel for receipt deduction."""
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    data = query.data

    if data == "receipt_confirm":
        matched = user_data.get("receipt_matched", [])
        telegram_id = query.from_user.id
        db_user = get_user(telegram_id)
        if not db_user:
            await query.edit_message_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.")
            user_data.clear()
            return

        success_lines = ["\u2705 \u062a\u0645 \u062a\u062d\u062f\u064a\u062b \u0627\u0644\u0645\u062e\u0632\u0648\u0646:\n"]
        fail_lines = []

        for m in matched:
            try:
                updated = db_update_quantity(m["product_id"], m["qty"], "remove")
                log_transaction(m["product_id"], "remove", m["qty"], db_user["id"])
                success_lines.append(
                    "  \u2714\ufe0f " + m["name"]
                    + ": " + str(m["current_stock"]) + " \u2192 " + str(updated["quantity"]) + " crt"
                )
            except ValueError as e:
                fail_lines.append("  \u274c " + m["name"] + ": " + str(e))
            except Exception as e:
                fail_lines.append("  \u274c " + m["name"] + ": " + str(e))

        result_text = "\n".join(success_lines)
        if fail_lines:
            result_text += "\n\n\u26a0\ufe0f \u0623\u062e\u0637\u0627\u0621:\n" + "\n".join(fail_lines)

        await query.edit_message_text(result_text)
        await send_low_stock_alerts(context.bot)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="\u2714\ufe0f",
            reply_markup=get_admin_keyboard(),
        )
        user_data.clear()

    elif data == "receipt_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0625\u0644\u063a\u0627\u0621 \u0639\u0645\u0644\u064a\u0629 \u0627\u0644\u0648\u0635\u0644\u0629.")
        user_data.clear()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:",
            reply_markup=get_admin_keyboard(),
        )


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


async def show_products_list(bot, chat_id, next_state, prompt_text):
    products = search_products("")
    if not products:
        await bot.send_message(chat_id=chat_id, text="\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639. \u0623\u0636\u0641 \u0633\u0644\u0639\u0627\u064b \u0623\u0648\u0644\u0627\u064b.", reply_markup=get_admin_keyboard())
        return
    lines = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
    for p in products:
        lines.append("  #" + str(p["id"]) + " " + p["name"] + " \u2014 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(p["quantity"]))
    await bot.send_message(chat_id=chat_id, text="\n".join(lines))
    context_ref = None  # Will be set by caller
    await bot.send_message(chat_id=chat_id, text=prompt_text, reply_markup=get_input_keyboard())


# ═══════════════════════════════════════════════════════════
#  COMMANDS
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
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════


def _set_state_and_prompt(context, update, state, text, prompt):
    """Helper to set state and send prompt with input keyboard."""
    context.user_data["state"] = state
    return update.message.reply_text(text + "\n\n" + prompt, reply_markup=get_input_keyboard())


async def handle_admin_message(update, context):
    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user
    chat_id = update.effective_chat.id
    bot = context.bot

    # No state: check button presses
    if state is None:
        if text == BTN_ADD:
            await ask_add_name(update, context)
            return

        if text == BTN_SEARCH:
            context.user_data["state"] = STATE_SEARCH_ADMIN
            await update.message.reply_text(
                "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_ADD_QTY:
            products = search_products("")
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639. \u0623\u0636\u0641 \u0633\u0644\u0639\u0627\u064b \u0623\u0648\u0644\u0627\u064b.", reply_markup=get_admin_keyboard())
                return
            lines = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"] + " \u2014 " + str(p["quantity"]) + " crt")
            await update.message.reply_text("\n".join(lines))
            context.user_data["state"] = STATE_EXISTING_ADD_QTY
            await update.message.reply_text(
                "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0639\u0644\u0628\n\n\u0623\u0631\u0633\u0644: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\n\u0645\u062b\u0627\u0644: 1, 25",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_REMOVE_QTY:
            products = search_products("")
            if not products:
                await update.message.reply_text("\U0001f4ed \u0644\u0627 \u062a\u0648\u062c\u062f \u0633\u0644\u0639.", reply_markup=get_admin_keyboard())
                return
            lines = ["\U0001f4e6 \u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0633\u0644\u0639\n"]
            for p in products:
                lines.append("  #" + str(p["id"]) + " " + p["name"] + " \u2014 " + str(p["quantity"]) + " crt")
            await update.message.reply_text("\n".join(lines))
            context.user_data["state"] = STATE_REMOVE_QTY
            await update.message.reply_text(
                "\u2796 \u0625\u062e\u0631\u0627\u062c \u0639\u0644\u0628\n\n\u0623\u0631\u0633\u0644: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\n\u0645\u062b\u0627\u0644: 1, 10",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_STOCK:
            await send_stock_report(bot, chat_id)
            return

        if text == BTN_LOG:
            await send_transaction_log(bot, chat_id)
            return

        if text == BTN_EDIT:
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
                "\u0623\u0631\u0633\u0644: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u062c\u062f\u064a\u062f\n\n"
                "\u0645\u062b\u0627\u0644: 1, \u0632\u064a\u062a 5 \u0644\u062a\u0631",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_DELETE:
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

        if text == BTN_CANCEL:
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
            return

        if text == BTN_RECEIPT:
            await handle_receipt_button(update, context)
            return

        # Unknown text without state
        await update.message.reply_text(
            "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u0648 \u0627\u0644\u0623\u0648\u0627\u0645\u0631:\n/add, /stock, /log, /search",
            reply_markup=get_admin_keyboard(),
        )
        return

    # In a state: process the input
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=get_admin_keyboard())
        return

    if state == STATE_ADD_NAME:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text(
                "\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.",
                reply_markup=get_input_keyboard(),
            )
            return
        await ask_add_qty(update, context, product_name)
    elif state == STATE_ADD_QTY:
        qty = parse_crt_input(text)
        if qty is None:
            await update.message.reply_text(
                "\u274c \u0623\u0631\u0633\u0644 \u0639\u062f\u062f \u0627\u0644\u0639\u0644\u0628.\n\u0645\u062b\u0627\u0644: 10 \u0623\u0648 10crt",
                reply_markup=get_input_keyboard(),
            )
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
        await update.message.reply_text(
            "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647.",
            reply_markup=get_admin_keyboard(),
        )


async def handle_customer_message(update, context):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u0625\u0630\u0627 \u0643\u0627\u0646\u062a \u0645\u062a\u0648\u0641\u0631\u0629.")
        return
    await process_search(update, context, text, admin_view=False)


async def route_message(update, context):
    user = update.effective_user
    text = update.message.text.strip()

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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


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
    app.add_handler(CallbackQueryHandler(callback_receipt_handler, pattern="^receipt_"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)
    return app


# ═══════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    init_pool()

    tg_app = build_application()
    render_port = int(os.getenv("PORT", 0))

    if WEBHOOK_URL:
        # Webhook mode
        port = render_port or 10000
        webhook_path = "/webhook"
        url = WEBHOOK_URL + webhook_path
        print("[BOT] Webhook mode, port " + str(port) + ", url: " + url)
        from aiohttp import web
        webhook_app = web.Application()

        async def health(request):
            return web.Response(text='{"status":"ok"}', content_type='application/json')

        webhook_app.router.add_get("/health", health)
        tg_app.run_webhook(
            webhook_path=webhook_path, url=url, port=port,
            drop_pending_updates=True, webhook_app=webhook_app,
        )

    elif render_port:
        # Polling + health server (for Render Web Service)
        from aiohttp import web
        health_app = web.Application()

        async def health(request):
            return web.Response(text='{"status":"ok"}', content_type='application/json')

        health_app.router.add_get("/health", health)

        async def on_startup(aio_app):
            await tg_app.initialize()
            await tg_app.start()
            await tg_app.updater.start_polling(drop_pending_updates=True)
            print("[BOT] Polling + health server on port " + str(render_port))

        async def on_cleanup(aio_app):
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()

        health_app.on_startup.append(on_startup)
        health_app.on_cleanup.append(on_cleanup)
        web.run_app(health_app, host="0.0.0.0", port=render_port)

    else:
        # Local polling (no server)
        print("[BOT] Local polling mode")
        tg_app.run_polling(drop_pending_updates=True)
