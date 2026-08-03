"""
main.py - Application Entry Point

This is the main entry point for the Telegram Inventory Bot.
It sets up the bot, registers all handlers, and starts the bot.

The bot can run in two modes:
1. Polling mode (local development) - no WEBHOOK_URL set
2. Webhook mode (production on Render) - WEBHOOK_URL is set

"""

import sys
import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import config
import database
from models import is_admin, register_user
from handlers.common import start_command, help_command, error_handler
from handlers.admin import (
    add_command,
    stock_command,
    log_command,
    search_command,
    handle_admin_message,
)
from handlers.customer import handle_customer_message
from handlers.callbacks import button_callback


# ── Logging ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Role-Based Message Router ─────────────────────────────

async def route_message(update, context):
    """
    Route incoming text messages based on user role.
    Admins get full control; customers only get search.
    Also handles the /cancel command to clear conversation state.
    """
    user = update.effective_user
    text = update.message.text.strip()

    # Handle cancel
    if text == "/cancel":
        context.user_data.clear()
        if is_admin(user.id):
            from handlers.admin import get_admin_keyboard
            await update.message.reply_text(
                "❌ تم الإلغاء.",
                reply_markup=get_admin_keyboard(),
            )
        else:
            await update.message.reply_text("❌ تم الإلغاء. أرسل اسم سلعة للبحث.")
        return

    # Auto-register user on every message
    register_user(telegram_id=user.id, username=user.username)

    # Route based on role
    if is_admin(user.id):
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ── Bot Setup ─────────────────────────────────────────────

def build_application() -> Application:
    """Build and configure the Telegram bot application."""
    app = Application.builder().token(config.BOT_TOKEN).build()

    # ── Command Handlers (available to all users) ─────
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))

    # ── Admin-Only Command Handlers ───────────────────
    # Registered for everyone; admin check happens inside each handler.
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("search", search_command))

    # ── Inline Keyboard Callbacks ─────────────────────
    app.add_handler(CallbackQueryHandler(button_callback))

    # ── Text Message Router ───────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    # ── Global Error Handler ──────────────────────────
    app.add_error_handler(error_handler)

    return app


# ── Entry Point ────────────────────────────────────────────

if __name__ == "__main__":
    # Initialize database schema and connection pool
    database.init_db()
    database.init_pool()

    app = build_application()

    if config.WEBHOOK_URL:
        # ── Webhook Mode (Render Production) ───────────
        # Render free tier requires the app to listen on PORT env var.
        # python-telegram-bot's run_webhook starts a built-in web server.
        import os
        port = int(os.getenv("PORT", 10000))
        webhook_path = "/webhook"
        url = f"{config.WEBHOOK_URL}{webhook_path}"

        print(f"[BOT] Starting in webhook mode on port {port}...")
        print(f"[BOT] Webhook URL: {url}")

        # Build a simple health check handler using PTB's webhook framework
        from aiohttp import web

        # Create a custom web app with a health check endpoint for Render
        webhook_app = web.Application()

        async def health_check(request):
            return web.Response(text='{"status":"ok"}', content_type='application/json')

        webhook_app.router.add_get('/health', health_check)

        # Run with webhook + health endpoint
        app.run_webhook(
            webhook_path=webhook_path,
            url=url,
            port=port,
            drop_pending_updates=True,
            webhook_app=webhook_app,
        )
    else:
        # ── Polling Mode (Local Development) ───────────
        print("[BOT] Starting in polling mode...")
        app.run_polling(drop_pending_updates=True)
