"""
customer.py - Customer Handlers

Handlers for customer (shop) users.
Customers can only search for products by name.
They see availability status but NEVER the actual quantity.

"""

from telegram import Update
from telegram.ext import ContextTypes

from models import search_products
from utils.helpers import format_product_for_customer
from handlers.common import STATE_SEARCH_CUSTOMER
from handlers.admin import _process_search


async def handle_customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any text message from a customer.
    Treats the message as a product search query.
    """
    text = update.message.text.strip()

    if not text:
        await update.message.reply_text(
            "أرسل اسم السلعة لمعرفة إذا كانت متوفرة."
        )
        return

    # Reuse the admin search processor with admin_view=False
    await _process_search(update, context, text, admin_view=False)
