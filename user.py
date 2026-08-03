"""
user.py - User Model

Handles all database operations related to users:
- Registering new users when they first interact with the bot
- Fetching user data by Telegram ID
- Checking admin privileges

"""

import psycopg2.extras

import database
import config


def register_user(telegram_id: int, username: str | None) -> dict:
    """
    Register a user in the database.
    If the user already exists, update their username and return existing record.
    Admin role is determined by checking against ADMIN_IDS in config.
    """
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Determine role based on ADMIN_IDS
            role = "admin" if telegram_id in config.ADMIN_IDS else "customer"

            # Try to insert; if conflict, update username and role
            cur.execute("""
                INSERT INTO users (telegram_id, username, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username   = EXCLUDED.username,
                    role       = EXCLUDED.role,
                    active     = TRUE
                RETURNING id, telegram_id, username, role, active, created_at
            """, (telegram_id, username, role))

            user = cur.fetchone()
        conn.commit()
        return dict(user)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def get_user(telegram_id: int) -> dict | None:
    """Fetch a user by Telegram ID. Returns None if not found."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE telegram_id = %s",
                (telegram_id,)
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def is_admin(telegram_id: int) -> bool:
    """Check if a Telegram ID belongs to an admin.
    Double-checks both the database and the config ADMIN_IDS for safety."""
    # Primary check: config (fast, no DB needed)
    if telegram_id in config.ADMIN_IDS:
        return True
    # Fallback check: database role
    user = get_user(telegram_id)
    return user is not None and user["role"] == "admin"
