"""
config.py - Application Configuration

This module loads all environment variables and provides
a centralized configuration for the entire application.
No secrets or sensitive data should ever be hardcoded here.
"""

import os
from dotenv import load_dotenv

# Load .env file from project root
load_dotenv()


# ── Telegram ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not set. "
        "Please create a .env file based on .env.example."
    )

# ── Database ──────────────────────────────────────────────
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Please create a .env file based on .env.example."
    )

# ── Admin IDs ─────────────────────────────────────────────
# Stored as a list of integers for easy lookup
_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [
    int(uid.strip())
    for uid in _raw_admin_ids.split(",")
    if uid.strip().isdigit()
]

if not ADMIN_IDS:
    raise RuntimeError(
        "ADMIN_IDS is not set or empty. "
        "Please add at least one admin Telegram ID in .env."
    )

# ── Webhook (optional, used in production on Render) ──────
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
