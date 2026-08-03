"""
helpers.py - Utility Functions

Shared helper functions used across handlers:
- Formatting product info for different user roles
- Sending low-stock alerts to all admins
- Parsing user input

"""

from datetime import datetime, timezone

import config
from models import get_low_stock_products


# ── Formatters ────────────────────────────────────────────

def format_product_for_admin(product: dict) -> str:
    """Format product info for admin view (shows all details including quantity)."""
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    status_emoji = "🔴" if qty <= min_stock else "🟢"
    location = product.get("location", "") or "غير محدد"

    return (
        f"📦 {product['name']}\n"
        f"   الكمية: {qty}\n"
        f"   الحد الأدنى: {min_stock}\n"
        f"   المكان: {location}\n"
        f"   الحالة: {status_emoji}"
    )


def format_product_for_customer(product: dict) -> str:
    """Format product info for customer view (hides quantity and location)."""
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return f"📦 {product['name']}\n🟢 متوفر"
    elif qty > 0:
        return f"📦 {product['name']}\n🟡 كمية محدودة"
    else:
        return f"📦 {product['name']}\n🔴 غير متوفر حالياً"


# ── Low Stock Alerts ─────────────────────────────────────

async def send_low_stock_alerts(bot):
    """
    Check for low-stock products and send alerts to all admins.
    Call this after any quantity change (add/remove).
    """
    try:
        low_stock = get_low_stock_products()
        if not low_stock:
            return

        alert_lines = ["⚠️ تنبيه: مخزون منخفض\n"]
        for p in low_stock:
            alert_lines.append(
                f"  • {p['name']} — الكمية: {p['quantity']} (الحد الأدنى: {p['minimum_stock']})"
            )
        alert_text = "\n".join(alert_lines)

        for admin_id in config.ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=alert_text)
            except Exception:
                pass  # Admin might not have started the bot
    except Exception:
        pass  # Don't let alert failures break the main flow


# ── Input Parsers ─────────────────────────────────────────

def parse_add_product_input(text: str) -> dict | None:
    """
    Parse product addition input.
    Expected formats:
      - "اسم السلعة, الكمية, المكان, الحد الأدنى"
      - "اسم السلعة" (quantity defaults to 0)
    Returns dict with keys: name, quantity, location, minimum_stock
    """
    parts = [p.strip() for p in text.split(",")]
    if not parts or not parts[0]:
        return None

    result = {
        "name": parts[0],
        "quantity": 0,
        "location": "",
        "minimum_stock": 5,
    }

    if len(parts) > 1 and parts[1].isdigit():
        result["quantity"] = int(parts[1])
    if len(parts) > 2 and parts[2]:
        result["location"] = parts[2]
    if len(parts) > 3 and parts[3].isdigit():
        result["minimum_stock"] = int(parts[3])

    return result


def parse_quantity_input(text: str) -> tuple[int, int] | None:
    """
    Parse quantity change input.
    Expected format: "product_id, quantity"
    Returns (product_id, quantity) or None on failure.
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None
