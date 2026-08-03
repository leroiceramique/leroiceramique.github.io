"""
product.py - Product Model

Handles all database operations related to products:
- Adding new products
- Searching products by name (partial match)
- Updating product details and quantities
- Deleting products
- Getting low-stock alerts
- Fetching all products for inventory reports

"""

import psycopg2.extras

import database


def add_product(name: str, quantity: int = 0, location: str = "", minimum_stock: int = 5) -> dict:
    """Add a new product to the inventory. Returns the created product."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO products (name, quantity, location, minimum_stock)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """, (name.strip(), quantity, location.strip(), minimum_stock))
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def get_product_by_id(product_id: int) -> dict | None:
    """Fetch a single product by its database ID."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def search_products(query: str) -> list[dict]:
    """
    Search products by partial name match (case-insensitive).
    Uses ILIKE for PostgreSQL case-insensitive pattern matching.
    """
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM products
                WHERE name ILIKE %s
                ORDER BY name
            """, (f"%{query.strip()}%",))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def update_product(product_id: int, name: str | None = None,
                   location: str | None = None, minimum_stock: int | None = None) -> dict:
    """Update product fields. Only non-None fields are updated."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Build dynamic UPDATE
            fields = []
            values = []
            if name is not None:
                fields.append("name = %s")
                values.append(name.strip())
            if location is not None:
                fields.append("location = %s")
                values.append(location.strip())
            if minimum_stock is not None:
                fields.append("minimum_stock = %s")
                values.append(minimum_stock)

            if not fields:
                raise ValueError("No fields to update")

            values.append(product_id)
            query = f"""
                UPDATE products SET {', '.join(fields)}
                WHERE id = %s
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """
            cur.execute(query, values)
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def update_quantity(product_id: int, change: int, transaction_type: str) -> dict:
    """
    Update product quantity (positive = add, negative = remove).
    Prevents negative stock. Returns the updated product.
    transaction_type must be 'add' or 'remove'.
    """
    if transaction_type not in ("add", "remove"):
        raise ValueError("transaction_type must be 'add' or 'remove'")

    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if transaction_type == "remove" and change < 0:
                change = abs(change)

            if transaction_type == "remove":
                # Check if enough stock
                cur.execute("SELECT quantity FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
                if row and row["quantity"] < change:
                    raise ValueError(
                        f"الكمية المطلوبة ({change}) أكبر من المتوفر ({row['quantity']})"
                    )
                new_qty_expr = "GREATEST(quantity - %s, 0)"
            else:
                new_qty_expr = "quantity + %s"

            cur.execute(f"""
                UPDATE products SET quantity = {new_qty_expr}
                WHERE id = %s
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """, (change, product_id))
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def delete_product(product_id: int) -> bool:
    """Delete a product and all its transactions (CASCADE). Returns True on success."""
    conn = database.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE id = %s RETURNING id", (product_id,))
            deleted = cur.fetchone() is not None
        conn.commit()
        return deleted
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def get_low_stock_products() -> list[dict]:
    """Get all products where quantity <= minimum_stock."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM products
                WHERE quantity <= minimum_stock
                ORDER BY quantity ASC
            """)
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)


def get_all_products() -> list[dict]:
    """Get all products ordered by name."""
    conn = database.get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products ORDER BY name")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception as e:
        conn.rollback()
        raise
    finally:
        database.release_connection(conn)
