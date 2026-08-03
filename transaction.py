"""
transaction.py - Transaction Model

Handles all database operations related to stock transactions:
- Logging add/remove transactions
- Fetching transaction history
- Getting per-product transaction history

"""

import psycopg2.extras

import database


def log_transaction(product_id: int, transaction_type: str, quantity: int, user_id: int) -> dict:
    """
    Log a stock transaction.
    transaction_type: 'add' or 'remove'
    quantity: the amount added or removed
    user_id: the database user ID who performed the action
    """
    conn = database.get_connection()
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
        database.release_connection(conn)


def get_transactions(limit: int = 20, offset: int = 0) -> list[dict]:
    """Get recent transactions with product names, newest first."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    t.id,
                    t.type,
                    t.quantity,
                    t.date,
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
        database.release_connection(conn)


def get_product_transactions(product_id: int, limit: int = 20) -> list[dict]:
    """Get transaction history for a specific product."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    t.id,
                    t.type,
                    t.quantity,
                    t.date,
                    u.username AS operator_name
                FROM transactions t
                LEFT JOIN users u ON u.id = t.user_id
                WHERE t.product_id = %s
                ORDER BY t.date DESC
                LIMIT %s
            """, (product_id, limit))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)
