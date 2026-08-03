"""
common.py - Shared Handlers

Handlers used by both admin and customer roles:
- /start command: greeting and auto-registration
- /help command: shows available commands based on role
"""

from telegram import Update
from telegram.ext import ContextTypes

from models import register_user, is_admin
from utils.helpers import send_low_stock_alerts


# Callback data patterns for conversation states
STATE_ADD_PRODUCT = "waiting_add_product"
STATE_ADD_QTY = "waiting_add_qty"
STATE_REMOVE_QTY = "waiting_remove_qty"
STATE_EDIT_PRODUCT = "waiting_edit_product"
STATE_DELETE_PRODUCT = "waiting_delete_product"
STATE_SEARCH_ADMIN = "waiting_search_admin"
STATE_SEARCH_CUSTOMER = "waiting_search_customer"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /start command.
    Registers the user (or updates their info) and shows the appropriate menu.
    """
    user = update.effective_user
    register_user(
        telegram_id=user.id,
        username=user.username,
    )

    if is_admin(user.id):
        text = (
            f"مرحباً {user.first_name}! 👋\n\n"
            f"أنت مدير النظام.\n"
            f"استخدم القائمة أدناه لإدارة المخزون."
        )
        from handlers.admin import get_admin_keyboard
        reply_markup = get_admin_keyboard()
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        text = (
            f"مرحباً {user.first_name}! 👋\n\n"
            f"أرسل اسم السلعة لمعرفة إذا كانت متوفرة.\n"
            f"مثال: زيت 5 لتر"
        )
        await update.message.reply_text(text)

    # Clear any pending conversation state
    context.user_data.clear()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command - shows commands based on user role."""
    user = update.effective_user

    if is_admin(user.id):
        text = (
            "📋 أوامر الإدارة:\n\n"
            "/start - القائمة الرئيسية\n"
            "/help - عرض هذا المساعد\n"
            "/add - إضافة سلعة جديدة\n"
            "/stock - تقرير المخزون الكامل\n"
            "/log - سجل العمليات الأخيرة\n"
            "/search - بحث في المخزون\n\n"
            "💡 يمكنك أيضاً استخدام الأزرار أدناه."
        )
    else:
        text = (
            "📋 المساعدة:\n\n"
            "أرسل اسم السلعة مباشرة لمعرفة توفرها.\n\n"
            "مثال:\n"
            "  زيت 5 لتر\n"
            "  زيت\n"
            "  حليب"
        )

    await update.message.reply_text(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors globally to prevent bot crashes."""
    import traceback
    print(f"[ERROR] {context.error}")
    traceback.print_exc()

    # Notify user if possible
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ حدث خطأ غير متوقع. حاول مرة أخرى."
            )
        except Exception:
            pass
