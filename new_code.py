# -*- coding: utf-8 -*-
# This file contains NEW code to be inserted into bot.py
# It is NOT a standalone module - these functions will be merged into bot.py

# ═══════════════════════════════════════════════════════════
#  PRODUCT TRANSACTIONS (per product)
# ═══════════════════════════════════════════════════════════

def get_product_transactions(product_id, limit=15):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    t.id, t.type, t.quantity, t.date,
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
        release_connection(conn)
