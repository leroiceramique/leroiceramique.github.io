"""
callbacks.py - Inline Keyboard Callback Handlers

Handles all callback queries from inline keyboard buttons.
Admin buttons trigger different flows, each setting a conversation state
so the next text message is routed correctly.

"""

from telegram import Update
from telegram.ext import ContextTypes

from models import is_admin, search_products
from handlers.common import (
    STATE_ADD_PRODUCT,
    STATE_ADD_QTY,
    STATE_REMOVE_QTY,
    STATE_EDIT_PRODUCT,
    STATE_DELETE_PRODUCT,
    STATE_SEARCH_ADMIN,
)
from handlers.admin import (
    get_admin_keyboard,
    send_stock_report,
    send_transaction_log,
)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Route all inline keyboard button presses.
    Only admins can press admin buttons.
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    user_id = query.from_user.id
    chat_id = query.effective_chat.id
    data = query.data

    # Security: verify admin for any admin_ callback
    if data.startswith("admin_") and not is_admin(user_id):
        await query.edit_message_text("❌ ليس لديك صلاحية للوصول هنا.")
        return

    # Route based on callback data
    if data == "admin_add":
        context.user_data["state"] = STATE_ADD_PRODUCT
        await query.edit_message_text(
            "📦 إضافة سلعة جديدة\n\n"
            "أرسل البيانات بهذا التنسيق:\n"
            "اسم السلعة, الكمية, المكان, الحد الأدنى\n\n"
            "مثال:\n"
            "زيت 5 لتر, 50, الرف A, 10\n\n"
            "أو أرسل اسم السلعة فقط (الكمية = 0):\n"
            "حليب"
        )

    elif data == "admin_add_qty":
        # Show products list first so admin knows the IDs
        products = search_products("")  # Get all
        if products:
            lines = ["📦 قائمة السلع (أرسل: رقم_السلعة, الكمية)\n"]
            for p in products:
                lines.append(f"  #{p['id']} {p['name']} — الكمية: {p['quantity']}")
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("📭 لا توجد سلع. أضف سلعاً أولاً.")
            return

        context.user_data["state"] = STATE_ADD_QTY
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "➕ إضافة كمية\n\n"
                "أرسل:\n"
                "رقم_السلعة, الكمية\n\n"
                "مثال:\n"
                "1, 25"
            ),
        )

    elif data == "admin_remove_qty":
        # Show products list first
        products = search_products("")
        if products:
            lines = ["📦 قائمة السلع (أرسل: رقم_السلعة, الكمية)\n"]
            for p in products:
                lines.append(f"  #{p['id']} {p['name']} — الكمية: {p['quantity']}")
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("📭 لا توجد سلع.")
            return

        context.user_data["state"] = STATE_REMOVE_QTY
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "➖ إخراج كمية\n\n"
                "أرسل:\n"
                "رقم_السلعة, الكمية\n\n"
                "مثال:\n"
                "1, 10"
            ),
        )

    elif data == "admin_search":
        context.user_data["state"] = STATE_SEARCH_ADMIN
        await query.edit_message_text(
            "🔍 ابحث في المخزون\n\n"
            "أرسل اسم أو جزء من اسم السلعة:"
        )

    elif data == "admin_stock":
        await query.edit_message_text("📊 جاري تحضير التقرير...")
        await send_stock_report(context.bot, chat_id)

    elif data == "admin_log":
        await query.edit_message_text("📜 جاري تحميل السجل...")
        await send_transaction_log(context.bot, chat_id)

    elif data == "admin_edit":
        # Show products list first
        products = search_products("")
        if products:
            lines = ["📦 قائمة السلع\n"]
            for p in products:
                lines.append(f"  #{p['id']} {p['name']} — المكان: {p.get('location', '') or '-'}")
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("📭 لا توجد سلع.")
            return

        context.user_data["state"] = STATE_EDIT_PRODUCT
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "✏️ تعديل سلعة\n\n"
                "أرسل:\n"
                "رقم_السلعة, الاسم الجديد, المكان الجديد, الحد الأدنى\n\n"
                "مثال:\n"
                "1, زيت 5 لتر, الرف B, 15\n\n"
                "يمكنك تعديل حقل واحد أو أكثر."
            ),
        )

    elif data == "admin_delete":
        # Show products list first
        products = search_products("")
        if products:
            lines = ["📦 قائمة السلع\n"]
            for p in products:
                lines.append(f"  #{p['id']} {p['name']}")
            await query.edit_message_text("\n".join(lines))
        else:
            await query.edit_message_text("📭 لا توجد سلع.")
            return

        context.user_data["state"] = STATE_DELETE_PRODUCT
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🗑️ حذف سلعة\n\n"
                "أرسل رقم السلعة فقط:\n"
                "مثال: 5\n\n"
                "⚠️ سيتم حذف السلعة وجميع سجلاتها نهائياً!"
            ),
        )

    else:
        await query.edit_message_text("❌ زر غير معروف.")
