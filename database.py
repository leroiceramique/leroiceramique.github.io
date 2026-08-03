"""
database.py - Database Connection and Schema Initialization

This module handles:
1. Creating a connection pool to Supabase PostgreSQL
2. Running initial schema migrations (creating tables if they don't exist)
3. Providing a get_connection() helper used by all other modules

We use a simple connection pool approach compatible with Render's free tier.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import psycopg2.pool

import config


# ── Connection Pool ───────────────────────────────────────
# Using SimpleConnectionPool: suitable for single-process deployments (Render free tier)
_pool: psycopg2.pool.SimpleConnectionPool | None = None


def init_pool() -> psycopg2.pool.SimpleConnectionPool:
    """Initialize the connection pool. Call once at startup."""
    global _pool
    _pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=5,
        dsn=config.DATABASE_URL,
    )
    return _pool


def get_connection():
    """Get a connection from the pool."""
    if _pool is None:
        init_pool()
    return _pool.getconn()


def release_connection(conn):
    """Return a connection to the pool."""
    if _pool:
        _pool.putconn(conn)


def close_pool():
    """Close all connections in the pool."""
    if _pool:
        _pool.closeall()


# ── Schema Initialization ────────────────────────────────
SCHEMA_SQL = """

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username    VARCHAR(255),
    role        VARCHAR(20) NOT NULL DEFAULT 'customer' CHECK (role IN ('admin', 'customer')),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Products table
CREATE TABLE IF NOT EXISTS products (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 0,
    location       VARCHAR(255) DEFAULT '',
    minimum_stock  INTEGER NOT NULL DEFAULT 5,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id         SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    type       VARCHAR(10) NOT NULL CHECK (type IN ('add', 'remove')),
    quantity   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    date       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_products_name ON products (name);
CREATE INDEX IF NOT EXISTS idx_transactions_product ON transactions (product_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

"""


def init_db():
    """Create tables if they do not exist. Safe to run on every startup."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        print("[DB] Schema initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"[DB] Schema init failed: {e}")
        raise
    finally:
        release_connection(conn)
