"""
admin.py - Admin Handlers

All handler functions for admin users:
- /add command: add a new product
- /stock command: full inventory report
- /log command: recent transactions
- /search command: search inventory
- Text message handler with state machine for multi-step operations

Admin-only middleware is applied at the router level in main.py.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from models import (
    is_admin,
    add_product,
    search_products,
    update_quantity,
    update_product,
    delete_product,
    get_all_products,
    get_transactions,
    log_transaction,
    get_user,
)
from utils.helpers import (
    send_low_stock_alerts,
    format_product_for_admin,
    format_product_for_customer,
    parse_add_product_input,
    parse_quantity_input,
)
from handlers.common import (
    STATE_ADD_PRODUCT,
    STATE_ADD_QTY,
    STATE_REMOVE_QTY,
    STATE_EDIT_PRODUCT,
    STATE_DELETE_PRODUCT,
    STATE_SEARCH_ADMIN,
)


# ── Keyboard Builders ─────────────────────────────────────

def get_admin_keyboard() -> InlineKeyboardMarkup:
    """Build the main admin inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📦 إضافة سلعة", callback_data="admin_add"),
            InlineKeyboardButton("🔍 بحث", callback_data="admin_search"),
        ],
        [
            InlineKeyboardButton("➕ إضافة كمية", callback_data="admin_add_qty"),
            InlineKeyboardButton("➖ إخراج كمية", callback_data="admin_remove_qty"),
        ],
        [
            InlineKeyboardButton("📊 تقرير المخزون", callback_data="admin_stock"),
            InlineKeyboardButton("📜 سجل العمليات", callback_data="admin_log"),
        ],
        [
            InlineKeyboardButton("✏️ تعديل سلعة", callback_data="admin_edit"),
            InlineKeyboardButton("🗑️ حذف سلعة", callback_data="admin_delete"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ── Command Handlers ──────────────────────────────────────

async def _admin_only(update: Update) -> bool:
    """Check if user is admin. Returns True if allowed, False if blocked."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ هذا الأمر للمديرين فقط.")
        return False
    return True


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the add product flow."""
    if not await _admin_only(update):
        return
    context.user_data["state"] = STATE_ADD_PRODUCT
    text = (
        "📦 إضافة سلعة جديدة\n\n"
        "أرسل البيانات بهذا التنسيق:\n"
        "اسم السلعة, الكمية, المكان, الحد الأدنى\n\n"
        "مثال:\n"
        "زيت 5 لتر, 50, الرف A, 10\n\n"
        "أو أرسل اسم السلعة فقط (الكمية = 0):\n"
        "حليب"
    )
    await update.message.reply_text(text)


async def send_stock_report(bot, chat_id: int):
    """Send full inventory report to a chat. Reusable from commands and callbacks."""
    products = get_all_products()

    if not products:
        await bot.send_message(chat_id=chat_id, text="📭 المخزون فارغ. لا توجد سلع بعد.")
        return

    lines = [f"📊 تقرير المخزون ({len(products)} سلعة)\n"]
    for i, p in enumerate(products, 1):
        lines.append(f"{i}. {format_product_for_admin(p)}")
        lines.append("")

    # Split into chunks of ~4096 chars (Telegram limit)
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

    await bot.send_message(
        chat_id=chat_id,
        text="🔙 العودة للقائمة:",
        reply_markup=get_admin_keyboard(),
    )


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show full inventory report."""
    if not await _admin_only(update):
        return
    await send_stock_report(context.bot, update.effective_chat.id)


async def send_transaction_log(bot, chat_id: int):
    """Send recent transaction log to a chat. Reusable from commands and callbacks."""
    transactions = get_transactions(limit=20)

    if not transactions:
        await bot.send_message(chat_id=chat_id, text="📭 لا توجد عمليات مسجلة بعد.")
        return

    lines = ["📜 آخر 20 عملية:\n"]
    for tx in transactions:
        type_emoji = "📥" if tx["type"] == "add" else "📤"
        date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
        operator = tx.get("operator_name") or "مجهول"
        sign = "+" if tx["type"] == "add" else "-"
        lines.append(
            f"{type_emoji} {tx['product_name']} | "
            f"{sign}{tx['quantity']} | "
            f"{operator} | {date_str}"
        )

    await bot.send_message(chat_id=chat_id, text="\n".join(lines))
    await bot.send_message(
        chat_id=chat_id,
        text="🔙 العودة للقائمة:",
        reply_markup=get_admin_keyboard(),
    )


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent transaction log."""
    if not await _admin_only(update):
        return
    await send_transaction_log(context.bot, update.effective_chat.id)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start search flow for admin."""
    if not await _admin_only(update):
        return
    context.user_data["state"] = STATE_SEARCH_ADMIN
    await update.message.reply_text(
        "🔍 ابحث في المخزون\n\n"
        "أرسل اسم أو جزء من اسم السلعة:"
    )


# ── Admin Text Message Handler ────────────────────────────

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Route admin text messages based on the current conversation state.
    This is a state machine that handles multi-step admin operations.
    """
    state = context.user_data.get("state")
    text = update.message.text.strip()
    user = update.effective_user

    if state == STATE_ADD_PRODUCT:
        await _process_add_product(update, context, text, user.id)
    elif state == STATE_ADD_QTY:
        await _process_add_quantity(update, context, text, user.id)
    elif state == STATE_REMOVE_QTY:
        await _process_remove_quantity(update, context, text, user.id)
    elif state == STATE_EDIT_PRODUCT:
        await _process_edit_product(update, context, text, user.id)
    elif state == STATE_DELETE_PRODUCT:
        await _process_delete_product(update, context, text, user.id)
    elif state == STATE_SEARCH_ADMIN:
        await _process_search(update, context, text, admin_view=True)
    else:
        await update.message.reply_text(
            "استخدم الأزرار أدناه أو الأوامر:\n/add, /stock, /log, /search",
            reply_markup=get_admin_keyboard(),
        )


# ── State Processors (Private) ────────────────────────────

async def _process_add_product(update, context, text, telegram_id):
    """Process the add product input."""
    parsed = parse_add_product_input(text)
    if not parsed:
        await update.message.reply_text("❌ تنسيق خاطئ. حاول مرة أخرى.")
        return

    try:
        product = add_product(
            name=parsed["name"],
            quantity=parsed["quantity"],
            location=parsed["location"],
            minimum_stock=parsed["minimum_stock"],
        )
        msg = (
            "✅ تمت إضافة السلعة بنجاح!\n\n"
            + format_product_for_admin(product)
        )
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")

    context.user_data.clear()


async def _process_add_quantity(update, context, text, telegram_id):
    """Process add quantity input: product_id, quantity."""
    parsed = parse_quantity_input(text)
    if not parsed:
        await update.message.reply_text(
            "❌ التنسيق: رقم_السلعة, الكمية\n"
            "مثال: 1, 25"
        )
        return

    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("❌ لم يتم العثور على حسابك.")
        return

    try:
        updated = update_quantity(product_id, qty, "add")
        log_transaction(product_id, "add", qty, db_user["id"])
        name = updated["name"]
        msg = (
            f"✅ تمت إضافة {qty} لـ {name}\n\n"
            + format_product_for_admin(updated)
        )
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")

    context.user_data.clear()


async def _process_remove_quantity(update, context, text, telegram_id):
    """Process remove quantity input: product_id, quantity."""
    parsed = parse_quantity_input(text)
    if not parsed:
        await update.message.reply_text(
            "❌ التنسيق: رقم_السلعة, الكمية\n"
            "مثال: 1, 10"
        )
        return

    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("❌ لم يتم العثور على حسابك.")
        return

    try:
        updated = update_quantity(product_id, qty, "remove")
        log_transaction(product_id, "remove", qty, db_user["id"])
        name = updated["name"]
        msg = (
            f"✅ تم إخراج {qty} من {name}\n\n"
            + format_product_for_admin(updated)
        )
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")

    context.user_data.clear()


async def _process_edit_product(update, context, text, telegram_id):
    """Process edit product input: product_id, new_name, new_location, new_min_stock."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2 or not parts[0].isdigit():
        await update.message.reply_text(
            "❌ التنسيق: رقم_السلعة, الاسم الجديد, المكان الجديد, الحد الأدنى\n"
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
        updated = update_product(product_id, **kwargs)
        msg = (
            "✅ تم تعديل السلعة بنجاح!\n\n"
            + format_product_for_admin(updated)
        )
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")

    context.user_data.clear()


async def _process_delete_product(update, context, text, telegram_id):
    """Process delete product input: product_id."""
    text = text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "❌ أرسل رقم السلعة فقط.\n"
            "مثال: 5"
        )
        return

    product_id = int(text)
    try:
        deleted = delete_product(product_id)
        if deleted:
            await update.message.reply_text(
                "✅ تم حذف السلعة بنجاح.",
                reply_markup=get_admin_keyboard(),
            )
        else:
            await update.message.reply_text("❌ لم يتم العثور على السلعة.")
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")

    context.user_data.clear()


async def _process_search(update, context, text, admin_view=False):
    """Process search and display results."""
    if not text:
        await update.message.reply_text("❌ أرسل اسم أو جزء من اسم السلعة.")
        return

    results = search_products(text)

    if not results:
        await update.message.reply_text(
            "🔍 لم يتم العثور على نتائج لـ " + text
        )
        return

    if admin_view:
        header = f"🔍 نتائج البحث عن {text} ({len(results)} نتيجة):\n"
        lines = [header]
        for p in results:
            lines.append(f"  #{p['id']} {format_product_for_admin(p)}")
            lines.append("")
        await update.message.reply_text("\n".join(lines))
        await update.message.reply_text(
            "🔙 العودة للقائمة:", reply_markup=get_admin_keyboard()
        )
    else:
        lines = []
        for p in results:
            lines.append(format_product_for_customer(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines))

    context.user_data.clear()
