import os
import re
import json
import html
import datetime
import logging
import asyncio
import contextvars
import unicodedata
import time
import hashlib
import base64
import secrets
import io
import threading
import aiohttp
from aiohttp import web
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    TypeHandler,
    ApplicationHandlerStop,
    filters,
)

# ═══════════════════════════════════════════════════════════
#  BRAND THEME — LE ROI DE LA CÉRAMIQUE (v88: الشعار خارج الملف)
#  سابقاً كان الشعار مضمّناً base64 داخل bot.py (~570KB تضخم الملف).
#  الآن: يُحمَّل مرة واحدة من الرابط (بذرة) ويُخزَّن في قاعدة Neon
#  (جدول app_assets) — القاعدة هي المصدر الدائم بعدها، والرابط لا
#  يُستخدم إلا إن كان الجدول فارغاً. كل المسارات لها fallback آمن:
#  رسائل البوت نص صريح (أسرع بلا رفع صورة مع كل رسالة)، وخلفية
#  الفاتورة سادة، وأيقونة التطبيق تُخدم فارغة مؤقتاً حتى البذرة.
#  الغلاف/الشعار باقٍ كاملين في التطبيق (PWA) وفي فواتير PDF.
# ═══════════════════════════════════════════════════════════
_THEME_LOGO_URL = "https://x0.at/6jJj.zip"   # zip يحوي brand_logo.jpg — بذرة أول تشغيل فقط
_THEME_LOGO_BYTES = None                    # كاش بالذاكرة — يُملأ كسولاً
_THEME_LOGO_LOCK = threading.Lock()
_THEME_LOGO_URL_TRIED_AT = 0.0              # آخر محاولة بذرة (cooldown 10 د)
_APP_ASSETS_TABLE_OK = False                # ضمان وجود الجدول مرة واحدة


def _theme_logo_ensure_table(conn):
    """v88: جدول أصول التطبيق — إنشاء دفاعي (يُجرّب مرة كل عملية)."""
    global _APP_ASSETS_TABLE_OK
    if _APP_ASSETS_TABLE_OK:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS app_assets ("
                "key TEXT PRIMARY KEY, data BYTEA NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        conn.commit()
        _APP_ASSETS_TABLE_OK = True
    except Exception:
        conn.rollback()


def _theme_logo_load():
    """v88: بايتات الشعار — من الذاكرة ثم القاعدة ثم بذرة الرابط.
    يرجع None عند تعذر كل المصادر (النظام يعمل بلا غلاف بأمان)."""
    global _THEME_LOGO_BYTES, _THEME_LOGO_URL_TRIED_AT
    if _THEME_LOGO_BYTES:
        return _THEME_LOGO_BYTES
    with _THEME_LOGO_LOCK:
        if _THEME_LOGO_BYTES:
            return _THEME_LOGO_BYTES
        # 1) القاعدة — المصدر الدائم بعد أول بذرة
        conn = None
        try:
            conn = get_connection()
            _theme_logo_ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM app_assets WHERE key = %s", ("brand_logo",))
                row = cur.fetchone()
            conn.commit()
            if row and row[0]:
                _THEME_LOGO_BYTES = bytes(row[0])
                logging.info("[THEME] v88 logo loaded from DB ("
                             + str(len(_THEME_LOGO_BYTES)) + " bytes)")
                return _THEME_LOGO_BYTES
        except Exception as db_err:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logging.warning("[THEME] logo DB load failed: " + str(db_err))
        finally:
            if conn:
                release_connection(conn)
        # 2) بذرة أولى من الرابط — مع cooldown حتى لا نطرق الرابط عند كل طلب
        now_t = time.time()
        if now_t - _THEME_LOGO_URL_TRIED_AT < 600:
            return _THEME_LOGO_BYTES
        _THEME_LOGO_URL_TRIED_AT = now_t
        try:
            import urllib.request
            import zipfile as _zf
            req = urllib.request.Request(
                _THEME_LOGO_URL, headers={"User-Agent": "bot-v88-theme"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                blob = resp.read()
            raw = None
            if blob[:2] == b"\xff\xd8":
                raw = blob                          # JPEG مباشرة
            else:
                with _zf.ZipFile(io.BytesIO(blob)) as z:
                    for nm in z.namelist():
                        if nm.lower().endswith((".jpg", ".jpeg", ".png")):
                            raw = z.read(nm)
                            break
            if raw and raw[:2] == b"\xff\xd8":
                _THEME_LOGO_BYTES = raw
                try:
                    conn = get_connection()
                    try:
                        _theme_logo_ensure_table(conn)
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO app_assets (key, data) VALUES (%s, %s) "
                                "ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, "
                                "updated_at = NOW()",
                                ("brand_logo", psycopg2.Binary(raw)))
                        conn.commit()
                        logging.info("[THEME] v88 logo seeded URL -> DB ("
                                     + str(len(raw)) + " bytes) — الرابط لن يُستخدم بعدها")
                    finally:
                        release_connection(conn)
                except Exception as seed_err:
                    logging.warning("[THEME] logo seed save failed (يعاد لاحقاً): "
                                    + str(seed_err))
                    if conn:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                return _THEME_LOGO_BYTES
            logging.warning("[THEME] logo URL payload ليس JPEG/zip صالحاً")
        except Exception as url_err:
            logging.warning("[THEME] logo URL seed failed: " + str(url_err))
    return _THEME_LOGO_BYTES


def _theme_logo_stream():
    """Fresh BytesIO of the brand logo, or None (v88: تحميل كسول من القاعدة)."""
    data = _theme_logo_load()
    if not data:
        return None
    try:
        return io.BytesIO(data)
    except Exception:
        return None


def _can_caption(text):
    """Telegram caption limit is 1024 chars — keep a safety margin."""
    return bool(text) and len(text) <= 1000


async def _send_themed(bot, chat_id, text, reply_markup=None, parse_mode=None):
    """v88: رسائل البوت نص صريح — أُزيل غلاف الصورة من رسائل البوت كلها
    (كان يرفع صورة ~430KB مع كل رسالة: بطء استجابة واستهلاك بيانات).
    الشعار باقٍ كاملاً في التطبيق (أيقونة/غلاف PWA) وفي خلفية فواتير PDF.
    التوقيع نفسه بقي كما هو حتى لا يتأثر أي من المستدعين (عشرات المواضع)."""
    return await bot.send_message(
        chat_id=chat_id, text=text or "…",
        reply_markup=reply_markup, parse_mode=parse_mode,
    )


def _build_invoice_background():
    """Compose the full-page A4 invoice background from the brand logo:
    beige base (sampled from the logo's own corners) + full logo header
    + faint centered watermark. Returns PNG bytes, or None on failure."""
    logo_bytes = _theme_logo_load()
    if not logo_bytes:
        return None
    try:
        from PIL import Image, ImageFilter

        def _feather_mask(size, inset, blur):
            """Soft-edged square mask so pasted logos blend into the page."""
            m = Image.new("L", (size, size), 0)
            m.paste(Image.new("L", (size - 2 * inset, size - 2 * inset), 255), (inset, inset))
            return m.filter(ImageFilter.GaussianBlur(blur))

        logo = Image.open(io.BytesIO(logo_bytes)).convert("RGB")
        W, H = 2480, 3508  # A4 @ 300dpi (print quality)
        # Trim the deckled paper edges + white margin so the logo blends clean
        _in = int(min(logo.size) * 0.07)
        logo = logo.crop((_in, _in, logo.size[0] - _in, logo.size[1] - _in))
        px = logo.load()
        w0, h0 = logo.size
        # Sample the base color from a clean paper area (left-middle band)
        _step = max(1, w0 // 30)
        patch = [px[x, y]
                 for y in range(int(h0 * 0.35), int(h0 * 0.50), _step)
                 for x in range(int(w0 * 0.03), int(w0 * 0.13), _step)]
        beige = tuple(sum(c[i] for c in patch) // len(patch) for i in range(3))
        page = Image.new("RGB", (W, H), beige)
        # Header: the full square logo, centered at the top, feathered edges
        head_side = 760
        head = logo.resize((head_side, head_side), Image.LANCZOS)
        page.paste(head, ((W - head_side) // 2, 72), _feather_mask(head_side, 48, 36))
        # Watermark: big faint logo behind the body area, feathered edges
        wm_side = 1280
        wm = logo.resize((wm_side, wm_side), Image.LANCZOS).convert("RGBA")
        wm.putalpha(_feather_mask(wm_side, 80, 72).point(lambda a: a * 28 // 255))
        page_rgba = page.convert("RGBA")
        page_rgba.alpha_composite(wm, ((W - wm_side) // 2, int(H * 0.52)))
        out = io.BytesIO()
        page_rgba.convert("RGB").save(out, "JPEG", quality=88)  # JPEG: paper texture compresses far better than PNG
        return out.getvalue()
    except Exception as bg_build_err:
        logging.warning("[THEME] invoice background build failed: " + str(bg_build_err))
        return None

# ═══════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════
#  EXTRACTION PERMISSION & REQUEST FUNCTIONS
# ═══════════════════════════════════════════════════════════

def can_extract(telegram_id):
    """Check if user has extraction permission."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT can_extract FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return bool(row and row["can_extract"])
    except Exception as e:
        conn.rollback()
        logging.error("[DB] can_extract check failed: " + str(e), exc_info=True)
        return False
    finally:
        release_connection(conn)


def get_product_by_id(product_id):
    """Fetch a single product by ID. Returns dict or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def process_extraction_request(request_id, approved, admin_id):
    """Approve or reject an extraction request. Returns (success, message)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT er.*, p.name AS product_name, p.quantity AS current_qty "
                "FROM extraction_requests er "
                "JOIN products p ON p.id = er.product_id "
                "WHERE er.id = %s AND er.status = 'pending' FOR UPDATE",
                (request_id,),
            )
            req = cur.fetchone()
            if not req:
                return False, "الطلب غير موجود أو تم معالجته."
            req = dict(req)
            if approved:
                if req["current_qty"] < req["quantity"]:
                    return False, (
                        "الكمية المطلوبة ("
                        + str(req["quantity"]) + ") أكبر من المتوفر ("
                        + str(req["current_qty"]) + ")."
                    )
                cur.execute(
                    "UPDATE products SET quantity = %s WHERE id = %s",
                    (req["current_qty"] - req["quantity"], req["product_id"]),
                )
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id) VALUES (%s, 'remove', %s, %s)",
                    (req["product_id"], req["quantity"], admin_id),
                )
            cur.execute(
                "UPDATE extraction_requests SET status = %s, processed_at = NOW() WHERE id = %s",
                ("approved" if approved else "rejected", request_id),
            )
        conn.commit()
        pname = req["product_name"]
        qty = req["quantity"]
        if approved:
            return True, "✅ تم الموافقة على إخراج " + str(qty) + " من " + pname
        else:
            return True, "❌ تم رفض طلب الإخراج."
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")
RENDER_EXTERNAL_URL: str = os.getenv("RENDER_EXTERNAL_URL", "")  # auto-set by Render

# Email backup (optional — for daily auto-backup via email)
SMTP_HOST = os.getenv("SMTP_HOST", "")         # e.g. smtp.gmail.com
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")       # sender email
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")   # app password
BACKUP_EMAIL = os.getenv("BACKUP_EMAIL", "")     # recipient email
# ── v33: قنوات تسليم رمز الدخول (OTP) ──
# البريد: نفس متغيرات SMTP_* أعلاه (مجاني: Brevo smtp-relay.brevo.com:587 أو Gmail smtp.gmail.com مع App Password)
# SMS: TextBee (textbee.dev) — هاتفك الاحتياطي يصبح بوابة إرسال SMS — مجاني
TEXTBEE_API_KEY = os.getenv("TEXTBEE_API_KEY", "")
TEXTBEE_DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "")
TEXTBEE_API_BASE = os.getenv("TEXTBEE_API_BASE", "https://api.textbee.dev/api/v1")
SMTP_TLS = os.getenv("SMTP_TLS", "").strip().lower()  # "" = 465→SSL وإلا STARTTLS | "ssl" | "none" (اختبار فقط)
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()  # v38: مفتاح Brevo API (HTTPS:443) — يتجاوز حجب منافذ SMTP في الاستضافة المجانية مثل Render
# ── v33: دخول بحساب Google (OAuth مجاني) ──
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()  # يُضبط يدوياً للاختبار المحلي؛ على Render يُشتق من RENDER_EXTERNAL_URL
GOOGLE_TOKEN_URL = os.getenv("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")  # قابل للتوجيه في الاختبار
IMAP_HOST = os.getenv("IMAP_HOST", "")  # e.g. imap.gmail.com
BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID", "")  # Telegram channel ID for backup files (e.g. "-1001234567890")

_last_backup_data = None  # keeps latest backup dict in memory
_startup_restore_notice = None  # رسالة استعادة تلقائية عند الإقلاع تُرسل للمدراء بعد تشغيل البوت


_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list = [
    int(uid.strip())
    for uid in _raw_admin_ids.split(",")
    if uid.strip().isdigit()
]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set.")

_raw_owner_ids = os.getenv("OWNER_IDS", "")
OWNER_IDS: list = [
    int(uid.strip())
    for uid in _raw_owner_ids.split(",")
    if uid.strip().isdigit()
]
if not OWNER_IDS:
    OWNER_IDS = [ADMIN_IDS[0]]

# ── Security codes: من الأفضل أن تُضبط عبر متغيرات البيئة في الإنتاج ──
# القيم الافتراضية محفوظة للتوافق مع النشر الحالي فقط.
RESET_STOCK_PIN = os.getenv("RESET_STOCK_PIN", "5614")

_previously_low_stock = {}  # {product_id: product_dict}

def _init_low_stock_ids():
    """Populate _previously_low_stock with current low-stock product IDs on startup.
    This prevents sending "new" alerts for products already low before restart."""
    global _previously_low_stock
    try:
        low_stock = get_low_stock_products()
        _previously_low_stock = {p["id"]: p for p in low_stock}
        logging.info("[INIT] Low-stock tracking initialized with " + str(len(_previously_low_stock)) + " products")
    except Exception as e:
        logging.warning("[INIT] Failed to init low-stock IDs: " + str(e))

# ═══════════════════════════════════════════════════════════
#  DATABASE — SINGLE SOURCE (v87)
# ═══════════════════════════════════════════════════════════
#  v87: قاعدة واحدة = مصدر البيانات الوحيد. لا نبض دوري ولا تبديل تلقائي
#  (Scale-to-Zero في Neon يعود للعمل — توفير ~80% من الحصة المجانية).
#  DATABASE_URLS ما زالت مدعومة للتوافق لكن يُستخدم الرابط الأول فقط.
#  عند فشل حقيقي: إشعار فوري للمالك + الطابور الأوفلاين يحمي العمليات،
#  وعند الإقلاع على قاعدة فارغة: استعادة تلقائية من آخر نسخة متاحة.

_pool = None
_active_db_index = 0
_db_urls = []
_pool_creation_lock = threading.Lock()
# v87: تنبيهات مبنية على الأخطاء الحقيقية فقط — صفر استعلامات دورية
_db_down_alert_pending = False
_db_was_down = False
_db_up_alert_pending = False


def _mark_db_ok():
    """v87: أول اتصال ناجح بعد حادثة هبوط → علم إشعار العودة (يُرسل مرة واحدة)."""
    global _db_was_down, _db_up_alert_pending
    if _db_was_down:
        _db_was_down = False
        _db_up_alert_pending = True


def _mark_db_down():
    """v87: فشل كل محاولات الاتصال → علم إشعار الهبوط (يُرسل مرة واحدة)."""
    global _db_down_alert_pending
    _db_down_alert_pending = True


def _init_db_urls():
    """Parse DATABASE_URLS (comma-separated) or fall back to DATABASE_URL.
    Sets _db_urls list and validates at least one URL exists."""
    global _db_urls, _active_db_index
    raw = os.getenv("DATABASE_URLS", "").strip()
    if raw:
        _db_urls = [u.strip() for u in raw.split(",") if u.strip()]
    # Fallback to single DATABASE_URL for backwards compatibility
    if not _db_urls:
        single = os.getenv("DATABASE_URL", "").strip()
        if single:
            _db_urls = [single]
    if not _db_urls:
        raise RuntimeError("DATABASE_URL or DATABASE_URLS is not set.")
    _active_db_index = 0
    logging.info("[DB] Initialized with " + str(len(_db_urls)) + " database URL(s). Active: #1")


def get_active_db_url():
    """Return the currently active database URL."""
    if not _db_urls:
        _init_db_urls()
    return _db_urls[_active_db_index % len(_db_urls)]


# ═══ v72: صمامات زمنية قسرية لكل اتصال Postgres — لا انتظار لانهائي بعد الآن ═══
# connect_timeout=10: أي محاولة اتصال معلقة تُقتل بعد 10 ث بدل دقائق/ساعات.
# TCP keepalives: الاتصال الميت (Neon suspend) يُكتشف خلال ~60 ث بدل 15+ دقيقة.
# statement_timeout: أي استعلام عالق يُقطع بعد 15 ث — لا تجميد دائم.
DB_CONNECT_KWARGS = {
    "connect_timeout": 10,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def init_pool(url=None):
    """Create connection pool for the given URL (or active DB).
    Thread-safe: guarded by a dedicated lock, and any previous pool is fully
    drained first so repeated calls never leak connections.
    v74: ThreadedConnectionPool إلزامي — توثيق psycopg2 يمنع مشاركة
    SimpleConnectionPool بين الخيوط (تزامن getconn/putconn يُتلف دفاتر
    الـPool تدريجياً → فشل شامل حتى /start يتشافى فقط بإعادة تشغيل)."""
    global _pool
    dsn = url or get_active_db_url()
    with _pool_creation_lock:
        old = _pool
        _pool = None
        if old is not None:
            try:
                old.closeall()
            except Exception:
                pass
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1, maxconn=5, dsn=dsn, **DB_CONNECT_KWARGS
        )
    return _pool


def get_connection():
    """v87: اتصال حي من الـ Pool للقاعدة الوحيدة — لا تبديل تلقائي إطلاقاً.
    عند الفشل المتكرر: إعادة بناء Pool مرة واحدة ثم RuntimeError مع رفع
    علم إشعار المالك (يُرسل عبر keep-alive مرة واحدة لكل حادثة).

    Fixes over the previous version:
    - init_pool() failure at startup now triggers failover instead of crashing.
    - Dead pooled connections are discarded via putconn(close=True) so the
      pool's internal bookkeeping stays consistent (no more leaked slots).
    - Brief backoff between attempts absorbs transient bursts/exhaustion."""
    global _pool, _active_db_index
    last_err = None
    for attempt in range(3):
        try:
            if _pool is None:
                init_pool()
            conn = _pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.execute("SET statement_timeout = 15000")  # v73
                    cur.execute("SET idle_in_transaction_session_timeout = 30000")
                    cur.execute("SET TIME ZONE 'Africa/Algiers'")
                _mark_db_ok()
                return conn
            except Exception:
                # connection is dead → return it CLOSED through the pool API
                try:
                    _pool.putconn(conn, close=True)
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                raise
        except Exception as e:
            last_err = e
            logging.warning(
                "[DB] get_connection attempt " + str(attempt + 1) + "/3 failed: "
                + str(e)[:150]
            )
            time.sleep(0.2 * (attempt + 1))
    # Last resort: recreate current pool
# Last resort: recreate current pool (single-DB or all multi-DBs failed)
    logging.warning("[DB] Recreating pool for current DB...")
    try:
        init_pool()
        conn = _pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SET statement_timeout = 15000")  # v73
            cur.execute("SET idle_in_transaction_session_timeout = 30000")
            cur.execute("SET TIME ZONE 'Africa/Algiers'")
        _mark_db_ok()
        return conn
    except Exception as e:
        _mark_db_down()
        raise RuntimeError(
            "No database connection available: " + str(last_err) + " / " + str(e)
        )


def release_connection(conn):
    if conn is None:
        return
    if conn.closed:
        return
    if _pool:
        try:
            _pool.putconn(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

# ═══════════════════════════════════════════════════════════
#  OFFLINE MODE + SYNC SYSTEM
# ═══════════════════════════════════════════════════════════
#  1. Operation Queue (WAL) — saves failed writes, retries later
#  2. Message Deduplication — prevents double-processing
#  3. Read Cache — serves cached data when DB is down

_OFFLINE_QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_offline_queue.json")
_BACKUP_DISK_PATH = "/tmp/backup_last.json"  # Render writable; survives within instance lifetime
_offline_queue = []
_offline_queue_lock = threading.Lock()
_processed_update_ids = {}
_DEDUP_MAX = 5000
_PRODUCT_CACHE = {}
_PRODUCT_CACHE_TIME = 0
_PRODUCT_CACHE_TTL = 120  # seconds
_cache_lock = threading.Lock()


def _load_offline_queue():
    """Load persisted queue from disk on startup."""
    global _offline_queue
    try:
        if os.path.exists(_OFFLINE_QUEUE_PATH):
            with open(_OFFLINE_QUEUE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _offline_queue = [op for op in data if isinstance(op, dict) and op.get("retries", 0) < 15]
                logging.info("[OFFLINE] Loaded " + str(len(_offline_queue)) + " pending ops from disk")
    except Exception as e:
        logging.error("[OFFLINE] Failed to load queue: " + str(e))
        _offline_queue = []


def _save_offline_queue():
    """Persist queue to disk."""
    try:
        with open(_OFFLINE_QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(_offline_queue, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logging.error("[OFFLINE] Failed to save queue: " + str(e))


def queue_operation(op_type, data, description=""):
    """Add a failed DB operation to the retry queue."""
    with _offline_queue_lock:
        op = {
            "id": hashlib.md5((op_type + str(data) + str(time.time())).encode()).hexdigest()[:12],
            "type": op_type,
            "data": data,
            "desc": description or op_type,
            "retries": 0,
            "created_at": time.time(),
        }
        _offline_queue.append(op)
        _save_offline_queue()
        count = len(_offline_queue)
    logging.warning("[OFFLINE] Queued: " + op["desc"] + " (queue: " + str(count) + ")")
    return op["id"]


def _execute_queued_op(op):
    """Execute a single queued operation. Returns True on success, "drop" for
    permanently invalid ops (must not be retried), False on transient failure.
    NOTE: called while holding _offline_queue_lock — must NEVER call
    queue_operation() itself (non-reentrant lock) or it deadlocks."""
    op_type = op["type"]
    data = op["data"]
    try:
        if op_type == "add_qty":
            db_update_quantity(data["product_id"], data["qty"], "add")
            # Reconcile pending deliveries covered by the replayed stock
            try:
                auto_reconcile_product(data["product_id"], data["qty"], data.get("user_id"))
            except Exception as rec_err:
                logging.error("[OFFLINE] Replay: reconcile failed: " + str(rec_err))
            if data.get("user_id"):
                try:
                    log_transaction(data["product_id"], "add", data["qty"], data["user_id"], reason=data.get("reason"))
                except Exception as tx_err:
                    # ledger entry lost on replay (stock already correct) — logged loudly
                    logging.error("[OFFLINE] Replay: ledger write failed after qty add: " + str(tx_err))
        elif op_type == "remove_qty":
            db_update_quantity(data["product_id"], data["qty"], "remove")
            if data.get("user_id"):
                try:
                    log_transaction(data["product_id"], "remove", data["qty"], data["user_id"], reason=data.get("reason"))
                except Exception as tx_err:
                    logging.error("[OFFLINE] Replay: ledger write failed after qty remove: " + str(tx_err))
            # (v80) قفل حلقة العجز: إعادة تشغيل إخراج أوفلاين قد تخلق سالب بلا تسجيل
            try:
                _sync_r = sync_product_pending_deficit_now(data["product_id"], data.get("user_id"))
                if _sync_r and _sync_r["created"] > 0:
                    logging.warning("[OFFLINE] Replay remove: deficit "
                                    + str(_sync_r["created"]) + " registered as pending (product "
                                    + str(data["product_id"]) + ")")
            except Exception as sync_err:
                logging.error("[OFFLINE] Replay remove: deficit sync failed: " + str(sync_err))
        elif op_type == "add_product":
            _rp, _rp_created = db_add_product(data["name"], data.get("quantity", 0), data.get("location", ""), data.get("minimum_stock", 5))
            # If the name already existed the stock just increased —
            # reconcile pending deliveries it may now cover
            try:
                auto_reconcile_product(_rp["id"], data.get("quantity", 0), data.get("user_id"))
            except Exception as rec_err:
                logging.error("[OFFLINE] Replay: reconcile failed: " + str(rec_err))
        elif op_type == "register_user":
            register_user(data["telegram_id"], data.get("username"), data.get("first_name"))
        elif op_type == "log_transaction":
            log_transaction(data["product_id"], data["type"], data["quantity"], data["user_id"], reason=data.get("reason"))
        else:
            logging.warning("[OFFLINE] Unknown op type: " + op_type)
            return "drop"
        return True
    except ValueError as e:
        # Permanent condition (e.g., product/user deleted meanwhile) — retrying is pointless.
        logging.warning("[OFFLINE] Dropping permanently invalid op '" + op["desc"] + "': " + str(e))
        return "drop"
    except Exception as e:
        logging.warning("[OFFLINE] Retry failed for " + op["desc"] + ": " + str(e))
        return False


def _retry_pending_operations(bot=None, loop=None):
    """Retry all pending operations. Called by background task.
    `loop` is the main asyncio event loop, needed to schedule admin
    notifications from this synchronous thread via
    run_coroutine_threadsafe.
    """
    global _offline_queue
    with _offline_queue_lock:
        if not _offline_queue:
            return 0
        remaining = []
        succeeded = 0
        failed_permanent = []
        dropped = 0
        for op in _offline_queue:
            op["retries"] += 1
            result = _execute_queued_op(op)
            if result == "drop":
                # permanently invalid → remove silently (already logged by executor)
                dropped += 1
            elif result:
                succeeded += 1
                logging.info("[OFFLINE] Recovered: " + op["desc"])
            elif op["retries"] >= 15:
                failed_permanent.append(op)
                logging.error("[OFFLINE] Permanently failed: " + op["desc"])
            else:
                remaining.append(op)
        _offline_queue = remaining
        _save_offline_queue()
        if dropped:
            logging.info("[OFFLINE] Dropped " + str(dropped) + " invalid queued op(s).")
    # Notify admin about permanent failures
    if failed_permanent and bot and loop is not None:
        for admin_id in ADMIN_IDS:
            try:
                msg = "\u26a0\ufe0f \u0639\u0645\u0644\u064a\u0627\u062a \u0644\u0645 \u062a\u0646\u062c\u062d \u0628\u0639\u062f \u0639\u062f\u0629 \u0645\u062d\u0627\u0648\u0644\u0627\u062a:\n\n"
                for op in failed_permanent:
                    age_min = int((time.time() - op["created_at"]) / 60)
                    msg += "\u2022 " + op["desc"] + " (" + str(age_min) + " \u062f\u0642\u064a\u0642\u0629)\n"
                msg += "\n\U0001f4cb \u064a\u0631\u062c\u0649 \u0627\u0644\u062a\u062d\u0642\u0642 \u062a\u062f\u0648\u064a\u0627\u064b"
                # run_coroutine_threadsafe schedules the coroutine on the
                # main event loop (which lives in the main thread). Calling
                # asyncio.ensure_future here would raise RuntimeError because
                # this function runs inside asyncio.to_thread().
                future = asyncio.run_coroutine_threadsafe(
                    bot.send_message(chat_id=admin_id, text=msg),
                    loop,
                )
                def _log_notify_err(fut, _aid=admin_id):
                    try:
                        fut.result()
                    except Exception as exc:
                        logging.warning("[OFFLINE] Failed to notify admin " + str(_aid) + ": " + str(exc))
                future.add_done_callback(_log_notify_err)
            except Exception:
                pass
    return succeeded


async def _offline_retry_loop(bot):
    """Background loop: retry queued operations every 30 seconds."""
    await asyncio.sleep(10)  # Wait for bot to fully start
    loop = asyncio.get_running_loop()
    while True:
        try:
            count = await asyncio.to_thread(_retry_pending_operations, bot, loop)
            if count > 0:
                logging.info("[OFFLINE] Recovered " + str(count) + " operations")
                _invalidate_product_cache()
        except Exception as e:
            logging.error("[OFFLINE] Retry loop error: " + str(e))
        await asyncio.sleep(30)


# ── Message Deduplication ──

def is_duplicate_update(update):
    """Check if this update was already processed. Returns True if duplicate."""
    try:
        uid = update.update_id
        if uid is None:
            return False
        if uid in _processed_update_ids:
            return True
        _processed_update_ids[uid] = time.time()
        # Evict old entries
        if len(_processed_update_ids) > _DEDUP_MAX:
            cutoff = time.time() - 3600
            expired = [k for k, v in _processed_update_ids.items() if v < cutoff]
            for k in expired:
                del _processed_update_ids[k]
            if len(_processed_update_ids) > _DEDUP_MAX:
                sorted_keys = sorted(_processed_update_ids, key=_processed_update_ids.get)
                for k in sorted_keys[:len(_processed_update_ids) - _DEDUP_MAX + 1000]:
                    del _processed_update_ids[k]
        return False
    except Exception:
        return False


# ── Product Read Cache ──

def _refresh_product_cache():
    """Fetch all products from DB and cache them."""
    global _PRODUCT_CACHE, _PRODUCT_CACHE_TIME
    try:
        products = get_all_products()
        with _cache_lock:
            _PRODUCT_CACHE = {p["id"]: p for p in products}
            _PRODUCT_CACHE_TIME = time.time()
        return True
    except Exception as e:
        logging.warning("[CACHE] Refresh failed: " + str(e))
        return False


def _invalidate_product_cache():
    """Mark cache as stale."""
    global _PRODUCT_CACHE_TIME
    _PRODUCT_CACHE_TIME = 0


def get_cached_products():
    """Get products from cache if fresh, otherwise from DB."""
    with _cache_lock:
        if _PRODUCT_CACHE and (time.time() - _PRODUCT_CACHE_TIME) < _PRODUCT_CACHE_TTL:
            return list(_PRODUCT_CACHE.values())
    if _refresh_product_cache():
        with _cache_lock:
            return list(_PRODUCT_CACHE.values())
    return []


def get_offline_queue_count():
    """Return number of pending operations in queue."""
    with _offline_queue_lock:
        return len(_offline_queue)


SCHEMA_SQL = """\nCREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username    VARCHAR(255),
    first_name  VARCHAR(255),
    role        VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer', 'pending')),
    requested_role VARCHAR(20) DEFAULT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    can_extract       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
    id             SERIAL PRIMARY KEY,
    name           VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 0,
    location       VARCHAR(255) DEFAULT '',
    minimum_stock  INTEGER NOT NULL DEFAULT 5,
    selling_price  NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id         SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    type       VARCHAR(10) NOT NULL CHECK (type IN ('add', 'remove', 'reconcile')),
    quantity   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    date       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason     VARCHAR(200)
);

CREATE INDEX IF NOT EXISTS idx_products_name ON products (name);
CREATE INDEX IF NOT EXISTS idx_transactions_product ON transactions (product_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);

CREATE TABLE IF NOT EXISTS extraction_requests (
    id            SERIAL PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity      INTEGER NOT NULL,
    requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extraction_requests_status ON extraction_requests (status);
CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id);

CREATE TABLE IF NOT EXISTS incoming_orders (
    id             SERIAL PRIMARY KEY,
    requester_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    product_name   VARCHAR(500) NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 1,
    status         VARCHAR(12) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'received', 'completed')),
    photo_file_id  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_incoming_orders_status ON incoming_orders (status);

CREATE TABLE IF NOT EXISTS notifications (
    id         SERIAL PRIMARY KEY,
    kind       VARCHAR(20) NOT NULL DEFAULT 'info',
    title      VARCHAR(300) NOT NULL,
    body       TEXT DEFAULT '',
    actor      VARCHAR(255) DEFAULT '',
    source     VARCHAR(10) DEFAULT 'bot',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notification_reads (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    last_read_id INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_notifications_id ON notifications (id DESC);


CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bot_settings (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role     VARCHAR(20) NOT NULL,
    perm     VARCHAR(60) NOT NULL,
    enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (role, perm)
);

CREATE TABLE IF NOT EXISTS invoices (
    id              SERIAL PRIMARY KEY,
    customer_name   VARCHAR(500) NOT NULL,
    worker_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    admin_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    total_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
    status          VARCHAR(15) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','cancelled')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id              SERIAL PRIMARY KEY,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name    VARCHAR(500) NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      NUMERIC(12,2) NOT NULL DEFAULT 0,
    subtotal        NUMERIC(12,2) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice ON invoice_items(invoice_id);

CREATE TABLE IF NOT EXISTS pending_deliveries (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    product_id    INTEGER REFERENCES products(id) ON DELETE SET NULL,
    pending_qty   INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fulfilled_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_pending_deliveries_product ON pending_deliveries(product_id);
CREATE INDEX IF NOT EXISTS idx_pending_deliveries_user ON pending_deliveries(user_id);
CREATE INDEX IF NOT EXISTS idx_pending_deliveries_pending ON pending_deliveries(product_id) WHERE pending_qty > 0;

-- v21: صيانة الرافعة الشوكية — دورة زيت المحرك
CREATE TABLE IF NOT EXISTS oil_status (
    id                   INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    baseline_hours       NUMERIC(10,1) NOT NULL DEFAULT 0,
    last_change_at       TIMESTAMPTZ,
    current_hours        NUMERIC(10,1),
    current_hours_at     TIMESTAMPTZ,
    confirm_worker_id    BIGINT,
    confirm_requested_at TIMESTAMPTZ,
    confirm_confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
    confirm_confirmed_at TIMESTAMPTZ,
    weekly_remind_date   DATE,
    predict_sent_at      TIMESTAMPTZ,
    notified_200_date    DATE,
    notified_250_at      TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oil_readings (
    id       SERIAL PRIMARY KEY,
    reading  NUMERIC(10,1) NOT NULL,
    read_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    by_name  VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS oil_log (
    id               SERIAL PRIMARY KEY,
    changed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hours_at_change  NUMERIC(10,1),
    recorded_by_name VARCHAR(255),
    note             VARCHAR(500)
);

CREATE INDEX IF NOT EXISTS idx_oil_readings_read_at ON oil_readings (read_at);
CREATE INDEX IF NOT EXISTS idx_oil_log_changed_at ON oil_log (changed_at);

-- v88: أصول التطبيق — شعار الغلاف يُخزَّن هنا بدل تضخيم bot.py
CREATE TABLE IF NOT EXISTS app_assets (
    key        TEXT PRIMARY KEY,
    data       BYTEA NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

"""


def init_db(conn=None):
    # v85: يقبل اتصالاً جاهزاً — لزرع المخطط أثناء تبديل القواعد مباشرة
    # بلا مرور بـ get_connection (يمنع أي حلقة قفل/فشل تبادلية).
    owned = conn is None
    if owned:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            # 🔐 زرع الصلاحيات الافتراضية للفئات (مرة واحدة فقط —
            # ON CONFLICT DO NOTHING يحمي تعديلات المدير اللاحقة)
            for _seed_role, _seed_perms in ROLE_PERM_CATALOG.items():
                for _seed_perm in _seed_perms:
                    cur.execute(
                        "INSERT INTO role_permissions (role, perm, enabled) VALUES (%s, %s, TRUE) "
                        "ON CONFLICT (role, perm) DO NOTHING",
                        (_seed_role, _seed_perm),
                    )
            # v20: تنظيف صف صلاحية unshipped من القاعدة (النظام أُزيل)
            cur.execute("DELETE FROM role_permissions WHERE perm = 'unshipped'")
            # Migration: add 'pending' role for existing databases
            cur.execute("""\n                DO $$ BEGIN
                    ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE users ADD CONSTRAINT users_role_check
                    CHECK (role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer', 'pending'));
                ALTER TABLE users ALTER COLUMN role SET DEFAULT 'pending';
                -- Migration: add can_extract column
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS can_extract BOOLEAN NOT NULL DEFAULT FALSE;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: add first_name column for auto-update display names
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name VARCHAR(255);
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: v58 عمود آخر ظهور — لحالة «متصل الآن» في الرسائل
                DO $$ BEGIN
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: update incoming_orders status to include 'received'
                DO $$ BEGIN
                    ALTER TABLE incoming_orders DROP CONSTRAINT IF EXISTS incoming_orders_status_check;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                ALTER TABLE incoming_orders ADD CONSTRAINT incoming_orders_status_check
                    CHECK (status IN ('pending', 'received', 'completed'));
                -- Migration: add photo_file_id column for receipt photos
                DO $$ BEGIN
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS photo_file_id TEXT;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration (v31): صورة وصل التوريد من الويب/APK — تُخزن في القاعدة
                -- (ملفات Render مؤقتة وتمحى مع كل نشر، لذا القرص غير صالح للتخزين)
                DO $$ BEGIN
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo BYTEA;
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo_mime VARCHAR(60);
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo_at TIMESTAMPTZ;
                    ALTER TABLE incoming_orders ADD COLUMN IF NOT EXISTS web_photo_by INTEGER;
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
                -- Migration: create extraction_requests table
                CREATE TABLE IF NOT EXISTS extraction_requests (
                    id            SERIAL PRIMARY KEY,
                    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    quantity      INTEGER NOT NULL,
                    requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    status        VARCHAR(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    processed_at  TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_extraction_requests_status ON extraction_requests (status);
            -- Migration: bot_updates table (also declared in SCHEMA_SQL above; idempotent)
            
CREATE TABLE IF NOT EXISTS bot_updates (
    id          SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
            -- Seed: insert bot updates if table is empty
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة طلبات التوريد مع خاصية تصوير الوصل' || chr(10) || chr(10) || '📦 يمكن للمستخدمين الآن إرسال طلبات توريد جديدة عن طريق تصوير وصل التوريد' || chr(10) || '📋 الطلب يظهر تلقائياً للمدير مع صورة الوصل' || chr(10) || '✅ المدير يضغط جاهز أو غير جاهز ويوصلك إشعار')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة زر الحركة بالتاريخ' || chr(10) || chr(10) || '📋 الآن زر الحركة يعرض آخر 7 أيام' || chr(10) || '📅 اضغط على أي تاريخ لعرض كل عمليات الإضافة والإخراج في ذلك اليوم')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة نظام طلبات التوريد' || chr(10) || chr(10) || '📦 زر جديد: طلب توريد للمستخدمين' || chr(10) || '📋 الطلب يظهر للمدير مع تفاصيل كاملة')
            ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS bot_settings (
                key         VARCHAR(100) PRIMARY KEY,
                value       TEXT
            );
            -- Migration: add selling_price to products
            DO $$ BEGIN
                ALTER TABLE products ADD COLUMN IF NOT EXISTS selling_price NUMERIC(12,2) NOT NULL DEFAULT 0;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: allow invoice_items.product_id to be NULL (for manual products)
            DO $$ BEGIN
                ALTER TABLE invoice_items ALTER COLUMN product_id DROP NOT NULL;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: history preservation on product delete —
            -- detach pending_deliveries rows instead of wiping them.
            DO $$ BEGIN
                ALTER TABLE pending_deliveries ALTER COLUMN product_id DROP NOT NULL;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Seed: add invoice update notification
            INSERT INTO bot_updates (description) VALUES
            ('✅ تم إضافة نظام الفواتير' || chr(10) || chr(10) || '🧾 زر جديد: إنشاء فاتورة' || chr(10) || '💰 يمكن للمدير إنشاء فاتورة عن بُعد' || chr(10) || '📎 توليد PDF احترافي تلقائياً' || chr(10) || '📨 إشعار تلقائي للعامل المختار')
            ON CONFLICT DO NOTHING;
            -- Migration: add reason column to transactions table
            DO $$ BEGIN
                ALTER TABLE transactions ADD COLUMN IF NOT EXISTS reason VARCHAR(200);
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: fix contradictory FK constraints (NOT NULL + ON DELETE SET NULL → RESTRICT)
            -- These require dropping and re-adding the FK constraint
            DO $$ BEGIN
                ALTER TABLE transactions DROP CONSTRAINT transactions_user_id_fkey;
                ALTER TABLE transactions ADD CONSTRAINT transactions_user_id_fkey
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            DO $$ BEGIN
                ALTER TABLE extraction_requests DROP CONSTRAINT extraction_requests_requester_id_fkey;
                ALTER TABLE extraction_requests ADD CONSTRAINT extraction_requests_requester_id_fkey
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            DO $$ BEGIN
                ALTER TABLE incoming_orders DROP CONSTRAINT incoming_orders_requester_id_fkey;
                ALTER TABLE incoming_orders ADD CONSTRAINT incoming_orders_requester_id_fkey
                    FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            DO $$ BEGIN
                ALTER TABLE invoices DROP CONSTRAINT invoices_admin_id_fkey;
                ALTER TABLE invoices ADD CONSTRAINT invoices_admin_id_fkey
                    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE RESTRICT;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            -- Migration: add 'reconcile' type to transactions
            DO $$ BEGIN
                ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_type_check;
            EXCEPTION WHEN OTHERS THEN NULL;
            END $$;
            ALTER TABLE transactions ADD CONSTRAINT transactions_type_check
                CHECK (type IN ('add', 'remove', 'reconcile'));
            -- Migration: create pending_deliveries table
            CREATE TABLE IF NOT EXISTS pending_deliveries (
                id            SERIAL PRIMARY KEY,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                product_id    INTEGER REFERENCES products(id) ON DELETE SET NULL,
                pending_qty   INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                fulfilled_at  TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_pending_deliveries_product ON pending_deliveries(product_id);
            CREATE INDEX IF NOT EXISTS idx_pending_deliveries_user ON pending_deliveries(user_id);
            -- Migration: legacy databases created pending_deliveries before
            -- v17 — CREATE TABLE IF NOT EXISTS does not add missing columns,
            -- so every auto-reconcile UPDATE (... fulfilled_at = NOW()) was
            -- failing silently and stale rows never healed. Ensure it exists.
            ALTER TABLE pending_deliveries
                ADD COLUMN IF NOT EXISTS fulfilled_at TIMESTAMPTZ;
            -- Seed: add negative inventory update notification
            -- Seed: add negative inventory update notification
            INSERT INTO bot_updates (description) VALUES
            ('✅ نظام المخزون السالب (الطلبيات المعلقة)' || chr(10) || chr(10) || '🔴 يمكن الآن البيع حتى لو الكمية غير كافية وتظهر بالسالب' || chr(10) || '⏳ كل زبون يرى طلباته المعلقة خاصة به' || chr(10) || '🟢 عند دخول بضاعة جديدة يتم التسوية التلقائية للعجز' || chr(10) || '📱 إشعار تلقائي للزبون عند تغطية طلبه')
            ON CONFLICT DO NOTHING;
            INSERT INTO bot_updates (description) VALUES
            ('🔍 بحث تلقائي: اكتب أي اسم سلعة مباشرة وسيتم البحث تلقائياً' || chr(10) || chr(10) || '⚙️ تحسينات: زيادة سرعة الاستجابة وإصلاح مشكلة الاتصال')
            ON CONFLICT DO NOTHING;
            """)
            # Migration: ensure all ADMIN_IDS users always get admin role
            if ADMIN_IDS:
                cur.execute(
                    "UPDATE users SET role = 'admin' WHERE telegram_id = ANY(%s) AND role != 'admin'",
                    (list(ADMIN_IDS),)
                )
            # Migration: نظام الفئات — عمود الفئة المطلوبة + قيد الأدوار الكامل
            # (idempotent: يعمل بأمان على كل إقلاع، ويُصلح قيود القواعد القديمة)
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'requested_role'"
            )
            if cur.fetchone()[0] == 0:
                cur.execute("ALTER TABLE users ADD COLUMN requested_role VARCHAR(20)")
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'users'::regclass AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE '%role%'"
            )
            for _role_con in cur.fetchall():
                _conname = str(_role_con[0]).replace('"', '')
                cur.execute('ALTER TABLE users DROP CONSTRAINT IF EXISTS "' + _conname + '"')
            cur.execute(
                "ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN "
                "('admin', 'manager', 'stock', 'seller', 'external', 'customer', 'pending'))"
            )
            # Migration: de-duplicate usernames so the unique index below can be created safely.
            # Keep the newest row's username (max telegram_id row wins; the current holder of the
            # handle on Telegram), older duplicates get NULL and fall back to first_name display.
            cur.execute("""
                UPDATE users u
                SET username = NULL
                WHERE u.username IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM users v
                      WHERE v.username IS NOT NULL
                        AND LOWER(v.username) = LOWER(u.username)
                        AND v.telegram_id > u.telegram_id
                  )
                  AND u.telegram_id <> COALESCE((
                      SELECT MAX(v.telegram_id) FROM users v
                      WHERE v.username IS NOT NULL
                        AND LOWER(v.username) = LOWER(u.username)
                  ), u.telegram_id)
            """)
            dedup_count = cur.rowcount
            # Hard guarantee: no two active accounts can ever hold the same username again.
            # Wrapped in a SAVEPOINT so a failure here never aborts the whole schema init.
            try:
                cur.execute("SAVEPOINT users_username_uniq")
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_uniq "
                    "ON users (LOWER(username)) WHERE username IS NOT NULL"
                )
                cur.execute("RELEASE SAVEPOINT users_username_uniq")
            except Exception as idx_err:
                cur.execute("ROLLBACK TO SAVEPOINT users_username_uniq")
                logging.warning("[DB] username unique index deferred: " + str(idx_err))

            # Migration: merge duplicate product names into the OLDEST row per name,
            # remapping all referencing tables first so no history is lost,
            # then enforce case-insensitive uniqueness going forward.
            # v81 حماية: عمود archived قد لا يكون موجوداً بعد على قاعدة جديدة —
            # إضافة idempotent رخيصة تضمن سلامة الدمج والشفاء أدناه في كل الأحوال.
            cur.execute(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS archived "
                "BOOLEAN NOT NULL DEFAULT FALSE"
            )
            # v81: اختيار الباقي من كل مجموعة أسماء مكررة تغيّر جذرياً:
            # قديماً كان الأقدم (أصغر id) يبقى حتى لو كان محذوفاً — فتُحذف السلعة
            # الحية المسجلة حديثاً! ولا كان أي دمج للكميات (تضيع كمية المحذوف)،
            # ولا إعادة ربط للطلبات المعلقة (product_id يُصفَّر بالـ FK).
            # الآن: الحية أولاً ثم الأقدم، مع دمج كميات المكررات في الباقية،
            # وإعادة ربط كل الجداول المرجعية (حتى المعلقات) قبل الحذف.
            cur.execute(
                "CREATE TEMP TABLE _dup_keepers ON COMMIT DROP AS "
                "SELECT DISTINCT ON (LOWER(name)) LOWER(name) AS lname, id AS keep_id "
                "FROM products "
                "WHERE LOWER(name) IN (SELECT LOWER(name) FROM products "
                "GROUP BY LOWER(name) HAVING COUNT(*) > 1) "
                "ORDER BY LOWER(name), archived ASC, id ASC"
            )
            _dup_remap_sql = """
                UPDATE {table} t SET product_id = k.keep_id
                FROM _dup_keepers k, products x
                WHERE t.product_id = x.id
                  AND LOWER(x.name) = k.lname AND x.id <> k.keep_id
            """
            for _t in ("transactions", "extraction_requests", "invoice_items",
                       "pending_deliveries"):
                cur.execute(_dup_remap_sql.format(table=_t))
            if _unshipped_table_exists(cur):
                cur.execute(_dup_remap_sql.format(table="unshipped_sales"))
            cur.execute(
                "UPDATE products p SET quantity = COALESCE(p.quantity, 0) + m.added "
                "FROM (SELECT k.keep_id, SUM(COALESCE(x.quantity, 0)) AS added "
                      "FROM _dup_keepers k, products x "
                      "WHERE LOWER(x.name) = k.lname AND x.id <> k.keep_id "
                      "GROUP BY k.keep_id) m "
                "WHERE p.id = m.keep_id AND m.added <> 0"
            )
            cur.execute(
                "DELETE FROM products WHERE id IN ("
                "SELECT x.id FROM products x, _dup_keepers k "
                "WHERE LOWER(x.name) = k.lname AND x.id <> k.keep_id)"
            )
            prod_dupes = cur.rowcount
            cur.execute("DROP TABLE IF EXISTS _dup_keepers")
            try:
                cur.execute("SAVEPOINT products_name_uniq")
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_name_uniq "
                    "ON products (LOWER(name))"
                )
                cur.execute("RELEASE SAVEPOINT products_name_uniq")
            except Exception as idx_err:
                cur.execute("ROLLBACK TO SAVEPOINT products_name_uniq")
                logging.warning("[DB] product name unique index deferred: " + str(idx_err))

            # Migration (legacy DBs): stop accidental data loss on product deletion.
            # - transactions history must NOT vanish with the product.
            # - invoice_items keeps billing rows alive with a detached product reference.
            # Constraint names follow PostgreSQL defaults (table_col_fkey), each guarded
            # in its own DO block so one legacy oddity never breaks initialization.
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_product_id_fkey;
                    ALTER TABLE transactions
                        ADD CONSTRAINT transactions_product_id_fkey
                        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT;
                EXCEPTION WHEN OTHERS THEN NULL; END $$;
            """)
            cur.execute("""
                DO $$ BEGIN
                    ALTER TABLE invoice_items DROP CONSTRAINT IF EXISTS invoice_items_product_id_fkey;
                    ALTER TABLE invoice_items
                        ADD CONSTRAINT invoice_items_product_id_fkey
                        FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;
                EXCEPTION WHEN OTHERS THEN NULL; END $$;
            """)
            # v81: شفاء السلع «المختفية» — أي سلعة محذوفة ما زالت تحمل كمية تعود
            # للحياة تلقائياً عند الإقلاع. الجذر: الكمية كانت تُطعَم صامتة في سلعة
            # محذوفة (تليغرام/الطابور) فتبدو السلعة مسجلة ومختفية من كل القوائم.
            # كل استعادة تُسجَّل في audit_log (action=auto_restore) ومحفوظة SAVEPOINT
            # حتى لا يسمّ أي فشل معاملة تهيئة القاعدة كاملة.
            try:
                cur.execute("SAVEPOINT v81_heal")
                cur.execute(
                    "UPDATE products SET archived = FALSE "
                    "WHERE archived AND COALESCE(quantity, 0) > 0 "
                    "RETURNING id, name, quantity"
                )
                _healed_rows = cur.fetchall()
                for _h in _healed_rows:
                    logging.warning(
                        "[DB][v81] استعادة تلقائية عند الإقلاع: «%s» (كمية %s)",
                        _h[1], _h[2])
                    try:
                        cur.execute("SAVEPOINT v81_heal_audit")
                        cur.execute(
                            "INSERT INTO audit_log (action, details) VALUES (%s, %s)",
                            ("auto_restore",
                             ("استعادة تلقائية عند الإقلاع: " + str(_h[1])
                              + " (كمية " + str(_h[2]) + ")")[:500]))
                        cur.execute("RELEASE SAVEPOINT v81_heal_audit")
                    except Exception:
                        try:
                            cur.execute("ROLLBACK TO SAVEPOINT v81_heal_audit")
                        except Exception:
                            pass
                cur.execute("RELEASE SAVEPOINT v81_heal")
                if _healed_rows:
                    print("[DB] v81: استُعيدت " + str(len(_healed_rows))
                          + " سلعة كانت محذوفة وبها كمية.")
            except Exception as heal_err:
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT v81_heal")
                except Exception:
                    pass
                logging.warning("[DB][v81] startup heal skipped: " + str(heal_err))
        conn.commit()
        if dedup_count:
            print("[DB] Deduplicated " + str(dedup_count) + " duplicate username(s).")
        if prod_dupes:
            print("[DB] Merged " + str(prod_dupes) + " duplicate product(s).")
        print("[DB] Schema initialized successfully.")
    except Exception as e:
        conn.rollback()
        print("[DB] Schema init failed: " + str(e))
        raise
    finally:
        if owned:
            release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  USER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def _normalize_username(username):
    """توحيد صيغة اسم المستخدم: إزالة @ والفراغات، وتحويل الفراغ الفارغ إلى NULL.
    يمنع تكرار نفس الحساب بصيغ مختلفة (@ali مقابل ali مقابل ' ali ')."""
    if username is None:
        return None
    cleaned = str(username).strip().lstrip("@").strip()
    return cleaned or None


def _claim_username_sql():
    """CTE مشترك: يُفرغ الاسم من أي حساب آخر يحمله قبل الإدراج/التحديث ذرّياً.
    الحامل الأحدث (أعلى telegram_id) هو من يستمر بالاسم، ويتوافق مع
    idx_users_username_uniq فلا تُرفع أي استثناء تفرد أبداً."""
    return """
        WITH cleared AS (
            UPDATE users SET username = NULL
            WHERE %s IS NOT NULL
              AND username IS NOT NULL
              AND LOWER(username) = LOWER(%s)
              AND telegram_id <> %s
            RETURNING 1
        )
    """


def register_user(telegram_id, username=None, first_name=None):
    username = _normalize_username(username)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            role = "admin" if telegram_id in ADMIN_IDS else "pending"
            cur.execute(_claim_username_sql() + """
                INSERT INTO users (telegram_id, username, first_name, role)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
                -- v23: لا إعادة تفعيل تلقائية هنا — المطرود يبقى مطروداً
                -- NEVER downgrade role on re-register
                -- (admin stays admin, approved customer stays customer)
                RETURNING id, telegram_id, username, first_name, role, active, created_at
            """, (username, username, telegram_id,
                  telegram_id, username, first_name, role))
            user = cur.fetchone()
        conn.commit()
        _users_cache_clear()
        return dict(user)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


_STATE_TIMEOUT_SECONDS = 600

def _is_state_expired(context):
    """Check if current state has been active longer than timeout. Returns True if expired."""
    set_at = context.user_data.get("state_set_at")
    if not set_at:
        return False
    elapsed = datetime.datetime.now(datetime.timezone.utc).timestamp() - set_at
    return elapsed > _STATE_TIMEOUT_SECONDS

def _set_state(context, state):
    """Set state and record timestamp for timeout tracking."""
    context.user_data["state"] = state
    context.user_data["state_set_at"] = datetime.datetime.now(datetime.timezone.utc).timestamp()

def sync_user_info(user):
    """تحديث اسم المستخدم تلقائياً عند أي تفاعل.
    يُنشئ السجل إذا لم يكن موجود، أو يُحدّث username و first_name إذا تغيرت.
    يفرّغ الاسم من أي حساب آخر يحمله أولاً حتى لا يتكرر الاسم في قاعدة البيانات."""
    username = _normalize_username(getattr(user, "username", None))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_claim_username_sql() + """
                INSERT INTO users (telegram_id, username, first_name, role)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
                -- v23: لا إعادة تفعيل تلقائية عند التفاعل — المطرود يبقى مطروداً
            """, (username, username, user.id,
                  user.id, username, user.first_name,
                  "admin" if user.id in ADMIN_IDS else "pending"))
        conn.commit()
        _users_cache_clear()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


# ── v76: كاش قراءة قصير للمسار الساخن — كل رسالة تمر عبر get_user/is_admin ──
_USER_CACHE = {}
_USER_CACHE_LOCK = threading.Lock()
_USER_CACHE_TTL = 45.0

def _users_cache_clear():
    """تفريغ كاش المستخدمين — يُستدعى بعد أي تعديل على users."""
    try:
        with _USER_CACHE_LOCK:
            _USER_CACHE.clear()
    except Exception:
        pass

def get_user(telegram_id):
    # v76: كاش 45ث — تعليق Neon اللحظي لم يعد يجمد الحلقة مع كل أمر
    try:
        _now = time.time()
        with _USER_CACHE_LOCK:
            _hit = _USER_CACHE.get(telegram_id)
            if _hit is not None and _now - _hit[0] < _USER_CACHE_TTL:
                return dict(_hit[1]) if _hit[1] is not None else None
    except Exception:
        pass
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        _result = dict(row) if row else None
        try:
            with _USER_CACHE_LOCK:
                if _result is not None:
                    if len(_USER_CACHE) > 300:
                        _USER_CACHE.clear()
                    _USER_CACHE[telegram_id] = (time.time(), dict(_result))
        except Exception:
            pass
        return dict(_result) if _result else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def is_admin(telegram_id):
    if telegram_id in ADMIN_IDS:
        return True
    user = get_user(telegram_id)
    # 🧑‍💼 مدير البوت (manager) يملك صلاحيات الإدارة الكاملة مثل admin
    return user is not None and user["role"] in ("admin", "manager")


# ═══════════════════════════════════════════════════════════


def is_owner(telegram_id):
    """Check if user is the bot owner."""
    return telegram_id in OWNER_IDS


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS (طلبات التوريد)
# ═══════════════════════════════════════════════════════════

def create_incoming_order(requester_id, product_name, quantity, photo_file_id=None):
    """إنشاء طلب توريد جديد من صاحب المحل."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO incoming_orders (requester_id, product_name, quantity, photo_file_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, requester_id, product_name, quantity, status, created_at, photo_file_id
                """, (requester_id, product_name, quantity, photo_file_id))
            order = cur.fetchone()
        conn.commit()
        return dict(order)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_user_pending_orders(user_db_id):
    """جلب طلبات التوريد المعلّقة + قيد التجهيز لمستخدم محدد."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                SELECT * FROM incoming_orders
                WHERE requester_id = %s AND status IN ('pending', 'received')
                ORDER BY created_at ASC
                """, (user_db_id,))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def complete_incoming_order(order_id):
    """تحديث طلب توريد إلى 'مكتمل' (من pending أو received)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'completed', completed_at = NOW()
                WHERE id = %s AND status IN ('pending', 'received')
                RETURNING *
                """, (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def receive_incoming_order(order_id):
    """تحديث طلب توريد إلى 'تم الاستلام' (جاري التجهيز)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                UPDATE incoming_orders
                SET status = 'received'
                WHERE id = %s AND status = 'pending'
                RETURNING *
                """, (order_id,))
            order = cur.fetchone()
        conn.commit()
        return dict(order) if order else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_pending_users():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE role = 'pending' ORDER BY created_at DESC")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def approve_user(telegram_id, role=None):
    """تفعيل المستخدم المعلّق: بالفئة التي اختارها إن وُجدت، وإلا عميل.
    يمكن تمرير role صراحة لتفعيله بفئة محددة (زر «تفعيل بفئة» في إشعار المدير)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if role:
                cur.execute(
                    "UPDATE users SET role = %s, requested_role = NULL "
                    "WHERE telegram_id = %s AND role = 'pending' RETURNING *",
                    (role, telegram_id),
                )
            else:
                cur.execute(
                    "UPDATE users SET role = COALESCE(requested_role, 'customer'), requested_role = NULL "
                    "WHERE telegram_id = %s AND role = 'pending' RETURNING *",
                    (telegram_id,),
                )
            row = cur.fetchone()
        conn.commit()
        _users_cache_clear()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def set_user_role(telegram_id, role):
    """تغيير فئة مستخدم مفعّل (من لوحة المستخدمين → زر 🎯 الفئة)."""
    if role not in ROLE_LABELS:
        raise ValueError("invalid role: " + str(role))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role = %s WHERE telegram_id = %s", (role, telegram_id))
            changed = cur.rowcount > 0
        conn.commit()
        _users_cache_clear()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def set_user_active(telegram_id, active):
    """🚫 طرد/إعادة تنشيط (v23): يبدّل عمود active — المطرود يبقى بسجله وبدوره
    ويرى البوت رسالة إيقاف فقط حتى يعيد المدير تفعيله من لوحة المستخدمين."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET active = %s WHERE telegram_id = %s RETURNING role",
                        (bool(active), telegram_id))
            row = cur.fetchone()
        conn.commit()
        _users_cache_clear()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def user_is_active(telegram_id):
    """فحص بوابة الطرد: True لغير الموجود في القاعدة (المسار الطبيعي قبل التسجيل)،
    وعند أي خطأ في القاعدة نُفشل الباب مفتوحاً حتى لا يُقفل النظام على أحد."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT active FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return True if row is None else bool(row[0])
    except Exception as e:
        logging.warning("[KICK] active check failed: " + str(e))
        return True
    finally:
        release_connection(conn)


def reject_user(telegram_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE telegram_id = %s AND role = 'pending'", (telegram_id,))
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_export_backup():
    """Export ALL data as a dict for full backup/restore."""
    import datetime as _dt
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products ORDER BY id")
            products = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM transactions ORDER BY id")
            transactions = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, telegram_id, username, role, active, can_extract, first_name, created_at FROM users ORDER BY id")
            users = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM invoices ORDER BY id")
            invoices = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM invoice_items ORDER BY id")
            invoice_items = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM extraction_requests ORDER BY id")
            extraction_requests = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM incoming_orders ORDER BY id")
            incoming_orders = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM pending_deliveries ORDER BY id")
            pending_deliveries = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return {
            "version": 5,
            "date": _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=1))).isoformat(),
            "products": products,
            "transactions": transactions,
            "users": users,
            "invoices": invoices,
            "invoice_items": invoice_items,
            "extraction_requests": extraction_requests,
            "incoming_orders": incoming_orders,
            "pending_deliveries": pending_deliveries,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_import_backup(data):
    """Restore data from a backup dict. Returns summary string.

    Uses executemany for bulk inserts — much faster than row-by-row.
    Handles all tables that reference users(id) with NOT NULL constraints.
    Supports old backup versions (2,3) that lack some tables.

    v87.1: مقاومة بقايا عصر تعدد القواعد (بقايا split-brain):
    users.id هو SERIAL — كل قاعدة كان لها تسلسل ترقيم مستقل، فنفس الشخص
    (نفس telegram_id) قد يحمل معرفين مختلفين في نسخة مدموجة من قاعدتين.
    الاستيراد القديم كان يرمي أحد الحسابين في فك التكرار → أي سجل عملية
    يشير للمعرف المرمي يصبح يتيمًا → قيد transactions_user_id_fkey يرفض
    ويُسقط الاستيراد كله (بلاغ: Key (user_id)=(8589) is not present).
    الآن:
      1) إعادة ربط (remap): كل معرف مكرر → حساب نفس الشخص الناجي
         (نفس telegram_id = نفس الشخص، تاريخه كله تحت حساب واحد).
      2) أي معرف يتيم لا حساب له إطلاقًا → حساب اصطناعي بنفس المعرف
         (telegram_id سالب مميز + first_name "سجل مسترجع") بدل إسقاط
         الاستيراد — لا سجل يُرمى ولا استيراد يفشل.
      3) نفس الحماية للمنتجات: مرجع لسلعة ميتة → سلعة اصطناعية مؤرشفة
         كمية صفر بدل فشل FK.
      4) تفريغ أسماء المستخدمين المكررة قبل الفهرس الفريد الجزئي.
    الاستيراد يبقى ذريًا: أي فشل غير متوقع = rollback كامل بلا ضرر.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ── DELETE in reverse dependency order ──
            cur.execute("DELETE FROM invoice_items")
            cur.execute("DELETE FROM invoices")
            cur.execute("DELETE FROM extraction_requests")
            cur.execute("DELETE FROM incoming_orders")
            if _unshipped_table_exists(cur):
                cur.execute("DELETE FROM unshipped_sales")
            cur.execute("DELETE FROM pending_deliveries")
            cur.execute("DELETE FROM transactions")
            cur.execute("DELETE FROM products")
            cur.execute("DELETE FROM users")

            # ── Users: dedup by telegram_id + بناء خريطة إعادة الربط ──
            # Deduplicate by telegram_id BEFORE inserting: a duplicated/corrupted backup
            # must never create two accounts for the same person.
            users = data.get("users", [])
            _remap = {}           # v87.1: معرف مكرر (خاسر) → معرف الناجي
            _canonical_ids = set()
            _used_usernames = set()
            if users:
                _seen = {}
                for u in users:
                    _seen[u["telegram_id"]] = u  # last occurrence wins (freshest state)
                users = list(_seen.values())
                _canonical_ids = {u["id"] for u in users}
                # v87.1: كل معرف ضاع بفك التكرار يُربط بحساب نفس الشخص الناجي
                for u in data.get("users", []):
                    if u["id"] not in _canonical_ids and u["id"] not in _remap:
                        _surv = _seen.get(u["telegram_id"])
                        if _surv is not None:
                            _remap[u["id"]] = _surv["id"]
                rows = []
                for u in users:
                    _un = _normalize_username(u.get("username"))
                    if _un:
                        if _un.lower() in _used_usernames:
                            _un = None  # v87.1: فهرس فريد على الاسم — المكرر يُفرَّغ
                        else:
                            _used_usernames.add(_un.lower())
                    rows.append((
                        u["id"], u["telegram_id"], _un,
                        u.get("role", "customer"), u.get("active", True),
                        u.get("created_at"),
                        u.get("can_extract", False),
                        u.get("first_name"),
                    ))
                cur.executemany("""
                    INSERT INTO users (id, telegram_id, username, role, active, created_at, can_extract, first_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (telegram_id) DO NOTHING
                """, rows)
                max_uid = max(u["id"] for u in users)
                cur.execute("SELECT setval('users_id_seq', %s)", (max_uid,))

            # ── Import products ──
            products = data.get("products", [])
            if products:
                # v81: حفظ حالة archived في الاستيراد — قديماً كانت المحذوفات
                # تعود حية بعد أي استعادة/تبديل قاعدة (ظهور/اختفاء مخالف للواقع)
                rows = [
                    (p["id"], p["name"], p["quantity"], p.get("location", ""),
                     p.get("minimum_stock", 5), p.get("created_at"),
                     p.get("selling_price", 0), bool(p.get("archived", False)))
                    for p in products
                ]
                cur.executemany("""
                    INSERT INTO products (id, name, quantity, location, minimum_stock, created_at, selling_price, archived)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, rows)
                max_pid = max(p["id"] for p in products)
                cur.execute("SELECT setval('products_id_seq', %s)", (max_pid,))
            _canonical_pids = {p["id"] for p in products}

            # ══ v87.1: تجهيز كل صفوف الجداول الابنة قبل الإدراج ══
            # إعادة ربط معرفات المستخدمين المكررة + حصر المعرفات اليتيمة
            # (لا حساب لها في النسخة نهائياً) ومراجع المنتجات الميتة —
            # تُنشأ لها سجلات اصطناعية أدناه بدل أن يفشل الاستيراد كله.
            transactions = data.get("transactions", [])
            invoices = data.get("invoices", [])
            invoice_items = data.get("invoice_items", [])
            ext_reqs = data.get("extraction_requests", [])
            inc_orders = data.get("incoming_orders", [])
            pend_delivs = data.get("pending_deliveries", [])

            _orphan_uids = set()
            _orphan_pids = set()

            def _ref_uid(v):
                """معرف مستخدم: مكرر → الناجي؛ يتيم → يُحصر للstub؛ سليم → كما هو."""
                if v is None:
                    return None
                _m = _remap.get(v)
                if _m is not None:
                    return _m
                if v not in _canonical_ids:
                    _orphan_uids.add(v)
                return v

            def _ref_pid(v):
                """معرف سلعة: ميت → يُحصر للstub؛ سليم → كما هو. None يمر كما هو."""
                if v is None:
                    return None
                if v not in _canonical_pids:
                    _orphan_pids.add(v)
                return v

            tx_rows = [
                (t["id"], _ref_pid(t["product_id"]), t["type"], t["quantity"],
                 _ref_uid(t.get("user_id")), t.get("date"), t.get("reason"))
                for t in transactions
            ]
            inv_rows = [
                (inv["id"], inv["customer_name"], _ref_uid(inv.get("worker_id")), _ref_uid(inv.get("admin_id")),
                 inv.get("total_amount", 0), inv.get("status", "approved"),
                 inv.get("created_at"), inv.get("approved_at"))
                for inv in invoices
            ]
            ii_rows = [
                (ii["id"], ii["invoice_id"], _ref_pid(ii.get("product_id")), ii["product_name"],
                 ii["quantity"], ii.get("unit_price", 0), ii.get("subtotal", 0))
                for ii in invoice_items
            ]
            er_rows = [
                (er["id"], _ref_pid(er["product_id"]), er["quantity"], _ref_uid(er.get("requester_id")),
                 er.get("status", "pending"), er.get("created_at"), er.get("processed_at"))
                for er in ext_reqs
            ]
            io_rows = [
                (io["id"], _ref_uid(io.get("requester_id")), io["product_name"], io["quantity"],
                 io.get("status", "pending"), io.get("photo_file_id"),
                 io.get("created_at"), io.get("completed_at"))
                for io in inc_orders
            ]
            pd_rows = [
                (pd["id"], _ref_uid(pd.get("user_id")), _ref_pid(pd["product_id"]), pd.get("pending_qty", 0),
                 pd.get("created_at"), pd.get("fulfilled_at"))
                for pd in pend_delivs
            ]

            # ── v87.1: سجلات اصطناعية للمعرفات اليتيمة (بنفس المعرف — FK يتحقق) ──
            # حساب اصطناعي: telegram_id سالب (معرفات تليجرام الحقيقية موجبة دائماً
            # فلا تصادم أبداً) + first_name يميزه كسجل مسترجع وليس حساباً حقيقياً.
            for _oid in sorted(_orphan_uids):
                cur.execute("""
                    INSERT INTO users (id, telegram_id, username, first_name, role, active, created_at)
                    VALUES (%s, %s, NULL, %s, 'customer', TRUE, NOW())
                    ON CONFLICT (id) DO NOTHING
                """, (_oid, -int(_oid), "سجل مسترجع #"))
            if _orphan_uids:
                cur.execute(
                    "SELECT setval('users_id_seq', GREATEST((SELECT COALESCE(MAX(id), 1) FROM users), %s))",
                    (max(_orphan_uids),))
            # سلعة اصطناعية: مؤرشفة كمية صفر — تُظهر نفسها في التقرير كعلامة مرجعية
            for _opid in sorted(_orphan_pids):
                cur.execute("""
                    INSERT INTO products (id, name, quantity, location, minimum_stock, selling_price, archived, created_at)
                    VALUES (%s, %s, 0, '', 0, 0, TRUE, NOW())
                    ON CONFLICT (id) DO NOTHING
                """, (_opid, "؟ سلعة مسترجعة #"))
            if _orphan_pids:
                cur.execute(
                    "SELECT setval('products_id_seq', GREATEST((SELECT COALESCE(MAX(id), 1) FROM products), %s))",
                    (max(_orphan_pids),))

            # ── Import transactions ──
            if tx_rows:
                cur.executemany("""
                    INSERT INTO transactions (id, product_id, type, quantity, user_id, date, reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, tx_rows)
                max_tid = max(t["id"] for t in transactions)
                cur.execute("SELECT setval('transactions_id_seq', %s)", (max_tid,))

            # ── Import invoices ──
            if inv_rows:
                cur.executemany("""
                    INSERT INTO invoices (id, customer_name, worker_id, admin_id, total_amount, status, created_at, approved_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, inv_rows)
                max_iid = max(inv["id"] for inv in invoices)
                cur.execute("SELECT setval('invoices_id_seq', %s)", (max_iid,))

            # ── Import invoice_items ──
            if ii_rows:
                cur.executemany("""
                    INSERT INTO invoice_items (id, invoice_id, product_id, product_name, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, ii_rows)
                max_iiid = max(ii["id"] for ii in invoice_items)
                cur.execute("SELECT setval('invoice_items_id_seq', %s)", (max_iiid,))

            # ── Import extraction_requests (version 4+) ──
            if er_rows:
                cur.executemany("""
                    INSERT INTO extraction_requests (id, product_id, quantity, requester_id, status, created_at, processed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, er_rows)
                max_erid = max(er["id"] for er in ext_reqs)
                cur.execute("SELECT setval('extraction_requests_id_seq', %s)", (max_erid,))

            # ── Import incoming_orders (version 4+) ──
            if io_rows:
                cur.executemany("""
                    INSERT INTO incoming_orders (id, requester_id, product_name, quantity, status, photo_file_id, created_at, completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, io_rows)
                max_ioid = max(io["id"] for io in inc_orders)
                cur.execute("SELECT setval('incoming_orders_id_seq', %s)", (max_ioid,))

            # ── Import pending_deliveries ──
            if pd_rows:
                cur.executemany("""
                    INSERT INTO pending_deliveries (id, user_id, product_id, pending_qty, created_at, fulfilled_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, pd_rows)
                max_pdid = max(pd["id"] for pd in pend_delivs)
                cur.execute("SELECT setval('pending_deliveries_id_seq', %s)", (max_pdid,))

        conn.commit()

        summary = ("✅ تم استيراد البيانات [" + PWA_VERSION + "]:\n"
            "  المستخدمين: " + str(len(users)) + "\n"
            "  السلع: " + str(len(products)) + "\n"
            "  العمليات: " + str(len(transactions)))
        if len(invoices) > 0:
            summary += "\n  الفواتير: " + str(len(invoices))
        if len(invoice_items) > 0:
            summary += "\n  عناصر الفواتير: " + str(len(invoice_items))
        if len(ext_reqs) > 0:
            summary += "\n  طلبات الاستخراج: " + str(len(ext_reqs))
        if len(inc_orders) > 0:
            summary += "\n  طلبات التوريد: " + str(len(inc_orders))
        if len(pend_delivs) > 0:
            summary += "\n  الطلبات المعلقة: " + str(len(pend_delivs))
        # (v87.1) شفافية كاملة: المالك يرى ماذا تم توحيده وماذا أُنشئ اصطناعياً
        if _remap:
            summary += ("\n  🔷 حسابات مكررة أُوحدت (بقايا تعدد القواعد): "
                        + str(len(_remap)) + " — تاريخها مربوط بحسابها الأصلي")
        if _orphan_uids:
            _oids = ", ".join("#" + str(x) for x in sorted(_orphan_uids)[:10])
            if len(_orphan_uids) > 10:
                _oids += " …"
            summary += ("\n  🧩 سجلات يتيمة حصلت على حساب مسترجع: "
                        + str(len(_orphan_uids)) + " (" + _oids + ")")
        if _orphan_pids:
            summary += ("\n  🧩 مراجع سلع ميتة حصلت على سلعة مسترجعة مؤرشفة: "
                        + str(len(_orphan_pids)))
        # (v80) قفل حلقة العجز بعد الاستيراد: كمية سالبة مستوردة بلا طلب معلق تُسجل فوراً
        try:
            _sync_sum = sync_all_pending_deficits()
            if _sync_sum["created_units"] > 0:
                summary += ("\n  ⚠️ عجز بلا طلبات معلقة سُجل تلقائياً: "
                            + str(_sync_sum["created_units"]) + " وحدة ("
                            + str(_sync_sum["created_products"]) + " سلعة)")
        except Exception as _sync_err:
            logging.error("[PENDING-SYNC][v80] post-import sync failed: " + str(_sync_err))
        _users_cache_clear()
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

# ═══════════════════════════════════════════════════════════
#  PRODUCT FUNCTIONS
# ═══════════════════════════════════════════════════════════

def db_add_product(name, quantity=0, location="", minimum_stock=5):
    """Add a new product. Returns (product_dict, is_new)."""
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("اسم المنتج فارغ.")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO products (name, quantity, location, minimum_stock)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id, name, quantity, location, minimum_stock, created_at
            """, (clean_name, quantity, location.strip(), minimum_stock))
            product = cur.fetchone()
            if product:
                conn.commit()
                return dict(product), True
            # Product already exists — try to find and update (case-insensitive to match
            # the idx_products_name_uniq expression index).
            # v81: نُفضّل السلعة الحية على المحذوفة. الجذر الموثق: الكمية كانت تدخل
            # صامتة في سلعة محذوفة (مؤرشفة) فتبدو السلعة المسجلة «مختفية» من كل
            # القوائم — الآن تُستعاد المحذوفة تلقائياً فتظهر فوراً في المخزون.
            cur.execute(
                "SELECT * FROM products WHERE LOWER(name) = LOWER(%s) "
                "ORDER BY archived ASC, id ASC LIMIT 1", (clean_name,))
            existing = cur.fetchone()
            if existing:
                if existing.get("archived"):
                    # عالم بلا فهرس فريد فقط: هل توجد حية بنفس الاسم؟ أضف لها
                    cur.execute(
                        "SELECT * FROM products WHERE LOWER(name) = LOWER(%s) "
                        "AND NOT archived ORDER BY id ASC LIMIT 1", (clean_name,))
                    _active_twin = cur.fetchone()
                    if _active_twin:
                        existing = _active_twin
                    else:
                        cur.execute(
                            "UPDATE products SET archived = FALSE WHERE id = %s",
                            (existing["id"],))
                        logging.warning(
                            "[DB][v81] استعادة تلقائية عند التسجيل: سلعة كانت محذوفة "
                            "عادت للحياة مع الكمية الجديدة: " + str(existing.get("name")))
                        existing["archived"] = False
                cur.execute("UPDATE products SET quantity = quantity + %s WHERE id = %s RETURNING *", (quantity, existing["id"]))
                updated = cur.fetchone()
                conn.commit()
                return dict(updated), False
            conn.commit()
            raise ValueError("Could not add or find product: " + clean_name)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _normalize_text(text):
    """Normalize text for fuzzy matching: strip accents, lowercase, remove extra spaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    # Remove diacritical marks (accents)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    # Replace multiple spaces with single
    text = re.sub(r"\s+", " ", text)
    # Normalize slashes: 60/60 -> 60x60
    text = text.replace("/", "x")
    text = text.replace("×", "x")
    return text


def _levenshtein_distance(s1, s2):
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _fuzzy_score(name, query_tokens):
    """Score how well a product name matches the query tokens.
    Returns a score (higher = better match)."""
    norm_name = _normalize_text(name)
    name_words = set(norm_name.split())
    score = 0
    # Exact substring match in normalized name (strongest signal)
    norm_query = " ".join(query_tokens)
    if norm_query in norm_name:
        score += 100
    # Each token that appears exactly as a word
    for token in query_tokens:
        if token in name_words:
            score += 30
        elif token in norm_name:
            score += 15
        else:
            # Fuzzy: check Levenshtein distance for each word in name
            for word in name_words:
                dist = _levenshtein_distance(token, word)
                max_len = max(len(token), len(word), 1)
                similarity = 1 - (dist / max_len)
                if similarity >= 0.6:
                    score += int(similarity * 20)
                    break
    return score


def search_products(query):
    """Smart search: ILIKE fast path, then fuzzy matching with typo tolerance.
    Normalized text, slash/x interchangeability, and accent handling."""
    if not query or not query.strip():
        return []
    # ── FAST PATH: ILIKE exact/partial match in database (no full scan) ──
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM products WHERE name ILIKE %s AND NOT archived ORDER BY id",
                ("%" + query.strip() + "%",),
            )
            ilike_rows = cur.fetchall()
        conn.commit()
        ilike_results = [dict(r) for r in ilike_rows]
        if ilike_results:
            return ilike_results
    except Exception as e:
        conn.rollback()
        logging.warning("[SEARCH] ILIKE fast path failed: " + str(e))
    finally:
        release_connection(conn)
    # ── SLOW PATH: Fuzzy matching (full scan, typo tolerance) — السلع الحية فقط ──
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE NOT archived ORDER BY id")
            all_rows = cur.fetchall()
        conn.commit()
        all_products = [dict(r) for r in all_rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    query_tokens = _normalize_text(query).split()
    if not query_tokens:
        return []
    # Score every product
    scored = []
    for p in all_products:
        s = _fuzzy_score(p["name"], query_tokens)
        if s > 0:
            scored.append((s, p))
    # Sort by score descending, then by id ascending
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    # Return all matched results (no limit)
    return [p for s, p in scored]


def db_update_product(product_id, name=None, location=None, minimum_stock=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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
            query_sql = "UPDATE products SET " + ", ".join(fields) + " WHERE id = %s RETURNING id, name, quantity, location, minimum_stock, created_at"
            cur.execute(query_sql, values)
            product = cur.fetchone()
        conn.commit()
        return dict(product)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_update_quantity(product_id, change, transaction_type):
    """Update product quantity atomically to prevent race conditions.
    
    Instead of SELECT then UPDATE (unsafe), we use a single atomic UPDATE:
    - ADD:  UPDATE ... SET quantity = quantity + X WHERE id = Y
    - REMOVE: UPDATE ... SET quantity = quantity - X WHERE id = Y AND quantity >= X
    
    PostgreSQL guarantees the UPDATE executes as one indivisible operation.
    If two users update the same product simultaneously, PostgreSQL serializes
    them automatically — no Redis lock needed.
    
    Example race condition prevented:
      Product has 10 units
      User A: UPDATE ... quantity = quantity + 5  → 15
      User B: UPDATE ... quantity = quantity - 3  → 12 (not 7!)
    """
    if transaction_type not in ("add", "remove"):
        raise ValueError("transaction_type must be 'add' or 'remove'")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if transaction_type == "remove" and change < 0:
                change = abs(change)
            if transaction_type == "remove":
                # Allow negative inventory — no quantity check
                cur.execute(
                    "UPDATE products SET quantity = quantity - %s "
                    "WHERE id = %s "
                    "RETURNING id, name, quantity, location, minimum_stock, created_at",
                    (change, product_id),
                )
                product = cur.fetchone()
                if product is None:
                    raise ValueError("السلعة غير موجودة.")
            else:
                # Atomic: increment in a single statement
                cur.execute(
                    "UPDATE products SET quantity = quantity + %s "
                    "WHERE id = %s "
                    "RETURNING id, name, quantity, location, minimum_stock, created_at",
                    (change, product_id),
                )
                product = cur.fetchone()
                if product is None:
                    raise ValueError("السلعة غير موجودة.")
            product = dict(product)
            # Compute old quantity from the result (no separate SELECT needed)
            if transaction_type == "add":
                product["_old_qty"] = product["quantity"] - change
            else:
                product["_old_qty"] = product["quantity"] + change
        conn.commit()
        return product
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _unshipped_table_exists(cur):
    """v20 أزال نظام unshipped_sales؛ الجدول قد يبقى أرشيفاً في قواعد
    بيانات ما قبل v20 ويغيب عن القواعد الجديدة — يُفحص وجوده قبل أي لمس."""
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name = 'unshipped_sales'"
    )
    return cur.fetchone() is not None


def db_delete_product(product_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Preserve business history: detach delivery records from this
            # product BEFORE deleting it, so nothing is cascade-swept.
            # (جدول unshipped_sales أُرشِف في v20 — يُفكّ ارتباطه فقط إن وُجد)
            if _unshipped_table_exists(cur):
                cur.execute("UPDATE unshipped_sales SET product_id = NULL WHERE product_id = %s", (product_id,))
            cur.execute("UPDATE pending_deliveries SET product_id = NULL WHERE product_id = %s", (product_id,))
            # v78: حركات السلعة تُحذف معها (الوعد في الواجهة: «حذف السلعة وجميع سجلاتها») —
            # قيد transactions RESTRICT كان يمنع حذف أي سلعة لها حركات.
            cur.execute("DELETE FROM transactions WHERE product_id = %s", (product_id,))
            cur.execute("DELETE FROM products WHERE id = %s RETURNING id", (product_id,))
            deleted = cur.fetchone() is not None
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def db_bulk_delete_products(id_list):
    """Delete multiple products by IDs. Returns (deleted_count, not_found_ids)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            deleted_count = 0
            not_found = []
            # Preserve business history for every product being removed.
            if id_list:
                if _unshipped_table_exists(cur):
                    cur.execute("UPDATE unshipped_sales SET product_id = NULL "
                                "WHERE product_id = ANY(%s)", (list(id_list),))
                cur.execute("UPDATE pending_deliveries SET product_id = NULL "
                            "WHERE product_id = ANY(%s)", (list(id_list),))
                # v78: حركات كل سلعة تُحذف معها — وإلا منعنا قيد RESTRICT الحذف
                cur.execute("DELETE FROM transactions WHERE product_id = ANY(%s)", (list(id_list),))
            for pid in id_list:
                cur.execute("DELETE FROM products WHERE id = %s RETURNING id", (pid,))
                if cur.fetchone():
                    deleted_count += 1
                else:
                    not_found.append(pid)
        conn.commit()
        return deleted_count, not_found
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_low_stock_products():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE quantity <= minimum_stock "
                        "AND NOT archived ORDER BY quantity ASC")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def get_all_products():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE NOT archived ORDER BY LOWER(name)")
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  TRANSACTION FUNCTIONS
# ═══════════════════════════════════════════════════════════

def log_transaction(product_id, transaction_type, quantity, user_id, reason=None):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\n                INSERT INTO transactions (product_id, type, quantity, user_id, reason)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, product_id, type, quantity, user_id, date, reason
            """, (product_id, transaction_type, quantity, user_id, reason))
            tx = cur.fetchone()
        conn.commit()
        return dict(tx)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def update_transaction_reason(tx_id, reason):
    """Update the reason field of an existing transaction."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE transactions SET reason = %s WHERE id = %s", (reason, tx_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[TX] Failed to update reason for tx " + str(tx_id) + ": " + str(e))
    finally:
        release_connection(conn)


def delete_transaction(tx_id, admin_tid):
    """Delete a transaction and reverse its effect on product quantity.
    Only allowed if the transaction belongs to the admin (by telegram_id).
    Returns (success, message)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get the transaction with user info
            cur.execute(
                "SELECT t.*, u.telegram_id AS user_tid "
                "FROM transactions t "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.id = %s", (tx_id,))
            tx = cur.fetchone()
            if not tx:
                return False, "❌ العملية غير موجودة."
            tx = dict(tx)
            if tx["type"] == "reconcile":
                return False, "❌ لا يمكن حذف عملية تسوية تلقائية."
            # Only allow admin to delete their OWN transactions
            if tx.get("user_tid") != admin_tid:
                return False, "❌ لا يمكنك حذف عملية ليست لك."
            # Reverse the quantity change on the product
            _undo_note = ""
            if tx["type"] == "add":
                cur.execute("UPDATE products SET quantity = quantity - %s WHERE id = %s RETURNING quantity", (tx["quantity"], tx["product_id"]))
                _undo_row = cur.fetchone()
                _undo_qty = _pd_row_val(_undo_row, "quantity", 0) or 0
                if _undo_qty < 0:
                    # (v80) قفل حلقة العجز: عكس الإضافة خلق عجزاً — سجله داخل نفس المعاملة
                    _sync_r = sync_product_pending_deficit(tx["product_id"], cur, owner_user_id=tx.get("user_id"))
                    if _sync_r and _sync_r["created"] > 0:
                        _undo_note = ("\n\n⚠️ الكمية أصبحت سالبة — تم تسجيل "
                                      + str(_sync_r["created"]) + " وحدة كطلب معلق")
            else:
                cur.execute("UPDATE products SET quantity = quantity + %s WHERE id = %s", (tx["quantity"], tx["product_id"]))
                # Reverse pending delivery: reduce or delete the most recent pending for this user+product
                if tx["type"] == "remove":
                    cur.execute(
                        "SELECT id, pending_qty FROM pending_deliveries "
                        "WHERE user_id = %s AND product_id = %s AND pending_qty > 0 "
                        "ORDER BY created_at DESC LIMIT 1",
                        (tx["user_id"], tx["product_id"]),
                    )
                    pd = cur.fetchone()
                    if pd:
                        new_pending = pd["pending_qty"] - tx["quantity"]
                        if new_pending <= 0:
                            cur.execute("DELETE FROM pending_deliveries WHERE id = %s", (pd["id"],))
                        else:
                            cur.execute("UPDATE pending_deliveries SET pending_qty = %s WHERE id = %s", (new_pending, pd["id"]))
            # Delete the transaction
            cur.execute("DELETE FROM transactions WHERE id = %s", (tx_id,))
        conn.commit()
        action = "إضافة" if tx["type"] == "add" else "إخراج"
        return True, "✅ تم حذف عملية " + action + " " + str(tx["quantity"]) + " بنجاح وعكس الكمية." + _undo_note
    except Exception as e:
        conn.rollback()
        logging.error("[DEL_TX] Failed to delete tx " + str(tx_id) + ": " + str(e))
        return False, "❌ حدث خطأ في الحذف."
    finally:
        release_connection(conn)


# ═══════════════════════════════════════════════════════════
#  PENDING DELIVERIES (المخزون السالب)
# ═══════════════════════════════════════════════════════════

def create_pending_delivery(user_id, product_id, pending_qty):
    """Create a pending delivery record when a sale exceeds available stock."""
    if pending_qty <= 0:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO pending_deliveries (user_id, product_id, pending_qty) "
                "VALUES (%s, %s, %s) "
                "RETURNING id, user_id, product_id, pending_qty, created_at",
                (user_id, product_id, pending_qty),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def auto_reconcile_product(product_id, added_qty=0, admin_user_id=None):
    """Auto-reconcile pending deliveries for one product.

    Invariant: مجموع الطلبات المعلقة المفتوحة للسلعة يجب أن يساوي عجزها
    الحالي max(0, -quantity). أي زيادة عن ذلك تعني صفوفاً عالقة غطاها
    المخزون ولم تُزل (كان سببها: إضافة بسبب «تصحيح مخزون» أو «إضافة
    سلعة» باسم موجود أو إعادة تشغيل عمليات أوفلاين) — تُغطى هنا FIFO
    وتُزال من قائمة الطلبات المعلقة ويُشعر أصحابها.

    تُستدعى بعد كل زيادة كمية وعند فتح قوائم الطلبات المعلقة (شفاء ذاتي).

    Returns dict with:
      - product_name: str
      - total_covered: int
      - fulfilled: list of {user_tid, user_name, product_name, qty, fully}
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get current product state
            cur.execute(
                "SELECT id, name, quantity FROM products WHERE id = %s",
                (product_id,),
            )
            product = cur.fetchone()
            if not product:
                return None

            new_qty = product["quantity"]

            # The open pending rows must mirror the real deficit. Anything
            # beyond it is already covered by stock and must be removed.
            target_pending = max(0, -new_qty)

            # Get pending deliveries for this product, oldest first
            cur.execute(
                "SELECT pd.*, u.telegram_id AS user_tid, u.first_name AS user_fname, u.username AS user_name "
                "FROM pending_deliveries pd "
                "LEFT JOIN users u ON u.id = pd.user_id "
                "WHERE pd.product_id = %s AND pd.pending_qty > 0 "
                "ORDER BY pd.created_at ASC",
                (product_id,),
            )
            pendings = cur.fetchall()

            if not pendings:
                return None

            total_pending = sum(p["pending_qty"] for p in pendings)
            excess = total_pending - target_pending
            if excess <= 0:
                return None

            # Fulfill FIFO down to the real deficit
            remaining_cover = excess
            fulfilled_list = []
            total_reconciled = 0

            for pd in pendings:
                if remaining_cover <= 0:
                    break
                pd = dict(pd)
                fulfill_amount = min(pd["pending_qty"], remaining_cover)
                new_pending = pd["pending_qty"] - fulfill_amount

                if new_pending <= 0:
                    # Fully fulfilled
                    cur.execute(
                        "UPDATE pending_deliveries SET pending_qty = 0, fulfilled_at = NOW() WHERE id = %s",
                        (pd["id"],),
                    )
                    fully = True
                else:
                    # Partially fulfilled
                    cur.execute(
                        "UPDATE pending_deliveries SET pending_qty = %s WHERE id = %s",
                        (new_pending, pd["id"]),
                    )
                    fully = False

                remaining_cover -= fulfill_amount
                total_reconciled += fulfill_amount

                user_display = pd.get("user_fname") or pd.get("user_name") or "مجهول"
                fulfilled_list.append({
                    "user_tid": pd.get("user_tid"),
                    "user_name": user_display,
                    "product_name": product["name"],
                    "qty": fulfill_amount,
                    "fully": fully,
                    "remaining": new_pending if not fully else 0,
                })

            # Create a reconcile transaction for the movements log
            if total_reconciled > 0:
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'reconcile', %s, %s, %s)",
                    (product_id, total_reconciled, admin_user_id, "تسوية عجز تلقائية"),
                )

        conn.commit()
        return {
            "product_name": product["name"],
            "total_covered": total_reconciled,
            "fulfilled": fulfilled_list,
            "remaining_deficit": max(0, -new_qty),
        }
    except Exception as e:
        conn.rollback()
        logging.error("[RECONCILE] Failed for product " + str(product_id) + ": " + str(e))
        return None
    finally:
        release_connection(conn)


async def _send_fulfilled_notifications(bot, fulfilled_list):
    """Notify customers whose pending orders just got covered by stock."""
    for f in (fulfilled_list or []):
        tid = f.get("user_tid")
        if not tid:
            continue
        try:
            if f["fully"]:
                notif = (
                    "✅ تم توفير " + str(f["qty"]) + " وحدة من " + f["product_name"] + chr(10)
                    + "⏳ طلبك المعلق تم تغطيته بالكامل ✓"
                )
            else:
                notif = (
                    "⏳ تم توفير " + str(f["qty"]) + " وحدة من " + f["product_name"] + chr(10)
                    + "📏 المتبقي: " + str(f["remaining"]) + " وحدة"
                )
            await bot.send_message(chat_id=tid, text=notif)
        except Exception:
            pass


def auto_reconcile_all(admin_user_id=None):
    """Self-heal: run auto_reconcile_product for every product that still
    has open pending rows. Removes entries already covered by stock
    (legacy drift) and returns a combined result or None."""
    product_ids = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT product_id FROM pending_deliveries "
                "WHERE pending_qty > 0 AND product_id IS NOT NULL"
            )
            product_ids = [r[0] for r in cur.fetchall()]
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[RECONCILE-ALL] Failed to list products: " + str(e))
        return None
    finally:
        release_connection(conn)

    total_fulfilled = []
    total_covered = 0
    for pid in product_ids:
        result = auto_reconcile_product(pid, 0, admin_user_id)
        if result and result["fulfilled"]:
            total_covered += result["total_covered"]
            total_fulfilled.extend(result["fulfilled"])
    if not total_fulfilled:
        return None
    return {"total_covered": total_covered, "fulfilled": total_fulfilled}


# ═══ (v80) قفل حلقة العجز: كل عجز مخزون له صف في الطلبات المعلقة ═══
# المعيار (Invariant): مجموع الطلبات المعلقة المفتوحة للسلعة = max(0, -كميتها).
# قبل v80 كان ثلاثة مسارات تنشئ كمية سالبة بلا تسجيل طلب معلق: الإخراج السريع
# (± من بطاقة السلعة)، حذف عملية «إضافة» كانت سترفع الكمية فوق المتاح، وإعادة
# تشغيل عمليات الإخراج الأوفلاين — إضافة إلى عجز تاريخي/مستورد من نسخ احتياطية.
# هذه الدوال تُنشئ الناقص فقط (اتجاه الزائد يبقى بيد auto_reconcile_product FIFO).

def _pd_row_val(row, key, idx):
    """قراءة عمود من صف بنمطي المؤشر (dict أو tuple) معاً."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[idx] if idx < len(row) else None


def sync_product_pending_deficit(product_id, cur, owner_user_id=None):
    """(v80) يضمن انعكاس عجز السلعة في الطلبات المعلقة — يُنشئ الناقص فقط.

    cur: مؤشر داخل معاملة قائمة (يلتزم بمعاملة المستدعي ولا يلتزم بنفسه).
    owner_user_id: صاحب العجز المعروف للمستدعي (المشغّل الذي نفذ العملية)؛
    وإلا يُستنتج من آخر حركة إخراج للسلعة، ثم من أول مدير في القاعدة.

    Returns {"product", "target", "open", "created"} أو None إن غابت السلعة.
    """
    cur.execute(
        "SELECT id, name, quantity FROM products WHERE id = %s",
        (product_id,),
    )
    prod = cur.fetchone()
    if not prod:
        return None
    qty = _pd_row_val(prod, "quantity", 2) or 0
    target = max(0, -qty)
    cur.execute(
        "SELECT COALESCE(SUM(pending_qty), 0) AS total FROM pending_deliveries "
        "WHERE product_id = %s AND pending_qty > 0",
        (product_id,),
    )
    total_open = _pd_row_val(cur.fetchone(), "total", 0) or 0
    created = 0
    missing = target - total_open
    if missing > 0:
        uid = owner_user_id
        if uid is not None:
            cur.execute("SELECT 1 AS ok FROM users WHERE id = %s", (uid,))
            if cur.fetchone() is None:
                uid = None
        if uid is None:
            cur.execute(
                "SELECT user_id AS uid FROM transactions "
                "WHERE product_id = %s AND type = 'remove' "
                "ORDER BY date DESC LIMIT 1",
                (product_id,),
            )
            uid = _pd_row_val(cur.fetchone(), "uid", 0)
        if uid is None:
            cur.execute(
                "SELECT id AS uid FROM users "
                "ORDER BY CASE WHEN role = 'admin' THEN 0 WHEN role = 'manager' THEN 1 ELSE 2 END, "
                "id ASC LIMIT 1"
            )
            uid = _pd_row_val(cur.fetchone(), "uid", 0)
        if uid is not None:
            cur.execute(
                "INSERT INTO pending_deliveries (user_id, product_id, pending_qty) "
                "VALUES (%s, %s, %s)",
                (uid, product_id, missing),
            )
            created = missing
    return {"product": _pd_row_val(prod, "name", 1), "target": target,
            "open": total_open, "created": created}


def sync_product_pending_deficit_now(product_id, owner_user_id=None):
    """(v80) نسخة ذاتية الاتصال — تفتح اتصالاً وتلتزم بنفسها، للنداءات خارج معاملة."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            r = sync_product_pending_deficit(product_id, cur, owner_user_id=owner_user_id)
        conn.commit()
        if r and r["created"] > 0:
            logging.warning(
                "[PENDING-SYNC][v80] product " + str(product_id)
                + ": deficit " + str(r["target"]) + " had " + str(r["created"])
                + " unregistered pending unit(s) — created now")
        return r
    except Exception as e:
        conn.rollback()
        logging.error("[PENDING-SYNC][v80] product " + str(product_id) + " failed: " + str(e))
        return None
    finally:
        release_connection(conn)


def sync_all_pending_deficits():
    """(v80) مسح شامل: أي سلعة سالبة بلا طلب معلق يُسجل عجزها فوراً.
    يُستدعى عند الإقلاع وبعد كل استيراد نسخة احتياطية — شفاء العجز التاريخي."""
    conn = get_connection()
    summary = {"scanned": 0, "deficit_products": 0, "created_products": 0,
               "created_units": 0, "products": []}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id FROM products WHERE quantity < 0 ORDER BY id")
            ids = [_pd_row_val(r, "id", 0) for r in cur.fetchall()]
        conn.commit()
        for pid in ids:
            if pid is None:
                continue
            summary["scanned"] += 1
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                r = sync_product_pending_deficit(pid, cur)
            conn.commit()
            if r:
                summary["deficit_products"] += 1
                if r["created"] > 0:
                    summary["created_products"] += 1
                    summary["created_units"] += r["created"]
                    summary["products"].append({"product": r["product"], "created": r["created"]})
        if summary["created_units"] > 0:
            logging.warning(
                "[PENDING-SYNC][v80] scan: " + str(summary["created_units"])
                + " unregistered deficit unit(s) across " + str(summary["created_products"])
                + " product(s) — registered now")
        return summary
    except Exception as e:
        conn.rollback()
        logging.error("[PENDING-SYNC][v80] scan failed: " + str(e))
        return summary
    finally:
        release_connection(conn)


def get_all_pending_deliveries():
    """Get all pending deliveries grouped by product for admin view."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pd.id, pd.product_id, pd.user_id, pd.pending_qty, pd.created_at, "
                "p.name AS product_name, p.quantity AS product_qty, "
                "u.telegram_id AS user_tid, u.first_name AS user_fname, u.username AS user_name "
                "FROM pending_deliveries pd "
                "JOIN products p ON p.id = pd.product_id "
                "LEFT JOIN users u ON u.id = pd.user_id "
                "WHERE pd.pending_qty > 0 "
                "ORDER BY p.name ASC, pd.created_at ASC"
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def get_user_pending_deliveries(user_db_id):
    """Get pending deliveries for a specific user (customer view)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pd.id, pd.product_id, pd.pending_qty, pd.created_at, "
                "p.name AS product_name, p.quantity AS product_qty "
                "FROM pending_deliveries pd "
                "JOIN products p ON p.id = pd.product_id "
                "WHERE pd.user_id = %s AND pd.pending_qty > 0 "
                "ORDER BY pd.created_at DESC",
                (user_db_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        return []
    finally:
        release_connection(conn)


def get_admin_last_transaction(admin_tid):
    """Get the admin's most recent non-reconcile transaction."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id AS tx_id, t.type AS tx_type, t.quantity, t.date, t.reason, "
                "p.name AS product_name, p.quantity AS current_qty "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "JOIN users u ON u.id = t.user_id "
                "WHERE u.telegram_id = %s AND t.type != 'reconcile' "
                "ORDER BY t.date DESC LIMIT 1", (admin_tid,))
            tx = cur.fetchone()
        conn.commit()
        return dict(tx) if tx else None
    except Exception as e:
        conn.rollback()
        logging.error("[GET_LAST_TX] Error: " + str(e))
        return None
    finally:
        release_connection(conn)


def get_product_transactions(product_id, limit=30):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    t.id, t.type AS tx_type, t.quantity, t.date,
                    t.reason, t.product_id,
                    u.username AS operator_name
                FROM transactions t
                LEFT JOIN users u ON u.id = t.user_id
                WHERE t.product_id = %s
                  AND t.type != 'reconcile'
                  AND (t.reason IS NULL OR t.reason NOT LIKE '%%تصحيح مخزون%%')
                ORDER BY t.date DESC
                LIMIT %s
            """, (product_id, limit))
            rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _filter_test_pairs(txns):
    """Filter out test/undo movement pairs: remove then add of same product, same quantity, within 5 minutes.
    Only filters remove→add (test or undo). Does NOT filter add→remove (legitimate sale after restock)."""
    if not txns or len(txns) < 2:
        return txns
    # Normalize id key (date-based queries use tx_id, product queries use id)
    for t in txns:
        if "id" not in t and "tx_id" in t:
            t["id"] = t["tx_id"]
    # Work on copies sorted ascending by date for pair detection
    sorted_txns = sorted(txns, key=lambda t: t["date"])
    excluded_ids = set()
    max_gap = datetime.timedelta(minutes=5)
    for i in range(len(sorted_txns)):
        if sorted_txns[i]["id"] in excluded_ids:
            continue
        for j in range(i + 1, len(sorted_txns)):
            if sorted_txns[j]["id"] in excluded_ids:
                continue
            # Stop if time gap is too large (list is sorted ascending)
            if sorted_txns[j]["date"] - sorted_txns[i]["date"] > max_gap:
                break
            a, b = sorted_txns[i], sorted_txns[j]
            pid_a = a.get("product_id") or a.get("product_name", "")
            pid_b = b.get("product_id") or b.get("product_name", "")
            # Only filter: remove THEN add (test/undo), NOT add THEN remove (legitimate)
            if (pid_a == pid_b
                    and a["tx_type"] == "remove" and b["tx_type"] == "add"
                    and a["quantity"] == b["quantity"]):
                excluded_ids.add(a["id"])
                excluded_ids.add(b["id"])
                break
    if not excluded_ids:
        return txns
    return [t for t in txns if t["id"] not in excluded_ids]


async def _send_product_list(update, products, context, state_key="id_map"):
    """Send product list with sequential numbering (#1, #2...) and store ID mapping."""
    id_map = {}
    lines = ["\U0001f4e6 قائمة السلع" + chr(10)]
    for idx, p in enumerate(products, 1):
        id_map[str(idx)] = p["id"]
        lines.append("  #" + str(idx) + " " + p["name"])
    context.user_data[state_key] = id_map
    full = chr(10).join(lines)
    if len(full) <= 4096:
        await update.message.reply_text(full)
    else:
        chunks = []
        cur_chunk = []
        cur_len = 0
        for ln in lines:
            if cur_len + len(ln) + 1 > 4000 and cur_chunk:
                chunks.append(chr(10).join(cur_chunk))
                cur_chunk = []
                cur_len = 0
            cur_chunk.append(ln)
            cur_len += len(ln) + 1
        if cur_chunk:
            chunks.append(chr(10).join(cur_chunk))
        for chunk in chunks:
            await update.message.reply_text(chunk)


def _resolve_product_id(context, display_num, state_key="id_map"):
    """Convert sequential display number to real DB ID."""
    id_map = context.user_data.get(state_key, {})
    real_id = id_map.get(str(display_num))
    if real_id is None:
        raise ValueError("رقم السلعة غير موجود: " + str(display_num))
    return real_id


# ═══════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════

STATE_ADD_NAME = "waiting_add_name"
STATE_ADD_QTY = "waiting_add_qty"
STATE_EXISTING_ADD_QTY = "waiting_existing_add_qty"
STATE_REMOVE_QTY = "waiting_remove_qty"
STATE_EDIT_PRODUCT = "waiting_edit_product"
STATE_DELETE_PRODUCT = "waiting_delete_product"
STATE_SEARCH_ADMIN = "waiting_search_admin"
STATE_WAITING_SELLER = "waiting_seller_name"
STATE_ADMIN_ADD_SEL = "waiting_admin_add_sel"
STATE_ADMIN_REM_SEL = "waiting_admin_rem_sel"
STATE_ADD_NAME_MOTIF = "waiting_add_name_motif"
STATE_ADD_NAME_12060 = "waiting_add_name_12060"
STATE_ADD_NAME_PLANTE = "waiting_add_name_plante"
STATE_ALERT_MSG = "waiting_alert_msg"
# v83: التنبيه عام + خاص — حالة اختيار النوع وحالة نص التنبيه الخاص
STATE_ALERT_MODE = "waiting_alert_mode"
STATE_ALERT_PRIV_MSG = "waiting_alert_priv_msg"
# v86: تنبيه خاص لعدة معنيين — تحديد المستلمين في رسالة واحدة بدل القوائم
STATE_ALERT_PRIV_MULTI = "waiting_alert_priv_multi"
STATE_ALERT_PRIV_MULTI_MSG = "waiting_alert_priv_multi_msg"
STATE_INVOICE_CUSTOMER = "waiting_invoice_customer"
STATE_INVOICE_PRODUCT = "waiting_invoice_product"
STATE_INVOICE_QTY = "waiting_invoice_qty"
STATE_INVOICE_PRICE = "waiting_invoice_price_m2"
STATE_INVOICE_AREA = "waiting_invoice_area"
STATE_OIL_READING = "waiting_oil_reading"
STATE_OIL_NEW_HOURS = "waiting_oil_new_hours"

BTN_ADD = "\U0001f4e6 \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629"
BTN_SEARCH = "\U0001f50d \u0628\u062d\u062b"
BTN_ADD_QTY = "\u2795 \u0625\u0636\u0627\u0641\u0629 \u0643\u0645\u064a\u0629"
BTN_REMOVE_QTY = "\u2796 \u0625\u062e\u0631\u0627\u062c \u0643\u0645\u064a\u0629"
BTN_EDIT = "\u270f\ufe0f \u062a\u0639\u062f\u064a\u0644 \u0633\u0644\u0639\u0629"
BTN_DELETE = "\U0001f5d1\ufe0f \u062d\u0630\u0641 \u0633\u0644\u0639\u0629"
BTN_CANCEL = "\u274c \u0625\u0644\u063a\u0627\u0621"
BTN_SKIP = "\u23ed \u062a\u062e\u0637\u064d"
BTN_USERS = "⚙️ المستخدمين"
BTN_BACKUP = "💾 نسخ احتياطي"
BTN_ALERT = "🔔 تنبيه"
BTN_UPDATE = "📢 إشعار تحديث"
BTN_LOW_STOCK = "📊 المخزون المنخفض"
BTN_MOVEMENTS = "📋 الحركة"
BTN_INCOMING = "🚚 طلبات التوريد"
BTN_INVOICE = "🧾 إنشاء فاتورة"
# Customer buttons
BTN_CUST_ALERT = "🔔 تنبيه المخزون"
BTN_CUST_ADD = "➕ إضافة"
BTN_CUST_REMOVE = "➖ إخراج"
BTN_CUST_MOVEMENTS = "📋 الحركة"
BTN_CUST_CANCEL = "❌ إلغاء"
BTN_CUST_ORDER = "📦 طلب توريد جديد"
BTN_CUST_ORDER_LEGACY = "📦 طلب توريد"  # توافق لوحات الإصدار السابق
BTN_CUST_MY_ORDERS = "📋 طلباتي"
BTN_CUST_PENDING = "⏳ طلباتي المعلقة"
BTN_PENDING_ADMIN = "🔴 الطلبات المعلقة"
BTN_OIL = "🔧 زيت الرافعة"

# ═══════════════════════════════════════════════════════════
#  نظام الفئات (الأدوار) — ROLES SYSTEM
#  كل فئة لها لوحة أزرار وميزاتها الخاصة؛ الميزات الجديدة
#  تُوجّه للفئة المعنية عبر ROLE_LABELS + بوابة الفئات.
# ═══════════════════════════════════════════════════════════
ROLE_ADMIN = "admin"        # 👑 مدير النظام (متغيرات البيئة/المالك)
ROLE_MANAGER = "manager"    # 🧑‍💼 مدير البوت (صلاحيات كاملة)
ROLE_STOCK = "stock"        # 📦 عامل مخزون (المخزون فقط)
ROLE_SELLER = "seller"      # 🛍 بائع (الفواتير والمبيعات)
ROLE_EXTERNAL = "external"  # 🌍 بائع خارجي (التوفر فقط دون كميات)
ROLE_CUSTOMER = "customer"  # 👤 عميل (السلوك القديم كما هو)
ROLE_PENDING = "pending"    # ⏳ قيد المراجعة (لم يُفعّل بعد)

ROLE_LABELS = {
    "admin": "👑 مدير النظام",
    "manager": "🧑‍💼 مدير البوت",
    "stock": "📦 عامل مخزون",
    "seller": "🛍 بائع",
    "external": "🌍 بائع خارجي",
    "customer": "👤 عميل",
    "pending": "⏳ قيد المراجعة",
}

# الخيارات التي تُعرض على المستخدم الجديد عند طرق باب البوت لأول مرة
ROLE_PICK_ORDER = (ROLE_SELLER, ROLE_STOCK, ROLE_MANAGER, ROLE_EXTERNAL)

# أزرار لوحة الإدارة المسموحة لعامل المخزون (تُفحص في بوابة الفئات)
STOCK_ADMIN_BTNS = (BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT,
                    BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_PENDING_ADMIN)
STOCK_ADMIN_PREFIXES = (BTN_PENDING_ADMIN,)


# ═══════════════════════════════════════════════════════════
#  🔐 نظام الصلاحيات الديناميكي — DYNAMIC ROLE PERMISSIONS
#  كل صلاحية لكل فئة مخزنة في قاعدة البيانات (جدول role_permissions)
#  ويُدار من داخل البوت بزر «🔐 الصلاحيات» في لوحة المدير —
#  بدون تعديل الكود وبدون رفع ملف جديد أبداً.
#  القيم الافتراضية أدناه = سلوك الفئات الحالي حرفياً، وتُزرع في
#  قاعدة البيانات مرة واحدة فقط (ON CONFLICT DO NOTHING) فلا تطغى
#  على تعديلات المدير لاحقاً.
# ═══════════════════════════════════════════════════════════
BTN_PERMS = "🔐 الصلاحيات"

PERM_SEARCH       = "search"        # 🔍 بحث
PERM_ADD_QTY      = "add_qty"       # ➕ إضافة كمية
PERM_REMOVE       = "remove"        # ➖ إخراج كمية
PERM_INVOICE      = "invoice"       # 🧾 إنشاء فاتورة
PERM_SUPPLY_ORDER = "supply_order"  # 📦 طلب توريد جديد
PERM_MY_ORDERS    = "my_orders"     # 📋 طلباتي
PERM_MOVEMENTS    = "movements"     # 📋 الحركة
PERM_ADD_PRODUCT  = "add_product"   # 📦 إضافة سلعة
PERM_EDIT_PRODUCT = "edit_product"  # ✏️ تعديل سلعة
PERM_LOW_STOCK    = "low_stock"     # 📊 المخزون المنخفض
PERM_PENDING      = "pending"       # 🔴 الطلبات المعلقة

PERM_LABELS = {
    PERM_SEARCH:       "🔍 البحث",
    PERM_ADD_QTY:      "➕ إضافة كمية",
    PERM_REMOVE:       "➖ إخراج كمية",
    PERM_INVOICE:      "🧾 إنشاء فاتورة",
    PERM_SUPPLY_ORDER: "📦 طلب توريد جديد",
    PERM_MY_ORDERS:    "📋 طلباتي",
    PERM_MOVEMENTS:    "📋 الحركة",
    PERM_ADD_PRODUCT:  "📦 إضافة سلعة",
    PERM_EDIT_PRODUCT: "✏️ تعديل سلعة",
    PERM_LOW_STOCK:    "📊 المخزون المنخفض",
    PERM_PENDING:      "🔴 الطلبات المعلقة",
}

# الصلاحيات القابلة للإدارة لكل فئة (= الافتراضي المفعّل عند أول تشغيل)
ROLE_PERM_CATALOG = {
    ROLE_SELLER: (PERM_SEARCH, PERM_ADD_QTY, PERM_REMOVE, PERM_INVOICE,
                  PERM_SUPPLY_ORDER, PERM_MY_ORDERS, PERM_MOVEMENTS),
    ROLE_STOCK:  (PERM_ADD_QTY, PERM_ADD_PRODUCT, PERM_REMOVE, PERM_EDIT_PRODUCT,
                  PERM_MOVEMENTS, PERM_LOW_STOCK, PERM_PENDING),
    ROLE_EXTERNAL: (PERM_SEARCH,),
}

# ربط أزرار كل فئة بصلاحياتها (للبوابات النصية) — أزرار اللوحة فقط هي المسموحة،
# وأي زر معروف خارج هذه الخرائط = مرفوض للفئة (قائمة سماح صارمة كما كان).
ROLE_BTN_PERM_MAP = {
    ROLE_SELLER: {
        BTN_SEARCH:            PERM_SEARCH,
        BTN_CUST_REMOVE:       PERM_REMOVE,
        BTN_ADD_QTY:           PERM_ADD_QTY,
        BTN_INVOICE:           PERM_INVOICE,
        BTN_CUST_ORDER:        PERM_SUPPLY_ORDER,
        BTN_CUST_ORDER_LEGACY: PERM_SUPPLY_ORDER,
        BTN_CUST_MY_ORDERS:    PERM_MY_ORDERS,
        BTN_CUST_MOVEMENTS:    PERM_MOVEMENTS,
    },
    ROLE_STOCK: {
        BTN_REMOVE_QTY:    PERM_REMOVE,
        BTN_ADD:           PERM_ADD_PRODUCT,
        BTN_ADD_QTY:       PERM_ADD_QTY,
        BTN_EDIT:          PERM_EDIT_PRODUCT,
        BTN_MOVEMENTS:     PERM_MOVEMENTS,
        BTN_LOW_STOCK:     PERM_LOW_STOCK,
        BTN_PENDING_ADMIN: PERM_PENDING,
    },
    ROLE_EXTERNAL: {
        BTN_SEARCH: PERM_SEARCH,
    },
}

# كاش الصلاحيات: يمنع لمس قاعدة البيانات في كل رسالة.
# يُفرغ تلقائياً عند أي تبديل + عمر أقصى 30 ثانية للأمان.
_ROLE_PERM_CACHE = {}
_ROLE_PERM_TTL = 30.0


def _load_role_perms_from_db(role):
    """يقرأ صلاحيات الفئة من قاعدة البيانات — يرجع None عند أي فشل
    (فتعود المصفوفة الافتراضية = نفس سلوك الكود في أسوأ الحالات)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT perm, enabled FROM role_permissions WHERE role = %s", (role,))
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            return None
        finally:
            release_connection(conn)
    except Exception:
        return None
    return {r[0]: bool(r[1]) for r in rows if r[0] in PERM_LABELS}


def get_role_perms(role):
    """قاموس كل صلاحيات الفئة (مع كاش 30 ثانية). الفشل = الافتراضي."""
    now = time.monotonic()
    _hit = _ROLE_PERM_CACHE.get(role)
    if _hit and _hit[0] > now:
        return _hit[1]
    perms = {p: False for p in PERM_LABELS}
    _rows = _load_role_perms_from_db(role)
    if _rows:
        # صفوف حقيقية مخزنة → الثقة بقاعدة البيانات كما هي
        perms.update(_rows)
    else:
        # لا صفوف لهذه الفئة (أو فشل DB) → الافتراضي الآمن = سلوك الكود
        for _p in ROLE_PERM_CATALOG.get(role, ()):
            perms[_p] = True
    _ROLE_PERM_CACHE[role] = (now + _ROLE_PERM_TTL, perms)
    return perms


def role_perm_enabled(role, perm):
    """هل الصلاحية مفعّلة لهذه الفئة؟ الإداريون فوق النظام دائماً."""
    if role in (ROLE_ADMIN, ROLE_MANAGER):
        return True
    if role not in ROLE_PERM_CATALOG:
        # فئات خارج النظام (عميل/قيد المراجعة): لا مصفوفة لها — الرفض هو الآمن
        return False
    return bool(get_role_perms(role).get(perm, False))


def set_role_perm(role, perm, enabled):
    """تبديل صلاحية فئة في قاعدة البيانات + تفريغ الكاش فوراً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO role_permissions (role, perm, enabled) VALUES (%s, %s, %s) "
                "ON CONFLICT (role, perm) DO UPDATE SET enabled = EXCLUDED.enabled",
                (role, perm, bool(enabled)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    _ROLE_PERM_CACHE.clear()


def _btn_perm_ok(role, text):
    """هل يُسمح لهذه الفئة بهذا الزر (نصاً)؟ يُستخدم في بوابات الفئات.
    دلالة قائمة السماح الصارمة لكل فئة (ROLE_BTN_PERM_MAP): أزرار لوحتها
    تُحكم بصلاحياتها، أي زر معروف خارج خريطة فئتها = مرفوض لها،
    والإلغاء متاح لكل الفئات دائماً. العدادات الديناميكية تُطابق ببادئتها."""
    if role in (ROLE_ADMIN, ROLE_MANAGER):
        return True
    if text in (BTN_CANCEL, BTN_CUST_CANCEL):
        return True  # الإلغاء متاح دائماً
    _map = ROLE_BTN_PERM_MAP.get(role) or {}
    _perm = _map.get(text)
    if _perm is None:
        # العدادات الديناميكية (نص الزر + عداد بين قوسين)
        if text.startswith(BTN_PENDING_ADMIN) and PERM_PENDING in _map.values():
            _perm = PERM_PENDING
    if _perm is None:
        return False  # زر معروف خارج لوحة الفئة → مرفوض
    return role_perm_enabled(role, _perm)


def _perms_roles_keyboard():
    """لوحة اختيار الفئة لإدارة صلاحياتها (inline)."""
    _rows = []
    for _r in (ROLE_SELLER, ROLE_STOCK, ROLE_EXTERNAL):
        _rows.append([InlineKeyboardButton(
            "🛍 البائع" if _r == ROLE_SELLER else ("📦 عامل المخزون" if _r == ROLE_STOCK else "🌍 البائع الخارجي"),
            callback_data="permsrole_" + _r)])
    _rows.append([InlineKeyboardButton("✅ إغلاق", callback_data="permsdone")])
    return InlineKeyboardMarkup(_rows)


def _perms_panel_keyboard(role):
    """لوحة صلاحيات فئة محددة: كل صلاحية زر يتبدل ✅/❌ بضغطة."""
    perms = get_role_perms(role)
    _rows = []
    for _p in ROLE_PERM_CATALOG.get(role, ()):
        _mark = "✅" if perms.get(_p) else "❌"
        _rows.append([InlineKeyboardButton(
            _mark + " " + PERM_LABELS.get(_p, _p),
            callback_data="permstgl_" + role + "_" + _p)])
    _rows.append([
        InlineKeyboardButton("🔄 تحديث", callback_data="permsrole_" + role),
        InlineKeyboardButton("✅ إغلاق", callback_data="permsdone"),
    ])
    return InlineKeyboardMarkup(_rows)


def _perms_panel_text(role):
    return ("🔐 إدارة صلاحيات الفئة: " + ROLE_LABELS.get(role, role) + chr(10) + chr(10)
            + "اضغط على أي صلاحية لتبديلها (✅ مفعّلة / ❌ موقفة)." + chr(10)
            + "التغيير يسري فوراً على جميع مستخدمي الفئة.")


async def callback_perms_handler(update, context):
    """🔐 معالج لوحة الصلاحيات: اختيار فئة → تبديل صلاحية فورياً في قاعدة البيانات."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذه اللوحة للمديرين فقط.", show_alert=True)
        return
    data = query.data
    if data == "permsdone":
        _KB_ROLE_UID.set(query.from_user.id)
        await query.answer()
        try:
            await query.edit_message_text("✅ تم إغلاق لوحة الصلاحيات.")
        except Exception:
            pass
        return
    if data.startswith("permsrole_"):
        role = data[len("permsrole_"):]
        if role not in ROLE_PERM_CATALOG:
            await query.answer("❌ فئة غير معروفة.", show_alert=True)
            return
        _KB_ROLE_UID.set(query.from_user.id)
        await query.answer()
        try:
            await query.edit_message_text(_perms_panel_text(role), reply_markup=_perms_panel_keyboard(role))
        except Exception:
            pass
        return
    if data.startswith("permstgl_"):
        rest = data[len("permstgl_"):]
        role, _, perm = rest.partition("_")
        if role not in ROLE_PERM_CATALOG or perm not in PERM_LABELS or perm not in ROLE_PERM_CATALOG.get(role, ()):
            await query.answer("❌ صلاحية غير معروفة.", show_alert=True)
            return
        new_val = not role_perm_enabled(role, perm)
        set_role_perm(role, perm, new_val)
        _KB_ROLE_UID.set(query.from_user.id)
        await query.answer("✅ تم التفعيل" if new_val else "⛔ تم الإيقاف")
        try:
            await query.edit_message_text(_perms_panel_text(role), reply_markup=_perms_panel_keyboard(role))
        except Exception:
            pass
        return


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════


def format_product_admin(product):
    qty = product["quantity"]
    unit = _get_unit_code(product["name"])
    return (
        "\U0001f4e6 " + product["name"] + "\n"
        "   \u0627\u0644\u0643\u0645\u064a\u0629: " + str(qty) + " " + unit
    )


async def _broadcast_update_notification(update, context):
    """إرسال إشعار آخر تحديث تلقائياً بدون كتابة من المدير."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT description FROM bot_updates ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        row = None
    finally:
        release_connection(conn)

    if not row:
        await update.message.reply_text(
            "📭 لا يوجد تحديثات مسجلة.",
            reply_markup=get_admin_keyboard(),
        )
        return

    update_desc = row["description"]

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer')")
            user_rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        user_rows = []
    finally:
        release_connection(conn)

    if not user_rows:
        await update.message.reply_text(
            "📭 لا يوجد مستخدمين مفعلين.",
            reply_markup=get_admin_keyboard(),
        )
        return

    broadcast_text = (
        "🆕 <b>تحديث جديد!</b>\n\n"
        + update_desc + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👉 اضغط الزر أدناه لتحديث الواجهة\n"
        "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الواجهة", callback_data="go_start")]
    ])
    sent = 0
    for r in user_rows:
        uid = r[0]
        try:
            await context.bot.send_message(
                chat_id=uid, text=broadcast_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception:
            pass

    await update.message.reply_text(
        "✅ تم إرسال إشعار التحديث إلى <b>" + str(sent) + "</b> مستخدم.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(),
    )
    logging.info("[UPDATE] Auto-broadcast sent to " + str(sent) + " users")


async def _broadcast_to_all_users(bot, message):
    """Send a message to ALL active users (admin + customer)."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') AND active = TRUE")
                rows = cur.fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            rows = []
        finally:
            release_connection(conn)
        for r in rows:
            try:
                await bot.send_message(chat_id=r[0], text=message)
            except Exception:
                pass
    except Exception as e:
        logging.warning("[BROADCAST] Failed: " + str(e))


async def _broadcast_add_notification(bot, product, qty, actor, reason="truck"):
    """Broadcast add-notification to all users with reason (truck arrival or customer return)."""
    pname = product.get("name", "") if isinstance(product, dict) else str(product)
    # (v30) أرشف الإشعار في القاعدة ليظهر في جرس تطبيق الويب أيضاً
    try:
        db_insert_notification("add",
            "🚚 وصول بضاعة جديدة" if reason == "truck" else "↩️ إرجاع سلعة من زبون",
            "📦 " + pname + "\n➕ الكمية: " + str(qty), str(actor or ""), "bot")
    except Exception:
        pass
    if reason == "truck":
        msg = ("🚚 وصول بضاعة جديدة!" + chr(10) + chr(10)
               + "📦 " + pname + chr(10)
               + "➕ الكمية: " + str(qty))
    else:
        msg = ("↩️ إرجاع سلعة من زبون" + chr(10) + chr(10)
               + "📦 " + pname + chr(10)
               + "➕ الكمية: " + str(qty))
    await _broadcast_to_all_users(bot, msg)


async def send_low_stock_alerts(bot):
    """Send individual alerts to ALL users when products enter or leave low-stock list."""
    try:
        low_stock = get_low_stock_products()
        if not low_stock:
            current = {}
        else:
            current = {p["id"]: p for p in low_stock}

        # Products that ENTERED low stock (new)
        new_ids = set(current.keys()) - set(_previously_low_stock.keys())
        if new_ids:
            for pid in new_ids:
                prod = current[pid]
                name = prod["name"]
                lower_name = name.lower().strip()
                if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                    continue
                qty = prod.get("quantity", "?")
                unit = _get_unit_ar(name)
                msg = ("🔴 تنبيه: مخزون منخفض جديد"
                    + chr(10) + chr(10)
                    + "📦 " + name + chr(10)
                    + "📏 الكمية: " + str(qty) + " " + unit)
                # (v30) أرشف تنبيه المخزون في جرس تطبيق الويب أيضاً
                try:
                    db_insert_notification("low", "🔴 مخزون منخفض",
                        "📦 " + name + "\n📏 الكمية: " + str(qty) + " " + unit, "", "bot")
                except Exception:
                    pass
                await _broadcast_to_all_users(bot, msg)

        # Products that LEFT low stock (restocked)
        left_ids = set(_previously_low_stock.keys()) - set(current.keys())
        if left_ids:
            # Fetch fresh data for restocked products (old cache has stale quantity)
            fresh = {}
            if left_ids:
                conn = get_connection()
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute("SELECT * FROM products WHERE id = ANY(%s)", (list(left_ids),))
                        fresh = {r["id"]: dict(r) for r in cur.fetchall()}
                    conn.commit()
                except Exception:
                    conn.rollback()
                finally:
                    release_connection(conn)
            for pid in left_ids:
                prod = fresh.get(pid) or _previously_low_stock[pid]
                name = prod["name"]
                lower_name = name.lower().strip()
                if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                    continue
                qty = prod.get("quantity", "?")
                unit = _get_unit_ar(name)
                msg = ("✅ تنبيه: توفرت سلعة"
                    + chr(10) + chr(10)
                    + "📦 " + name + chr(10)
                    + "📏 الكمية: " + str(qty) + " " + unit)
                # (v30) أرشف عودة السلعة في جرس تطبيق الويب أيضاً
                try:
                    db_insert_notification("low_ok", "✅ عادت السلعة للمستوى الآمن",
                        "📦 " + name + "\n📏 الكمية: " + str(qty) + " " + unit, "", "bot")
                except Exception:
                    pass
                await _broadcast_to_all_users(bot, msg)

        # Update tracked dict
        _previously_low_stock.clear()
        _previously_low_stock.update(current)
    except Exception as e:
        logging.warning("[ALERT] Low stock check failed: " + str(e))


async def send_low_stock_list(update=None, context=None, bot=None):
    """Send full low-stock list. Works for admin or customer button."""
    try:
        low_stock = get_low_stock_products()
        valid_items = []
        for p in low_stock:
            lower_name = p["name"].lower().strip()
            if lower_name in ("plante pcs", "plante", "120/60pcs", "120/60 pcs", "120/60"):
                continue
            valid_items.append(p)
        if not valid_items:
            msg = "✅ لا توجد سلع منخفضة المخزون."
            if update:
                await update.message.reply_text(msg)
            return
        txt_lines = ["⚠️ قائمة المخزون المنخفض"]
        for p in valid_items:
            txt_lines.append("  • " + p["name"])
        text = chr(10).join(txt_lines)
        if len(text) > 4096:
            chunks = []
            cur = ["⚠️ قائمة المخزون المنخفض"]
            for ln in txt_lines[1:]:
                if len(chr(10).join(cur)) + len(chr(10) + ln) > 4000:
                    chunks.append(chr(10).join(cur))
                    cur = ["⚠️ مخزون منخفض (تابع)"]
                cur.append(ln)
            if cur:
                chunks.append(chr(10).join(cur))
            for chunk in chunks:
                if update:
                    await update.message.reply_text(chunk)
        else:
            if update:
                await update.message.reply_text(text)
    except Exception as e:
        logging.warning("[LOW_STOCK_LIST] Error: " + str(e))


def parse_crt_input(text):
    """Parse quantity input: accepts '10', '10crt', '10 crt'."""
    cleaned = text.strip().lower().replace(" ", "").replace("crt", "").replace("pcs", "")
    if cleaned.isdigit() and int(cleaned) > 0:
        return int(cleaned)
    return None


def parse_qty_input(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        pid, qty = int(parts[0]), int(parts[1])
        if pid <= 0 or qty <= 0:
            return None
        return pid, qty
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════
#  KEYBOARDS

# ═══════════════════════════════════════════════
#  BOT SETTINGS HELPERS
# ═══════════════════════════════════════════════


# 🎯 سياق المستخدم الحالي: تضبطه نقاط الدخول (متعقب الرسائل + معالجات الكولباك)
# كي تعيد get_admin_keyboard لوحة الفئة الصحيحة تلقائياً لغير المديرين
# بدل تسريب لوحة الإدارة الكاملة لعامل المخزون/البائع/الخارجي.
_KB_ROLE_UID = contextvars.ContextVar("kb_role_uid", default=None)


def get_admin_keyboard(user_id=None):
    """لوحة المفاتيح الديناميكية - تعرض عدادات الطلبات والمستخدمين المعلّقين.

    🎯 حماية الفئات: عند الاستدعاء من مسار مستخدم غير إداري تُعاد لوحة فئته تلقائياً."""
    if user_id is None:
        user_id = _KB_ROLE_UID.get()
    if user_id is not None and not is_admin(user_id):
        return get_role_keyboard(user_id)
    # عداد طلبات التوريد المعلّقة
    incoming_count = 0
    pending_deliveries_count = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'pending'")
            incoming_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM incoming_orders WHERE status = 'received'")
            incoming_count += cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE role = 'pending'")
            pending_users_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT product_id) FROM pending_deliveries WHERE pending_qty > 0")
            pending_deliveries_count = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        pending_users_count = 0
        pending_deliveries_count = 0
    finally:
        release_connection(conn)

    # نص الأزرار مع العدادات
    users_btn = ("⚙️ المستخدمين (" + str(pending_users_count) + ")") if pending_users_count > 0 else BTN_USERS
    incoming_btn = ("🚚 طلبات التوريد (" + str(incoming_count) + ")") if incoming_count > 0 else BTN_INCOMING
    pending_btn = ("🔴 الطلبات المعلقة (" + str(pending_deliveries_count) + ")") if pending_deliveries_count > 0 else BTN_PENDING_ADMIN

    rows = [
        [BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY],
        [BTN_EDIT, BTN_DELETE],
        [users_btn, BTN_BACKUP, BTN_ALERT],
        [BTN_LOW_STOCK, BTN_MOVEMENTS, incoming_btn],
        [BTN_INVOICE, pending_btn, BTN_UPDATE],
        [BTN_OIL],
        [BTN_PERMS],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_input_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


def get_seller_keyboard():
    """Keyboard with Skip button for optional seller name."""
    return ReplyKeyboardMarkup([[BTN_SKIP], [BTN_CANCEL]], resize_keyboard=True)


def get_customer_keyboard(telegram_id=None):
    """لوحة العميل."""
    rows = [
        [BTN_CUST_ADD, BTN_CUST_REMOVE],
        [BTN_CUST_ORDER],
        [BTN_CUST_MY_ORDERS, BTN_CUST_PENDING],
        [BTN_CUST_ALERT],
        [BTN_CUST_MOVEMENTS],
        [BTN_CUST_CANCEL],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _get_role(telegram_id):
    """قراءة فئة المستخدم بسرعة (حسابات البيئة = admin دائماً)."""
    if telegram_id is not None and telegram_id in ADMIN_IDS:
        return ROLE_ADMIN
    try:
        u = get_user(telegram_id)
        return (u or {}).get("role") or ROLE_PENDING
    except Exception:
        return ROLE_PENDING


def get_stock_keyboard():
    """📦 لوحة عامل المخزون — تُبنى ديناميكياً من صلاحيات الفئة المفعّلة
    (نظام الصلاحيات الديناميكي: الإلغاء ظاهر دائماً)."""
    perms = get_role_perms(ROLE_STOCK)
    rows = []
    _r1 = []
    if perms.get(PERM_REMOVE):
        _r1.append(BTN_REMOVE_QTY)
    if perms.get(PERM_ADD_PRODUCT):
        _r1.append(BTN_ADD)
    if _r1:
        rows.append(_r1)
    _r2 = []
    if perms.get(PERM_ADD_QTY):
        _r2.append(BTN_ADD_QTY)
    if perms.get(PERM_EDIT_PRODUCT):
        _r2.append(BTN_EDIT)
    if _r2:
        rows.append(_r2)
    _r3 = []
    if perms.get(PERM_MOVEMENTS):
        _r3.append(BTN_MOVEMENTS)
    if perms.get(PERM_LOW_STOCK):
        _r3.append(BTN_LOW_STOCK)
    if _r3:
        rows.append(_r3)
    _r4 = []
    if perms.get(PERM_PENDING):
        _r4.append(BTN_PENDING_ADMIN)
    if _r4:
        rows.append(_r4)
    rows.append([BTN_CUST_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_seller_main_keyboard(telegram_id=None):
    """🛍 لوحة البائع — تُبنى ديناميكياً من صلاحيات الفئة المفعّلة
    (نظام الصلاحيات الديناميكي: الإلغاء ظاهر دائماً)."""
    perms = get_role_perms(ROLE_SELLER)
    rows = []
    _r1 = []
    if perms.get(PERM_SEARCH):
        _r1.append(BTN_SEARCH)
    if perms.get(PERM_REMOVE):
        _r1.append(BTN_CUST_REMOVE)
    if _r1:
        rows.append(_r1)
    if perms.get(PERM_ADD_QTY):
        rows.append([BTN_ADD_QTY])
    if perms.get(PERM_INVOICE):
        rows.append([BTN_INVOICE])
    if perms.get(PERM_SUPPLY_ORDER):
        rows.append([BTN_CUST_ORDER])
    _r2 = []
    if perms.get(PERM_MY_ORDERS):
        _r2.append(BTN_CUST_MY_ORDERS)
    if _r2:
        rows.append(_r2)
    if perms.get(PERM_MOVEMENTS):
        rows.append([BTN_CUST_MOVEMENTS])
    rows.append([BTN_CUST_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_external_keyboard():
    """🌍 لوحة البائع الخارجي — زر البحث يظهر حسب صلاحية الفئة
    (نظام الصلاحيات الديناميكي: الإلغاء ظاهر دائماً)."""
    rows = []
    if role_perm_enabled(ROLE_EXTERNAL, PERM_SEARCH):
        rows.append([BTN_SEARCH])
    rows.append([BTN_CUST_CANCEL])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_role_keyboard(telegram_id=None):
    """لوحة الأزرار حسب فئة المستخدم — الفئة تحدد ما يراه (الميزات توجه للفئة المعنية)."""
    role = _get_role(telegram_id) if telegram_id else ROLE_CUSTOMER
    if role in (ROLE_ADMIN, ROLE_MANAGER):
        return get_admin_keyboard(telegram_id)
    if role == ROLE_STOCK:
        return get_stock_keyboard()
    if role == ROLE_SELLER:
        return get_seller_main_keyboard(telegram_id)
    if role == ROLE_EXTERNAL:
        return get_external_keyboard()
    return get_customer_keyboard(telegram_id)


def _role_picker_keyboard(prefix="pickrole"):
    """أزرار اختيار الفئة للمستخدم الجديد (بائع / عامل مخزون / مدير بوت / بائع خارجي)."""
    rows = []
    for r in ROLE_PICK_ORDER:
        rows.append([InlineKeyboardButton(ROLE_LABELS[r], callback_data=prefix + "_" + r)])
    return InlineKeyboardMarkup(rows)


async def ask_add_name(update, context):
    """Step 1: Ask product type (motif or other)."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 موتيف (قطع pcs)", callback_data="addtype_motif")],
        [InlineKeyboardButton("🎨 120/60 (قطع pcs)", callback_data="addtype_12060")],
        [InlineKeyboardButton("🌿 plante (قطع pcs)", callback_data="addtype_plante")],
        [InlineKeyboardButton("📦 باقي السلع (كرتون crt)", callback_data="addtype_other")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="addtype_cancel")],
    ])
    await update.message.reply_text(
        "📦 إضافة سلعة جديدة\n\nاختر نوع السلعة:",
        reply_markup=keyboard,
    )


async def ask_add_qty(update, context, product_name, is_motif=False):
    """Step 2: Ask for quantity."""
    _set_state(context, STATE_ADD_QTY)
    context.user_data["add_name"] = product_name
    context.user_data["add_is_motif"] = is_motif
    unit = "pcs" if is_motif else "crt"
    await update.message.reply_text(
        "\U0001f4e6 " + product_name + "\n"
        "\U0001f4e6 \u0643\u0645 \u0627\u0644\u0639\u062f\u062f (" + unit + ")?",
        reply_markup=get_input_keyboard(),
    )


async def confirm_add_product(update, context, product_name, qty, is_motif=False):
    """Step 3: Show summary with inline confirm button."""
    context.user_data["add_qty"] = qty
    context.user_data["add_is_motif"] = is_motif
    _set_state(context, "waiting_add_confirm")
    unit = "pcs" if is_motif else "crt"
    text = (
        "\U0001f4cb \u062a\u0623\u0643\u064a\u062f \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629:\n\n"
        "\U0001f4e6 \u0627\u0644\u0627\u0633\u0645: " + product_name + "\n"
        "\U0001f4e6 \u0627\u0644\u0639\u062f\u062f: " + str(qty) + " " + unit
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="add_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="add_cancel")],
    ])
    await update.message.reply_text(text, reply_markup=keyboard)


async def callback_addtype_handler(update, context):
    """Handle product type selection (motif vs other)."""
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data
    try:
        if data == "addtype_motif":
            _set_state(context, STATE_ADD_NAME_MOTIF)
            await query.edit_message_text(
                "🎨 موتيف (قطع pcs)\n\nأرسل اسم السلعة:",
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 أرسل اسم السلعة (موتيف):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_12060":
            _set_state(context, STATE_ADD_NAME_12060)
            await query.edit_message_text("🎨 120/60 (قطع pcs)\n\nأرسل اسم السلعة:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🎨 أرسل اسم السلعة (120/60):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_plante":
            _set_state(context, STATE_ADD_NAME_PLANTE)
            await query.edit_message_text("🌿 plante (قطع pcs)\n\nأرسل اسم السلعة:")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="🌿 أرسل اسم السلعة (plante):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_other":
            _set_state(context, STATE_ADD_NAME)
            await query.edit_message_text(
                "📦 باقي السلع (كرتون crt)\n\nأرسل اسم السلعة:",
            )
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📦 أرسل اسم السلعة (باقي السلع):",
                reply_markup=get_input_keyboard(),
            )
        elif data == "addtype_cancel":
            await query.edit_message_text("❌ تم الإلغاء.")
            try:
                await _send_themed(context.bot, query.message.chat_id, "📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
            except Exception:
                pass
    except Exception as e:
        logging.error("[ADDTYPE] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ: " + str(e))
        except Exception:
            pass


async def callback_add_handler(update, context):
    """Handle inline button press for add confirmation."""
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()

    data = query.data
    user_data = context.user_data

    if data == "add_confirm":
        name = user_data.get("add_name", "")
        qty = user_data.get("add_qty", 0)
        try:
            product, created_now = db_add_product(name=name, quantity=qty)
            msg = "\u2705 \u062a\u0645\u062a \u0625\u0636\u0627\u0641\u0629 \u0627\u0644\u0633\u0644\u0639\u0629 \u0628\u0646\u062c\u0627\u062d!\n\n" + format_product_admin(product)
            # If the name already existed the stock just increased —
            # reconcile pending deliveries the new stock now covers
            if not created_now:
                try:
                    reconcile_result = await asyncio.to_thread(
                        auto_reconcile_product, product["id"], qty, None
                    )
                    if reconcile_result and reconcile_result["fulfilled"]:
                        await _send_fulfilled_notifications(context.bot, reconcile_result["fulfilled"])
                except Exception as rec_err:
                    logging.error("[ADD] Reconcile failed: " + str(rec_err))
            await query.edit_message_text(msg)
            await context.bot.send_message(chat_id=query.message.chat_id, text="\u2714\ufe0f", reply_markup=get_admin_keyboard())
            # Auto-broadcast new product notification to all users
            pname = product.get("name", "")
            # (v30) أرشف «سلعة جديدة» في القاعدة لجرس تطبيق الويب أيضاً
            try:
                _actor = ""
                try:
                    _actor = (query.from_user.first_name if query.from_user else "") or ""
                except Exception:
                    _actor = ""
                db_insert_notification("new_item", "🆕 سلعة جديدة!",
                    "📦 " + pname + "\n➕ الكمية: " + str(qty), _actor, "bot")
            except Exception:
                pass
            new_msg = ("\U0001f195 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629!" + chr(10) + chr(10)
                      + "\U0001f4e6 " + pname + chr(10)
                      + "\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(qty))
            await _broadcast_to_all_users(context.bot, new_msg)
        except Exception as e:
            await query.edit_message_text("\u274c \u062d\u062f\u062b \u062e\u0637\u0623: " + str(e))
        user_data.clear()
        return

    if data == "add_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        user_data.clear()
        await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())


async def process_seller_input(update, context, text):
    """Handle optional seller name input, then execute the add/remove."""
    text = text.strip()
    # Handle skip/cancel buttons
    if text == BTN_SKIP:
        seller = None
    elif text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_admin_keyboard(update.effective_user.id if update.effective_user else None))
        return
    else:
        seller = text if text else None

    action = context.user_data.get("seller_action")
    product_id = context.user_data.get("seller_product_id")
    qty = context.user_data.get("seller_qty")
    user_id = context.user_data.get("seller_user_id")

    if not product_id or not action or not user_id:
        context.user_data.clear()
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_admin_keyboard())
        return

    try:
        updated = db_update_quantity(product_id, qty, action)
        # ⚠️ فصل السجل عن الكمية عمداً: لو فشل log_transaction بعد نجاح الكمية
        # كان المسار القديم يعيد طلب العملية المركّبة كاملة → ازدواج في المخزون عند الإعادة.
        # الآن: الكمية سليمة، والسجل الفاشل فقط هو ما يدخل الطابور.
        try:
            tx = log_transaction(product_id, action, qty, user_id, reason=seller)
            tx_id = tx["id"] if tx else None
        except Exception as tx_err:
            logging.error("[TX] Log failed while qty already applied: " + str(tx_err))
            queue_operation(
                "log_transaction",
                {"product_id": product_id, "type": action, "quantity": qty,
                 "user_id": user_id, "reason": seller},
                description="سجل حركة معلّق",
            )
            tx_id = None
        _invalidate_product_cache()
        unit = _get_unit_code(updated["name"])
        if action == "add":
            msg = "✅ تمت إضافة " + str(qty) + " " + unit + " لـ " + updated["name"]
        else:
            msg = "✅ تم إخراج " + str(qty) + " " + unit + " من " + updated["name"]
            # Check if this created a deficit (negative stock)
            old_qty = updated.get("_old_qty", 0)
            new_qty = updated["quantity"]
            additional_deficit = max(0, -new_qty) - max(0, -old_qty)
            if additional_deficit > 0:
                try:
                    create_pending_delivery(user_id, product_id, additional_deficit)
                    msg += "\n\n⚠️ الكمية أصبحت سالبة — تم تسجيل " + str(additional_deficit) + " وحدة كطلب معلق"
                except Exception as e:
                    logging.error("[PENDING] Failed to create: " + str(e))
        if action == "add":
            # (v19) تسوية تلقائية: فور زيادة المخزون غطِّ المنتظرين وأشعِرهم
            try:
                rec = await asyncio.to_thread(auto_reconcile_product, product_id, qty, user_id)
                if rec and rec["fulfilled"]:
                    await _send_fulfilled_notifications(context.bot, rec["fulfilled"])
                    msg += ("\n🔔 تم إشعار " + str(len(rec["fulfilled"]))
                            + " زبون منتظر بأن السلعة صارت متوفرة ("
                            + str(rec["total_covered"]) + " وحدة)")
            except Exception as rec_err:
                logging.error("[PENDING] Reconcile after add failed: " + str(rec_err))
        if seller:
            msg += "\n👤 البائع: " + seller
        msg += "\n\n" + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
        await send_low_stock_alerts(context.bot)
        # Ask reason for notification
        db_user = get_user(update.effective_user.id)
        if db_user:
            if action == "add":
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚚 وصول شاحنة", callback_data="addnotify_truck"),
                     InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="addnotify_return")],
                    [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="addnotify_correction")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id, "seller": seller}
                await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
            else:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="remnotify_return"),
                     InlineKeyboardButton("🚚 بيع لزبون", callback_data="remnotify_sale")],
                    [InlineKeyboardButton("🏪 بيع لرابح Saoula", callback_data="remnotify_saoula"),
                     InlineKeyboardButton("🏪 بيع لحسان Carthago", callback_data="remnotify_carthago")],
                    [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="remnotify_correction")],
                ])
                context.user_data["pending_rem_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id, "seller": seller}
                await update.message.reply_text("📤 ما سبب الإخراج؟", reply_markup=notify_kb)
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        # ── Offline fallback: queue operation for retry ──
        logging.error("[OFFLINE] DB write failed: " + str(e))
        _desc = ("\u0625\u0636\u0627\u0641\u0629" if action == "add" else "\u0625\u062e\u0631\u0627\u062c") + " " + str(qty) + " \u0648\u062d\u062f\u0629"
        queue_operation(action + "_qty", {"product_id": product_id, "qty": qty, "user_id": user_id, "reason": seller}, description=_desc)
        _invalidate_product_cache()
        await update.message.reply_text("\u26a0\ufe0f \u062a\u0645 \u062d\u0641\u0638 \u0627\u0644\u0639\u0645\u0644\u064a\u0629 \u0648\u0633\u064a\u062a\u0645 \u062a\u0646\u0641\u064a\u0630\u0647\u0627 \u062a\u0644\u0642\u0627\u0626\u064a\u0627\u064b.\n\n\U0001f4cb \u0627\u0644\u0639\u0645\u0644\u064a\u0627\u062a \u0627\u0644\u0645\u0639\u0644\u0642\u0629: " + str(get_offline_queue_count()), reply_markup=get_admin_keyboard())
        context.user_data.clear()
    # Don't clear if pending notify needs user_data
    if not context.user_data.get("pending_add_notify") and not context.user_data.get("pending_rem_notify"):
        context.user_data.clear()


async def process_add_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 25",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    # Ask for optional seller name before executing
    context.user_data["state"] = STATE_WAITING_SELLER
    context.user_data["seller_action"] = "add"
    context.user_data["seller_product_id"] = product_id
    context.user_data["seller_qty"] = qty
    context.user_data["seller_user_id"] = db_user["id"]
    await update.message.reply_text(
        "\U0001f3f4 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0628\u0627\u0626\u0639 (\u0627\u062e\u062a\u064a\u0627\u0631\u064a):",
        reply_markup=get_seller_keyboard(),
    )


async def process_remove_quantity(update, context, text, telegram_id):
    parsed = parse_qty_input(text)
    if not parsed:
        await update.message.reply_text(
            "\u274c \u0627\u0644\u062a\u0646\u0633\u064a\u0642: \u0631\u0642\u0645_\u0627\u0644\u0633\u0644\u0639\u0629, \u0627\u0644\u0639\u062f\u062f\n\u0645\u062b\u0627\u0644: 1, 10",
            reply_markup=get_input_keyboard(),
        )
        return
    product_id, qty = parsed
    db_user = get_user(telegram_id)
    if not db_user:
        await update.message.reply_text("\u274c \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u062d\u0633\u0627\u0628\u0643.", reply_markup=get_input_keyboard())
        return
    # Ask for optional seller name before executing
    context.user_data["state"] = STATE_WAITING_SELLER
    context.user_data["seller_action"] = "remove"
    context.user_data["seller_product_id"] = product_id
    context.user_data["seller_qty"] = qty
    context.user_data["seller_user_id"] = db_user["id"]
    await update.message.reply_text(
        "\U0001f3f4 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0628\u0627\u0626\u0639 (\u0627\u062e\u062a\u064a\u0627\u0631\u064a):",
        reply_markup=get_seller_keyboard(),
    )


async def process_edit_product(update, context, text, telegram_id):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2 or not parts[0].isdigit():
        err_msg = ("❌ التنسيق: رقم_السلعة, الاسم الجديد"
            + chr(10) + chr(10)
            + "مثال: 1, زيت 5 لتر")
        await update.message.reply_text(err_msg, reply_markup=get_input_keyboard())
        return
    product_id = int(parts[0])
    try:
        product_id = _resolve_product_id(context, product_id, "edit_id_map")
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
        return
    new_name = parts[1]
    if not new_name:
        await update.message.reply_text("❌ أرسل الاسم الجديد.", reply_markup=get_input_keyboard())
        return
    try:
        updated = db_update_product(product_id, name=new_name)
        msg = "✅ تم تعديل الاسم بنجاح!"
        msg = msg + chr(10) + chr(10) + format_product_admin(updated)
        await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    except ValueError as e:
        await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
    except Exception as e:
        await update.message.reply_text("❌ حدث خطأ: " + str(e), reply_markup=get_input_keyboard())
    context.user_data.clear()

async def process_delete_product(update, context, text, telegram_id):
    """Process delete: supports single (5) or bulk (5, 10, 15) or range (360-362)."""
    text = text.strip()
    if not text:
        await update.message.reply_text(
            "❌ أرسل رقم السلعة.\nمثال: 5 أو 5, 10, 15 أو 360-362",
            reply_markup=get_input_keyboard(),
        )
        return

    # Parse display numbers from input
    display_nums = set()
    import re as _re
    range_match = _re.findall(r"(\d+)\s*-\s*(\d+)", text)
    for start_s, end_s in range_match:
        for i in range(int(start_s), int(end_s) + 1):
            display_nums.add(i)
    comma_parts = _re.split(r"[,\s]+", text)
    for part in comma_parts:
        part = part.strip()
        if part.isdigit():
            display_nums.add(int(part))

    if not display_nums:
        await update.message.reply_text(
            "❌ أرسل رقم صحيح.\nمثال: 5 أو 5, 10, 15 أو 360-362",
            reply_markup=get_input_keyboard(),
        )
        return

    # Resolve display numbers to real DB IDs
    delete_map = context.user_data.get("delete_id_map", {})
    to_delete = []
    not_found = []
    for dn in sorted(display_nums):
        real_id = delete_map.get(str(dn))
        if real_id:
            to_delete.append(real_id)
        else:
            not_found.append(dn)

    if not to_delete:
        msg = "❌ لا توجد سلع بهذه الأرقام."
        if not_found:
            msg += "\nأرقام غير موجودة: " + ", ".join(str(x) for x in not_found)
        await update.message.reply_text(msg, reply_markup=get_input_keyboard())
        return

    # Delete all
    deleted_count, _ = db_bulk_delete_products(to_delete)
    if len(to_delete) == 1:
        msg = "✅ تم حذف سلعة واحدة بنجاح."
    else:
        msg = "✅ تم حذف " + str(deleted_count) + " سلعة بنجاح."
    await update.message.reply_text(msg, reply_markup=get_admin_keyboard())
    context.user_data.clear()


async def process_search(update, context, text, admin_view=False):
    if not text:
        await update.message.reply_text(
            "\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.",
            reply_markup=get_input_keyboard(),
        )
        return
    results = search_products(text)
    if not results:
        if admin_view:
            msg = ("\U0001f50d لم يتم العثور على نتائج لـ " + text)
        else:
            msg = ("🔍 العنصر غير موجود." + chr(10) + chr(10)
                + "💡 البحث الذكي يفهم الأخطاء الإملائية ويعامل / و × بشكل متساوٍ." + chr(10)
                + "جرّب البحث بكلمة واحدة أقصر (مثلاً: ceram أو 60x60 أو اسم المصنع).")
        reply_kb = get_input_keyboard() if admin_view else None
        await update.message.reply_text(msg, reply_markup=reply_kb)
        return
    if admin_view:
        header = "\U0001f50d \u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0628\u062d\u062b \u0639\u0646 " + text + " (" + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629):\n"
        lines = [header]
        for p in results:
            lines.append("  #" + str(p["id"]) + " " + format_product_admin(p))
            lines.append("")
        await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())
    else:
        # 🌍 البائع الخارجي: نتائج التوفر فقط (متوفرة/غير متوفرة) دون كميات
        try:
            _ps_role = _get_role(update.effective_user.id)
        except Exception:
            _ps_role = ROLE_CUSTOMER
        if _ps_role == ROLE_EXTERNAL:
            if len(results) == 1:
                p = results[0]
                mark = "✅ متوفرة حالياً" if p["quantity"] > 0 else "❌ غير متوفرة حالياً"
                await update.message.reply_text(p["name"] + "\n\n" + mark)
            else:
                _cap = 30  # v78: نفس سقف الأمان لنتائج البائع الخارجي
                _shown = results[:_cap]
                lines = ["🔍 نتائج البحث عن " + text + " (" + str(len(results)) + "):", ""]
                buttons = []
                for p in _shown:
                    mark = "✅" if p["quantity"] > 0 else "❌"
                    lines.append(mark + " " + p["name"])
                    label = p["name"]
                    if len(label) > 40:
                        label = label[:37] + "..."
                    buttons.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
                if len(results) > _cap:
                    lines.append("ℹ️ توجد نتائج أخرى — دقّق البحث لعرضها.")
                lines.append("👆 اضغط على السلعة لمعرفة توفرها.")
                await update.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
            context.user_data.clear()
            return
        if len(results) == 1:
            p = results[0]
            await _show_product_detail(update, context, p)
        else:
            _cap = 30  # v78: حد تليغرام 4096 حرف — بحث واسع كان يفجّر «Message is too long»
            _shown = results[:_cap]
            header = "🔍 وجدت " + str(len(results)) + " منتجات:\n"
            if len(results) > _cap:
                header = ("🔍 وجدت " + str(len(results)) + " منتجات — أعرض أول " + str(_cap)
                          + " فقط (دقّق البحث لرؤية البقية):\n")
            text_lines = [header]
            buttons = []
            for p in _shown:
                qty = p["quantity"]
                unit_ar = _get_unit_ar(p["name"])
                text_lines.append(
                    str(p["id"]) + ". " + p["name"]
                    + "\n   \U0001f4e6 " + str(qty) + " " + unit_ar
                )
                text_lines.append("")
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                buttons.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            text_lines.append("👆 اختر المنتج المطلوب.")
            await update.message.reply_text(
                "\n".join(text_lines),
                reply_markup=InlineKeyboardMarkup(buttons),
            )
    context.user_data.clear()


# ═══════════════════════════════════════════════════════════

def _product_status_icon(product):
    qty = product["quantity"]
    min_stock = product["minimum_stock"]
    if qty > min_stock:
        return "🟢"
    elif qty > 0:
        return "🟡"
    else:
        return "🔴"

def _is_pcs_product(name):
    """Check if product should use pcs unit."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return (n.startswith("motif") or n.startswith("plante")
            or n.startswith("120/60") or n.startswith("120x60")
            or n.startswith("120×60"))


def _invoice_product_type(name):
    """Detect invoice product type from name. Returns:
       'm2'    -> sold by m² with box area (باقي السلع)
       'pcs'   -> sold by piece with unit price (motif, 120/60)
       'plant' -> sold by linear meter with length choice (plante)
    """
    if not name or not isinstance(name, str):
        return "m2"
    n = name.strip().lower()
    if n.startswith("motif") or n.startswith("120/60") or n.startswith("120x60") or n.startswith("120×60"):
        return "pcs"
    if n.startswith("plante"):
        return "plant"
    return "m2"


def _get_unit_code(product_name):
    """Return unit code: 'pcs' for motif/plante/120x60 products, 'crt' for others."""
    return "pcs" if _is_pcs_product(product_name) else "crt"

def _get_unit_ar(product_name):
    """Return Arabic unit label."""
    return "قطعة" if _is_pcs_product(product_name) else "كرتون"

async def _show_product_detail(update, context, product):
    p = product
    # 🎯 بوابة الفئات: الخارجي يرى التوفر فقط دون كميات أو أزرار،
    # والبائع يرى الكمية مع الإضافة والإخراج والسجل (نفس صلاحيات البيع).
    try:
        _det_role = _get_role(update.effective_user.id)
    except Exception:
        _det_role = ROLE_CUSTOMER
    pid = str(p["id"])
    if _det_role == ROLE_EXTERNAL:
        avail = "✅ متوفرة حالياً" if p["quantity"] > 0 else "❌ غير متوفرة حالياً"
        ext_text = p["name"] + "\n\n" + avail
        if hasattr(update, "callback_query") and update.callback_query:
            await update.callback_query.edit_message_text(ext_text)
        else:
            await update.message.reply_text(ext_text)
        return
    icon = _product_status_icon(p)
    qty = p["quantity"]
    unit_ar = _get_unit_ar(p["name"])
    text = p["name"] + "\n\n" + "📦 الكمية: " + str(qty) + " " + unit_ar + " " + icon
    # 🔐 أزرار البطاقة تُبنى حسب صلاحيات الفئة (الإداريون والعميل: كما هو)
    if _det_role == ROLE_SELLER:
        _perms = get_role_perms(ROLE_SELLER)
        buttons = []
        _row = []
        if _perms.get(PERM_ADD_QTY):
            _row.append(InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid))
        if _perms.get(PERM_REMOVE):
            _row.append(InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid))
        if _row:
            buttons.append(_row)
        if _perms.get(PERM_MOVEMENTS):
            buttons.append([InlineKeyboardButton("📜 سجل الحركات", callback_data="pact_log_" + pid)])
        if not buttons:
            buttons = None
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
        return
    if _det_role == ROLE_STOCK:
        _perms = get_role_perms(ROLE_STOCK)
        buttons = []
        _row = []
        if _perms.get(PERM_ADD_QTY):
            _row.append(InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid))
        if _perms.get(PERM_REMOVE):
            _row.append(InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid))
        if _row:
            buttons.append(_row)
        if _perms.get(PERM_MOVEMENTS):
            buttons.append([InlineKeyboardButton("📜 سجل الحركات", callback_data="pact_log_" + pid)])
        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)
        return
    buttons = [
        [
            InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
            InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid),
        ],
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


def _build_movements_text(rows):
    """Build formatted movements text from transaction rows. Shows reason, operator, and time."""
    if not rows:
        return None
    lines = ["📋 حركة اليوم (" + str(len(rows)) + "):", ""]
    for r in rows:
        r = dict(r)
        try:
            dt = r.get("date")
            time_str = dt.strftime("%H:%M") if dt else "??"
            prod_raw = str(r.get("product_name") or "")
            prod = html.escape(prod_raw)
            qty = r["quantity"]
            unit = _get_unit_code(prod_raw)
            # Build user display — use first_name as fallback when username is None
            user_tid = r.get("user_tid")
            user_name = str(r.get("user_name") or "").strip()
            user_fname = str(r.get("user_fname") or "").strip()
            display_name = user_name if user_name else (user_fname if user_fname else "مجهول")
            display_name_escaped = html.escape(display_name)
            if user_tid:
                user_link = "<a href=\"tg://user?id=" + str(user_tid) + "\">" + display_name_escaped + "</a>"
            else:
                user_link = display_name_escaped
            # Show reason (operation cause) if present
            reason = r.get("reason")
            reason_part = ""
            if reason:
                reason_part = " — " + html.escape(str(reason))
            if r["tx_type"] == "add":
                # Green for additions
                line = "🟢 " + prod + " +" + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
            elif r["tx_type"] == "reconcile":
                # Blue for reconciliation
                line = "🔵 " + prod + " -" + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
            else:
                # Blue 🔵 if this product has unfulfilled pending deliveries (deficit),
                # red 🔴 otherwise.
                has_pending_deficit = r.get("has_pending_deficit")
                if has_pending_deficit:
                    line = "🔵 " + prod + " -" + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
                else:
                    line = "🔴 " + prod + " " + str(qty) + " " + unit + reason_part + " — " + user_link + " " + time_str
            lines.append(line)
        except Exception as e:
            logging.error("[BUILD_MOV] Error on row: " + str(e) + " row=" + str(r))
            lines.append("❌ خطأ في عرض عملية")

    add_count = sum(1 for r in rows if r["tx_type"] == "add")
    rem_count = sum(1 for r in rows if r["tx_type"] == "remove")
    pending_count = sum(1 for r in rows if r.get("has_pending_deficit") and r["tx_type"] == "remove")
    summary = chr(10) + "🟢 " + str(add_count) + " إضافة  |  🔴 " + str(rem_count) + " إخراج"
    if pending_count > 0:
        summary += "  |  🔵 " + str(pending_count) + " طلب معلق"
    lines.append(summary)
    return chr(10).join(lines)

def _fetch_movements_by_date(target_date):
    """Fetch movements for a specific date from DB."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.id AS tx_id, p.name AS product_name, "
                "u.username AS user_name, u.first_name AS user_fname, u.telegram_id AS user_tid, t.reason, "
                "(EXISTS(SELECT 1 FROM pending_deliveries pd WHERE pd.product_id = t.product_id AND pd.pending_qty > 0)) AS has_pending_deficit "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= %s AND t.date < %s AND t.type != 'reconcile' AND (t.reason IS NULL OR t.reason NOT LIKE '%%تصحيح مخزون%%') "
                "ORDER BY t.date DESC",
                (target_date, target_date + datetime.timedelta(days=1)))
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

def _fetch_movements_by_range(start_date, end_date):
    """Fetch movements between start_date (inclusive) and end_date (inclusive).
    end_date is treated as end-of-day (so end_date+1 day exclusive upper bound).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.type AS tx_type, t.quantity, t.date, t.id AS tx_id, p.name AS product_name, "
                "u.username AS user_name, u.first_name AS user_fname, u.telegram_id AS user_tid, t.reason, "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.date >= %s AND t.date < %s AND t.type != 'reconcile' AND (t.reason IS NULL OR t.reason NOT LIKE '%%تصحيح مخزون%%') "
                "ORDER BY t.date DESC",
                (start_date, end_date + datetime.timedelta(days=1)))
            col_names = [desc[0] for desc in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)

async def _show_all_movements(update, context, admin_view=True):
    """Show date picker for last 7 days - user picks a date to see movements."""
    try:
        today = datetime.date.today()
        buttons = []
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        for d in range(6, -1, -1):
            day = today - datetime.timedelta(days=d)
            wd = day.weekday()
            if wd == 4:  # skip Friday
                continue
            day_str = day.strftime("%Y-%m-%d")
            if d == 0:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
            else:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m")
            buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])

        # Add custom range button at the bottom
        buttons.append([InlineKeyboardButton("📅 نطاق مخصص (من - إلى)", callback_data="movd_range")])
        # Add product-specific movement button
        buttons.append([InlineKeyboardButton("📦 حركة سلعة محددة", callback_data="movd_product")])
        text = "📋 اختار طريقة عرض الحركة:"
        markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(text, reply_markup=markup)
    except Exception as e:
        logging.error("[MOVEMENTS_DATE] Error: " + str(e))
        kb = get_role_keyboard(update.effective_user.id)
        await update.message.reply_text("❌ حدث خطأ في عرض الحركات.", reply_markup=kb)


async def callback_mov_product_handler(update, context):
    """Handle product selection from movement product picker."""
    query = update.callback_query
    # 🎯 البائع الخارجي: الحركة غير متاحة لفئته (بحث فقط)
    if _get_role(query.from_user.id) == ROLE_EXTERNAL:
        await query.answer("❌ هذا الإجراء غير متاح لفئتك.", show_alert=True)
        return
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    try:
        product_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    if not row:
        await query.edit_message_text("❌ السلعة غير موجودة.")
        return
    product = dict(row)
    await query.edit_message_text(
        "⏳ جارٍ تحميل حركات " + product["name"] + "..."
    )
    try:
        txns = get_product_transactions(product["id"], limit=200)
        txns = _filter_test_pairs(txns)
        unit = _get_unit_ar(product["name"])
        if not txns:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="📜 لا توجد حركات مسجلة لـ" + chr(10) + product["name"],
            )
            return
        lines = ["📜 سجل حركات — " + product["name"] + " (" + str(len(txns)) + ")"]
        for tx in txns:
            icon = "\U0001f7e2" if tx["tx_type"] == "add" else "\U0001f534"
            date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
            op = tx.get("operator_name") or "مجهول"
            sign = "+" if tx["tx_type"] == "add" else "-"
            reason = tx.get("reason") or ""
            if reason:
                reason = " | " + reason
            lines.append(
                icon + " " + date_str + " — " + sign + str(tx["quantity"]) + " " + unit
                + " | " + op + reason
            )
        text_out = "\n".join(lines)
        if len(text_out) > 4000:
            parts = []
            current = [lines[0]]
            for ln in lines[1:]:
                if len("\n".join(current)) + len("\n" + ln) > 3900:
                    parts.append("\n".join(current))
                    current = ["📜 تابع حركات — " + product["name"]]
                current.append(ln)
            if current:
                parts.append("\n".join(current))
            for part in parts:
                await context.bot.send_message(chat_id=query.message.chat_id, text=part)
        else:
            await context.bot.send_message(chat_id=query.message.chat_id, text=text_out)
    except Exception as e:
        logging.error("[MOVP] Error: " + str(e), exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="❌ حدث خطأ في تحميل الحركات: " + str(e),
        )


async def callback_mov_date_handler(update, context):
    """Handle date selection from movements date picker."""
    query = update.callback_query
    # 🎯 البائع الخارجي: الحركة غير متاحة لفئته (بحث فقط)
    if _get_role(query.from_user.id) == ROLE_EXTERNAL:
        await query.answer("❌ هذا الإجراء غير متاح لفئتك.", show_alert=True)
        return
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data  # "movd_2025-01-15"

    # ── Custom range entry: prompt user for from-date ──
    if data == "movd_range":
        context.user_data.clear()
        context.user_data["state"] = "waiting_mov_range_from"
        await query.edit_message_text(
            "📅 نطاق مخصص للحركة\n\n"
            "اكتب تاريخ البداية بالصيغة:\n"
            "<code>YYYY-MM-DD</code> (مثال: 2026-08-01)\n\n"
            "أو أرسل ❌ للإلغاء.",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Product-specific movement: ask for product name ──
    if data == "movd_product":
        context.user_data.clear()
        context.user_data["state"] = "waiting_mov_product"
        await query.edit_message_text(
            "📦 حركة سلعة محددة\n\n"
            "اكتب اسم أو جزء من اسم السلعة:\n\n"
            "أو أرسل ❌ للإلغاء.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="movd_back")]
            ])
        )
        return

    # Back button
    if data == "movd_back":
        today = datetime.date.today()
        buttons = []
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        for d in range(6, -1, -1):
            day = today - datetime.timedelta(days=d)
            wd = day.weekday()
            if wd == 4:  # skip Friday
                continue
            day_str = day.strftime("%Y-%m-%d")
            if d == 0:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m") + " (اليوم)"
            else:
                label = "📅 " + weekday_ar.get(wd, "") + " " + day.strftime("%d/%m")
            buttons.append([InlineKeyboardButton(label, callback_data="movd_" + day_str)])
        await query.edit_message_text(
            "📋 اختار التاريخ لعرض الحركة:",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    date_str = data.replace("movd_", "")
    try:
        target_date = datetime.date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text("❌ تاريخ غير صالح.")
        return

    try:
        rows = _fetch_movements_by_date(target_date)
        rows = _filter_test_pairs(rows)
        weekday_ar = {
            0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء",
            3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"
        }
        wd = target_date.weekday()
        date_display = weekday_ar.get(wd, "") + " " + target_date.strftime("%d/%m/%Y")

        if not rows:
            await query.edit_message_text(
                "📋 حركة " + date_display + ":\n\nلا توجد عمليات في هذا اليوم.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]
                ]))
            return

        text = _build_movements_text(rows)
        # Replace header to show date
        header = "📋 حركة " + date_display + " (" + str(len(rows)) + "):"
        body_lines = text.split("\n")[2:]  # skip old header and blank line
        text = header + "\n" + "\n".join(body_lines)

        back_buttons = [[InlineKeyboardButton("🔙 رجوع للتواريخ", callback_data="movd_back")]]
        # Add delete-last-movement button for admin only
        caller_id = query.from_user.id
        if is_admin(caller_id) and rows:
            back_buttons.append([InlineKeyboardButton("🗑 حذف آخر حركتي", callback_data="movp_del_last")])
        back_button = InlineKeyboardMarkup(back_buttons)

        # Try HTML first, fallback to plain text
        try:
            if len(text) <= 4096:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button)
            else:
                await query.edit_message_text(text[:3900], parse_mode=ParseMode.HTML, reply_markup=back_button)
                remaining = text[3900:]
                while remaining:
                    chunk = remaining[:4096]
                    remaining = remaining[4096:]
                    await context.bot.send_message(
                        chat_id=(query.message.chat_id if query.message else query.from_user.id),
                        text=chunk,
                        parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error("[MOV_DATE_HANDLER] HTML send failed: " + str(e))
            # Fallback: strip HTML and send as plain text
            plain = re.sub(r'<[^>]+>', '', text)
            try:
                if len(plain) <= 4096:
                    await query.edit_message_text(plain, reply_markup=back_button)
                else:
                    await query.edit_message_text(plain[:4096], reply_markup=back_button)
            except Exception as e2:
                logging.error("[MOV_DATE_HANDLER] Fallback also failed: " + str(e2))
                await query.edit_message_text("❌ حدث خطأ في عرض الحركات.")
    except Exception as e:
        logging.error("[MOV_DATE_HANDLER] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ في عرض الحركات.")
        except Exception:
            pass


async def callback_mov_delete_handler(update, context):
    """Handle admin request to delete last own movement."""
    query = update.callback_query
    await query.answer()
    data = query.data
    caller_id = query.from_user.id

    if not is_admin(caller_id):
        await query.answer("❌ هذا الخيار للمدير فقط.", show_alert=True)
        return

    if data == "movp_del_last":
        # Show confirmation with last transaction details
        tx = get_admin_last_transaction(caller_id)
        if not tx:
            await query.answer("❌ لا توجد حركات لك.", show_alert=True)
            return
        tx_type_ar = "إضافة" if tx["tx_type"] == "add" else "إخراج"
        reason_txt = " (" + tx.get("reason", "") + ")" if tx.get("reason") else ""
        confirm_text = (
            "⚠️ تأكيد حذف آخر حركة:\n\n"
            "📦 " + str(tx["product_name"]) + "\n"
            "🏷 " + tx_type_ar + ": " + str(tx["quantity"]) + "\n"
            + reason_txt + "\n\n"
            "سيتم عكس الكمية تلقائياً.\n"
            "هل تريد الحذف؟"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، احذف", callback_data="movp_del_confirm_" + str(tx["tx_id"]))],
            [InlineKeyboardButton("❌ إلغاء", callback_data="movp_del_cancel")]
        ])
        try:
            await query.edit_message_text(confirm_text, reply_markup=markup)
        except Exception:
            await query.message.reply_text(confirm_text, reply_markup=markup)
        return

    if data.startswith("movp_del_confirm_"):
        try:
            tx_id = int(data.split("_")[-1])
        except (IndexError, ValueError):
            await query.answer("❌ خطأ في رقم العملية.", show_alert=True)
            return
        success, msg = delete_transaction(tx_id, caller_id)
        await query.answer(msg, show_alert=True)
        if success:
            await query.edit_message_text(msg)
        return

    if data == "movp_del_cancel":
        await query.edit_message_text("✅ تم الإلغاء.")
        return


async def callback_product_select_handler(update, context):
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data
    try:
        product_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطأ في الاختيار.")
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        await _show_product_detail(update, context, dict(row))
    except Exception as e:
        logging.error("[PSEL] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass


async def callback_product_action_handler(update, context):
    query = update.callback_query
    # 🎯 بوابة الفئات: الخارجي ممنوع نهائياً؛ البائع يضيف ويخرج مثل مسار البيع
    _pact_role = _get_role(query.from_user.id)
    if _pact_role == ROLE_EXTERNAL:
        await query.answer("❌ هذا الإجراء غير متاح لفئتك.", show_alert=True)
        return
    # 🔐 الصلاحيات الديناميكية: البائع/عامل المخزون يمرّ فقط بما هو مفعّل لفئته
    if _pact_role in (ROLE_SELLER, ROLE_STOCK):
        _pact_need = {"add": PERM_ADD_QTY, "rem": PERM_REMOVE, "log": PERM_MOVEMENTS}.get(
            query.data.split("_", 2)[1] if "_" in query.data else ""
        )
        if _pact_need and not role_perm_enabled(_pact_role, _pact_need):
            await query.answer("❌ هذا الإجراء موقف لفئتك — راجع المدير.", show_alert=True)
            return
    _KB_ROLE_UID.set(query.from_user.id)
    await query.answer()
    data = query.data
    parts = data.split("_", 2)
    if len(parts) != 3:
        return
    action = parts[1]
    try:
        product_id = int(parts[2])
    except ValueError:
        return
    if action == "log":
        try:
            txns = get_product_transactions(product_id, limit=30)
            txns = _filter_test_pairs(txns)
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT name FROM products WHERE id = %s", (product_id,))
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)
            product_name = dict(row)["name"] if row else "?"
            unit = _get_unit_ar(product_name)
            if not txns:
                text = "📜 لا توجد حركات مسجلة لـ" + product_name
            else:
                tx_lines = ["📜 سجل حركات — " + product_name + " (" + str(len(txns)) + ")"]
                for tx in txns:
                    icon = "🟢" if tx["tx_type"] == "add" else "🔴"
                    date_str = tx["date"].strftime("%Y-%m-%d %H:%M")
                    op = tx.get("operator_name") or "مجهول"
                    sign = "+" if tx["tx_type"] == "add" else "-"
                    reason = tx.get("reason") or ""
                    if reason:
                        reason = " | " + reason
                    tx_lines.append(
                        icon + " " + date_str + " — " + sign + str(tx["quantity"]) + " " + unit
                        + " | " + op + reason
                    )
                text = "\n".join(tx_lines)
        except Exception as e:
            logging.error("[PACT_LOG] Error: " + str(e))
            try:
                await query.edit_message_text("❌ خطأ في تحميل الحركة.")
            except Exception:
                pass
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, name, quantity FROM products WHERE id = %s", (product_id,))
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_connection(conn)
        if not row:
            await query.edit_message_text("❌ المنتج غير موجود.")
            return
        product = dict(row)
        context.user_data["cust_product"] = product
        if action == "add":
            unit = _get_unit_code(product["name"])
            unit_ar = _get_unit_ar(product["name"])
            text = "\U0001f4e6 " + product["name"] + "\n\n" + "\u2795 \u0625\u0636\u0627\u0641\u0629 " + unit_ar + "\n\n" + "\u0623\u0631\u0633\u0644 \u0627\u0644\u0639\u062f\u062f (" + unit + "):"
        else:
            unit = _get_unit_code(product["name"])
            unit_ar = _get_unit_ar(product["name"])
            text = ("\U0001f4e6 " + product["name"]
                + " (\u0627\u0644\u0645\u062a\u0648\u0641\u0631: " + str(product["quantity"]) + " " + unit + ")\n\n"
                + "\u2796 \u0625\u062e\u0631\u0627\u062c " + unit_ar + "\n\n"
                + "\u0623\u0631\u0633\u0644 \u0627\u0644\u0639\u062f\u062f (" + unit + "):")
        context.user_data["pact_action"] = "remove" if action == "rem" else action
        _set_state(context, "waiting_pact_qty")
        await query.edit_message_text(text)
    except Exception as e:
        logging.error("[PACT] Error: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass


#  SHOW PRODUCTS LIST
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
#  تنظيف الشاشة التلقائي — AUTO SCREEN SWEEP
#  عند الضغط على "القائمة الرئيسية" (admin_done) أو "تحديث
#  الواجهة" (go_start) أو إرسال /start تُحذف كل رسائل المهمة
#  السابقة (رسائل البوت + رسائل المستخدم) لئلا تمتلئ الشاشة.
#  ملاحظات:
#  - الفواتير (المستندات) تبقى ظاهرة عمداً: send_document غير متتبع.
#  - تيليجرام يمنع حذف الرسائل الأقدم من 48 ساعة → تُتجاهل بصمت.
#  - التنظيف يعمل في الخاص فقط (المجموعات تحتاج صلاحيات إدارة).
# ═══════════════════════════════════════════════════════════
from collections import deque as _sweep_deque

_SWEEP_BOT_MSGS = {}                    # chat_id -> deque معرّفات رسائل البوت الصادرة
_SWEEP_USER_KEY = "_sweep_user_msgs"    # مفتاح chat_data: معرّفات رسائل المستخدم
_SWEEP_MAX_TRACKED = 160                # سقف الذاكرة لكل محادثة

# الدوال المغلَّفة للتتبع — send_document مستثناة عمداً لتبقى الفواتير ظاهرة
_SWEEP_TRACK_METHODS = (
    "send_message", "send_photo", "send_animation", "send_video",
    "send_audio", "send_voice", "send_media_group", "copy_message",
)


def _sweep_track(chat_id, message_id):
    """تسجيل رسالة أرسلها البوت كي يمكن حذفها لاحقاً عند تنظيف الشاشة."""
    try:
        if chat_id is None or message_id is None:
            return
        dq = _SWEEP_BOT_MSGS.setdefault(
            int(chat_id), _sweep_deque(maxlen=_SWEEP_MAX_TRACKED)
        )
        dq.append(int(message_id))
    except Exception:
        pass


def _install_sweep_tracking(bot):
    """تغليف دوال الإرسال في كائن البوت: كل رسالة صادرة تُسجَّل تلقائياً.

    يعمل مع كل طرق الإرسال (send_message/send_photo/...) بما فيها
    الاختصارات مثل message.reply_text لأنها تستدعي bot.send_message داخلياً.
    """
    class _TrackedSend:
        """غلاف قابل للاستدعاء: يستدعي الدالة الأصلية ثم يسجل معرّف الرسالة."""
        __slots__ = ("_orig",)

        def __init__(self, orig_fn):
            self._orig = orig_fn

        async def __call__(self, *args, **kwargs):
            res = await self._orig(*args, **kwargs)
            try:
                chat_id = kwargs.get("chat_id")
                if chat_id is None and args:
                    chat_id = args[0]
                if hasattr(res, "message_id"):
                    _sweep_track(chat_id, res.message_id)
                elif isinstance(res, (list, tuple)):
                    for _m in res:
                        if hasattr(_m, "message_id"):
                            _sweep_track(chat_id, _m.message_id)
            except Exception:
                pass
            return res

    for name in _SWEEP_TRACK_METHODS:
        orig = getattr(bot, name, None)
        if orig is None or not callable(orig):
            continue
        try:
            setattr(bot, name, _TrackedSend(orig))
        except Exception:
            pass


async def _sweep_safe_delete(bot, chat_id, message_id):
    """حذف رسالة واحدة بأمان (يتجاهل: أقدم من 48 ساعة / محذوفة سابقاً)."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception:
        return False


async def sweep_screen(update, context):
    """تنظيف الشاشة: حذف كل الرسائل المتتبعة في هذه المحادثة
    (رسائل البوت + رسائل المستخدم) قبل عرض قائمة رئيسية نظيفة.
    يعمل في الخاص فقط ويفشل بصمت دون إفساد تدفق المعالج الرئيسي."""
    try:
        chat = update.effective_chat if update is not None else None
        if chat is None or chat.type != "private":
            return 0
        cid = int(chat.id)
        mids = list(_SWEEP_BOT_MSGS.pop(cid, _sweep_deque()))
        cd = getattr(context, "chat_data", None)
        if isinstance(cd, dict):
            stored = cd.pop(_SWEEP_USER_KEY, None)
            if isinstance(stored, list):
                mids.extend(stored)
        seen, ordered = set(), []
        for m in mids:
            try:
                m = int(m)
            except Exception:
                continue
            if m not in seen:
                seen.add(m)
                ordered.append(m)
        if not ordered:
            return 0
        deleted = 0
        for i in range(0, len(ordered), 8):
            chunk = ordered[i:i + 8]
            results = await asyncio.gather(
                *[_sweep_safe_delete(context.bot, cid, m) for m in chunk]
            )
            deleted += sum(1 for r in results if r)
        logging.info(
            "[SWEEP] chat " + str(cid) + ": deleted "
            + str(deleted) + "/" + str(len(ordered)) + " messages"
        )
        return deleted
    except Exception as sweep_err:
        try:
            logging.debug("[SWEEP] failed: " + str(sweep_err))
        except Exception:
            pass
        return 0


async def _sweep_user_tracker(update, context):
    """متعقب صامت (group=-2): يحفظ معرّفات رسائل المستخدم (نصوص/صور/مستندات)
    كي تُحذف مع رسائل البوت عند تنظيف الشاشة التالي."""
    try:
        _ef_user = getattr(update, "effective_user", None)
        _KB_ROLE_UID.set(_ef_user.id if _ef_user is not None else None)
        msg = update.message
        chat = update.effective_chat
        if msg is None or chat is None or chat.type != "private":
            return
        cd = context.chat_data
        lst = cd.get(_SWEEP_USER_KEY)
        if not isinstance(lst, list):
            lst = []
            cd[_SWEEP_USER_KEY] = lst
        lst.append(msg.message_id)
        if len(lst) > _SWEEP_MAX_TRACKED:
            del lst[:-_SWEEP_MAX_TRACKED]
    except Exception:
        pass


async def start_command(update, context):
    # تنظيف الشاشة: حذف رسائل المهمة السابقة قبل عرض القائمة
    await sweep_screen(update, context)
    # v82: مسح الحالة المعلقة — /start بداية نظيفة كي لا تختطف مهمة قديمة الرسائل التالية
    try:
        context.user_data.clear()
    except Exception:
        pass
    user = update.effective_user
    sync_user_info(user)
    # ── v32: deep link تسجيل من التطبيق: ?start=reg_123456 ──
    try:
        _start_args = list(getattr(context, "args", None) or [])
    except Exception:
        _start_args = []
    if (_start_args and str(_start_args[0]).startswith("reg_")
            and getattr(update.effective_chat, "type", None) == "private"):
        _reg_code = str(_start_args[0])[4:]
        if _reg_code.isdigit() and len(_reg_code) == 6:
            ok, msg = await asyncio.to_thread(_bot_confirm_registration, user, _reg_code)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            if ok:
                try:
                    db_reg = await asyncio.to_thread(_reg_lookup_by_user, user.id)
                    await _notify_admins_registration(
                        context, user,
                        db_reg.get("full_name"), ROLE_LABELS.get(db_reg.get("role"), db_reg.get("role")),
                        db_reg.get("phone"), db_reg.get("email"))
                except Exception as e:
                    logging.error("[REG] post-confirm notify failed: " + str(e))
            return
    # Notify admins about new pending user
    if not is_admin(user.id):
        db_user = get_user(user.id)
        if db_user and db_user.get("role") == "pending":
            uname = user.first_name or user.username or str(user.id)
            for admin_id in ADMIN_IDS:
                try:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ تفعيل", callback_data="approve_" + str(user.id)),
                         InlineKeyboardButton("❌ رفض", callback_data="reject_" + str(user.id))],
                    ])
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text="🔔 طلب انضمام جديد:\n\n👤 " + uname + "\n📧 " + str(user.id),
                        reply_markup=kb)
                    logging.info("[JOIN] Notification sent to admin " + str(admin_id) + " for user " + str(user.id))
                except Exception as notify_err:
                    logging.error("[JOIN] Failed to notify admin " + str(admin_id) + ": " + str(notify_err))
    if is_admin(user.id):
        name = user.first_name or ""
        await _send_themed(context.bot, update.effective_chat.id,
            "\U0001f44b \u0645\u0631\u062d\u0628\u0627\u064b <b>" + name + "</b>\n\n\U0001f3e2 \u0623\u0646\u062a \u0645\u062f\u064a\u0631 \u0627\u0644\u0646\u0638\u0627\u0645.\n\U0001f4cb \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0644\u0625\u062f\u0627\u0631\u0629 \u0627\u0644\u0645\u062e\u0632\u0648\u0646.\n\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\u26a1 \u062a\u0645 \u0627\u0644\u062a\u0637\u0648\u064a\u0631 \u0628\u0648\u0627\u0633\u0637\u0629 <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
            reply_markup=get_admin_keyboard(),
            parse_mode=ParseMode.HTML,
        )
    else:
        name = user.first_name or ""
        db_user = get_user(user.id)
        if db_user and db_user.get("role") == "pending":
            # 🚪 المستخدم الجديد يختار فئته: بائع / عامل مخزون / مدير بوت / بائع خارجي
            await _send_themed(context.bot, update.effective_chat.id,
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "🎯 اختر فئتك في هذا النظام:\n"
                "(سيصل طلبك للمدير مع فئتك لتفعيلها)\n\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=_role_picker_keyboard(),
            )
        else:
            await _send_themed(context.bot, update.effective_chat.id,
                "👋 مرحباً <b>" + name + "</b>\n\n⌛ بحث عن المنتجات المتاحة\n📁 استخدم الأزرار أدناه.\n\n━━━━━━━━━━━━━━━━━━━━━\n⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                reply_markup=get_role_keyboard(user.id),
                parse_mode=ParseMode.HTML,
            )
    context.user_data.clear()


async def help_command(update, context):
    user = update.effective_user
    if is_admin(user.id):
        text = (
            "\U0001f4cb \u0623\u0648\u0627\u0645\u0631 \u0627\u0644\u0625\u062f\u0627\u0631\u0629:\n\n"
            "/start - \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629\n"
            "/help - \u0639\u0631\u0636 \u0647\u0630\u0627 \u0627\u0644\u0645\u0633\u0627\u0639\u062f\n"
            "/add - \u0625\u0636\u0627\u0641\u0629 \u0633\u0644\u0639\u0629 \u062c\u062f\u064a\u062f\u0629\n"
            "/search - \u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n"
            "\U0001f4a1 \u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u064a\u0636\u0627\u064b."
        )
    else:
        text = (
            "\U0001f4cb \u0627\u0644\u0645\u0633\u0627\u0639\u062f\u0629:\n\n"
            "\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0644\u0645\u0639\u0631\u0641\u0629 \u062a\u0648\u0641\u0631\u0647\u0627.\n\n"
            "\u0645\u062b\u0627\u0644:\n  \u0632\u064a\u062a 5 \u0644\u062a\u0631\n  \u0632\u064a\u062a\n  \u062d\u0644\u064a\u0628"
        )
    await update.message.reply_text(text)


async def add_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    await ask_add_name(update, context)


async def search_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("\u274c \u0647\u0630\u0627 \u0627\u0644\u0623\u0645\u0631 \u0644\u0644\u0645\u062f\u064a\u0631\u064a\u0646 \u0641\u0642\u0637.")
        return
    _set_state(context, STATE_SEARCH_ADMIN)
    await update.message.reply_text(
        "\U0001f50d \u0627\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646\n\n\u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0623\u0648 \u062c\u0632\u0621 \u0645\u0646 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629:",
        reply_markup=get_input_keyboard(),
    )


# ═══════════════════════════════════════════════════════════

# ── Whitelist: Approval Handler ───────────────────────

async def callback_approval_handler(update, context):
    """Handle approve/reject for pending users."""
    query = update.callback_query
    # ── AUTH GUARD ── يجب أن يكون المستدعي مديراً قبل أي شيء، وإلا يُرفض الطلب.
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        logging.warning(
            "[USERS] Unauthorized approve/reject attempt by "
            + str(query.from_user.id) + " on data=" + str(query.data)
        )
        return
    await query.answer()
    data = query.data  # "approve_123456" / "reject_123456" / "approveas_seller_123456"
    req_role = None
    if data.startswith("approveas_"):
        # تفعيل بفئة محددة اختارها المستخدم الجديد
        parts = data.split("_", 2)
        if len(parts) != 3 or not parts[2].isdigit():
            return
        action, req_role, tid_str = parts[0], parts[1], parts[2]
        if req_role not in ROLE_LABELS:
            return
    else:
        parts = data.split("_", 1)
        if len(parts) != 2:
            return
        action, tid_str = parts[0], parts[1]
        if not tid_str.isdigit():
            return
    target_id = int(tid_str)

    if action == "approve":
        # يحترم الفئة التي اختارها المستخدم (requested_role) وإلا يُفعّل عميلاً
        user = approve_user(target_id)
        if user:
            role_now = user.get("role") or "customer"
            await query.edit_message_text(
                "\u2705 تم تفعيل المستخدم:\n"
                "👤 " + (user.get("username") or str(user["telegram_id"]))
                + "\n🎯 الفئة: " + ROLE_LABELS.get(role_now, role_now)
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="\u2705 تم تفعيل حسابك!\n"
                    "🎯 فئتك: " + ROLE_LABELS.get(role_now, role_now) + "\n\n"
                    "🔄 اضغط /start لظهور لوحتك الخاصة.",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود أو مفعل بالفعل.")

    elif action == "approveas":
        # تفعيل صريح بالفئة المطلوبة في زر الإشعار
        user = approve_user(target_id, role=req_role)
        if user:
            await query.edit_message_text(
                "\u2705 تم تفعيل المستخدم بفئة " + ROLE_LABELS.get(req_role, req_role) + ":\n"
                "👤 " + (user.get("username") or str(user["telegram_id"]))
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="\u2705 تم تفعيل حسابك!\n"
                    "🎯 فئتك: " + ROLE_LABELS.get(req_role, req_role) + "\n\n"
                    "🔄 اضغط /start لظهور لوحتك الخاصة.",
                )
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود أو مفعل بالفعل.")

    elif action == "reject":
        deleted = reject_user(target_id)
        if deleted:
            await query.edit_message_text("❌ تم رفض و حذف المستخدم.")
            try:
                await context.bot.send_message(chat_id=target_id, text="❌ تم رفض طلبك.")
            except Exception:
                pass
        else:
            await query.edit_message_text("⚠️ المستخدم غير موجود.")


def _protected_account(telegram_id):
    """حسابات محمية لا يجوز تعديل أدوارها من داخل البوت إطلاقاً
    (مدراء وفّاح البيئة + الملاك). التعديل يكون فقط عبر env/ADMIN_IDS."""
    return telegram_id in ADMIN_IDS or telegram_id in OWNER_IDS


KICK_NOTICE = "⛔ تم إيقاف حسابك من قبل الإدارة.\nللاستفسار تواصل مع إدارة البوت."

MERGE_NOTICE = ("🔗 تم دمج هذا الحساب في حساب آخر في تطبيق المخزون.\n"
                "هذا الحساب لم يعد مستخدماً — استخدم حسابك الأساسي.")


def _user_merged_into(telegram_id):
    """v53: يعيد معرّف الحساب الناجي إن كان هذا الحساب مدموجاً فيه، وإلا None."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT merged_into FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    finally:
        release_connection(conn)


async def kicked_gate(update, context):
    """🚫 بوابة الطرد (v23): أول معالج على كل تحديث (group=-2) —
    المستخدم المطرود (active=FALSE) لا يرى إلا رسالة الإيقاف
    وتُوقف بقية المعالجات كلياً. المدراء والملاك مستثنون دائماً.
    v53: الحساب المدموج في حساب آخر يرى رسالة الدمج بدل رسالة الطرد."""
    user = update.effective_user if update else None
    if user is None or user.id in ADMIN_IDS or user.id in OWNER_IDS:
        return
    if await asyncio.to_thread(user_is_active, user.id):
        return
    merged = await asyncio.to_thread(_user_merged_into, user.id)
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(
                "🔗 تم دمج هذا الحساب في حساب آخر — استخدم حسابك الأساسي." if merged
                else "⛔ تم إيقاف حسابك من قبل الإدارة.", show_alert=True)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(MERGE_NOTICE if merged else KICK_NOTICE)
    except Exception:
        pass
    raise ApplicationHandlerStop


async def callback_pickrole_handler(update, context):
    """🚪 المستخدم الجديد اختار فئته (بائع/عامل مخزون/مدير بوت/بائع خارجي)
    → تُحفظ الفئة المطلوبة ويصل إشعار للمدراء بأزرار تفعيل بالفئة."""
    query = update.callback_query
    role = query.data.replace("pickrole_", "", 1)
    if role not in ROLE_PICK_ORDER:
        await query.answer("❌ فئة غير معروفة.", show_alert=True)
        return
    await query.answer("✅ تم إرسال طلبك للمدير")
    user = query.from_user
    sync_user_info(user)
    try:
        db_user = get_user(user.id)
    except Exception:
        db_user = None
    if db_user and db_user.get("role") != "pending":
        try:
            await query.edit_message_text("ℹ️ تمت معالجة حسابك مسبقاً — اضغط /start")
        except Exception:
            pass
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET requested_role = %s "
                "WHERE telegram_id = %s AND role = 'pending'",
                (role, user.id),
            )
        conn.commit()
    except Exception as pick_err:
        conn.rollback()
        logging.error("[ROLE] Failed to save requested role: " + str(pick_err))
    finally:
        release_connection(conn)
    try:
        await query.edit_message_text(
            "✅ تم تسجيل طلبك بفئة: " + ROLE_LABELS[role]
            + "\n⏳ بانتظار موافقة المدير — سيصلك إشعار عند التفعيل."
        )
    except Exception:
        pass
    uname = user.first_name or ("@" + user.username if user.username else str(user.id))
    for admin_id in ADMIN_IDS:
        try:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "✅ تفعيل " + ROLE_LABELS[role],
                    callback_data="approveas_" + role + "_" + str(user.id))],
                [InlineKeyboardButton("👤 تفعيل كعميل", callback_data="approve_" + str(user.id))],
                [InlineKeyboardButton("🗑 رفض", callback_data="reject_" + str(user.id))],
            ])
            await context.bot.send_message(
                chat_id=admin_id,
                text="🔔 طلب فئة جديد:\n\n👤 " + uname
                + "\n📧 " + str(user.id)
                + "\n🎯 الفئة المطلوبة: " + ROLE_LABELS[role],
                reply_markup=kb)
        except Exception as notify_err:
            logging.error("[ROLE] Notify admin failed: " + str(notify_err))


# ── Confirm-before-grant: يمنع المنح العرضي ويوقف فوضى النقر المزدوج ──

async def callback_users_ask_handler(update, context):
    """خطوة تأكيد صريحة قبل أي ترقية/إزالة: تعرض بطاقة تضم
    اسم الهدف ومعرّفه والدور المطلوب، ثم زر تنفيذ وزر إلغاء."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()

    data = query.data  # "askpromote_123" أو "askdemote_123"
    parts = data.split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    action, target_id = parts[0], int(parts[1])

    if _protected_account(target_id):
        await query.answer("🛡️ هذا الحساب محمي ولا يمكن تعديله من هنا.", show_alert=True)
        return

    target = get_user(target_id)
    if not target:
        await _safe_edit(query, "⚠️ المستخدم غير موجود في قاعدة البيانات.")
        return

    # escape any HTML-sensitive chars coming from Telegram names BEFORE building the card
    raw_name = target.get("first_name") or ("@" + target["username"] if target.get("username") else str(target_id))
    tname = (
        str(raw_name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    tid = str(target_id)
    user_line = (
        "👤 المستخدم: <b>" + tname + "</b>\n"
        "🆔 المعرف: <code>" + tid + "</code>"
        + ("\n📧 اليوزر: @" + target["username"] if target.get("username") else "")
    )

    cards = {
        "askpromote": (
            "👑 <b>تأكيد ترقية إلى مدير</b>\n\n"
            + user_line +
            "\n⚙️ الدور الحالي: عميل\n"
            "✏️ الدور الجديد: <b>مدير</b> (صلاحية كاملة على المخزون)\n\n"
            "هل أنت متأكد من التنفيذ؟",
            "👑 نعم، رقِّه",
            "promote_" + tid,
        ),
        "askdemote": (
            "👤 <b>تأكيد إزالة صلاحية الإدارة</b>\n\n"
            + user_line +
            "\n⚙️ الدور الحالي: مدير\n"
            "✏️ الدور الجديد: <b>عميل</b> (سيفقد صلاحية الإدارة)\n\n"
            "هل أنت متأكد من التنفيذ؟",
            "👤 نعم، أزل الإدارة",
            "demote_" + tid,
        ),
        "askkick": (
            "🚫 <b>تأكيد طرد المستخدم</b>\n\n"
            + user_line +
            "\n⛔ سيُوقف وصوله فوراً ولن يرى البوت إلا رسالة إيقاف."
            "\n📊 سجل حركاته يبقى محفوظاً ويمكن إعادته من قسم المطرودين.\n\n"
            "هل أنت متأكد من الطرد؟",
            "🚫 نعم، اطرده",
            "kick_" + tid,
        ),
    }

    if action not in cards:
        return
    text, confirm_label, confirm_cb = cards[action]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(confirm_label, callback_data=confirm_cb)],
        [InlineKeyboardButton("❌ إلغاء", callback_data="users_cancel")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def callback_users_cancel_handler(update, context):
    """إلغاء أي عملية قيد التأكيد والعودة للوحة المستخدمين."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer("تم الإلغاء.")
    try:
        await users_command(update, context)
    except Exception as e:
        logging.error("[USERS] Cancel->render failed: " + str(e))
        await _safe_edit(query, "تم الإلغاء.")


def _merge_candidate_label(u):
    """سطر تعريف مختصر لحساب في بطاقات الدمج (v53)."""
    name = _merge_display_name(u)
    tid = u.get("telegram_id")
    extras = []
    if u.get("username"):
        extras.append("@" + str(u["username"]))
    extras.append("🆔 " + (str(tid) if tid else "#" + str(u["id"])))
    if u.get("role"):
        extras.append(ROLE_LABELS.get(u["role"], u["role"]))
    if not u.get("active"):
        extras.append("🚫 مطرود")
    return name, " | ".join(extras)


async def callback_merge_list_handler(update, context):
    """v53: زر «🧬 دمج الحساب المكرر» — يعرض بقية الحسابات بنفس الاسم (أو المشابه
    بادئةً) ليختار المدير الحساب الذي سيُدمج في الحساب الناجي (الذي فتحت لوحته).
    يحل أي تكرار لم يلحقه الدمج التلقائي (حسابان فعليان في تلجرام بنفس الاسم)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    tid_str = query.data.replace("askmerge_", "", 1)
    if not tid_str.isdigit():
        return
    keep = get_user(int(tid_str))
    if not keep:
        await _safe_edit(query, "⚠️ المستخدم غير موجود في قاعدة البيانات.")
        return
    if keep.get("role") == "admin" or _protected_account(keep.get("telegram_id")):
        await query.answer("🛡️ لا يمكن الدمج من/إلى حساب محمي أو إداري.", show_alert=True)
        return
    await query.answer()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, telegram_id, username, first_name, display_name, role, active "
                        "FROM users ORDER BY created_at")
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
    finally:
        release_connection(conn)
    keep_key = _norm_name_key(_merge_display_name(keep))
    cands = []
    for r in rows:
        if int(r["id"]) == int(keep["id"]):
            continue
        if r.get("role") == "admin" or _protected_account(r.get("telegram_id")):
            continue
        k = _norm_name_key(_merge_display_name(r))
        if not k or not keep_key:
            continue
        same = (k == keep_key)
        pref = (min(len(k), len(keep_key)) >= 3
                and (k.startswith(keep_key) or keep_key.startswith(k)))
        if same or pref:
            cands.append((0 if same else 1, r))
    cands.sort(key=lambda x: (x[0], x[1]["id"]))
    if not cands:
        await query.answer("✅ لا يوجد حساب آخر بنفس الاسم — لا شيء للدمج.", show_alert=True)
        return
    keep_name, _ = _merge_candidate_label(keep)
    lines = [
        "🧬 <b>دمج الحسابات المكررة</b>",
        "",
        "الحساب الناجي (يبقى كما هو): <b>" + _esc_h(keep_name) + "</b>",
        "اختر الحساب الذي سيُدمج فيه — تُنقل كل سجلاته (المحادثات، الطلبات، الفواتير، "
        "حركات المخزون، دخول التطبيق) ثم يُوقَف نهائياً:",
    ]
    buttons = []
    for _, r in cands[:10]:
        tid2 = r.get("telegram_id")
        nm = _merge_display_name(r)
        short = nm[:14] + " · 🆔" + (str(tid2) if tid2 else "#" + str(r["id"]))
        if not r.get("active"):
            short += " (مطرود)"
        buttons.append([InlineKeyboardButton(
            "دمج: " + short,
            callback_data="mergesel_" + tid_str + "_" + str(r["id"]))])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="users_cancel")])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.HTML,
                     reply_markup=InlineKeyboardMarkup(buttons))


async def callback_merge_confirm_handler(update, context):
    """v53: بطاقة تأكيد صريحة قبل تنفيذ الدمج."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    parts = query.data.split("_")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return
    keep_tid, src_id = int(parts[1]), int(parts[2])
    keep = get_user(keep_tid)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (src_id,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    src = dict(row) if row else None
    if not keep or not src or int(keep["id"]) == int(src_id):
        await _safe_edit(query, "⚠️ أحد الحسابين غير موجود أو غير صالح للدمج.")
        return
    if src.get("role") == "admin" or _protected_account(src.get("telegram_id")) \
            or keep.get("role") == "admin" or _protected_account(keep.get("telegram_id")):
        await query.answer("🛡️ الدمج مع حساب محمي/إداري غير مسموح.", show_alert=True)
        return
    await query.answer()
    kn, kex = _merge_candidate_label(keep)
    sn, sex = _merge_candidate_label(src)
    text = ("🧬 <b>تأكيد دمج الحسابات</b>\n\n"
            "✅ <b>يبقى</b>: " + _esc_h(kn) + "\n"
            "<i>" + _esc_h(kex) + "</i>\n\n"
            "🧩 <b>يُدمج ثم يُوقف</b>: " + _esc_h(sn) + "\n"
            "<i>" + _esc_h(sex) + "</i>\n\n"
            "سيُنقل إلى الحساب الباقي: المحادثات، طلبات التوريد، الفواتير، "
            "حركات المخزون، طلبات الاستخراج، دخول التطبيق والجلسات.\n"
            "⚠️ لا يمكن التراجع بعد التنفيذ.\n\n"
            "هل أنت متأكد؟")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، ادمج الآن",
                              callback_data="mergego_" + str(keep_tid) + "_" + str(src_id))],
        [InlineKeyboardButton("❌ إلغاء", callback_data="users_cancel")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def callback_merge_go_handler(update, context):
    """v53: تنفيذ الدمج بعد التأكيد — نقل السجلات ثم إيقاف الحساب المكرر."""
    query = update.callback_query
    caller_id = query.from_user.id
    if not is_admin(caller_id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        logging.warning(
            "[MERGE] Unauthorized merge attempt by " + str(caller_id)
            + " data=" + str(query.data))
        return
    parts = query.data.split("_")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return
    keep_tid, src_id = int(parts[1]), int(parts[2])
    keep = get_user(keep_tid)
    if not keep or int(keep["id"]) == int(src_id):
        await _safe_edit(query, "⚠️ الحسابات غير صالحة للدمج.")
        return
    await query.answer("… جارٍ الدمج")
    try:
        counts = await asyncio.to_thread(_merge_two_users, int(keep["id"]), int(src_id), False)
    except Exception as e:
        logging.error("[MERGE] failed keep=%s src=%s: %s", keep["id"], src_id, e)
        await _safe_edit(query, "⚠️ فشل الدمج: " + _esc_h(str(e))[:200])
        return
    try:
        await asyncio.to_thread(
            _audit_log, "merge_users", {"id": None, "username": str(caller_id)},
            details="دمج الحساب #" + str(src_id) + " في #"
                    + str(keep["id"]) + " — رسائل منقولة: "
                    + str(counts.get("messages", 0)))
    except Exception:
        pass
    kn, _ = _merge_candidate_label(keep)
    text = ("✅ <b>تم الدمج بنجاح</b>\n\n"
            "الحساب الباقي: <b>" + _esc_h(kn) + "</b>\n"
            "📨 رسائل منقولة: " + str(counts.get("messages", 0)) + "\n"
            "📦 حركات مخزون: " + str(counts.get("movements", 0)) + "\n"
            "🚚 طلبات توريد: " + str(counts.get("supply_orders", 0)) + "\n"
            "🧾 فواتير: " + str(counts.get("invoices", 0)) + "\n\n"
            "الحساب المكرر أُوقف ولن يظهر في «الرسائل» بعد الآن — "
            "افتح التطبيق وأعد فتح قائمة الرسائل.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 لوحة المستخدمين", callback_data="users_refresh")],
    ])
    await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def callback_promote_handler(update, context):
    """تنفيذ الترقية/الإزالة بعد التأكيد — محمي بفحص صلاحيات المستدعي."""
    query = update.callback_query
    # ── AUTH GUARD ── منع أي شخص غير مصرح له بتغيير الأدوار.
    caller_id = query.from_user.id
    if not is_admin(caller_id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        logging.warning(
            "[USERS] Unauthorized role change attempt by "
            + str(caller_id) + " on data=" + str(query.data)
        )
        return

    data = query.data  # "promote_123456" or "demote_123456"
    parts = data.split("_", 1)
    if len(parts) != 2:
        return
    action, tid_str = parts[0], parts[1]
    if not tid_str.isdigit():
        return
    target_id = int(tid_str)

    # ── ESCALATION GUARD ── لا يمكن تعديل حسابات البيئة المحمية إطلاقاً.
    if _protected_account(target_id):
        await query.answer("🛡️ لا يمكن تعديل دور حساب محمي (مالك/بيئة).", show_alert=True)
        return
    # ── LEVEL GUARD ── إزالة إدارة مدير آخر تتطلب صلاحية مالك فقط.
    if action == "demote" and not is_owner(caller_id):
        await query.answer("🔐 إزالة الإدارة متاحة لصاحب البوت فقط.", show_alert=True)
        return
    if target_id == caller_id:
        await query.answer("🚫 لا يمكنك تنفيذ هذا الإجراء على نفسك.", show_alert=True)
        return

    await query.answer()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # env-protected accounts can never be touched, even if a guard above changes later
            protected = tuple(set(list(ADMIN_IDS) + list(OWNER_IDS)))
            if action == "promote":
                cur.execute(
                    "UPDATE users SET role = 'admin' WHERE telegram_id = %s AND role = 'customer' "
                    "AND telegram_id NOT IN %s RETURNING *",
                    (target_id, protected),
                )
                user = cur.fetchone()
            elif action == "demote":
                cur.execute(
                    "UPDATE users SET role = 'customer' WHERE telegram_id = %s AND role = 'admin' "
                    "AND telegram_id NOT IN %s RETURNING *",
                    (target_id, protected),
                )
                user = cur.fetchone()
            else:
                return
        conn.commit()
    except Exception:
        conn.rollback()
        user = None
    finally:
        release_connection(conn)

    if user:
        user = dict(user)
        raw_name = user.get("first_name") or user.get("username") or str(user["telegram_id"])
        name = str(raw_name).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if action == "promote":
            await query.edit_message_text(
                "👑 تم ترقية <b>" + name + "</b> إلى مدير!\n🆔 <code>" + str(target_id) + "</code>",
                parse_mode=ParseMode.HTML,
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="👑 تم ترقيتك إلى مدير!\nالآن يمكنك إدارة المخزون.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        else:
            await query.edit_message_text(
                "👤 تم إزالة صلاحية الإدارة من <b>" + name + "</b>.\n🆔 <code>" + str(target_id) + "</code>",
                parse_mode=ParseMode.HTML,
            )
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text="👤 تم إزالة صلاحية الإدارة من حسابك.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        logging.info(
            "[USERS] " + action + " target=" + str(target_id)
            + " by=" + str(caller_id)
        )
    else:
        await query.edit_message_text("⚠️ لم يتم تطبيق التغيير.")


async def callback_kick_handler(update, context):
    """🚫 تنفيذ الطرد/إعادة التنشيط بعد التأكيد (v23) — للمدراء فقط،
    والحسابات المحمية (ADMIN_IDS/OWNER_IDS) مستثناة من الطرد."""
    query = update.callback_query
    caller_id = query.from_user.id
    if not is_admin(caller_id):
        await query.answer("❌ هذا الإجراء للمدراء فقط.", show_alert=True)
        logging.warning(
            "[USERS] Unauthorized kick attempt by "
            + str(caller_id) + " on data=" + str(query.data)
        )
        return
    parts = (query.data or "").split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    action, tid_str = parts[0], parts[1]
    target_id = int(tid_str)
    if action == "kick" and _protected_account(target_id):
        await query.answer("🛡️ لا يمكن طرد حساب محمي (مالك/بيئة).", show_alert=True)
        return
    if target_id == caller_id:
        await query.answer("🚫 لا يمكنك تنفيذ هذا الإجراء على نفسك.", show_alert=True)
        return
    await query.answer()
    role = set_user_active(target_id, action == "unban")
    if role is None:
        await query.edit_message_text("⚠️ المستخدم غير موجود.")
        return
    if action == "kick":
        await query.edit_message_text(
            "🚫 تم طرد المستخدم وإيقاف وصوله.\n"
            "📊 سجل حركاته محفوظ — يمكن إعادته من لوحة المستخدمين (قسم المطرودين)."
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="⛔ تم إيقاف حسابك من قبل الإدارة.\nللاستفسار تواصل مع إدارة البوت.",
            )
        except Exception:
            pass
    else:
        await query.edit_message_text(
            "✅ تم إعادة تنشيط المستخدم وفتح وصوله.\n🔄 سيعود للعمل عند ضغط /start."
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text="✅ تم إعادة تفعيل حسابك!\n🔄 اضغط /start للعودة إلى لوحتك.",
            )
        except Exception:
            pass
    logging.info("[USERS] " + action + " target=" + str(target_id) + " by=" + str(caller_id))

# ── Backup Command ─────────────────────────────────────

async def backup_command(update, context):
    global _last_backup_data
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    try:
        import io
        from telegram import InputFile
        backup = await asyncio.to_thread(db_export_backup)  # v72: خارج حلقة الأحداث
        _last_backup_data = backup
        # v75: بناء JSON في خيط منفصل وبلا indent — لا تجميد ولا ملف ضخم
        json_str = await asyncio.to_thread(
            lambda: json.dumps(backup, ensure_ascii=False, default=str)
        )
        file_bytes = json_str.encode("utf-8")
        del json_str
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d_%H-%M")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 استيراد هذه النسخة", callback_data="restore_last")],
            [InlineKeyboardButton("📡 استيراد من القناة", callback_data="restore_from_channel")],
        ])
        await update.message.reply_document(
            document=InputFile(io.BytesIO(file_bytes), filename="backup_" + now_str + ".json"),
            caption=("💾 نسخة احتياطية كاملة\n"
                + "📄 السلع: " + str(len(backup["products"]))
                + "\n📜 العمليات: " + str(len(backup["transactions"]))
                + "\n👥 المستخدمين: " + str(len(backup["users"]))
                + "\n🧾 الفواتير: " + str(len(backup.get("invoices", [])))
                + "\n🚚 طلبات التوريد: " + str(len(backup.get("incoming_orders", [])))
                + "\n📤 طلبات الاستخراج: " + str(len(backup.get("extraction_requests", [])))
                + "\n💡 حفظ الملف. لإستيراد البيانات أرسله للبوت."),
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.message.reply_text("❌ فشل النسخ الاحتياطي: " + str(e))


async def backup_test_command(update, context):
    """Force a backup NOW and report success/failure for each channel (memory/disk/email).
    Admin-only. Use this to verify the email backup system is actually working.
    """
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    await update.message.reply_text("⏳ جاري تنفيذ نسخة اختبارية الآن...")
    result = await _daily_backup(context.bot, label="test")
    # Build report
    lines = ["🧪 تقرير النسخة الاختبارية:", ""]
    lines.append("💾 الذاكرة: " + ("✅ نعم" if result["memory"] else "❌ فشل"))
    lines.append("💿 القرص: " + ("✅ نعم" if result["disk"] else "❌ فشل"))
    channel_status = "✅ نجح الإرسال" if result["channel"] else "❌ فشل الإرسال"
    if not BACKUP_CHANNEL_ID:
        channel_status = "⚠️ غير مُعدّ (BACKUP_CHANNEL_ID غير موجود في متغيرات البيئة)"
    lines.append("📡 القناة: " + channel_status)
    lines.append("📦 الحجم: " + str(result["bytes"]) + " bytes")
    lines.append("")
    if result["error"]:
        lines.append("❌ خطأ: " + str(result["error"]))
    elif result["channel"]:
        lines.append("✅ تم إرسال النسخة للقناة بنجاح!")
    else:
        lines.append("💡 لتشغيل نسخ القناة:")
        lines.append("   1. أنشئ قناة خاصة في تيليغرام")
        lines.append("   2. أضف البوت كمسؤول في القناة")
        lines.append("   3. أرسل رسالة في القناة")
        lines.append("   4. أضف BACKUP_CHANNEL_ID في متغيرات البيئة (مثل: -1001234567890)")
        lines.append("   💡 للحصول على ID القناة: أرسل رسالة ثم افتح:")
        lines.append("   https://api.telegram.org/bot<TOKEN>/getUpdates")
    await update.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())


async def handle_backup_document(update, context):
    """Admin sends a backup .json file to restore data."""
    if not is_admin(update.effective_user.id):
        return
    if not update.message.document:
        return
    file_name = update.message.document.file_name or ""
    if not file_name.endswith(".json"):
        return
    try:
        file = await update.message.document.get_file()
        raw = await file.download_as_bytearray()
        data = json.loads(bytes(raw).decode("utf-8"))
        if "products" not in data:
            await update.message.reply_text("❌ ملف غير صالح.")
            return
        context.user_data["restore_data"] = data
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ تأكيد استيراد", callback_data="restore_confirm"),
             InlineKeyboardButton("❌ إلغاء", callback_data="restore_cancel")],
        ])
        prod_count = len(data.get("products", []))
        tx_count = len(data.get("transactions", []))
        inv_count = len(data.get("invoices", []))
        confirm_msg = (
            "⚠️ تأكيد استيراد البيانات:\n\n"
            "📦 السلع: " + str(prod_count) + "\n"
            "📜 العمليات: " + str(tx_count) + "\n"
        )
        if inv_count > 0:
            confirm_msg += "🧾 الفواتير: " + str(inv_count) + "\n"
        confirm_msg += "\n\U0001f534 \u062a\u0646\u0628\u064a\u0647: \u0633\u064a\u062a\u0645 \u062d\u0630\u0641 \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a \u0627\u0644\u062d\u0627\u0644\u064a\u0629 \u0648\u0627\u0633\u062a\u0628\u062f\u0627\u0644\u0647\u0627!"
        await update.message.reply_text(
            confirm_msg,
            reply_markup=keyboard,
        )
    except Exception as e:
        await update.message.reply_text("\u274c \u0641\u0634\u0644 \u0642\u0631\u0627\u0621\u0629 \u0627\u0644\u0645\u0644\u0641: " + str(e))


async def callback_restore_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "restore_confirm":
        restore_data = context.user_data.get("restore_data")
        if not restore_data:
            await query.edit_message_text("\u274c \u0644\u0627 \u064a\u0648\u062c\u062f \u0628\u064a\u0627\u0646\u0627\u062a \u0644\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f.")
            return
        try:
            result = db_import_backup(restore_data)
            await query.edit_message_text(result)
        except Exception as e:
            await query.edit_message_text("\u274c \u0641\u0634\u0644 \u0627\u0644\u0627\u0633\u062a\u064a\u0631\u0627\u062f: " + str(e))
        context.user_data.pop("restore_data", None)
        try:
            await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "restore_cancel":
        await query.edit_message_text("\u274c \u062a\u0645 \u0625\u0644\u063a\u0627\u0621 \u0627\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a.")
        context.user_data.pop("restore_data", None)
        try:
            await _send_themed(context.bot, query.message.chat_id, "\U0001f4e6 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629:", reply_markup=get_admin_keyboard())
        except Exception:
            pass


async def _safe_edit(query, text, reply_markup=None, parse_mode=None):
    """Edit message safely — works for both text messages and document captions."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await query.edit_message_caption(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass


async def callback_restore_from_channel_handler(update, context):
    """توجيه المستخدم لاستيراد النسخة من قناة النسخ الاحتياطي."""
    query = update.callback_query
    await query.answer()
    if not BACKUP_CHANNEL_ID:
        await _safe_edit(query, "❌ القناة غير مُعدّة.\n\nأضف BACKUP_CHANNEL_ID في متغيرات البيئة.")
        return
    await _safe_edit(query,
        "📡 استيراد من القناة:\n\n"
        "1. اذهب لقناة النسخ الاحتياطي\n"
        "2. اضغط مطولاً على ملف النسخة الأخيرة\n"
        "3. اختر (إعادة توجيه) → أرسله هنا\n"
        "4. سيظهر زر (تأكيد استيراد)\n\n"
        "💡 يمكنك أيضاً تحميل الملف وإرساله مباشرة."
    )

async def callback_restore_last_handler(update, context):
    query = update.callback_query
    await query.answer()
    if not _last_backup_data:
        await _safe_edit(query, "❌ لا توجد نسخة احتياطية محفوضة.\nاستخدم /backup لإنشاء واحدة.")
        return
    data = _last_backup_data
    prod_count = len(data.get("products", []))
    tx_count = len(data.get("transactions", []))
    backup_date = data.get("date", "?")[:16]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ تأكيد استيراد", callback_data="last_restore_confirm"),
         InlineKeyboardButton("❌ إلغاء", callback_data="last_restore_cancel")],
    ])
    text = "💾 استيراد آخر نسخة احتياطية:\n\n" + "📅 التاريخ: " + backup_date + "\n" + "📦 السلع: " + str(prod_count) + "\n" + "📜 العمليات: " + str(tx_count) + "\n\n" + "🔴 تنبيه: سيتم حذف البيانات الحالية واستبدالها!"
    await _safe_edit(query, text, reply_markup=keyboard)


# ═══ v83: التنبيه عام + خاص — اختيار النوع ثم المستخدم ثم النص ═══
_ALERT_PAGE = 12  # مستخدم لكل صفحة في قائمة الاختيار


def _alert_users_keyboard(users, offset=0):
    """لوحة اختيار مستخدم للتنبيه الخاص — 12 لكل صفحة مع تنقل وإلغاء وعام."""
    rows = []
    for uid, uname in users[offset:offset + _ALERT_PAGE]:
        rows.append([InlineKeyboardButton("👤 " + uname[:40],
                                          callback_data="alertu_" + str(uid))])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀ السابق",
                    callback_data="alertpg_" + str(max(0, offset - _ALERT_PAGE))))
    if offset + _ALERT_PAGE < len(users):
        nav.append(InlineKeyboardButton("التالي ▶",
                    callback_data="alertpg_" + str(offset + _ALERT_PAGE)))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("📢 بدلاً من ذلك: عام",
                                      callback_data="alertmode_all")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="alertmode_cancel")])
    return InlineKeyboardMarkup(rows)


async def _alert_load_users():
    """المستخدمون القابلون للتنبيه — نفس فلتر التنبيه العام: لديهم تلغرام ونشطون.
    v86 إصلاح: العنصر الأول = telegram_id (وليس users.id) — إرسال التنبيه
    يحتاج chat_id التلغرامي؛ كان يُرسل للرقم التسلسلي الداخلي فلا تصل الرسالة."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.telegram_id, COALESCE(NULLIF(u.display_name, ''), u.first_name, "
                "u.username, '#' || u.telegram_id::text) AS name, u.username "
                "FROM users u WHERE u.active = TRUE AND u.telegram_id IS NOT NULL "
                "AND (u.role IS NULL OR u.role <> %s) ORDER BY name",
                (ROLE_PENDING,))
            rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        nm = str(r[1] or "").strip() or ("#" + str(r[0]))
        if r[2]:
            nm = nm + " (@" + str(r[2]) + ")"
        out.append((int(r[0]), nm))
    return out


def _alert_load_users_full():
    """v86: كل المستخدمين النشطين ذوي تلغرام مع أعمدة المطابقة الكاملة —
    لتطابق أسماء المستلمين في ميزة «عدة معنيين برسالة واحدة»."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username, u.first_name, u.display_name "
                "FROM users u WHERE u.active = TRUE AND u.telegram_id IS NOT NULL "
                "AND (u.role IS NULL OR u.role <> %s)",
                (ROLE_PENDING,))
            rows = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return rows


def _resolve_alert_recipients(text):
    """v86: مطابقة أسماء المستلمين من رسالة واحدة.
    يقبل مزيجاً من: @username / username / الاسم المعروض / الاسم الأول / ID تلغرام،
    مفصولة بمسافة أو سطر أو فاصلة (عربية ، لاتينية , أو ؛).
    المطابقة: دقيقة أولاً (معرف ← اسم مستخدم ← اسم معروض/أول) ثم جزئية فريدة.
    يعيد (resolved, unknown): resolved قواميس {id, tid, name}، unknown التوكنات غير المعروفة."""
    tokens = [t for t in re.split(r"[\s،,؛;\n\r\t]+", str(text or "").strip()) if t]
    if not tokens:
        return [], []
    rows = _alert_load_users_full()
    by_uname, by_display, by_tid = {}, {}, {}
    for r in rows:
        _id, tid, uname, fname, dname = r[0], r[1], r[2], r[3], r[4]
        u = _pwa_norm_username(uname)
        if u:
            by_uname[u] = (_id, tid, uname, fname, dname)
        for nm in (_pwa_norm_username(dname), _pwa_norm_username(fname)):
            if nm:
                by_display.setdefault(nm, []).append((_id, tid, uname, fname, dname))
        if tid:
            by_tid[str(int(tid))] = (_id, tid, uname, fname, dname)
    resolved, seen, unknown = [], set(), []
    for tok in tokens:
        key = _pwa_norm_username(tok)
        hit = None
        if key in by_tid:
            hit = by_tid[key]
        elif key and key in by_uname:
            hit = by_uname[key]
        elif key and key in by_display:
            # v86: تطابق اسم معروض/أول لعدة مستخدمين = غموض — يُرفض ولا يُخمّن
            # (وإلا وصل التنبيه لشخص خاطئ بصمت). الفريد فقط يُقبل.
            cands0 = by_display[key]
            uniq0 = {(c[0], c[1]) for c in cands0}
            if len(uniq0) == 1:
                hit = cands0[0]
        if hit is None:
            # مطابقة جزئية فريدة على الاسم المعروض/الأول — تسامح مع أسماء ناقصة
            cands = [v for k, lst in by_display.items() if key and key in k for v in lst]
            uniq = {(c[0], c[1]) for c in cands}
            if len(uniq) == 1:
                hit = cands[0]
        if hit is None:
            unknown.append(tok)
            continue
        if hit[0] in seen:
            continue  # نفس المستخدم ذُكر مرتين — يُحسب مرة واحدة
        seen.add(hit[0])
        _id, tid, uname, fname, dname = hit
        nm = str(dname or fname or uname or ("#" + str(tid))).strip()
        if uname:
            nm = nm + " (@" + str(uname) + ")"
        resolved.append({"id": int(_id), "tid": int(tid), "name": nm})
    return resolved, unknown


async def callback_alert_pick_handler(update, context):
    """v83: أزرار التنبيه — النوع (عام/خاص/إلغاء) + اختيار المستخدم + ترقيم الصفحات."""
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        await query.answer("للمدير فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""
    if data == "alertmode_cancel":
        context.user_data.clear()
        # v86 إصلاح BadRequest: edit_message_text تقبل لوحة inline فقط —
        # لوحة الرد العادية تُرسل في رسالة منفصلة (نفس نمط المشروع الآمن).
        await query.edit_message_text("تم الإلغاء.")
        await context.bot.send_message(
            chat_id=query.message.chat_id, text="🏠 القائمة الرئيسية:",
            reply_markup=get_admin_keyboard())
        return
    if data == "alertmode_all":
        _set_state(context, STATE_ALERT_MSG)
        # v86 إصلاح BadRequest: لا لوحة رد داخل edit — رسالة منفصلة للوحة
        await query.edit_message_text(
            "\U0001f514 أرسل نص التنبيه العام:" + chr(10) + chr(10)
            + "سيتم إرساله لجميع المستخدمين.")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✍️ اكتب النص الآن أو اضغط ❌ إلغاء:",
            reply_markup=get_input_keyboard())
        return
    if data == "alertmode_priv_multi":
        # v86: تنبيه خاص لعدة معنيين — تحديدهم كلهم في رسالة واحدة
        _set_state(context, STATE_ALERT_PRIV_MULTI)
        await query.edit_message_text(
            "👥 تنبيه خاص لعدة معنيين:" + chr(10) + chr(10)
            + "أرسل المستلمين في رسالة واحدة، مفصولين بمسافة أو سطر أو فاصلة:" + chr(10)
            + "• @اسم_المستخدم" + chr(10)
            + "• الاسم المعروض أو الاسم الأول" + chr(10)
            + "• رقم المستخدم ID" + chr(10) + chr(10)
            + "مثال: @ali @محمد 123456789")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✍️ أرسل الأسماء الآن أو اضغط ❌ إلغاء:",
            reply_markup=get_input_keyboard())
        return
    if data == "alertmode_priv":
        try:
            users = await _alert_load_users()
        except Exception as e:
            context.user_data.clear()
            await query.edit_message_text("❌ خطأ في قراءة المستخدمين: " + str(e))
            return
        if not users:
            context.user_data.clear()
            # v86 إصلاح BadRequest: لا لوحة رد داخل edit — رسالة منفصلة
            await query.edit_message_text(
                "لا يوجد مستخدمون لديهم حساب تلغرام.")
            await context.bot.send_message(
                chat_id=query.message.chat_id, text="🏠 القائمة الرئيسية:",
                reply_markup=get_admin_keyboard())
            return
        context.user_data["alert_users"] = users
        pages = (len(users) + _ALERT_PAGE - 1) // _ALERT_PAGE
        await query.edit_message_text(
            "👤 اختر المستخدم المُراد تنبيهه (صفحة 1 من " + str(pages) + "):",
            reply_markup=_alert_users_keyboard(users, 0))
        return
    if data.startswith("alertpg_"):
        users = context.user_data.get("alert_users") or []
        if not users:
            context.user_data.clear()
            await query.edit_message_text(
                "❌ انتهت صلاحية القائمة — اضغط 🔔 تنبيه من جديد.")
            return
        try:
            off = int(data.split("_", 1)[1])
        except ValueError:
            off = 0
        off = max(0, min(off, ((len(users) - 1) // _ALERT_PAGE) * _ALERT_PAGE))
        await query.edit_message_reply_markup(
            reply_markup=_alert_users_keyboard(users, off))
        return
    if data.startswith("alertu_"):
        try:
            uid = int(data.split("_", 1)[1])
        except ValueError:
            uid = 0
        users = context.user_data.get("alert_users") or []
        uname = next((n for i, n in users if i == uid), "#" + str(uid))
        context.user_data["alert_target"] = {"id": uid, "name": uname}
        _set_state(context, STATE_ALERT_PRIV_MSG)
        # v86 إصلاح BadRequest: لا لوحة رد داخل edit — رسالة منفصلة
        await query.edit_message_text(
            "\U0001f514 أرسل نص التنبيه الخاص إلى " + uname + ":")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✍️ اكتب النص الآن أو اضغط ❌ إلغاء:",
            reply_markup=get_input_keyboard())
        return


async def callback_add_notify_handler(update, context):
    """Handle the 'reason for add' choice: truck, customer return, or correction."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "addnotify_truck":
        reason = "truck"
    elif data == "addnotify_return":
        reason = "return"
    else:
        reason = "correction"
    pending = context.user_data.get("pending_add_notify")
    if not pending:
        await query.edit_message_text("❌ انتهت صلاحية هذا الاختيار.")
        context.user_data.clear()
        return
    product = pending["product"]
    qty = pending["qty"]
    actor = pending["user"]
    tx_id = pending.get("tx_id")
    seller = pending.get("seller")
    # Build reason label for movements log
    reason_labels = {"truck": "🚚 وصول شاحنة", "return": "↩️ إرجاع من زبون", "correction": "📝 تصحيح مخزون"}
    reason_label = reason_labels.get(reason, "")
    if seller:
        reason_label = reason_label + " — " + seller
    # Update transaction reason in DB
    if tx_id:
        update_transaction_reason(tx_id, reason_label)
    try:
        if reason == "correction":
            confirm = "📝 تم تصحيح المخزون ✅"
        else:
            # Broadcast only for truck arrival and customer return
            if reason in ("truck", "return"):
                await _broadcast_add_notification(context.bot, product, qty, actor, reason=reason)
            if reason == "return":
                confirm = "↩️ تم إرسال تنبيه إرجاع للجميع ✅"
            else:
                confirm = "🚚 تم إرسال تنبيه وصول بضاعة للجميع ✅"
        # Auto-reconcile pending deliveries for EVERY reason (truck/return/
        # correction): any stock that covers open pending entries removes
        # them from the pending list and notifies the waiting customers.
        rec_note = ""
        if actor:
            try:
                reconcile_result = await asyncio.to_thread(
                    auto_reconcile_product, product["id"], qty, actor["id"]
                )
                if reconcile_result and reconcile_result["fulfilled"]:
                    rec_note = (chr(10) + chr(10) + "🔄 تمت تسوية الطلبات المعلقة تلقائياً:"
                                + chr(10) + "   ✅ تم تغطية " + str(reconcile_result["total_covered"]) + " وحدة")
                    if reconcile_result["remaining_deficit"] > 0:
                        rec_note += chr(10) + "   ⚠️ متبقي عجز: " + str(reconcile_result["remaining_deficit"])
                    else:
                        rec_note += chr(10) + "   🎉 تم تغطية العجز بالكامل!"
                    await _send_fulfilled_notifications(context.bot, reconcile_result["fulfilled"])
            except Exception as rec_err:
                logging.error("[ADD_NOTIFY] Reconcile failed: " + str(rec_err))
        await query.edit_message_text(confirm + rec_note)
    except Exception as e:
        logging.error("[ADD_NOTIFY] Confirm failed: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass
    context.user_data.pop("pending_add_notify", None)
    context.user_data.pop("cust_product", None)
    context.user_data.pop("pact_action", None)
    context.user_data["state"] = None


async def callback_rem_notify_handler(update, context):
    """Handle the 'reason for remove' choice."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "remnotify_return":
        reason = "return"
    elif data == "remnotify_sale":
        reason = "sale"
    elif data == "remnotify_saoula":
        reason = "saoula"
    elif data == "remnotify_carthago":
        reason = "carthago"
    else:
        reason = "correction"
    pending = context.user_data.get("pending_rem_notify")
    if not pending:
        await query.edit_message_text("❌ انتهت صلاحية هذا الاختيار.")
        context.user_data.clear()
        return
    product = pending["product"]
    qty = pending["qty"]
    actor = pending["user"]
    tx_id = pending.get("tx_id")
    seller = pending.get("seller")
    # Build reason label for movements log
    reason_labels_rem = {
        "return": "↩️ إرجاع من زبون",
        "sale": "🚚 بيع لزبون",
        "saoula": "🏪 بيع لرابح Saoula",
        "carthago": "🏪 بيع لحسان Carthago",
        "correction": "📝 تصحيح مخزون",
    }
    reason_label = reason_labels_rem.get(reason, "")
    if seller:
        reason_label = reason_label + " — " + seller
    # Update transaction reason in DB
    if tx_id:
        update_transaction_reason(tx_id, reason_label)
    try:
        if reason == "correction":
            confirm = "📝 تم تصحيح المخزون ✅"
        else:
            confirm = "✅ تم التسجيل في سجل الحركة"
        await query.edit_message_text(confirm)
    except Exception as e:
        logging.error("[REM_NOTIFY] Confirm failed: " + str(e))
        try:
            await query.edit_message_text("❌ حدث خطأ.")
        except Exception:
            pass
    context.user_data.pop("pending_rem_notify", None)
    context.user_data.pop("cust_product", None)
    context.user_data.pop("pact_action", None)
    context.user_data["state"] = None


async def callback_restore_last_confirm_handler(update, context):
    query = update.callback_query
    # ── AUTH GUARD ── الاستعادة تحلّ محل قاعدة البيانات بالكامل: صاحب البوت فقط.
    if not is_owner(query.from_user.id):
        await query.answer("🔐 الاستعادة الكاملة متاحة لصاحب البوت فقط.", show_alert=True)
        logging.warning(
            "[BACKUP] Unauthorized restore attempt by " + str(query.from_user.id)
        )
        return
    await query.answer()
    # _last_backup_data is only read here, not assigned; no `global` declaration needed.
    data = query.data
    if data == "last_restore_confirm":
        if not _last_backup_data:
            await _safe_edit(query, "❌ لا توجد نسخة.")
            return
        try:
            result = db_import_backup(_last_backup_data)
            await _safe_edit(query, result)
        except Exception as e:
            await _safe_edit(query, "❌ فشل الاستيراد [" + PWA_VERSION + "]: " + str(e))
        try:
            await _send_themed(context.bot, query.message.chat_id, "📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
        except Exception:
            pass
    elif data == "last_restore_cancel":
        await _safe_edit(query, "❌ تم الإلغاء.")
        try:
            await _send_themed(context.bot, query.message.chat_id, "📦 القائمة الرئيسية:", reply_markup=get_admin_keyboard())
        except Exception:
            pass

# ── Users Command (لوحة إدارة المستخدمين) ──

_MAX_PANEL_ROWS = 60  # حماية من تجاوز حد 4096 حرف في رسائل تليجرام


def _display_label(u):
    """تسمية عرض موحّدة: الاسم ثم @اليوزر ثم ID دائماً —
    حتى لو تشابهت الأسماء يظل كل صف مميزاً بمعرّفه الفريد."""
    tid = u["telegram_id"]
    uname = u.get("username")
    fname = u.get("first_name")
    label = fname or ("@" + uname if uname else str(tid))
    extras = []
    if uname:
        extras.append("@" + uname)
    extras.append("🆔 " + str(tid))
    return label, extras


def _get_users_snapshot():
    """قراءة واحدة متسقة لقائمتَي المعلّقين والمفعّلين."""
    pending = get_pending_users()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') "
                "ORDER BY role, created_at"
            )
            approved = [dict(r) for r in cur.fetchall()]
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[USERS] Failed to fetch approved users: " + str(e))
        approved = []
    finally:
        release_connection(conn)
    return pending, approved


def _build_users_panel():
    """يبني نص اللوحة وأزرارها. كل إجراء تغيير دور يمر عبر ask* لتأكيد صريح،
    ويختفي الزر فور الضغط (استبدال الرسالة بالبطاقة) فلا يتكرر النقر المزدوج."""
    pending, approved = _get_users_snapshot()

    # v53: عدّ الأسماء المطبّعة لكشف المكرر — من له مكرر يظهر له زر «دمج»
    name_dup = {}
    for u in approved:
        k = _norm_name_key(_merge_display_name(u))
        if k:
            name_dup[k] = name_dup.get(k, 0) + 1

    buttons = []
    msg_lines = ["⚙️ <b>إدارة المستخدمين</b>\n"]

    # ── المعلّقون ──
    if pending:
        msg_lines.append("🔔 <b>منتظرون للموافقة (" + str(len(pending)) + "):</b>")
        shown_pending = pending[:_MAX_PANEL_ROWS]
        for u in shown_pending:
            label, extras = _display_label(u)
            extras = list(extras)
            if u.get("requested_role"):
                extras.append("🎯 يطلب: " + ROLE_LABELS.get(u["requested_role"], u["requested_role"]))
            date_str = (
                u["created_at"].strftime("%m/%d %H:%M") if u.get("created_at") else ""
            )
            msg_lines.append("  • " + label + " — " + " | ".join(extras) + (" ⏱ " + date_str if date_str else ""))
            buttons.append([
                InlineKeyboardButton(
                    "✅ تنشيط: " + label[:20],
                    callback_data="approve_" + str(u["telegram_id"]),
                ),
                InlineKeyboardButton("🗑 رفض", callback_data="reject_" + str(u["telegram_id"])),
            ])
        if len(pending) > len(shown_pending):
            msg_lines.append("  … و" + str(len(pending) - len(shown_pending)) + " معلّق آخر (حد العرض)")
        msg_lines.append("")
    else:
        msg_lines.append("✅ لا توجد طلبات معلقة.")

    msg_lines.append("")

    # ── المفعّلون (مجمّعون حسب الفئة) ──
    role_counts = {}
    for u in approved:
        _r = u.get("role", "customer")
        role_counts[_r] = role_counts.get(_r, 0) + 1
    counts_str = " | ".join(
        ROLE_LABELS.get(_r, _r) + ": " + str(_c) for _r, _c in role_counts.items()
    )
    kicked_n = sum(1 for u in approved if not u.get("active"))
    if kicked_n:
        counts_str = counts_str + " | 🚫 مطرودون: " + str(kicked_n)

    if approved:
        msg_lines.append(
            "✅ <b>المستخدمون المفعّلون (" + str(len(approved)) + ")</b> "
            "— " + counts_str
        )

    shown_rows = 0
    for u in approved:
        if shown_rows >= _MAX_PANEL_ROWS:
            break
        shown_rows += 1
        label, extras = _display_label(u)
        role_icon = ROLE_LABELS.get(u.get("role", "customer"), "👤").split(" ")[0]
        flags = []
        if u.get("can_extract"):
            flags.append("🛒")
        if not u.get("active"):
            flags.append("🚫 مطرود")
        msg_lines.append(
            "  " + role_icon + " " + label + " — " + " | ".join(extras)
            + (("  " + "".join(flags)) if flags else "")
        )
    if len(approved) > shown_rows:
        msg_lines.append("  … و" + str(len(approved) - shown_rows) + " مستخدم آخر (حد العرض)")

    # ── أزرار الإجراءات (كلها عبر خطوة تأكيد ask*) ──
    action_rows = 0
    for u in approved:
        tid = u["telegram_id"]
        if _protected_account(tid):
            continue  # حسابات البيئة/المالك لا تظهر لها أزرار تعديل
        if action_rows >= _MAX_PANEL_ROWS:
            break
        label = (u.get("first_name") or ("@" + u["username"] if u.get("username") else str(tid)))[:18]
        if not u.get("active"):
            # 🚫 مطرود — الإعادة بيد المدير فقط من هنا (بلا إعادة طلب ذاتي)
            buttons.append([
                InlineKeyboardButton(
                    "✅ إعادة تنشيط: " + label,
                    callback_data="unban_" + str(tid),
                ),
            ])
            action_rows += 1
        elif u["role"] == "admin":
            # أدوار إدارية: تغيير الفئة فقط — الطرد بعد تنزيله إلى عميل (بحماية المالك)
            buttons.append([
                InlineKeyboardButton(
                    "🎯 الفئة: " + label,
                    callback_data="askrole_" + str(tid),
                ),
            ])
            action_rows += 1
        elif u["role"] == "customer":
            buttons.append([
                InlineKeyboardButton(
                    "🎯 الفئة: " + label,
                    callback_data="askrole_" + str(tid),
                ),
                InlineKeyboardButton("🚫 طرد", callback_data="askkick_" + str(tid)),
            ])
            action_rows += 1
        else:
            # الفئات الجديدة (manager / stock / seller / external)
            buttons.append([
                InlineKeyboardButton(
                    "🎯 الفئة: " + label,
                    callback_data="askrole_" + str(tid),
                ),
                InlineKeyboardButton("🚫 طرد", callback_data="askkick_" + str(tid)),
            ])
            action_rows += 1
        # v53: زر الدمج — فقط لمن يشارك اسمه مع حساب آخر (المكرر مثل Mostafa)
        if tid and name_dup.get(_norm_name_key(_merge_display_name(u)), 0) > 1 \
                and action_rows < _MAX_PANEL_ROWS:
            buttons.append([
                InlineKeyboardButton(
                    "🧬 دمج الحساب المكرر: " + label[:16],
                    callback_data="askmerge_" + str(tid),
                ),
            ])
            action_rows += 1

    # ── أدوات اللوحة ──
    buttons.append([
        InlineKeyboardButton("🔄 تحديث", callback_data="users_refresh"),
        InlineKeyboardButton("✖️ إغلاق", callback_data="users_close"),
    ])

    markup = InlineKeyboardMarkup(buttons)
    text = "\n".join(msg_lines)
    return text, markup


async def _send_users_panel(update, context):
    """عرض اللوحة: كردّ على أمر أو كتعديل لرسالة قائمة (يطفئ الأزرار القديمة)."""
    query = update.callback_query
    text, markup = _build_users_panel()
    if query is not None:
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def users_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ للمديرين فقط.")
        return
    await _send_users_panel(update, context)


async def callback_users_refresh_handler(update, context):
    """زر التحديث: يعيد رسم اللوحة بأحدث البيانات."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer("🔄 تم التحديث")
    try:
        await _send_users_panel(update, context)
    except Exception as e:
        logging.error("[USERS] Refresh failed: " + str(e))


async def callback_users_close_handler(update, context):
    """زر الإغلاق: يجفف الرسالة ويزيل كل الأزرار."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    await _safe_edit(query, "🔒 أُغلقت لوحة المستخدمين.")


async def callback_role_menu_handler(update, context):
    """🎯 قائمة تغيير فئة مستخدم من لوحة المستخدمين
    (بديل شامل للترقية/الإزالة: كل الفئات في قائمة واحدة)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return
    tid = int(parts[1])
    if _protected_account(tid):
        await _safe_edit(query, "🔒 هذا الحساب محمي (المالك/متغيرات البيئة) — لا يمكن تعديل فئته من هنا.")
        return
    try:
        target = get_user(tid)
    except Exception:
        target = None
    tname = (target.get("first_name") or ("@" + target["username"] if target.get("username") else str(tid))) if target else str(tid)
    current = (target or {}).get("role", "customer")
    rows = []
    for r in (ROLE_ADMIN, ROLE_MANAGER, ROLE_STOCK, ROLE_SELLER, ROLE_EXTERNAL, ROLE_CUSTOMER):
        mark = "✅" if r == current else "▫️"
        rows.append([InlineKeyboardButton(
            mark + " " + ROLE_LABELS[r],
            callback_data="setrole_" + r + "_" + str(tid))])
    rows.append([InlineKeyboardButton("🔙 رجوع للوحة", callback_data="users_refresh")])
    await _safe_edit(
        query,
        "🎯 تغيير الفئة\n\n👤 " + tname
        + "\n📌 الفئة الحالية: " + ROLE_LABELS.get(current, current)
        + "\n\nاختر الفئة الجديدة:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def callback_role_set_handler(update, context):
    """تنفيذ تغيير الفئة بعد الاختيار من القائمة + إشعار المستخدم."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_")  # setrole_<role>_<tid>
    if len(parts) != 3:
        return
    _, role, tid_str = parts
    if role not in ROLE_LABELS or not tid_str.isdigit():
        return
    tid = int(tid_str)
    if _protected_account(tid):
        await _safe_edit(query, "🔒 حساب محمي — لا يمكن تعديل فئته من داخل البوت.")
        return
    try:
        changed = set_user_role(tid, role)
    except Exception as role_err:
        await _safe_edit(query, "❌ خطأ في تغيير الفئة: " + str(role_err))
        return
    try:
        target = get_user(tid)
    except Exception:
        target = None
    tname = (target.get("first_name") or ("@" + target["username"] if target.get("username") else str(tid))) if target else str(tid)
    if changed:
        await _safe_edit(
            query,
            "✅ تم تغيير الفئة بنجاح\n\n👤 " + tname
            + "\n🎯 الفئة الجديدة: " + ROLE_LABELS[role],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع للوحة", callback_data="users_refresh")],
            ]),
        )
        try:
            await context.bot.send_message(
                chat_id=tid,
                text="🎯 حدّث المدير فئتك إلى: " + ROLE_LABELS[role]
                + "\n🔄 اضغط 🏠 القائمة الرئيسية أو /start لظهور لوحتك الجديدة."
                + "\n📱 في التطبيق: تُحدَّث صلاحياتك تلقائياً لحظة عودتك إليه.",
            )
        except Exception:
            pass
    else:
        await _safe_edit(query, "⚠️ لم يتغير شيء (المستخدم غير موجود).")


# ═══════════════════════════════════════════════════════════
#  INVOICE SYSTEM FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def _invoice_add_item(update, context, product_id, product_name, qty, unit_price, ptype, extra_info=None):
    """Add item to invoice draft. ptype: 'm2', 'pcs', 'plant'."""
    items = context.user_data.get("invoice_items", [])
    item = {
        "product_id": product_id,
        "product_name": product_name,
        "ptype": ptype,
        "quantity": qty,
        "unit_price": unit_price,
    }
    if ptype == "m2":
        # qty_m2 is the user-requested area; subtotal = area × price/m²
        item["qty_m2"] = extra_info.get("requested_area", qty) if extra_info else qty
        item["box_area"] = extra_info.get("box_area", 1) if extra_info else 1
        item["num_boxes"] = extra_info.get("num_boxes", qty) if extra_info else qty
        item["subtotal"] = item["qty_m2"] * unit_price
    elif ptype == "pcs":
        item["subtotal"] = qty * unit_price
    elif ptype == "plant":
        import math
        item["length_m"] = extra_info.get("length_m", 0.45) if extra_info else 0.45
        item["qty_m"] = qty
        item["num_pieces"] = math.ceil(qty / item["length_m"])
        item["subtotal"] = qty * unit_price
    items.append(item)
    context.user_data["invoice_items"] = items
    chat_id = update.effective_chat.id
    total = sum(i["subtotal"] for i in items)
    msg = "✅ تمت الإضافة (" + str(len(items)) + " عناصر)" + chr(10) + chr(10)
    if ptype == "m2":
        msg += "📦 " + product_name + " → " + str(round(item["qty_m2"], 2)) + " m² × " + _fmt_price(unit_price) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    elif ptype == "pcs":
        msg += "📦 " + product_name + " → " + str(qty) + " قطعة = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    elif ptype == "plant":
        msg += "📦 " + product_name + " → " + str(qty) + " م.ط = " + str(item["num_pieces"]) + " قطعة (" + str(item["length_m"]) + " م) = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    msg += chr(10) + "💰 الإجمالي الحالي: " + _fmt_price(total) + " د.ج"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة منتج جديد", callback_data="invoice_add_new")],
        [InlineKeyboardButton("📋 إنهاء الفاتورة", callback_data="invoice_review"),
         InlineKeyboardButton("🗑 حذف آخر عنصر", callback_data="invoice_remove_last")],
        [InlineKeyboardButton("❌ إلغاء الفاتورة", callback_data="invoice_cancel")],
    ])
    context.user_data["state"] = STATE_INVOICE_PRODUCT
    await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard)


def _fmt_price(val):
    """Format price with Algerian convention (comma as decimal separator)."""
    return "{:,.2f}".format(val).replace(",", "X").replace(".", ",").replace("X", ".")


async def _invoice_show_review(update_or_query, context, from_callback=False):
    """Show invoice draft for approval."""
    items = context.user_data.get("invoice_items", [])
    customer = context.user_data.get("invoice_customer", "")
    if not items:
        if from_callback:
            await update_or_query.edit_message_text("❌ لا توجد عناصر في الفاتورة.")
        else:
            await update_or_query.message.reply_text("❌ لا توجد عناصر.")
        return
    total = sum(i["subtotal"] for i in items)
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y/%m/%d %H:%M")
    msg = "🧾 مسودة فاتورة" + chr(10) + chr(10)
    msg += "👤 الزبون: " + customer + chr(10)
    msg += "📅 التاريخ: " + now_str + chr(10)
    msg += "━" * 20 + chr(10)
    for i, item in enumerate(items, 1):
        msg += str(i) + ". " + item["product_name"] + chr(10)
        pt = item.get("ptype", "m2")
        if pt == "m2":
            msg += "   📐 " + str(item.get("qty_m2", "")) + " m² → " + str(item["quantity"]) + " علبة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/m² × " + str(round(item.get("qty_m2", 0), 2)) + " m² = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
        elif pt == "pcs":
            msg += "   📦 " + str(item["quantity"]) + " قطعة" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/قطعة × " + str(item["quantity"]) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
        elif pt == "plant":
            msg += "   📏 " + str(item.get("qty_m", item["quantity"])) + " م.ط = " + str(item.get("num_pieces", "")) + " قطعة (" + str(item.get("length_m", "")) + " م)" + chr(10)
            msg += "   💰 " + _fmt_price(item["unit_price"]) + " د.ج/م.ط × " + str(item.get("qty_m", item["quantity"])) + " = " + _fmt_price(item["subtotal"]) + " د.ج" + chr(10)
    msg += "━" * 20 + chr(10)
    msg += "💰 الإجمالي: " + _fmt_price(total) + " د.ج"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ اعتماد الفاتورة والخصم", callback_data="invoice_approve"),
            InlineKeyboardButton("❌ إلغاء وتعديل", callback_data="invoice_cancel"),
        ],
    ])
    if from_callback:
        await update_or_query.edit_message_text(msg, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(msg, reply_markup=keyboard)


async def _invoice_show_workers(update_or_query, context, from_callback=False):
    """Show worker selection list after invoice approval."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, telegram_id, username, first_name FROM users "
                "WHERE role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') AND active = TRUE "
                "ORDER BY first_name"
            )
            workers = cur.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        workers = []
    finally:
        release_connection(conn)

    if not workers:
        msg = "❌ لا يوجد عمال مسجلين في النظام."
        if from_callback:
            await update_or_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    msg = "👷 اختر العامل المسؤول عن تجهيز الطلبية:" + chr(10) + chr(10)
    buttons = []
    for w in workers:
        name = w["first_name"] or w["username"] or str(w["telegram_id"])
        msg += "  👤 " + name + chr(10)
        buttons.append([InlineKeyboardButton("👤 " + name, callback_data="inv_worker_" + str(w["id"]) + "_" + str(w["telegram_id"]))])

    if from_callback:
        await update_or_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update_or_query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))


def _generate_invoice_pdf(invoice_id, customer_name, items, total, worker_name=None):
    """Generate a professional PDF invoice file. Returns file path."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import tempfile
        import os

        # Register fonts - French-first order: DejaVu covers é à ç ° × fully
        font_path = None
        _font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf",
            "/usr/share/fonts/truetype/noto-serif-sc/NotoSerifSC-Regular.ttf",
        ]
        for _fp in _font_candidates:
            if os.path.isfile(_fp):
                font_path = _fp
                break
        if not font_path:
            # Try to find any TTF font on the system
            import subprocess
            try:
                result = subprocess.run(["find", "/usr/share/fonts", "-name", "*.ttf"],
                                        capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    font_path = result.stdout.strip().split("\n")[0]
            except Exception:
                pass
        if not font_path:
            logging.error("[INVOICE] No TTF font found on system for PDF generation")
            return None
        pdfmetrics.registerFont(TTFont("NotoSans", font_path))

        # ── Theme: full-page branded background (LE ROI DE LA CÉRAMIQUE) ──
        _theme_bg_bytes = _build_invoice_background()
        from reportlab.lib.utils import ImageReader

        def _paint_theme_bg(canvas, _doc):
            """Paint the branded background behind invoice content."""
            try:
                if _theme_bg_bytes:
                    canvas.drawImage(ImageReader(io.BytesIO(_theme_bg_bytes)),
                                     0, 0, width=A4[0], height=A4[1])
                elif _theme_logo_load():
                    # Fallback without PIL: draw the logo as a top header
                    _side = 52 * mm
                    canvas.drawImage(ImageReader(io.BytesIO(_theme_logo_load())),
                                     (A4[0] - _side) / 2.0, A4[1] - _side - 12 * mm,
                                     width=_side, height=_side, mask="auto")
            except Exception as bg_paint_err:
                logging.warning("[INVOICE] Background paint failed: " + str(bg_paint_err))

        # Create temp file
        fd, pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="invoice_")
        os.close(fd)

        doc = SimpleDocTemplate(
            pdf_path, pagesize=A4,
            rightMargin=15*mm, leftMargin=15*mm,
            topMargin=88*mm, bottomMargin=15*mm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("ArabicTitle", parent=styles["Title"], fontName="NotoSans", fontSize=22, alignment=1, spaceAfter=5*mm, textColor=colors.Color(0.60, 0.31, 0.17))
        subtitle_style = ParagraphStyle("ArabicSubtitle", parent=styles["Normal"], fontName="NotoSans", fontSize=11, alignment=1, spaceAfter=3*mm)
        body_style = ParagraphStyle("ArabicBody", parent=styles["Normal"], fontName="NotoSans", fontSize=10, leading=14)
        small_style = ParagraphStyle("ArabicSmall", parent=styles["Normal"], fontName="NotoSans", fontSize=8, alignment=1)

        elements = []
        elements.append(Paragraph("LE ROI DE LA CÉRAMIQUE", title_style))
        elements.append(Paragraph("Facture de Vente", subtitle_style))

        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))
        date_str = now.strftime("%Y/%m/%d %H:%M")

        info_data = [
            [Paragraph("N° Facture", body_style), Paragraph("#" + str(invoice_id), body_style)],
            [Paragraph("Date", body_style), Paragraph(date_str, body_style)],
            [Paragraph("Client", body_style), Paragraph(customer_name, body_style)],
        ]
        if worker_name:
            info_data.append([Paragraph("Livreur", body_style), Paragraph(worker_name, body_style)])

        info_table = Table(info_data, colWidths=[60*mm, 80*mm])
        info_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3*mm),
            ("LINEBELOW", (0, -1), (-1, -1), 1, colors.grey),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 5*mm))

        # Items table - dynamic columns based on product types
        header = [
            Paragraph("Produit", body_style),
            Paragraph("Quantité", body_style),
            Paragraph("Prix", body_style),
            Paragraph("Total (DA)", body_style),
        ]
        table_data = [header]
        for item in items:
            pt = item.get("ptype", "m2")
            if pt == "pcs":
                table_data.append([
                    Paragraph(item["product_name"], body_style),
                    Paragraph(str(item["quantity"]) + " pcs", body_style),
                    Paragraph("{:,.2f}".format(item["unit_price"]) + " DA/pcs", body_style),
                    Paragraph("{:,.2f}".format(item["subtotal"]), body_style),
                ])
            elif pt == "plant":
                n_pcs = item.get("num_pieces", "")
                qty_txt = str(item.get("qty_m", item["quantity"])) + " m. lin."
                if n_pcs:
                    qty_txt += " (" + str(n_pcs) + " pcs)"
                table_data.append([
                    Paragraph(item["product_name"], body_style),
                    Paragraph(qty_txt, body_style),
                    Paragraph("{:,.2f}".format(item["unit_price"]) + " DA/m", body_style),
                    Paragraph("{:,.2f}".format(item["subtotal"]), body_style),
                ])
            else:
                qty_display = str(round(item.get("qty_m2", item["quantity"]), 2)) + " m²"
                crt_info = item.get("num_boxes") and item.get("num_boxes") != item.get("qty_m2")
                if crt_info:
                    qty_display += " (" + str(item["num_boxes"]) + " crt)"
                table_data.append([
                    Paragraph(item["product_name"], body_style),
                    Paragraph(qty_display, body_style),
                    Paragraph("{:,.2f}".format(item["unit_price"]) + " DA/m²", body_style),
                    Paragraph("{:,.2f}".format(item["subtotal"]), body_style),
                ])

        table_data.append([
            Paragraph("TOTAL", body_style), "", "",
            Paragraph("{:,.2f}".format(total), body_style),
        ])

        items_table = Table(table_data, colWidths=[55*mm, 35*mm, 35*mm, 30*mm])
        items_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.60, 0.31, 0.17)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3*mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3*mm),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LINEBELOW", (0, -1), (-1, -1), 2, colors.Color(0.60, 0.31, 0.17)),
            ("BACKGROUND", (0, -1), (-1, -1), colors.white),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 10*mm))
        elements.append(Paragraph("Merci pour votre confiance !", small_style))

        doc.build(elements, onFirstPage=_paint_theme_bg, onLaterPages=_paint_theme_bg)
        return pdf_path
    except Exception as e:
        logging.error("[INVOICE] PDF generation failed: " + str(e))
        return None


async def _invoice_finalize(update, context, worker_id, worker_tid, worker_name):
    """Finalize invoice: save to DB, deduct stock, generate PDF, notify worker.
    ⚡ Idempotency guard: the draft is CLAIMED via pop() as the very first
    synchronous step. A double-click on the worker button (or any replay)
    finds no draft and exits immediately — so one click can never create
    two invoices or deduct stock twice."""
    items = context.user_data.pop("invoice_items", None)
    customer = context.user_data.get("invoice_customer", "")
    if not items:
        chat_hint = update.effective_chat.id if update.effective_chat else None
        if chat_hint:
            try:
                await context.bot.send_message(chat_id=chat_hint, text="⚠️ لا توجد مسودة فاتورة نشطة — ربما تم إنشاؤها للتو.")
            except Exception:
                pass
        return
    admin = update.effective_user
    chat_id = update.effective_chat.id
    total = sum(i["subtotal"] for i in items)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))

    async def _send(text, markup=None):
        """Safe send helper — works for both Update and CallbackQuery."""
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)

    conn = get_connection()
    try:
        # Get admin DB id first (admin.id is telegram_id, not DB id)
        admin_user = get_user(admin.id)
        admin_db_id = admin_user["id"] if admin_user else None
        if not admin_db_id:
            await _send("❌ حدث خطأ: لم يتم العثور على بيانات المدير.", get_admin_keyboard())
            return

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Create invoice record
            cur.execute(
                "INSERT INTO invoices (customer_name, worker_id, admin_id, total_amount, status, approved_at) "
                "VALUES (%s, %s, %s, %s, 'approved', NOW()) RETURNING id",
                (customer, worker_id, admin_db_id, total)
            )
            invoice = cur.fetchone()
            invoice_id = invoice["id"]
            for item in items:
                # For m2 products, quantity is the area (m²); use num_boxes for stock deduction
                stock_qty = item.get("num_boxes", item["quantity"]) if item.get("ptype") == "m2" else item["quantity"]
                cur.execute(
                    "INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, subtotal) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (invoice_id, item["product_id"], item["product_name"], item["quantity"], item["unit_price"], item["subtotal"])
                )
                # Deduct from stock if product exists in DB
                if item["product_id"]:
                    cur.execute(
                        "UPDATE products SET quantity = quantity - %s WHERE id = %s AND quantity >= %s",
                        (stock_qty, item["product_id"], stock_qty)
                    )
                    if cur.rowcount == 0:
                        logging.warning("[INVOICE] Insufficient stock for product %s (need %s)", item["product_id"], stock_qty)
                    # Log transaction
                    cur.execute(
                        "INSERT INTO transactions (product_id, type, quantity, user_id) VALUES (%s, 'remove', %s, %s)",
                        (item["product_id"], stock_qty, admin_db_id)
                    )

        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[INVOICE] DB save failed: " + str(e))
        await _send("❌ حدث خطأ في حفظ الفاتورة: " + str(e), get_admin_keyboard())
        return
    finally:
        release_connection(conn)

    # Generate PDF — reportlab مكلف CPU/IO، نشغّله في thread حتى لا تتجمد
    # بقية المستخدمين لحظة إنشاء الفاتورة (الحلقة تبقى حرة).
    pdf_path = await asyncio.to_thread(
        _generate_invoice_pdf, invoice_id, customer, items, total, worker_name
    )

    # Send PDF to admin
    if pdf_path:
        try:
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename="invoice_" + str(invoice_id) + ".pdf",
                    caption="🧾 فاتورة #" + str(invoice_id) + " - " + customer + chr(10)
                    + "💰 الإجمالي: " + _fmt_price(total) + " د.ج" + chr(10)
                    + "👷 العامل: " + worker_name
                )
            os.remove(pdf_path)
        except Exception as e:
            logging.error("[INVOICE] PDF send failed: " + str(e))

    # Notify worker
    worker_notified = False
    try:
        worker_msg = "🧾 فاتورة جديدة للتجهيز!" + chr(10) + chr(10)
        worker_msg += "👤 الزبون: " + customer + chr(10)
        worker_msg += "📅 " + now.strftime("%Y/%m/%d %H:%M") + chr(10)
        worker_msg += "━" * 15 + chr(10)
        for i, item in enumerate(items, 1):
            pt = item.get("ptype", "m2")
            if pt == "pcs":
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item["quantity"]) + " قطعة" + chr(10)
            elif pt == "plant":
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item.get("qty_m", item["quantity"])) + " م.ط" + chr(10)
            else:
                worker_msg += str(i) + ". " + item["product_name"] + " — " + str(item.get("qty_m2", "")) + "m² → " + str(item["quantity"]) + " علبة" + chr(10)
        worker_msg += "━" * 15 + chr(10)
        worker_msg += "💰 الإجمالي: " + _fmt_price(total) + " د.ج" + chr(10) + chr(10)
        worker_msg += "⚙️ يرجى تجهيز البضاعة فوراً"
        await context.bot.send_message(chat_id=worker_tid, text=worker_msg)
        worker_notified = True
    except Exception as e:
        logging.error("[INVOICE] Worker notification failed: " + str(e))

    # Clear state and return to admin panel
    context.user_data.clear()
    result_msg = "✅ تم إنشاء الفاتورة بنجاح!" + chr(10) + chr(10)
    result_msg += "🧾 #" + str(invoice_id) + chr(10)
    result_msg += "👤 " + customer + chr(10)
    result_msg += "💰 " + _fmt_price(total) + " د.ج" + chr(10)
    result_msg += "👷 " + worker_name + chr(10) + chr(10)
    if pdf_path:
        result_msg += "📎 تم إرسال الفاتورة PDF" + chr(10)
    if worker_notified:
        result_msg += "📨 تم إشعار العامل" + chr(10)
    result_msg += "✅ تم الخصم من المخزون"
    await _send(result_msg, get_admin_keyboard())


async def callback_invoice_handler(update, context):
    """Handle invoice-related callback queries."""
    query = update.callback_query
    _KB_ROLE_UID.set(query.from_user.id)
    # 🎯 الفواتير للمديرين والبائعين (البائع يدير فواتيره عبر نفس المسار)
    if not (is_admin(query.from_user.id) or _get_role(query.from_user.id) == ROLE_SELLER):
        await query.answer("❌ هذا الإجراء للمديرين والبائعين فقط.", show_alert=True)
        return
    await query.answer()
    data = query.data

    if data == "invoice_review":
        await _invoice_show_review(query, context, from_callback=True)

    elif data == "invoice_approve":
        await _invoice_show_workers(query, context, from_callback=True)

    elif data == "invoice_cancel":
        context.user_data.clear()
        try:
            await query.edit_message_text(
                "❌ تم إلغاء الفاتورة.",
                reply_markup=None
            )
        except Exception:
            pass
        await _send_themed(
            context.bot, query.message.chat_id,
            "📦 القائمة الرئيسية:",
            reply_markup=get_admin_keyboard()
        )

    elif data == "invoice_back_customer":
        _set_state(context, STATE_INVOICE_CUSTOMER)
        context.user_data.pop("invoice_customer", None)
        try:
            await query.edit_message_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\U0001f464 \u0623\u0639\u062f \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646:")

    elif data == "invoice_back_product":
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        context.user_data.pop("invoice_tmp_product", None)
        context.user_data.pop("invoice_tmp_pid", None)
        try:
            await query.edit_message_text(
                "\U0001f4e6 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:" + chr(10) + chr(10)
                + "\U0001f50d \u0627\u0643\u062a\u0628 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629 \u0644\u0644\u0628\u062d\u062b \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646" + chr(10)
                + "\U0001f4cb \u0623\u0648 \u0627\u0643\u062a\u0628 \u0627\u0644\u0633\u0644\u0639\u0629 \u0645\u0628\u0627\u0634\u0631\u0629 \u0625\u0630\u0627 \u0644\u0645 \u062a\u0643\u0646 \u0641\u064a \u0627\u0644\u0645\u062e\u0632\u0648\u0646",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f519 \u062a\u0631\u0627\u062c\u0639", callback_data="invoice_back_customer"),
                     InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="\U0001f4e6 \u0623\u0639\u062f \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:")

    elif data == "invoice_back_qty":
        context.user_data["state"] = STATE_INVOICE_QTY
        context.user_data.pop("invoice_tmp_qty", None)
        product_name = context.user_data.get("invoice_tmp_product", "\u0627\u0644\u0645\u0646\u062a\u062c")
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")
        if ptype == "pcs":
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل عدد القطع:"
        elif ptype == "plant":
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل الطول بالمتر (م.ط):"
        else:
            q_text = "📦 " + product_name + chr(10) + chr(10) + "🔢 أرسل الكمية المطلوبة (m²):"
        try:
            await query.edit_message_text(
                q_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=q_text)

    elif data == "invoice_remove_last":
        items = context.user_data.get("invoice_items", [])
        if items:
            removed = items.pop()
            context.user_data["invoice_items"] = items
            msg = "\U0001f5d1 \u062a\u0645 \u062d\u0630\u0641: " + removed["product_name"] + " \u00d7 " + str(removed["quantity"])
            if items:
                total = sum(i["subtotal"] for i in items)
                msg += chr(10) + chr(10) + "\U0001f4cb \u0627\u0644\u0639\u0646\u0627\u0635\u0631 \u0627\u0644\u0645\u062a\u0628\u0642\u064a\u0629 (" + str(len(items)) + "):" + chr(10)
                for i, item in enumerate(items, 1):
                    msg += "  " + str(i) + ". " + item["product_name"] + " \u00d7 " + str(item["quantity"]) + chr(10)
                msg += chr(10) + "\U0001f4b0 \u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a: " + _fmt_price(total) + " \u062f.\u062c" + chr(10)
                msg += chr(10) + "\U0001f4dd \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c \u0627\u0644\u062a\u0627\u0644\u064a"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\U0001f4cb \u0645\u0631\u0627\u062c\u0639\u0629 \u0627\u0644\u0641\u0627\u062a\u0648\u0631\u0629", callback_data="invoice_review"),
                     InlineKeyboardButton("\U0001f5d1 \u062d\u0630\u0641 \u0622\u062e\u0631 \u0639\u0646\u0635\u0631", callback_data="invoice_remove_last")],
                ])
            else:
                msg += chr(10) + chr(10) + "\U0001f4dd \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0645\u0646\u062a\u062c:"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ])
            try:
                await query.edit_message_text(msg, reply_markup=keyboard)
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text=msg, reply_markup=keyboard)
        else:
            try:
                await query.edit_message_text("\u274c \u0644\u0627 \u062a\u0648\u062c\u062f \u0639\u0646\u0627\u0635\u0631 \u0644\u0644\u062d\u0630\u0641.")
            except Exception:
                pass

    elif data.startswith("invsel_"):
        # Product selection callback from invoice product search
        if data.startswith("invsel_manual_"):
            # Manual entry - go to qty step
            manual_name = data[len("invsel_manual_"):]
            ptype = _invoice_product_type(manual_name)
            context.user_data["invoice_tmp_product"] = manual_name
            context.user_data["invoice_tmp_pid"] = None
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            if ptype == "pcs":
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q_text = "✍️ إدخال يدوي: " + manual_name + chr(10) + chr(10) + "🔢 أرسل المساحة المطلوبة (m²):"
            try:
                await query.edit_message_text(
                    q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
        else:
            # Selected from DB - extract product id
            try:
                product_id = int(data[len("invsel_"):])
            except ValueError:
                return
            conn = get_connection()
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT id, name, quantity, selling_price FROM products WHERE id = %s", (product_id,))
                    prod = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                prod = None
            finally:
                release_connection(conn)
            if not prod:
                try:
                    await query.edit_message_text("❌ المنتج غير موجود.")
                except Exception:
                    pass
                return
            ptype = _invoice_product_type(prod["name"])
            context.user_data["invoice_tmp_product"] = prod["name"]
            context.user_data["invoice_tmp_pid"] = prod["id"]
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            stock_info = " (المتوفر: " + str(prod["quantity"]) + ")" if prod["quantity"] > 0 else " (غير متوفر! ⚠️)"
            if ptype == "pcs":
                q_text = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q_text = "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q_text = "🔢 أرسل المساحة المطلوبة (m²):"
            try:
                await query.edit_message_text(
                    "📦 " + prod["name"] + stock_info + chr(10) + chr(10) + q_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("\U0001f519 تراجع", callback_data="invoice_back_product"),
                         InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                    ]))
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="📦 " + prod["name"] + stock_info + chr(10) + chr(10) + q_text)

    elif data == "invoice_add_new":
        # Add new product to invoice
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        try:
            await query.edit_message_text("📦 أرسل اسم المنتج:")
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="📦 أرسل اسم المنتج:")

    elif data.startswith("inv_plen_"):
        # Plante length selection
        try:
            length_m = float(data[len("inv_plen_"):])
        except ValueError:
            return
        context.user_data["invoice_tmp_plant_len"] = length_m
        qty = context.user_data.get("invoice_tmp_qty", 0)
        product_name = context.user_data.get("invoice_tmp_product", "")
        context.user_data["state"] = STATE_INVOICE_PRICE
        try:
            await query.edit_message_text(
                "💰 أرسل سعر المتر الطولي (د.ج/م.ط):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📏 " + str(qty) + " م.ط (شريط " + str(length_m) + " م)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="💰 أرسل سعر المتر الطولي (د.ج/م.ط):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📏 " + str(qty) + " م.ط (شريط " + str(length_m) + " م)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))

    elif data.startswith("inv_worker_"):
        parts = data.split("_")
        worker_id = int(parts[2])
        worker_tid = int(parts[3])
        # Get worker name
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT first_name, username FROM users WHERE id = %s", (worker_id,))
                w = cur.fetchone()
                worker_name = (w["first_name"] or w["username"] or str(worker_tid)) if w else str(worker_tid)
            conn.commit()
        except Exception:
            conn.rollback()
            worker_name = str(worker_tid)
        finally:
            release_connection(conn)
        # Show processing message
        try:
            await query.edit_message_text("⏳ جاري معالجة الفاتورة...")
        except Exception:
            pass
        # Finalize
        try:
            await _invoice_finalize(update, context, worker_id, worker_tid, worker_name)
        except Exception as e:
            logging.error("[INVOICE] Finalize unexpected error: " + str(e), exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="❌ حدث خطأ أثناء إنشاء الفاتورة: " + str(e),
                    reply_markup=get_admin_keyboard()
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
#  MESSAGE ROUTER
# ═══════════════════════════════════════════════════════════

async def _process_mov_product_input(update, context, text, is_admin=True):
    """Handle product name input — show matching products as buttons."""
    text = text.strip()
    if text == "❌" or text == BTN_CANCEL or text == BTN_CUST_CANCEL:
        context.user_data.clear()
        kb = get_role_keyboard(update.effective_user.id)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=kb)
        return
    results = search_products(text)
    if not results:
        await update.message.reply_text(
            "❌ لا توجد نتائج لـ '" + text + "'\n\nجرب اسم آخر أو أرسل ❌ للإلغاء.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="movd_back")]
            ]),
        )
        return
    # Show matching products as buttons
    buttons = []
    for p in results:
        unit_ar = _get_unit_ar(p["name"])
        icon = _product_status_icon(p)
        label = p["name"] + " — " + str(p["quantity"]) + " " + unit_ar + " " + icon
        buttons.append([InlineKeyboardButton(label, callback_data="movp_" + str(p["id"]))])
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="movd_back")])
    await update.message.reply_text(
        "📦 اختر السلعة لعرض حركاتها:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _process_mov_range_input(update, context, text, is_admin):
    """Shared handler for date-range input states (waiting_mov_range_from / _to)."""
    state = context.user_data.get("state")
    text = text.strip()
    if text == "❌" or text == BTN_CANCEL or text == BTN_CUST_CANCEL:
        context.user_data.clear()
        kb = get_role_keyboard(update.effective_user.id)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=kb)
        return True
    try:
        parsed_date = datetime.date.fromisoformat(text)
    except ValueError:
        await update.message.reply_text(
            "❌ صيغة التاريخ غير صحيحة. اكتبها هكذا:\n"
            "<code>YYYY-MM-DD</code> (مثال: 2026-08-01)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_input_keyboard(),
        )
        return True
    if state == "waiting_mov_range_from":
        context.user_data["mov_range_from"] = parsed_date
        context.user_data["state"] = "waiting_mov_range_to"
        await update.message.reply_text(
            "✅ تاريخ البداية: " + parsed_date.strftime("%Y-%m-%d") + "\n\n"
            "الآن اكتب تاريخ النهاية بنفس الصيغة:",
            reply_markup=get_input_keyboard(),
        )
        return True
    if state == "waiting_mov_range_to":
        start_date = context.user_data.get("mov_range_from")
        if not start_date:
            context.user_data.clear()
            kb = get_role_keyboard(update.effective_user.id)
            await update.message.reply_text("❌ فُقدت بيانات البداية. حاول مرة أخرى.", reply_markup=kb)
            return True
        if parsed_date < start_date:
            await update.message.reply_text(
                "❌ تاريخ النهاية يجب أن يكون بعد تاريخ البداية (" + start_date.strftime("%Y-%m-%d") + ").",
                reply_markup=get_input_keyboard(),
            )
            return True
        try:
            rows = _fetch_movements_by_range(start_date, parsed_date)
            rows = _filter_test_pairs(rows)
            date_display = start_date.strftime("%Y-%m-%d") + " ← " + parsed_date.strftime("%Y-%m-%d")
            if not rows:
                await update.message.reply_text(
                    "📋 حركة " + date_display + ":\n\nلا توجد عمليات في هذا النطاق.",
                    reply_markup=get_role_keyboard(update.effective_user.id),
                )
                context.user_data.clear()
                return True
            text_out = _build_movements_text(rows)
            header = "📋 حركة " + date_display + " (" + str(len(rows)) + "):"
            body_lines = text_out.split("\n")[2:]
            final_text = header + "\n" + "\n".join(body_lines)
            kb = get_role_keyboard(update.effective_user.id)
            try:
                if len(final_text) <= 4096:
                    await update.message.reply_text(final_text, parse_mode=ParseMode.HTML, reply_markup=kb)
                else:
                    # Send in chunks
                    await update.message.reply_text(final_text[:3900], parse_mode=ParseMode.HTML)
                    remaining = final_text[3900:]
                    while remaining:
                        chunk = remaining[:4096]
                        remaining = remaining[4096:]
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=chunk, parse_mode=ParseMode.HTML)
                    await update.message.reply_text("📋 انتهت قائمة الحركات", reply_markup=kb)
            except Exception as e:
                logging.error("[MOV_RANGE] HTML send failed: " + str(e))
                plain = re.sub(r"<[^>]+>", "", final_text)
                await update.message.reply_text(plain[:4096], reply_markup=kb)
        except Exception as e:
            logging.error("[MOV_RANGE] fetch failed: " + str(e))
            kb = get_role_keyboard(update.effective_user.id)
            await update.message.reply_text("❌ فشل جلب الحركات: " + str(e), reply_markup=kb)
        context.user_data.clear()
        return True
    return False


async def handle_admin_message(update, context):

    text = update.message.text.strip()
    user = update.effective_user
    admin_buttons = [BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT, BTN_DELETE, BTN_BACKUP, BTN_ALERT, BTN_UPDATE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INVOICE, BTN_PENDING_ADMIN, BTN_PERMS, BTN_OIL]
    # هذه الأزرار ديناميكية (قد تحتوي عدادات)
    dynamic_prefixes = [BTN_USERS, BTN_INCOMING, BTN_PENDING_ADMIN]
    is_admin_btn = text in admin_buttons or any(text.startswith(p) for p in dynamic_prefixes)

    # ═══════════════════════════════════════════════════════════
    #  بوابة الفئات: عامل المخزون/البائع يمرّ فقط بما تسمح به فئته
    #  (حتى لو كتب نص الزر يدوياً دون وجوده في لوحته)
    # ═══════════════════════════════════════════════════════════
    if not is_admin(user.id):
        _gate_role = await asyncio.to_thread(_get_role, user.id)
        _gate_state = context.user_data.get("state") or ""
        if _gate_role == ROLE_STOCK:
            _allowed = (
                not is_admin_btn
                or (text in STOCK_ADMIN_BTNS and _btn_perm_ok(_gate_role, text))
                or any(text.startswith(p) and _btn_perm_ok(_gate_role, text)
                       for p in STOCK_ADMIN_PREFIXES)
            )
        elif _gate_role == ROLE_SELLER:
            _allowed = (
                (text == BTN_INVOICE and _btn_perm_ok(_gate_role, text))
                or _gate_state.startswith("waiting_invoice")
                or text in (BTN_CANCEL, BTN_CUST_CANCEL)
            )
        else:
            _allowed = False
        if text in (BTN_CANCEL, BTN_CUST_CANCEL) and _allowed:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_role_keyboard(user.id))
            return
        if not _allowed:
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_gate_role, _gate_role) + ").",
                reply_markup=get_role_keyboard(user.id),
            )
            return

    # ═══════════════════════════════════════════════════════════
    #  Handle admin buttons FIRST — before any state handler
    # ═══════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════
    #  Handle admin buttons FIRST — before any state handler
    # ═══════════════════════════════════════════════════════════
    if is_admin_btn:
        context.user_data.clear()
        if text == BTN_PERMS:
            _KB_ROLE_UID.set(user.id)
            await update.message.reply_text(
                "🔐 إدارة صلاحيات الفئات" + chr(10) + chr(10)
                + "اختر الفئة لعرض صلاحياتها وتبديلها فوراً:",
                reply_markup=_perms_roles_keyboard(),
            )
            return
        if text == BTN_OIL:
            # 🔧 صيانة الرافعة — وصل هنا مدير فقط (بوابة الفئات أعلاه)
            card = await asyncio.to_thread(oil_status_card)
            await update.message.reply_text(card, reply_markup=oil_menu_keyboard())
            return
        if text == BTN_ADD:
            await ask_add_name(update, context)
            return
        if text == BTN_ADD_QTY:
            _set_state(context, STATE_ADMIN_ADD_SEL)
            msg = "➕ إضافة علب" + chr(10) + chr(10) + "أرسل اسم السلعة:"
            await update.message.reply_text(msg, reply_markup=get_input_keyboard())
            return
        if text == BTN_REMOVE_QTY:
            _set_state(context, STATE_ADMIN_REM_SEL)
            msg = "➖ إخراج علب" + chr(10) + chr(10) + "أرسل اسم السلعة:"
            await update.message.reply_text(msg, reply_markup=get_input_keyboard())
            return
        if text == BTN_EDIT:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=get_admin_keyboard())
                return
            await _send_product_list(update, products, context, "edit_id_map")
            _set_state(context, STATE_EDIT_PRODUCT)
            edit_prompt = ("\u270f\ufe0f تعديل سلعة" + chr(10) + chr(10)
                + "أرسل: رقم_السلعة, الاسم الجديد" + chr(10) + chr(10)
                + "مثال: 1, زيت 5 لتر")
            await update.message.reply_text(edit_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_DELETE:
            products = get_all_products()
            if not products:
                await update.message.reply_text("\U0001f4ed لا توجد سلع.", reply_markup=get_admin_keyboard())
                return
            await _send_product_list(update, products, context, "delete_id_map")
            _set_state(context, STATE_DELETE_PRODUCT)
            del_prompt = ("\U0001f5d1\ufe0f حذف سلعة" + chr(10) + chr(10)
                + "أرسل رقم السلعة فقط:" + chr(10)
                + "مثال: 5" + chr(10) + chr(10)
                + "\u26a0\ufe0f سيتم حذف السلعة وجميع سجلاتها نهائياً!")
            await update.message.reply_text(del_prompt, reply_markup=get_input_keyboard())
            return
        if text == BTN_ALERT:
            # v83: التنبيه عام + خاص — اختيار النوع بلوحة أزرار قبل كتابة النص
            _set_state(context, STATE_ALERT_MODE)
            alert_mode_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 عام — لكل المستخدمين", callback_data="alertmode_all")],
                [InlineKeyboardButton("👤 خاص — مستخدم محدد", callback_data="alertmode_priv")],
                [InlineKeyboardButton("👥 خاص — عدة معنيين برسالة واحدة", callback_data="alertmode_priv_multi")],
                [InlineKeyboardButton("❌ إلغاء", callback_data="alertmode_cancel")],
            ])
            await update.message.reply_text(
                "\U0001f514 إرسال تنبيه — اختر النوع:", reply_markup=alert_mode_kb)
            return
        if text == BTN_UPDATE:
            await _broadcast_update_notification(update, context)
            return
        if text == BTN_LOW_STOCK:
            await send_low_stock_list(update, context)
            return
        if text == BTN_MOVEMENTS:
            await _show_all_movements(update, context, admin_view=True)
            return
        if text == BTN_INVOICE:
            _set_state(context, STATE_INVOICE_CUSTOMER)
            context.user_data["invoice_items"] = []
            await update.message.reply_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
            return
        if text == BTN_INCOMING or text.startswith(BTN_INCOMING):
            await _show_admin_incoming_orders(update, context)
            return
        if text == BTN_PENDING_ADMIN or text.startswith(BTN_PENDING_ADMIN):
            await _show_pending_admin(update, context)
            return

        if text == BTN_USERS or text.startswith(BTN_USERS):
            await users_command(update, context)
            return
        if text == BTN_BACKUP:
            await backup_command(update, context)
            return
        # Fallback — should not reach here
        return

    # ═══════════════════════════════════════════════════════════
    #  No admin button pressed → handle cancel, state, or auto-search
    # ═══════════════════════════════════════════════════════════
    state = context.user_data.get("state")
    # Check state timeout (10 minutes)
    if state and _is_state_expired(context):
        logging.info("[ADMIN] State expired: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        await update.message.reply_text("⏰ انتهت مهلة العملية.", reply_markup=get_admin_keyboard())
        return
    # State handlers
    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text("\u274c تم الإلغاء.", reply_markup=get_admin_keyboard())
        return

    # No active state → auto-search or help
    if not state:
        if text and not text.startswith('/'):
            await process_search(update, context, text, admin_view=True)
            return
        await update.message.reply_text(
            "\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647 \u0623\u0648 \u0627\u0644\u0623\u0648\u0627\u0645\u0631:" + chr(10) + "/add, /search",
            reply_markup=get_admin_keyboard())
        return

    # ── Invoice FSM States ──
    if state == STATE_INVOICE_CUSTOMER:
        customer_name = text.strip()
        if not customer_name:
            await update.message.reply_text("❌ أرسل اسم الزبون.", reply_markup=get_input_keyboard())
            return
        context.user_data["invoice_customer"] = customer_name
        context.user_data["state"] = STATE_INVOICE_PRODUCT
        await update.message.reply_text(
            "📦 أرسل اسم المنتج:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_customer"),
                 InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
            ]))
        return

    if state == STATE_INVOICE_PRODUCT:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم المنتج.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_customer"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        results = search_products(product_name)
        if not results:
            context.user_data["invoice_tmp_product"] = product_name
            context.user_data["invoice_tmp_pid"] = None
            ptype = _invoice_product_type(product_name)
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            if ptype == "pcs":
                q = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q = "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q = "🔢 أرسل المساحة المطلوبة (m²):"
            await update.message.reply_text(
                "❓ \"" + product_name + "\" غير موجود في المخزون" + chr(10) + chr(10) + q,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        if len(results) == 1:
            found = results[0]
            context.user_data["invoice_tmp_product"] = found["name"]
            context.user_data["invoice_tmp_pid"] = found["id"]
            ptype = _invoice_product_type(found["name"])
            context.user_data["invoice_tmp_ptype"] = ptype
            context.user_data["state"] = STATE_INVOICE_QTY
            stock_info = " (" + str(found["quantity"]) + ")" if found["quantity"] > 0 else " (غير متوفر! ⚠️)"
            if ptype == "pcs":
                q = "🔢 أرسل عدد القطع:"
            elif ptype == "plant":
                q = "🔢 أرسل الطول بالمتر (م.ط):"
            else:
                q = "🔢 أرسل المساحة المطلوبة (m²):"
            await update.message.reply_text(
                "📦 " + found["name"] + stock_info + chr(10) + chr(10) + q,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        msg = "🔍 " + str(len(results)) + " نتائج:" + chr(10) + chr(10)
        buttons = []
        for p in results:
            stock = str(p["quantity"])
            label = p["name"] + " [" + stock + "]"
            if len(label) > 40:
                label = label[:37] + "..."
            buttons.append([InlineKeyboardButton(label, callback_data="invsel_" + str(p["id"]))])
        buttons.append([InlineKeyboardButton("✍️ كتابة يدوية", callback_data="invsel_manual_" + product_name)])
        buttons.append([InlineKeyboardButton("🔍 بحث آخر", callback_data="invoice_back_product")])
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if state == STATE_INVOICE_QTY:
        try:
            qty = float(text.strip())
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل رقم صحيح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")
        context.user_data["invoice_tmp_qty"] = qty

        if ptype == "pcs":
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر القطعة (د.ج):" + chr(10) + chr(10)
                + "📦 " + product_name + " × " + str(int(qty)) + " قطعة",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        elif ptype == "plant":
            _set_state(context, "waiting_invoice_plant_length")
            await update.message.reply_text(
                "📏 اختر طول الشريط:" + chr(10) + chr(10)
                + "📦 " + product_name + " × " + str(qty) + " م.ط",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("0.45 م", callback_data="inv_plen_0.45"),
                     InlineKeyboardButton("0.60 م", callback_data="inv_plen_0.60")],
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return
        else:
            context.user_data["state"] = STATE_INVOICE_PRICE
            await update.message.reply_text(
                "💰 أرسل سعر المتر المربع (د.ج/m²):" + chr(10) + chr(10)
                + "📦 " + product_name + chr(10)
                + "📐 المساحة: " + str(qty) + " m²",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_product"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return

    if state == "waiting_invoice_plant_length":
        await update.message.reply_text("❌ اختر الطول من الأزرار أعلاه.")
        return

    if state == STATE_INVOICE_PRICE:
        try:
            price = float(text.strip())
            if price < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل سعر صحيح.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty = context.user_data.get("invoice_tmp_qty", 0)
        ptype = context.user_data.get("invoice_tmp_ptype", "m2")

        if ptype == "pcs":
            await _invoice_add_item(update, context, product_id, product_name, int(qty), price, "pcs")
            return
        elif ptype == "plant":
            length_m = context.user_data.get("invoice_tmp_plant_len", 0.45)
            await _invoice_add_item(update, context, product_id, product_name, qty, price, "plant",
                                     extra_info={"length_m": length_m, "qty_m": qty})
            return
        else:
            context.user_data["invoice_tmp_price"] = price
            context.user_data["state"] = STATE_INVOICE_AREA
            await update.message.reply_text(
                "📦 " + product_name + chr(10) + chr(10)
                + "📐 المساحة: " + str(qty) + " m²" + chr(10)
                + "💰 السعر: " + _fmt_price(price) + " د.ج/m²" + chr(10) + chr(10)
                + "📏 أرسل مساحة العلبة (m²):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"),
                     InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]
                ]))
            return

    if state == STATE_INVOICE_AREA:
        try:
            box_area = float(text.strip())
            if box_area <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ أرسل مساحة العلبة صحيحة (مثلاً: 1.44).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 تراجع", callback_data="invoice_back_qty"), InlineKeyboardButton("❌ إلغاء", callback_data="invoice_cancel")]]))
            return
        product_name = context.user_data.get("invoice_tmp_product", "")
        product_id = context.user_data.get("invoice_tmp_pid")
        qty_m2 = context.user_data.get("invoice_tmp_qty", 0)
        price_m2 = context.user_data.get("invoice_tmp_price", 0)
        import math
        raw_boxes = qty_m2 / box_area
        num_boxes = math.ceil(raw_boxes)
        # Subtotal = user-requested area × price/m² (exact, not box-rounded)
        await _invoice_add_item(update, context, product_id, product_name, qty_m2, price_m2, "m2",
                                 extra_info={"requested_area": qty_m2, "box_area": box_area, "num_boxes": num_boxes})
        return

    if state == STATE_ADD_NAME:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0633\u0644\u0639\u0629.", reply_markup=get_input_keyboard())
            return
        await ask_add_qty(update, context, product_name, is_motif=False)
        return
    elif state == STATE_ADD_NAME_MOTIF:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("motif"):
            product_name = "motif " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
        return
    elif state == STATE_ADD_NAME_12060:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("120/60") and not product_name.lower().startswith("120x60"):
            product_name = "120/60 " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
        return
    elif state == STATE_ADD_NAME_PLANTE:
        product_name = text.strip()
        if not product_name:
            await update.message.reply_text("❌ أرسل اسم السلعة.", reply_markup=get_input_keyboard())
            return
        if not product_name.lower().startswith("plante"):
            product_name = "plante " + product_name
        await ask_add_qty(update, context, product_name, is_motif=True)
        return
    elif state == STATE_ADD_QTY:
        qty = parse_crt_input(text)
        if qty is None:
            await update.message.reply_text("\u274c \u0623\u0631\u0633\u0644 \u0639\u062f\u062f \u0627\u0644\u0639\u0644\u0628.\n\u0645\u062b\u0627\u0644: 10 \u0623\u0648 10crt", reply_markup=get_input_keyboard())
            return
        product_name = context.user_data.get("add_name", "")
        is_motif = context.user_data.get("add_is_motif", False)
        await confirm_add_product(update, context, product_name, qty, is_motif=is_motif)
        return
    # ── Custom date range for movements ──
    if state in ("waiting_mov_range_from", "waiting_mov_range_to"):
        handled = await _process_mov_range_input(update, context, text, is_admin=True)
        if handled:
            return
    # ── Product-specific movement ──
    if state == "waiting_mov_product":
        await _process_mov_product_input(update, context, text, is_admin=True)
        return

    if state == STATE_EXISTING_ADD_QTY:
        await process_add_quantity(update, context, text, user.id)
    elif state == STATE_REMOVE_QTY:
        await process_remove_quantity(update, context, text, user.id)
    elif state == STATE_WAITING_SELLER:
        await process_seller_input(update, context, text)
    elif state == STATE_EDIT_PRODUCT:
        await process_edit_product(update, context, text, user.id)
    elif state == STATE_DELETE_PRODUCT:
        await process_delete_product(update, context, text, user.id)
    elif state == STATE_ADMIN_ADD_SEL:
        results = search_products(text)
        if not results:
            await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c.", reply_markup=get_input_keyboard())
            return
        if len(results) == 1:
            await _show_product_detail(update, context, results[0])
        else:
            header = "\U0001f50d " + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629:\n"
            btns = []
            txt = [header]
            for p in results:
                txt.append(str(p["id"]) + ". " + p["name"] + " — " + str(p["quantity"]) + " " + _get_unit_code(p["name"]))
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                btns.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            txt.append("\u2b06\ufe0f \u0627\u062e\u062a\u0631 \u0627\u0644\u0633\u0644\u0639\u0629.")
            await update.message.reply_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(btns))
        context.user_data["state"] = None
        return
    elif state == STATE_ADMIN_REM_SEL:
        results = search_products(text)
        if not results:
            await update.message.reply_text("\U0001f50d \u0644\u0645 \u064a\u062a\u0645 \u0627\u0644\u0639\u062b\u0648\u0631 \u0639\u0644\u0649 \u0646\u062a\u0627\u0626\u062c.", reply_markup=get_input_keyboard())
            return
        if len(results) == 1:
            await _show_product_detail(update, context, results[0])
        else:
            header = "\U0001f50d " + str(len(results)) + " \u0646\u062a\u064a\u062c\u0629:\n"
            btns = []
            txt = [header]
            for p in results:
                txt.append(str(p["id"]) + ". " + p["name"] + " — " + str(p["quantity"]) + " " + _get_unit_code(p["name"]))
                label = p["name"]
                if len(label) > 40:
                    label = label[:37] + "..."
                btns.append([InlineKeyboardButton(label, callback_data="psel_" + str(p["id"]))])
            txt.append("\u2b06\ufe0f \u0627\u062e\u062a\u0631 \u0627\u0644\u0633\u0644\u0639\u0629.")
            await update.message.reply_text("\n".join(txt), reply_markup=InlineKeyboardMarkup(btns))
        context.user_data["state"] = None
        return
    elif state == "waiting_pact_qty":
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_admin_keyboard())
            return
        if text == BTN_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_admin_keyboard(user.id))
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا.\nمثال: 10 أو 10" + unit, reply_markup=get_admin_keyboard())
            return
        qty = parsed
        try:
            updated = db_update_quantity(product["id"], qty, action)
            db_user = get_user(user.id)
            tx = None
            if db_user:
                tx = log_transaction(product["id"], action, qty, db_user["id"])
            tx_id = tx["id"] if tx else None
            icon = _product_status_icon(updated)
            unit = _get_unit_code(updated["name"])
            unit_ar = _get_unit_ar(updated["name"])
            if action == "add":
                msg = "\u2705 \u062a\u0645\u062a \u0627\u0644\u0625\u0636\u0627\u0641\u0629!\n\n" + updated["name"] + "\n\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            else:
                msg = "\u2705 \u062a\u0645 \u0625\u062e\u0631\u0627\u062c!\n\n" + updated["name"] + "\n\U0001f4e6 \u0627\u0644\u0643\u0645\u064a\u0629: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            if action == "remove":
                # (v80) قفل حلقة العجز: الإخراج السريع كان يسمح بالسالب بلا تسجيل
                try:
                    _sync_r = sync_product_pending_deficit_now(
                        updated["id"], db_user["id"] if db_user else None)
                    if _sync_r and _sync_r["created"] > 0:
                        msg += ("\n\n⚠️ الكمية أصبحت سالبة — تم تسجيل "
                                + str(_sync_r["created"]) + " وحدة كطلب معلق")
                except Exception as _sync_err:
                    logging.error("[PENDING-SYNC][v80] pact flow failed: " + str(_sync_err))
            if action == "add":
                # (v19) تسوية تلقائية: فور زيادة المخزون غطِّ المنتظرين وأشعِرهم
                try:
                    rec = await asyncio.to_thread(
                        auto_reconcile_product, product["id"], qty,
                        db_user["id"] if db_user else None,
                    )
                    if rec and rec["fulfilled"]:
                        await _send_fulfilled_notifications(context.bot, rec["fulfilled"])
                        msg += ("\n🔔 تم إشعار " + str(len(rec["fulfilled"]))
                                + " زبون منتظر بأن السلعة صارت متوفرة ("
                                + str(rec["total_covered"]) + " وحدة)")
                except Exception as rec_err:
                    logging.error("[PENDING] Reconcile after quick-add failed: " + str(rec_err))
            pid = str(updated["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("\u2795 \u0625\u0636\u0627\u0641\u0629", callback_data="pact_add_" + pid),
                 InlineKeyboardButton("\u2796 \u0625\u062e\u0631\u0627\u062c", callback_data="pact_rem_" + pid)],
                [InlineKeyboardButton("\U0001f4ca \u0627\u0644\u062d\u0631\u0643\u0629", callback_data="pact_log_" + pid)],
                [InlineKeyboardButton("\u2705 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="admin_done")],
            ])
            context.user_data["cust_product"] = dict(updated)
            await update.message.reply_text(msg, reply_markup=keyboard)
            await send_low_stock_alerts(context.bot)
            # After action: ask reason for notification
            # CRITICAL: clear the qty state first to prevent ghost-state repeat
            context.user_data["state"] = None
            if db_user:
                if action == "add":
                    notify_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚚 وصول شاحنة", callback_data="addnotify_truck"),
                         InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="addnotify_return")],
                        [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="addnotify_correction")],
                    ])
                    context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id}
                    await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
                else:
                    notify_kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="remnotify_return"),
                         InlineKeyboardButton("🚚 بيع لزبون", callback_data="remnotify_sale")],
                        [InlineKeyboardButton("🏪 بيع لرابح Saoula", callback_data="remnotify_saoula"),
                         InlineKeyboardButton("🏪 بيع لحسان Carthago", callback_data="remnotify_carthago")],
                        [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="remnotify_correction")],
                    ])
                    context.user_data["pending_rem_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id}
                    await update.message.reply_text("📤 ما سبب الإخراج؟", reply_markup=notify_kb)
        except ValueError as e:
            await update.message.reply_text("❌ " + str(e), reply_markup=get_input_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ: " + str(e), reply_markup=get_input_keyboard())
        return

    elif state == STATE_SEARCH_ADMIN:
        await process_search(update, context, text, admin_view=True)
        return
    # (v19) حُذفت حالة waiting_provide_qty — زر «توفير» في لوحة المعلق أصبح
    # إشعاراً مباشراً بلا مطالبة بكتابة كمية؛ الإضافة تتم من «➕ إضافة كمية»
    # والتسوية والإشعار تلقائيان على كل مسارات الإضافة.
    elif state == STATE_ALERT_MSG:
        alert_text = text.strip()
        if not alert_text:
            await update.message.reply_text("❌ أرسل نص التنبيه.", reply_markup=get_input_keyboard())
            return
        try:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer')")
                    users = [r[0] for r in cur.fetchall()]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                release_connection(conn)
            sent = 0
            for uid in users:
                try:
                    alert_msg = "🔔 تنبيه من الإدارة:"
                    alert_msg = alert_msg + chr(10) + chr(10) + alert_text
                    await context.bot.send_message(chat_id=uid, text=alert_msg)
                    sent += 1
                except Exception as e:
                    logging.warning("[ALERT] Failed for " + str(uid) + ": " + str(e))
            result_msg = "✅ تم إرسال التنبيه لـ " + str(sent) + " مستخدم."
            await update.message.reply_text(result_msg, reply_markup=get_admin_keyboard())
        except Exception as e:
            await update.message.reply_text("❌ خطأ: " + str(e), reply_markup=get_input_keyboard())
        context.user_data.clear()
        return
    
    elif state == STATE_ALERT_MODE:
        # v83: كتب نصاً قبل اختيار النوع — تذكير بالأزرار أعلاه
        await update.message.reply_text(
            "🔔 اختر نوع التنبيه أولاً من الأزرار أعلاه: 📢 عام أو 👤 خاص.",
            reply_markup=get_input_keyboard())
        return
    elif state == STATE_ALERT_PRIV_MSG:
        # v83: إرسال التنبيه إلى مستخدم محدد فقط
        target = context.user_data.get("alert_target")
        if not target:
            context.user_data.clear()
            await update.message.reply_text(
                "❌ انتهت صلاحية اختيار المستخدم — ابدأ من زر 🔔 تنبيه.",
                reply_markup=get_admin_keyboard())
            return
        alert_text = text.strip()
        if not alert_text:
            await update.message.reply_text("❌ أرسل نص التنبيه.", reply_markup=get_input_keyboard())
            return
        try:
            priv_msg = "🔔 تنبيه خاص من الإدارة:"
            priv_msg = priv_msg + chr(10) + chr(10) + alert_text[:4000]
            await context.bot.send_message(chat_id=int(target["id"]), text=priv_msg)
            tname = str(target.get("name") or "#" + str(target["id"]))
            await update.message.reply_text(
                "✅ تم إرسال التنبيه الخاص إلى " + tname + ".",
                reply_markup=get_admin_keyboard())
        except Exception as e:
            await update.message.reply_text(
                "❌ تعذر الإرسال: " + str(e) + chr(10)
                + "ربما أوقف المستخدم البوت — لم يُرسل التنبيه.",
                reply_markup=get_input_keyboard())
        context.user_data.clear()
        return
    elif state == STATE_ALERT_PRIV_MULTI:
        # v86: تحديد المعنيين كلهم في رسالة واحدة
        try:
            resolved, unknown = await asyncio.to_thread(_resolve_alert_recipients, text)
        except Exception as e:
            context.user_data.clear()
            await update.message.reply_text("❌ خطأ في قراءة المستخدمين: " + str(e),
                                            reply_markup=get_input_keyboard())
            return
        if not resolved:
            await update.message.reply_text(
                "❌ لم أتعرف على أي اسم مما أرسلته:" + chr(10)
                + chr(10).join("• " + u for u in unknown[:10]) + chr(10) + chr(10)
                + "أعد إرسال الأسماء (أو اضغط ❌ إلغاء).",
                reply_markup=get_input_keyboard())
            return
        context.user_data["alert_targets"] = resolved
        _set_state(context, STATE_ALERT_PRIV_MULTI_MSG)
        msg = "✅ تعرفت على " + str(len(resolved)) + " مستلم:" + chr(10)
        msg = msg + chr(10).join("• " + r["name"] for r in resolved[:25])
        if unknown:
            msg = msg + chr(10) + chr(10) + "⚠️ لم أتعرف على: " + "، ".join(unknown[:10])
        msg = msg + chr(10) + chr(10) + "✍️ الآن أرسل نص التنبيه (سيصل لكل المعنيين):"
        await update.message.reply_text(msg, reply_markup=get_input_keyboard())
        return
    elif state == STATE_ALERT_PRIV_MULTI_MSG:
        # v86: إرسال التنبيه الخاص لكل المعنيين المحددين في رسالة واحدة
        targets = context.user_data.get("alert_targets") or []
        alert_text = text.strip()
        if not targets:
            context.user_data.clear()
            await update.message.reply_text(
                "❌ انتهت صلاحية القائمة — ابدأ من زر 🔔 تنبيه.",
                reply_markup=get_admin_keyboard())
            return
        if not alert_text:
            await update.message.reply_text("❌ أرسل نص التنبيه.", reply_markup=get_input_keyboard())
            return
        priv_msg = "🔔 تنبيه خاص من الإدارة:"
        priv_msg = priv_msg + chr(10) + chr(10) + alert_text[:4000]
        sent, failed = 0, []
        for t in targets:
            try:
                await context.bot.send_message(chat_id=int(t["tid"]), text=priv_msg)
                sent = sent + 1
            except Exception:
                failed.append(str(t.get("name") or "#" + str(t.get("tid"))))
            await asyncio.sleep(0.05)
        result_msg = "✅ تم إرسال التنبيه الخاص إلى " + str(sent) + " من " + str(len(targets)) + "."
        if failed:
            result_msg = (result_msg + chr(10) + "⚠️ فشل الإرسال لـ: " + "، ".join(failed[:10])
                          + chr(10) + "ربما أوقفوا البوت — حاول لاحقاً.")
        await update.message.reply_text(result_msg, reply_markup=get_admin_keyboard())
        context.user_data.clear()
        return
    elif state == STATE_OIL_READING:
        # 🔧 تحديث قراءة عداد الرافعة
        val = _oil_parse_hours(text)
        if val is None:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً للقراءة (مثال: 1520.5).", reply_markup=get_input_keyboard())
            return
        _st_ens = await asyncio.to_thread(oil_ensure_row)
        _st_base = _oil_f(_st_ens.get("baseline_hours")) or 0.0
        if _st_ens.get("last_change_at") is not None and val < _st_base:
            await update.message.reply_text(
                "❌ القراءة أقل من خط الأساس الحالي (" + _oil_fmt(_st_base) + " ساعة).\n"
                "إن استُبدل العداد أو تم تغيير الزيت فاستخدم: 🔧 زيت الرافعة ← «🔧 تسجيل تغيير زيت جديد».",
                reply_markup=get_input_keyboard())
            return
        await asyncio.to_thread(oil_record_reading, (user.first_name or user.username or str(user.id)), val)
        context.user_data.clear()
        card = await asyncio.to_thread(oil_status_card)
        await update.message.reply_text("✅ تم تحديث القراءة.\n\n" + card, reply_markup=get_admin_keyboard())
        return
    elif state == STATE_OIL_NEW_HOURS:
        # 🔧 تسجيل تغيير زيت جديد — تصفير الدورة وبدء دورة جديدة
        val = _oil_parse_hours(text)
        if val is None:
            await update.message.reply_text("❌ أرسل رقماً صحيحاً (مثال: 1520.5).", reply_markup=get_input_keyboard())
            return
        _st_ens = await asyncio.to_thread(oil_ensure_row)
        _note = "تأكيد مباشر من المدير"
        if bool(_st_ens.get("confirm_confirmed")):
            _note = "تأكيد العامل عبر الزر + إدخال المدير"
        await asyncio.to_thread(oil_record_change, (user.first_name or user.username or str(user.id)), val, _note)
        context.user_data.clear()
        card = await asyncio.to_thread(oil_status_card)
        await update.message.reply_text("🛢 تم تسجيل تغيير الزيت وبدء دورة جديدة.\n\n" + card, reply_markup=get_admin_keyboard())
        return
    else:
        # Unknown state — log and clear safely instead of silent data destruction
        logging.warning("[ADMIN] Unhandled state: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        await update.message.reply_text("\u0627\u0633\u062a\u062e\u062f\u0645 \u0627\u0644\u0623\u0632\u0631\u0627\u0631 \u0623\u062f\u0646\u0627\u0647.", reply_markup=get_admin_keyboard())
        return

async def handle_customer_message(update, context):
    text = update.message.text.strip()
    state = context.user_data.get("state")
    user = update.effective_user

    # Check state timeout (10 minutes)
    if state and _is_state_expired(context):
        logging.info("[CUST] State expired: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        state = None

    # Handle customer buttons (clear state first)
    cust_buttons = [BTN_CUST_ALERT, BTN_CUST_ADD, BTN_CUST_REMOVE, BTN_CUST_MOVEMENTS, BTN_CUST_CANCEL, BTN_CUST_ORDER, BTN_CUST_MY_ORDERS, BTN_CUST_PENDING]

    # ═══════════════════════════════════════════════════════════
    #  🎯 بوابة الفئات لمسار العميل: البائع/الخارجي يمرّان فقط بما تسمح به فئتهما
    # ═══════════════════════════════════════════════════════════
    _cust_role = await asyncio.to_thread(_get_role, user.id)
    if _cust_role == ROLE_EXTERNAL:
        # 🌍 البائع الخارجي: بحث فقط (زر البحث أو إرسال اسم السلعة) + إلغاء
        # 🔐 صلاحية البحث قابلة للإطفاء من لوحة الصلاحيات
        if text == BTN_SEARCH and not _btn_perm_ok(_cust_role, text):
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_external_keyboard(),
            )
            return
        if text in (BTN_CUST_CANCEL, BTN_CANCEL):
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_external_keyboard())
            return
        _known_btn = (
            text in cust_buttons
            or text in (BTN_INVOICE, BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT,
                        BTN_DELETE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INCOMING,
                        BTN_PENDING_ADMIN, BTN_CUST_ORDER_LEGACY)
            or text.startswith(BTN_USERS) or text.startswith(BTN_INCOMING)
            or text.startswith(BTN_PENDING_ADMIN)
        )
        if _known_btn:
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_external_keyboard(),
            )
            return
    elif _cust_role == ROLE_SELLER:
        # 🛍 البائع: أزرار فئته تُحكم بالصلاحيات الديناميكية (لوحة 🔐 الصلاحيات)
        # البحث: صلاحية مستقلة قابلة للإطفاء
        if text == BTN_SEARCH and not _btn_perm_ok(_cust_role, text):
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_seller_main_keyboard(user.id),
            )
            return
        _known_btn = (
            text in cust_buttons
            or text in (BTN_INVOICE, BTN_ADD, BTN_ADD_QTY, BTN_REMOVE_QTY, BTN_EDIT,
                        BTN_DELETE, BTN_LOW_STOCK, BTN_MOVEMENTS, BTN_INCOMING,
                        BTN_PENDING_ADMIN)
            or text.startswith(BTN_USERS) or text.startswith(BTN_INCOMING)
            or text.startswith(BTN_PENDING_ADMIN)
        )
        # زر معروف → يُقبل فقط إن كانت صلاحيته مفعّلة (زر غير مربوط = مسموح)
        if _known_btn and not _btn_perm_ok(_cust_role, text):
            await update.message.reply_text(
                "❌ هذا الإجراء خارج صلاحيات فئتك ("
                + ROLE_LABELS.get(_cust_role, _cust_role) + ").",
                reply_markup=get_seller_main_keyboard(user.id),
            )
            return

    # 🔍 بحث صريح (البائع/الخارجي/العميل): نفس حالة بحث العميل
    if text == BTN_SEARCH:
        _set_state(context, "waiting_cust_search")
        await update.message.reply_text("🔍 اكتب اسم السلعة:", reply_markup=get_input_keyboard())
        return

    # ➕ البائع: إضافة كمية — نفس مسار الإخراج (بحث ثم بطاقة السلعة)
    if text == BTN_ADD_QTY:
        _set_state(context, "waiting_cust_search")
        await update.message.reply_text("🔍 اكتب اسم السلعة:", reply_markup=get_input_keyboard())
        return

    # 🛍 البائع: إنشاء فاتورة (نفس مسار فاتورة الإدارة) — ولغير البائع رفض صريح
    if text == BTN_INVOICE:
        if _get_role(user.id) == ROLE_SELLER and role_perm_enabled(ROLE_SELLER, PERM_INVOICE):
            _set_state(context, STATE_INVOICE_CUSTOMER)
            context.user_data["invoice_items"] = []
            await update.message.reply_text(
                "\U0001f9fe \u0625\u0646\u0634\u0627\u0621 \u0641\u0627\u062a\u0648\u0631\u0629" + chr(10) + chr(10)
                + "\U0001f464 \u0623\u0631\u0633\u0644 \u0627\u0633\u0645 \u0627\u0644\u0632\u0628\u0648\u0646 \u0623\u0648 \u0631\u0642\u0645 \u0647\u0627\u062a\u0641\u0647:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\u274c \u0625\u0644\u063a\u0627\u0621", callback_data="invoice_cancel")]
                ]))
        else:
            await update.message.reply_text(
                "❌ هذا الإجراء غير متاح لفئتك.",
                reply_markup=get_role_keyboard(user.id),
            )
        return

    if text in cust_buttons:
        context.user_data.clear()
        state = None

        if text == BTN_CUST_CANCEL:
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_role_keyboard(user.id))
            return

        if text == BTN_CUST_ALERT:
            await send_low_stock_list(update, context)
            return

        if text in (BTN_CUST_ORDER, BTN_CUST_ORDER_LEGACY):
            _set_state(context, "waiting_order_photo")
            await update.message.reply_text(
                "📦 طلب توريد جديد\n\n"
                "📷 صوّر الوصل وأرسله مباشرة\n"
                "✏️ أو اكتب تفاصيل الطلب كنص",
                reply_markup=get_input_keyboard(),
            )
            return

        if text == BTN_CUST_MY_ORDERS:
            await _show_customer_orders(update, context)
            return

        if text == BTN_CUST_PENDING:
            await _show_pending_customer(update, context)
            return

        if text in (BTN_CUST_ADD, BTN_CUST_REMOVE):
            _set_state(context, "waiting_cust_search")
            await update.message.reply_text("🔍 اكتب اسم السلعة:")
            return

        if text == BTN_CUST_MOVEMENTS:
            await _show_all_movements(update, context, admin_view=False)
            return

    # ── Custom date range for movements ──
    if state in ("waiting_mov_range_from", "waiting_mov_range_to"):
        handled = await _process_mov_range_input(update, context, text, is_admin=False)
        if handled:
            return
    # ── Product-specific movement ──
    if state == "waiting_mov_product":
        await _process_mov_product_input(update, context, text, is_admin=False)
        return

    if state == "waiting_cust_search":
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_role_keyboard(user.id))
            return
        await process_search(update, context, text, admin_view=False)
        context.user_data.clear()
        return

    # State: waiting_order_photo - صورة وصل أو كتابة نص
    if state in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        if text == BTN_CUST_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return
        # Accept text as order description
        user = update.effective_user
        db_user = get_user(user.id)
        if not db_user:
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
            context.user_data.clear()
            return
        try:
            req_name = html.escape(user.first_name or user.username or str(user.id))
            order_text = html.escape(text.strip())
            order = create_incoming_order(db_user["id"], text.strip(), 0)
            order_id = order["id"]
            caption = (
                "🚚 طلب توريد جديد!\n\n"
                "👤 من: <b>" + req_name + "</b>\n"
                "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
                "✏️ الطلب:\n" + order_text
            )
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=_incoming_order_keyboard(order_id),
                    )
                except Exception:
                    pass
            await update.message.reply_text(
                "✅ تم تسجيل طلب التوريد!\n\n"
                "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
                "⏳ الحالة: <b>قيد التوريد</b>\n\n"
                "📡 سيتم إشعارك تلقائياً عند التحديث.\n"
                "تابع طلبك من 📋 طلباتي",
                parse_mode=ParseMode.HTML,
                reply_markup=get_customer_keyboard(),
            )
            logging.info("[INCOMING] Text order #" + str(order_id) + " by user " + str(user.id))
        except Exception as e:
            logging.error("[INCOMING] Failed text order: " + str(e))
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        context.user_data.clear()
        return

    # State: waiting_pact_qty
    if state == "waiting_pact_qty":
        product = context.user_data.get("cust_product")
        action = context.user_data.get("pact_action", "add")
        if action not in ("add", "remove"):
            action = "remove" if action == "rem" else "add"
        if not product:
            context.user_data.clear()
            await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
            return
        if text == BTN_CUST_CANCEL or text == BTN_CANCEL:
            context.user_data.clear()
            await update.message.reply_text("❌ تم الإلغاء.", reply_markup=get_customer_keyboard())
            return
        parsed = parse_crt_input(text)
        if parsed is None:
            unit = _get_unit_code(product["name"])
            await update.message.reply_text("❌ أرسل عددا صحيحا." + chr(10) + "مثال: 10 أو 10" + unit, reply_markup=get_customer_keyboard())
            return
        qty = parsed
        try:
            updated = db_update_quantity(product["id"], qty, action)
            db_user = get_user(user.id)
            tx = None
            if db_user:
                tx = log_transaction(product["id"], action, qty, db_user["id"])
            tx_id = tx["id"] if tx else None
            icon = _product_status_icon(updated)
            unit = _get_unit_code(updated["name"])
            unit_ar = _get_unit_ar(updated["name"])
            if action == "add":
                msg = "✅ تمت الإضافة!" + chr(10) + chr(10) + updated["name"] + chr(10) + "📦 الكمية: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
            else:
                msg = "✅ تم إخراج!" + chr(10) + chr(10) + updated["name"] + chr(10) + "📦 الكمية: " + str(updated["quantity"]) + " " + unit_ar + " " + icon
                # Check if this created a deficit (negative stock)
                old_qty = updated.get("_old_qty", 0)
                new_qty = updated["quantity"]
                additional_deficit = max(0, -new_qty) - max(0, -old_qty)
                if additional_deficit > 0 and db_user:
                    try:
                        create_pending_delivery(db_user["id"], updated["id"], additional_deficit)
                        msg += chr(10) + chr(10) + "⚠️ الكمية أصبحت سالبة — تم تسجيل " + str(additional_deficit) + " وحدة كطلب معلق"
                    except Exception as e:
                        logging.error("[PENDING] Failed to create: " + str(e))
            pid = str(updated["id"])
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ إضافة", callback_data="pact_add_" + pid),
                 InlineKeyboardButton("➖ إخراج", callback_data="pact_rem_" + pid)],
                [InlineKeyboardButton("📜 سجل الحركات", callback_data="pact_log_" + pid)],
            ])
            context.user_data["cust_product"] = dict(updated)
            await update.message.reply_text(msg, reply_markup=keyboard)
            await send_low_stock_alerts(context.bot)
            # CRITICAL: clear the qty state first to prevent ghost-state repeat
            context.user_data["state"] = None
            # After add: ask reason for notification
            if action == "add" and db_user:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚚 وصول شاحنة", callback_data="addnotify_truck"),
                     InlineKeyboardButton("↩️ إرجاع من زبون", callback_data="addnotify_return")],
                    [InlineKeyboardButton("📝 تصحيح مخزون", callback_data="addnotify_correction")],
                ])
                context.user_data["pending_add_notify"] = {"product": dict(updated), "qty": qty, "user": db_user, "tx_id": tx_id}
                await update.message.reply_text("📤 ما سبب الإضافة؟", reply_markup=notify_kb)
                return  # Don't clear user_data yet — callback needs pending_add_notify
        except ValueError as e:
            await update.message.reply_text("❌ " + str(e))
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ: " + str(e))
        context.user_data.clear()
        return

    # Default: treat as search query (only if no active state)
    if state:
        logging.warning("[CUST] Unhandled state reached default: " + str(state) + " for user " + str(user.id))
        context.user_data.clear()
        await update.message.reply_text("استخدم الأزرار أدناه.", reply_markup=get_customer_keyboard())
        return
    if not text:
        await update.message.reply_text("أرسل اسم السلعة لمعرفة إذا كانت متوفرة.")
        return
    await process_search(update, context, text, admin_view=False)


# ═══════════ v32: تأكيد التسجيل الذاتي عبر البوت ═══════════

def _bot_confirm_registration(tg_user, code):
    """تأكيد تسجيل قادم من التطبيق: إرسال الرمز للبوت يربط تلغرام ويفعّل الفئة المختارة.
    القواعد: الرمز صالح 48 ساعة، والمُرسل إما مستخدم جديد أو صف pending/customer فقط.
    يعيد (ok, رسالة جاهزة للمحادثة)."""
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False, "❌ رمز غير صالح — أرسل الرمز المكوّن من 6 أرقام."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, full_name, email, phone, role, status, user_id "
                "FROM pwa_registrations WHERE code = %s AND status = 'waiting' "
                "AND created_at > NOW() - INTERVAL '" + str(_PWA_REG_EXPIRE_HOURS) + " hours'",
                (code,))
            reg = cur.fetchone()
            if reg is None:
                return False, "❌ الرمز غير صحيح أو منتهي الصلاحية (صلاحيته " + str(_PWA_REG_EXPIRE_HOURS) + " ساعة)."
            existing = None
            cur.execute("SELECT id, role FROM users WHERE telegram_id = %s", (tg_user.id,))
            existing = cur.fetchone()
            if existing and existing["role"] not in (ROLE_PENDING, ROLE_CUSTOMER):
                return False, ("ℹ️ حسابك مرتبط مسبقاً بالبوت بفئة "
                               + ROLE_LABELS.get(existing["role"], existing["role"])
                               + " — لا حاجة لرمز تأكيد.")
            role = reg["role"] if reg["role"] in _PWA_ROLES_SELF else ROLE_SELLER
            if existing:
                cur.execute(
                    "UPDATE users SET first_name = %s, email = COALESCE(%s, email), "
                    "phone = COALESCE(%s, phone), role = %s, requested_role = NULL, "
                    "active = TRUE, identity_verified = TRUE WHERE id = %s RETURNING id",
                    (reg["full_name"], reg["email"], reg["phone"], role, existing["id"]))
                uid = cur.fetchone()["id"]
            else:
                cur.execute(
                    "INSERT INTO users (telegram_id, username, first_name, role, active, "
                    "email, phone, identity_verified) "
                    "VALUES (%s, %s, %s, %s, TRUE, %s, %s, TRUE) RETURNING id",
                    (tg_user.id, tg_user.username, reg["full_name"], role,
                     reg["email"], reg["phone"]))
                uid = cur.fetchone()["id"]
            cur.execute(
                "UPDATE pwa_registrations SET status = 'confirmed', confirmed_at = NOW(), "
                "user_id = %s WHERE id = %s", (uid, reg["id"]))
            # نظّف رموز دخول قديمة معلقة (احتياطاً)
            cur.execute("DELETE FROM pwa_otp WHERE user_id = %s AND used_at IS NULL", (uid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[REG] confirm failed: " + str(e))
        return False, "⚠️ خطأ في الخادم — أعد إرسال الرمز بعد لحظات."
    finally:
        release_connection(conn)
    try:
        _audit_log("register_confirm", {"id": uid, "name": reg["full_name"]},
                   details="رمز:" + code + " فئة:" + str(role))
    except Exception:
        pass
    return True, (
        "✅ تم تأكيد حسابك يا <b>" + html.escape(reg["full_name"]) + "</b>\n"
        "🎯 الفئة: <b>" + ROLE_LABELS.get(role, role) + "</b>\n"
        + ("📞 " + html.escape(str(reg["phone"])) + "\n" if reg["phone"] else "")
        + ("✉️ " + html.escape(str(reg["email"])) + "\n" if reg["email"] else "")
        + "\n🔑 سجّل دخولك في التطبيق الآن:\n"
        "«رمز سري» ← أدخل هاتفك أو إيميلك ← يصلك الرمز هنا.\n"
        "كلمة المرور تُعطى من المدير عند الحاجة.")


def _reg_lookup_by_user(telegram_id):
    """آخر تسجيل مؤكد لحساب تلغرام معيّن (لبيانات إشعار المديرين بعد التأكيد)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT r.full_name, r.role, r.phone, r.email FROM pwa_registrations r "
                "JOIN users u ON u.id = r.user_id WHERE u.telegram_id = %s "
                "ORDER BY r.confirmed_at DESC NULLS LAST LIMIT 1", (telegram_id,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}
    except Exception:
        conn.rollback()
        return {}
    finally:
        release_connection(conn)


async def _notify_admins_registration(context, tg_user, full_name, role_label, reg_phone, reg_email):
    """إشعار المديرين بانضمام مؤكد جديد مع أزرار إدارة سريعة."""
    if TG_APP is None:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 تغيير الفئة", callback_data="askrole_" + str(tg_user.id))],
        [InlineKeyboardButton("👥 لوحة المستخدمين", callback_data="users_refresh")],
    ])
    lines = [
        "🔔 انضمام جديد مؤكد (تسجيل من التطبيق):",
        "👤 " + str(full_name or ""),
        "🎯 الفئة: " + str(role_label or ""),
        "🆔 " + str(tg_user.id),
    ]
    if reg_phone:
        lines.append("📞 " + str(reg_phone))
    if reg_email:
        lines.append("✉️ " + str(reg_email))
    text = "\n".join(lines)
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text, reply_markup=kb)
        except Exception as e:
            logging.error("[REG] admin notify failed for " + str(aid) + ": " + str(e))


async def route_message(update, context):
    # Deduplication: skip already-processed updates
    if is_duplicate_update(update):
        return
    user = update.effective_user
    text = update.message.text.strip()
    # ── Efficient: check role with single DB call instead of 4 ──
    db_error = False
    _role = None
    try:
        db_user = await asyncio.to_thread(get_user, user.id)
        # 🎯 نظام الفئات: role يحدد المسار واللوحة
        _role = (db_user or {}).get("role") or ("admin" if user.id in ADMIN_IDS else "pending")
        is_adm = _role in ("admin", "manager")
        is_appr = is_adm or _role in ("stock", "seller", "external", "customer")
    except Exception as e:
        logging.error("[ROUTE] DB check failed: " + str(e))
        db_error = True
        is_adm = user.id in ADMIN_IDS
        is_appr = is_adm
    if not is_adm and not is_appr:
        # ── v32: رمز تأكيد تسجيل من التطبيق (6 أرقام، خاص فقط) —
        #   يمرّ هنا لأن المُرسل إمّا جديد أو pending/customer،
        #   فلا تُختطف أرقام الكميات من المستخدمين المعتمدين ──
        if (text.isdigit() and len(text) == 6
                and getattr(update.effective_chat, "type", None) == "private"):
            ok, msg = await asyncio.to_thread(_bot_confirm_registration, user, text)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            if ok:
                try:
                    db_reg = await asyncio.to_thread(_reg_lookup_by_user, user.id)
                    await _notify_admins_registration(
                        context, user,
                        db_reg.get("full_name"), ROLE_LABELS.get(db_reg.get("role"), db_reg.get("role")),
                        db_reg.get("phone"), db_reg.get("email"))
                except Exception as e:
                    logging.error("[REG] post-confirm notify failed: " + str(e))
            return
        if db_error:
            await update.message.reply_text("حاول مرة ثانية")
        elif text == "/cancel":
            await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.")
        elif text != "/start":
            await update.message.reply_text("\u23f3 \u062d\u0633\u0627\u0628\u0643 \u0642\u064a\u062f \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629.\n\u064a\u062c\u0628 \u0627\u0644\u0627\u0646\u062a\u0638\u0627\u0631 \u062d\u062a\u0649 \u064a\u0641\u0639\u0644\u0643 \u0627\u0644\u0645\u062f\u064a\u0631.")
        return
    if text == "/cancel":
        context.user_data.clear()
        await update.message.reply_text("\u274c \u062a\u0645 \u0627\u0644\u0625\u0644\u063a\u0627\u0621.", reply_markup=await asyncio.to_thread(lambda: get_role_keyboard(update.effective_user.id)))
        return
    await asyncio.to_thread(register_user, telegram_id=user.id, username=user.username, first_name=user.first_name)
    if is_adm:
        await handle_admin_message(update, context)
    elif _role == "stock":
        # 📦 عامل المخزون: يمر عبر مسار الإدارة داخل بوابة الفئات (أزرار المخزون فقط)
        await handle_admin_message(update, context)
    elif _role == "seller" and (
        text == BTN_INVOICE
        or str(context.user_data.get("state") or "").startswith("waiting_invoice")
    ):
        # 🛍 البائع: مسار الفواتير فقط عبر مسار الإدارة المشروط
        await handle_admin_message(update, context)
    else:
        await handle_customer_message(update, context)


# ═══════════════════════════════════════════════════════════
#  🔧 صيانة الرافعة الشوكية — دورة زيت المحرك (v21)
#  حصرياً للمدراء (admin/manager). رافعة واحدة، دورة واحدة.
#  التدفق: تسجيل آخر تغيير (خط أساس) ← قراءة أسبوعية للعداد
#  (تذكير كل جمعة) ← تعلّم معدل الاستهلاك الأسبوعي ← تنبؤ
#  بالوصول إلى 200 ساعة ← تنبيه يومي عند 200 ← إشعار أحمر
#  إجباري كل 3 ساعات عند 250 ← تأكيد العامل بزر واحد ← تسجيل
#  التغيير الجديد ← دورة جديدة.
# ═══════════════════════════════════════════════════════════

OIL_H_LIMIT = 200.0           # عتبة التنبيه اليومي (ساعة)
OIL_H_RED = 250.0             # عتبة الإشعار الأحمر الإجباري (ساعة)
OIL_RED_EVERY_SEC = 3 * 3600  # إيقاع الأحمر: كل 3 ساعات
OIL_PREDICT_DAYS = 7          # التنبيه التوقعي قبل بلوغ العتبة بهذا العدد من الأيام
OIL_WEEKLY_DAY = 4            # يوم تذكير القراءة الأسبوعي: الجمعة (weekday: الاثنين=0)
OIL_MIN_RATE_DAYS = 1.0       # أدنى فاصل زمني يُحتسب في تعلّم المعدل (يوم) — يمنع تلويث التعلم بالإدخالات المتتالية
_OIL_TZ = datetime.timezone(datetime.timedelta(hours=1))  # توقيت الجزائر UTC+1 (بلا توقيت صيفي)
_OIL_DAYS_AR = {0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس",
                4: "الجمعة", 5: "السبت", 6: "الأحد"}


def oil_algiers_now():
    """الوقت الحالي بتوقيت الجزائر (fixed UTC+1 — لا يعتمد على tzdata الخادم)."""
    return datetime.datetime.now(_OIL_TZ)


def _oil_f(v):
    """تحويل آمن إلى float (يقبل Decimal) أو None."""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _oil_fmt(v):
    """تنسيق الساعات: بلا أصفار زائدة أو — عند الغياب."""
    v = _oil_f(v)
    if v is None:
        return "—"
    return ("{:.1f}".format(v)).rstrip("0").rstrip(".")


def _oil_dt(dt):
    """توحيد عرض التواريخ بتوقيت الجزائر."""
    try:
        if dt is None:
            return None
        if isinstance(dt, datetime.datetime):
            return dt.astimezone(_OIL_TZ)
        return dt
    except Exception:
        return dt


def oil_compute_rate(readings):
    """معدل الاستهلاك الأسبوعي (ساعة/أسبوع) من قراءات [(reading, read_at)].
    متوسط مرجّح بفارق الزمن بين القراءات؛ يرجع None حتى تتوفر قراءتان
    صالحتان على الأقل (فارق موجب وفاصل زمني ≥ OIL_MIN_RATE_DAYS —
    الإدخالات المتتالية أو المتقاربة لا تدخل في التحليل الذكي)."""
    pts = []
    for r, at in readings or []:
        v = _oil_f(r)
        if v is None or at is None:
            continue
        pts.append((v, at))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda x: x[1])
    dh_total = 0.0
    dw_total = 0.0
    for (v1, t1), (v2, t2) in zip(pts, pts[1:]):
        days = (t2 - t1).total_seconds() / 86400.0
        dv = v2 - v1
        if days < OIL_MIN_RATE_DAYS or dv <= 0:
            continue
        dh_total += dv
        dw_total += days / 7.0
    if dw_total <= 0:
        return None
    return dh_total / dw_total


def oil_due_alerts(st, rate, now):
    """قرار التنبيهات (دالة نقية قابلة للاختبار) — يرجع قائمة أحداث:
    [{"kind": weekly|predict|daily200|red250, ...}].
    st: dict بمفاتيح has_cycle, baseline_hours, current_hours,
        confirm_confirmed, weekly_remind_date, predict_sent_at,
        notified_200_date, notified_250_at.
    rate: معدل الاستهلاك الأسبوعي أو None. now: وقت توقيت الجزائر."""
    out = []
    if not st.get("has_cycle"):
        return out
    base = _oil_f(st.get("baseline_hours")) or 0.0
    cur = _oil_f(st.get("current_hours"))
    if cur is None:
        cur = base
    hours_since = cur - base
    today = now.date()
    confirmed = bool(st.get("confirm_confirmed"))

    # تذكير القراءة الأسبوعي (الجمعة) — مستقل عن حالة التأكيد
    if now.weekday() == OIL_WEEKLY_DAY and st.get("weekly_remind_date") != today:
        out.append({"kind": "weekly", "hours_since": hours_since})

    if confirmed:
        # العامل أكّد التغيير — التنبيهات متوقفة حتى تسجيل الدورة الجديدة
        return out

    remaining = OIL_H_LIMIT - hours_since
    # 🔮 تنبؤ بالاقتراب من 200 (يعلم من القراءات)
    if rate and rate > 0 and remaining > 0:
        days = remaining / (rate / 7.0)
        if days <= OIL_PREDICT_DAYS:
            last_p = st.get("predict_sent_at")
            if last_p is None or (now - last_p).total_seconds() >= OIL_PREDICT_DAYS * 86400.0 * 0.9:
                eta = today + datetime.timedelta(days=days)
                out.append({"kind": "predict", "hours_since": hours_since, "rate": rate, "eta": eta})
    # 🟠 عتبة 200 — تنبيه هادئ مرة كل يوم
    if OIL_H_LIMIT <= hours_since < OIL_H_RED and st.get("notified_200_date") != today:
        out.append({"kind": "daily200", "hours_since": hours_since})
    # 🔴 عتبة 250 — إشعار أحمر إجباري كل 3 ساعات
    if hours_since >= OIL_H_RED:
        last_r = st.get("notified_250_at")
        if last_r is None or (now - last_r).total_seconds() >= OIL_RED_EVERY_SEC:
            out.append({"kind": "red250", "hours_since": hours_since})
    return out


def oil_alert_text(kind, info):
    """نص التنبيه لكل نوع."""
    hs = _oil_fmt(info.get("hours_since"))
    if kind == "weekly":
        return ("🔧 تذكير أسبوعي: تحديث قراءة عداد الرافعة\n\n"
                "📊 الساعات منذ آخر تغيير زيت: " + hs + " ساعة\n\n"
                "افتح: 🔧 زيت الرافعة ← 📝 تحديث قراءة العداد\n"
                "(القراءات الأسبوعية تجعل التنبؤ بالعتبة دقيقاً)")
    if kind == "predict":
        eta = info.get("eta")
        day = _OIL_DAYS_AR.get(eta.weekday(), "") if eta is not None else ""
        eta_s = eta.strftime("%Y-%m-%d") if eta is not None else ""
        return ("🟡 تنبؤ صيانة الرافعة الشوكية\n\n"
                "حسب استهلاكك المتعلّم (" + _oil_fmt(info.get("rate")) + " ساعة/أسبوع) "
                "ستصل عتبة " + str(int(OIL_H_LIMIT)) + " ساعة يوم "
                + day + " " + eta_s + "\n\n"
                "📊 الساعات منذ آخر تغيير: " + hs + " ساعة\n"
                "🛢 جهّز تغيير الزيت مبكراً حتى لا يسبقك العدّاد.")
    if kind == "daily200":
        return ("🟠 الرافعة تجاوزت " + str(int(OIL_H_LIMIT)) + " ساعة دون تغيير زيت\n\n"
                "📊 الساعات منذ آخر تغيير: " + hs + " ساعة\n"
                "⚠️ حان وقت التغيير — هذا تنبيه يومي حتى يتم التغيير.")
    if kind == "red250":
        return ("🔴 إشعار أحمر — تغيير زيت الرافعة إجباري!\n\n"
                "📊 الساعات منذ آخر تغيير: " + hs
                + " ساعة (تجاوزت حد " + str(int(OIL_H_RED)) + " ساعة)\n"
                "🛑 استمرار التشغيل بهذه الحالة يعرّض المحرك للتلف.\n"
                "نفّذ التغيير فوراً ثم سجّله: 🔧 زيت الرافعة ← 🔧 تسجيل تغيير زيت جديد")
    return ""


def oil_ensure_row():
    """يضمن وجود صف الحالة (id=1) ويرجعه كقاموس — قاعدة بيانات متزامنة."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("INSERT INTO oil_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cur.execute("SELECT * FROM oil_status WHERE id = 1")
            row = dict(cur.fetchone() or {})
        conn.commit()
        return row
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] ensure/read failed: " + str(e))
        return {}
    finally:
        release_connection(conn)


def oil_recent_readings(limit=6):
    """آخر قراءات العداد [(reading, read_at)] تصاعدياً — لتعلّم المعدل."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT reading, read_at FROM oil_readings ORDER BY read_at DESC LIMIT %s", (limit,))
            rows = [(r[0], r[1]) for r in cur.fetchall()]
        conn.commit()
        rows.reverse()
        return rows
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] readings failed: " + str(e))
        return []
    finally:
        release_connection(conn)


def oil_record_reading(by_name, reading):
    """تحديث قراءة العداد الحالية + أرشفتها للتعلم."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO oil_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cur.execute("UPDATE oil_status SET current_hours = %s, current_hours_at = NOW(), updated_at = NOW() WHERE id = 1",
                        (reading,))
            cur.execute("INSERT INTO oil_readings (reading, by_name) VALUES (%s, %s)", (reading, by_name or ""))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def oil_record_change(recorded_by_name, reading, note):
    """تسجيل تغيير زيت جديد: سجل الصيانة + خط أساس جديد + تصفير كل أعلام التنبيه.
    ملاحظة v22: خط الأساس لا يُؤرشف في oil_readings — التعلم يقتصر على
    القراءات الفعلية المتباعدة حتى لا يولّد الإدخال الافتتاحي معدلاً وهمياً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO oil_log (hours_at_change, recorded_by_name, note) VALUES (%s, %s, %s)",
                        (reading, recorded_by_name or "", note or ""))
            cur.execute("""UPDATE oil_status
                           SET baseline_hours = %s, last_change_at = NOW(),
                               current_hours = %s, current_hours_at = NOW(),
                               confirm_worker_id = NULL, confirm_requested_at = NULL,
                               confirm_confirmed = FALSE, confirm_confirmed_at = NULL,
                               predict_sent_at = NULL, notified_200_date = NULL, notified_250_at = NULL,
                               updated_at = NOW()
                           WHERE id = 1""", (reading, reading))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def oil_recent_log(limit=20):
    """سجل الصيانة — آخر التغييرات."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM oil_log ORDER BY changed_at DESC LIMIT %s", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return rows
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] log failed: " + str(e))
        return []
    finally:
        release_connection(conn)


def oil_set_confirm_worker(worker_tid):
    """تسجيل العامل المُرسل له طلب التأكيد."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO oil_status (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cur.execute("""UPDATE oil_status SET confirm_worker_id = %s, confirm_requested_at = NOW(),
                           confirm_confirmed = FALSE, confirm_confirmed_at = NULL, updated_at = NOW()
                           WHERE id = 1""", (worker_tid,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def oil_worker_confirmed(worker_tid):
    """تثبيت تأكيد العامل — ينجح فقط إن كان هو صاحب الطلب ولم يُؤكد سابقاً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE oil_status SET confirm_confirmed = TRUE, confirm_confirmed_at = NOW(), updated_at = NOW()
                           WHERE id = 1 AND confirm_worker_id = %s AND confirm_confirmed = FALSE""",
                        (worker_tid,))
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        conn.rollback()
        return False
    finally:
        release_connection(conn)


def oil_worker_candidates():
    """مرشحو تأكيد التغيير: مستخدمون نشطون (غير مدراء) — آخر 15 تسجيلاً."""
    out = []
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT telegram_id, username, first_name, role FROM users
                               WHERE active = TRUE AND role NOT IN ('admin', 'manager', 'pending')
                               ORDER BY created_at DESC LIMIT 15""")
                for tid, username, first_name, role in cur.fetchall():
                    name = first_name or username or str(tid)
                    out.append((int(tid), name + " (" + ROLE_LABELS.get(role, role) + ")"))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    except Exception as e:
        logging.warning("[OIL] candidates failed: " + str(e))
    return out


def oil_manager_ids():
    """معرفات كل المدراء (حسابات البيئة + فئة admin/manager النشطة)."""
    ids = set(ADMIN_IDS or ())
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN ('admin', 'manager') AND active = TRUE")
                for r in cur.fetchall():
                    ids.add(r[0])
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    except Exception:
        pass
    return [i for i in ids if i]


def oil_status_card():
    """بطاقة حالة دورة الزيت (نص كامل)."""
    st = oil_ensure_row()
    rate = oil_compute_rate(oil_recent_readings(6))
    now = oil_algiers_now()
    has_cycle = st.get("last_change_at") is not None
    base = _oil_f(st.get("baseline_hours")) or 0.0
    cur = _oil_f(st.get("current_hours"))
    hours_since = max(0.0, (cur if cur is not None else base) - base)
    if not has_cycle:
        stage = "⚪ لم تبدأ الدورة بعد"
    elif bool(st.get("confirm_confirmed")):
        stage = "⏸ مؤكد — بانتظار تسجيل القراءة الجديدة"
    elif hours_since >= OIL_H_RED:
        stage = "🔴 تجاوز 250 — تغيير إجباري"
    elif hours_since >= OIL_H_LIMIT:
        stage = "🟠 تجاوز 200 — تنبيه يومي"
    elif rate and rate > 0 and (OIL_H_LIMIT - hours_since) / (rate / 7.0) <= OIL_PREDICT_DAYS:
        stage = "🟡 اقتراب العتبة (تنبؤ)"
    else:
        stage = "🟢 طبيعي"
    lines = ["🔧 زيت محرك الرافعة الشوكية", ""]
    if has_cycle:
        lca = _oil_dt(st.get("last_change_at"))
        lines.append("🛢 آخر تغيير زيت: " + (lca.strftime("%Y-%m-%d %H:%M") if lca else "—"))
        lines.append("🔢 خط الأساس (قراءة العداد عند التغيير): " + _oil_fmt(base) + " ساعة")
        lines.append("📊 آخر قراءة للعداد: " + (_oil_fmt(cur) if cur is not None else "—") + " ساعة")
        lines.append("⏱ الساعات منذ آخر تغيير: " + _oil_fmt(hours_since) + " ساعة")
    else:
        lines.append("لا توجد دورة مسجلة بعد.")
        lines.append("ابدأ بـ«🔧 تسجيل تغيير زيت جديد» أو أدخل قراءة العداد الحالية.")
    lines.append("")
    if rate and rate > 0:
        lines.append("🧠 معدل الاستهلاك المتعلّم: " + _oil_fmt(rate) + " ساعة/أسبوع")
        remaining = OIL_H_LIMIT - hours_since
        if remaining > 0:
            days = remaining / (rate / 7.0)
            eta = now + datetime.timedelta(days=days)
            lines.append("🔮 التوقع: بلوغ " + str(int(OIL_H_LIMIT)) + " ساعة يوم "
                         + _OIL_DAYS_AR.get(eta.weekday(), "") + " " + eta.strftime("%Y-%m-%d"))
    else:
        lines.append("🧠 معدل الاستهلاك: غير معروف بعد — يبدأ التحليل الذكي تلقائياً بعد أول قراءتين متباعدتين")
    lines.append("🚦 الحالة: " + stage)
    lines.append("")
    lines.append("العتبات: تنبيه يومي عند " + str(int(OIL_H_LIMIT))
                 + " ساعة • إشعار أحمر كل 3 ساعات عند " + str(int(OIL_H_RED)) + " ساعة")
    return "\n".join(lines)


def oil_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تحديث قراءة العداد", callback_data="oil_update"),
         InlineKeyboardButton("🔧 تسجيل تغيير زيت جديد", callback_data="oil_change")],
        [InlineKeyboardButton("📜 سجل الصيانة", callback_data="oil_log"),
         InlineKeyboardButton("👷 طلب تأكيد من العامل", callback_data="oil_askconfirm")],
        [InlineKeyboardButton("🔄 تحديث", callback_data="oil_menu")],
    ])


def oil_log_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="oil_back")]])


def _oil_parse_hours(text):
    """تحويل نص القراءة إلى رقم آمن أو None."""
    t = (text or "").strip().replace(",", ".")
    try:
        v = float(t)
    except (TypeError, ValueError):
        return None
    if v < 0 or v > 10000000:
        return None
    return round(v, 1)


async def callback_oil_handler(update, context):
    """كل كولباكات الزيت (النمط ^oil_): إدارة للمدراء + تأكيد للعامل."""
    query = update.callback_query
    data = query.data or ""
    if data == "oil_wconfirm":
        await _oil_worker_confirm_cb(update, context, query)
        return
    user = query.from_user
    if not await asyncio.to_thread(is_admin, user.id):
        await query.answer("❌ هذا القسم للمدراء فقط.", show_alert=True)
        return
    _KB_ROLE_UID.set(user.id)
    if data in ("oil_menu", "oil_back"):
        card = await asyncio.to_thread(oil_status_card)
        await query.answer()
        try:
            await query.edit_message_text(card, reply_markup=oil_menu_keyboard())
        except Exception:
            await query.message.reply_text(card, reply_markup=oil_menu_keyboard())
        return
    if data == "oil_update":
        context.user_data.clear()
        _set_state(context, STATE_OIL_READING)
        await query.answer()
        await query.message.reply_text(
            "📝 تحديث قراءة العداد\n\nأرسل القراءة الحالية من عداد الساعات (رقم فقط):\nمثال: 1520.5",
            reply_markup=get_input_keyboard())
        return
    if data == "oil_change":
        context.user_data.clear()
        _set_state(context, STATE_OIL_NEW_HOURS)
        await query.answer()
        await query.message.reply_text(
            "🔧 تسجيل تغيير زيت جديد\n\nأرسل قراءة العداد الحالية (رقم فقط):\nمثال: 1520.5",
            reply_markup=get_input_keyboard())
        return
    if data == "oil_log":
        rows = await asyncio.to_thread(oil_recent_log, 20)
        await query.answer()
        if not rows:
            txt = "📜 سجل الصيانة\n\nلا توجد تغييرات مسجلة بعد."
        else:
            lines = ["📜 سجل الصيانة — آخر " + str(len(rows)) + " تغيير", ""]
            for r in rows:
                ca = _oil_dt(r.get("changed_at"))
                lines.append("🛢 " + (ca.strftime("%Y-%m-%d %H:%M") if ca else "—")
                             + " — عند " + _oil_fmt(r.get("hours_at_change")) + " ساعة")
                if r.get("recorded_by_name"):
                    lines.append("   بواسطة: " + r["recorded_by_name"])
                if r.get("note"):
                    lines.append("   " + r["note"])
            txt = "\n".join(lines)
        try:
            await query.edit_message_text(txt, reply_markup=oil_log_back_keyboard())
        except Exception:
            await query.message.reply_text(txt, reply_markup=oil_log_back_keyboard())
        return
    if data == "oil_askconfirm":
        users = await asyncio.to_thread(oil_worker_candidates)
        await query.answer()
        if not users:
            await query.message.reply_text("❌ لا يوجد مستخدمون نشطون لإرسال طلب التأكيد لهم.")
            return
        wrows = [[InlineKeyboardButton("👷 " + name, callback_data="oilwk_" + str(tid))] for tid, name in users]
        wrows.append([InlineKeyboardButton("❌ إلغاء", callback_data="oil_back")])
        await query.message.reply_text("👷 اختر العامل لإرسال طلب تأكيد تغيير الزيت:",
                                       reply_markup=InlineKeyboardMarkup(wrows))
        return
    if data.startswith("oilwk_"):
        tid_s = data[len("oilwk_"):]
        if not tid_s.isdigit():
            await query.answer("❌ طلب غير صالح.", show_alert=True)
            return
        tid = int(tid_s)
        users = await asyncio.to_thread(oil_worker_candidates)
        wname = next((n for t, n in users if t == tid), "العامل")
        try:
            await asyncio.to_thread(oil_set_confirm_worker, tid)
            await context.bot.send_message(
                chat_id=tid,
                text=("🔧 طلب تأكيد تغيير زيت الرافعة الشوكية\n\n"
                      "اضغط الزر أدناه بعد إتمام تغيير الزيت فعلياً:"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✅ أكدت تغيير الزيت", callback_data="oil_wconfirm")]]))
            await query.answer("✅ تم إرسال طلب التأكيد.")
            await query.message.reply_text("📨 أُرسل طلب التأكيد إلى: " + wname)
        except Exception as e:
            await query.answer("❌ فشل الإرسال: " + str(e), show_alert=True)
        return
    await query.answer()


async def _oil_worker_confirm_cb(update, context, query):
    """زر العامل: يعمل فقط لمن أُرسل له طلب التأكيد — بلا أي وصول آخر."""
    user = query.from_user
    st = await asyncio.to_thread(oil_ensure_row)
    if not st or st.get("confirm_worker_id") != user.id:
        await query.answer("❌ لا يوجد طلب تأكيد موجّه لك.", show_alert=True)
        return
    if bool(st.get("confirm_confirmed")):
        await query.answer("✅ تأكيدك مسجل مسبقاً — بانتظار إدخال القراءة الجديدة.")
        return
    ok = await asyncio.to_thread(oil_worker_confirmed, user.id)
    if not ok:
        await query.answer("❌ انتهت صلاحية الطلب أو أُلغي.", show_alert=True)
        return
    await query.answer("✅ تم تسجيل تأكيدك. شكراً!")
    try:
        await query.edit_message_text("✅ تم تسجيل تأكيد تغيير الزيت — شكراً لك!")
    except Exception:
        pass
    wname = user.first_name or user.username or str(user.id)
    text = ("✅ أكّد العامل " + wname + " تغيير زيت الرافعة.\n\n"
            "إتمام الدورة: 🔧 زيت الرافعة ← «🔧 تسجيل تغيير زيت جديد» "
            "وأدخل القراءة الحالية لتصفير العدّاد.")
    ids = await asyncio.to_thread(oil_manager_ids)
    for tid in ids:
        try:
            await context.bot.send_message(chat_id=tid, text=text)
        except Exception as e:
            logging.warning("[OIL] manager notify " + str(tid) + " failed: " + str(e))


def _oil_monitor_prepare():
    """يقرأ الحالة ويقرر التنبيهات ويحدّث أعلام الإرسال (متزامن — يُستدعى من خيط)."""
    st = oil_ensure_row()
    if not st:
        return []
    st["has_cycle"] = st.get("last_change_at") is not None
    rate = oil_compute_rate(oil_recent_readings(6))
    now = oil_algiers_now()
    due = oil_due_alerts(st, rate, now)
    if not due:
        return []
    today = now.date()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for a in due:
                if a["kind"] == "weekly":
                    cur.execute("UPDATE oil_status SET weekly_remind_date = %s WHERE id = 1", (today,))
                elif a["kind"] == "predict":
                    cur.execute("UPDATE oil_status SET predict_sent_at = %s WHERE id = 1", (now,))
                elif a["kind"] == "daily200":
                    cur.execute("UPDATE oil_status SET notified_200_date = %s WHERE id = 1", (today,))
                elif a["kind"] == "red250":
                    cur.execute("UPDATE oil_status SET notified_250_at = %s WHERE id = 1", (now,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.warning("[OIL] flag update failed: " + str(e))
    finally:
        release_connection(conn)
    return [(a["kind"], oil_alert_text(a["kind"], a)) for a in due]


async def _oil_send_alerts(bot, alerts):
    """إرسال التنبيهات لكل المدراء فقط."""
    ids = await asyncio.to_thread(oil_manager_ids)
    for kind, text in alerts:
        if not text:
            continue
        for tid in ids:
            try:
                await bot.send_message(chat_id=tid, text=text)
            except Exception as e:
                logging.warning("[OIL] alert to " + str(tid) + " failed: " + str(e))


_OIL_LOOP_SEC = int(os.getenv("OIL_LOOP_SEC", "3600"))  # v87: فحص الزيت كل ساعة (كان 600 ث)

async def _oil_monitor_loop(bot):
    """حلقة مراقبة زيت الرافعة: فحص كل ساعة (v87 — كان كل 10 دقائق) —
    تذكير الجمعة الأسبوعي، تنبؤ قبل 200، يومي عند 200، أحمر كل 3 ساعات.
    بلا أي مكتبة خارجية: مهمة asyncio خفيفة تعمل داخل البوت."""
    await asyncio.sleep(20)  # انتظار اكتمال إقلاع البوت
    while True:
        try:
            alerts = await asyncio.to_thread(_oil_monitor_prepare)
            if alerts:
                await _oil_send_alerts(bot, alerts)
        except Exception as e:
            logging.error("[OIL] monitor error: " + str(e))
        # v87: كل ساعة بدل 10 دقائق — تنبيهات الزيت أسبوعية/تنبؤات فقط (قرار المالك)
        await asyncio.sleep(_OIL_LOOP_SEC)


# ═══════════════════════════════════════════════════════════
#  EXTRACTION PERMISSION & REQUEST HANDLERS
# ═══════════════════════════════════════════════════════════


async def callback_extract_approve_handler(update, context):
    """Handle admin approve/reject extraction request."""
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("❌ للمديرين فقط.", show_alert=True)
        return
    data = query.data
    parts = data.split("_")
    action = parts[1]
    try:
        request_id = int(parts[2])
    except (IndexError, ValueError):
        return
    admin_db = get_user(query.from_user.id)
    admin_uid = admin_db["id"] if admin_db else 0
    try:
        success, msg = process_extraction_request(request_id, action == "approve", admin_uid)
        await query.edit_message_text(msg)
        if success:
            try:
                conn = get_connection()
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        cur.execute(
                            "SELECT er.requester_id, u.telegram_id, p.name AS product_name, er.quantity "
                            "FROM extraction_requests er "
                            "JOIN users u ON u.id = er.requester_id "
                            "JOIN products p ON p.id = er.product_id "
                            "WHERE er.id = %s",
                            (request_id,),
                        )
                        req_info = cur.fetchone()
                    conn.commit()
                finally:
                    release_connection(conn)
                if req_info:
                    unit_ar = _get_unit_ar(req_info["product_name"])
                    if action == "approve":
                        notify_text = (
                            "✅ تم الموافقة على طلبك!\n\n"
                            + "📦 " + req_info["product_name"] + "\n"
                            + "📦 الكمية: " + str(req_info["quantity"]) + " " + unit_ar
                        )
                    else:
                        notify_text = "❌ تم رفض طلب الإخراج."
                    await context.bot.send_message(chat_id=req_info["telegram_id"], text=notify_text)
            except Exception as e:
                logging.error("[EXTRACT] Notify requester failed: " + str(e))
            if action == "approve":
                await send_low_stock_alerts(context.bot)
    except Exception as e:
        logging.error("[EXTRACT_APPROVE] Error: " + str(e))
        try:
            await query.edit_message_text("❌ خطأ: " + str(e))
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════

# ── v76: سجل الأخطاء الحي — لا تخمين بعد اليوم ──
_recent_errors = []          # (time, type, msg, user_hint)
_err_notify_rate = {}        # توقيع الخطأ -> آخر إشعار (حد 60ث ضد الفيضان)
_err_lock = threading.Lock()

def _read_bot_setting(key):
    """قراءة قيمة من bot_settings (v76). الفشل = None."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
            r = cur.fetchone()
        conn.commit()
        return r[0] if r else None
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        release_connection(conn)


def _write_bot_setting(key, value):
    """كتابة قيمة في bot_settings (v76). الفشل = False."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(value)),
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        release_connection(conn)


async def error_handler(update, context):
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        logging.warning("[BOT] Conflict detected - another instance running. Will recover automatically.")
        return
    err = context.error
    err_type = type(err).__name__
    err_msg = str(err)[:400]
    _hint = ""
    try:
        _eu = update.effective_user if update is not None else None
        if _eu is not None:
            _hint = _eu.username or _eu.first_name or str(_eu.id)
    except Exception:
        pass
    try:
        with _err_lock:
            _recent_errors.append((
                datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%m-%d %H:%M:%S"),
                err_type, err_msg, _hint,
            ))
            del _recent_errors[:-30]
    except Exception:
        pass
    logging.error("[BOT ERROR] %s: %s", err_type, err, exc_info=True)
    # v76: إشعار المالك بالخطأ الحقيقي فوراً (مرة/دقيقة لكل توقيع خطأ)
    try:
        _sig = err_type + ":" + err_msg[:80]
        _now = time.time()
        _send = False
        with _err_lock:
            if _now - _err_notify_rate.get(_sig, 0) > 60:
                _err_notify_rate[_sig] = _now
                _send = True
        if _send:
            _txt = ("🚨 خطأ حقيقي في البوت\n"
                    "🔸 النوع: " + err_type + "\n"
                    "🔸 التفاصيل: " + err_msg[:350] +
                    ((" — من: " + _hint) if _hint else "") + "\n"
                    "🧾 /errors لآخر الأخطاء")
            for _oid in list(OWNER_IDS)[:3]:
                try:
                    await context.bot.send_message(chat_id=_oid, text=_txt)
                except Exception:
                    pass
    except Exception:
        pass
    # v76: الحقيقة للمدراء/المالك — والرسالة العامة للبقية
    try:
        if update and update.effective_message:
            _eu = update.effective_user
            if _eu is not None and (_eu.id in OWNER_IDS or _eu.id in ADMIN_IDS):
                await update.effective_message.reply_text("❌ خطأ: " + err_type + " — " + err_msg[:200])
            else:
                await update.effective_message.reply_text("❌ حدث خطأ حاول مرة أخرى.")
    except Exception:
        pass


async def errors_command(update, context):
    """v76: آخر الأخطاء الحقيقية المسجلة منذ الإقلاع — للمالك فقط."""
    if not is_owner(update.effective_user.id):
        try:
            await update.message.reply_text("🔒 للمالك فقط.")
        except Exception:
            pass
        return
    with _err_lock:
        rows = list(_recent_errors)[-20:]
    if not rows:
        try:
            await update.message.reply_text("✅ لا أخطاء مسجلة منذ الإقلاع.")
        except Exception:
            pass
        return
    lines = ["🧾 آخر الأخطاء (" + str(len(rows)) + "):"]
    for _t, _ty, _m, _h in reversed(rows):
        lines.append("▪ " + _t + " [" + _ty + "] " + _m[:160] + ((" — " + _h) if _h else ""))
    try:
        await update.message.reply_text("\n".join(lines)[:4000])
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#  BUILD APP
# ═══════════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)


async def callback_admin_done_handler(update, context):
    query = update.callback_query
    await query.answer()
    # تنظيف الشاشة: حذف كل رسائل المهمة المنتهية + تصفير حالة المهمة
    await sweep_screen(update, context)
    try:
        context.user_data.clear()
    except Exception:
        pass
    try:
        await query.delete_message()
    except Exception:
        pass
    await _send_themed(context.bot, query.message.chat_id, "✅ القائمة الرئيسية:", reply_markup=get_role_keyboard(query.from_user.id))


async def callback_go_start_handler(update, context):
    """ينفّذ /start عند ضغط المستخدم على زر التحديث."""
    query = update.callback_query
    await query.answer()
    # تنظيف الشاشة: حذف كل رسائل المهمة السابقة قبل عرض القائمة
    await sweep_screen(update, context)
    # v82: مسح الحالة المعلقة — زر التحديث بداية نظيفة كي لا تختطف مهمة قديمة الرسائل التالية
    try:
        context.user_data.clear()
    except Exception:
        pass
    user = query.from_user
    sync_user_info(user)

    db_user = get_user(user.id)
    name = user.first_name or ""

    if is_admin(user.id):
        try:
            await query.delete_message()
        except Exception:
            pass
        try:
            await _send_themed(context.bot, query.message.chat_id,
                "\U0001f44b مرحباً <b>" + name + "</b>\n\n"
                "\U0001f3e2 أنت مدير النظام.\n"
                "\U0001f4cb استخدم الأزرار أدناه لإدارة المخزون.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_keyboard(),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=get_admin_keyboard())
    elif db_user and db_user.get("role") == "pending":
        try:
            await query.delete_message()
        except Exception:
            pass
        try:
            await _send_themed(context.bot, query.message.chat_id,
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "⏳ حسابك قيد المراجعة.\n"
                "يجب الانتظار حتى يفعلك المدير.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        try:
            await query.delete_message()
        except Exception:
            pass
        try:
            await _send_themed(context.bot, query.message.chat_id,
                "👋 مرحباً <b>" + name + "</b>\n\n"
                "⌛ بحث عن المنتجات المتاحة\n"
                "📁 استخدم الأزرار أدناه.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ تم التطوير بواسطة <a href=\"tg://resolve?screen=YOUSSEF_D1\">@YOUSSEF_D1</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_role_keyboard(user.id),
            )
        except Exception:
            await query.message.reply_text("🔄 اضغط /start", reply_markup=get_role_keyboard(user.id))


# ═══════════════════════════════════════════════════════════
#  PENDING DELIVERIES DISPLAY
# ═══════════════════════════════════════════════════════════

async def callback_provide_handler(update, context):
    """Handle 'provide_<product_id>' — عرض المنتظرين وإشعارهم عند التوفّر.

    وفق «إشعار فقط»: لا يُسأل المدير عن كتابة كمية هنا ولا يُعدَّل المخزون
    من هذا الزر — الإضافة تتم من «➕ إضافة كمية» كالمعتاد، وفور تغطية
    الكمية للمنتظرين يُشعَرون تلقائياً (تسوية تلقائية على كل مسارات الإضافة).
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("provide_"):
        return
    try:
        product_id = int(data.split("_")[1])
    except (IndexError, ValueError):
        return
    product = get_product_by_id(product_id)
    if not product:
        await query.message.reply_text("❌ السلعة غير موجودة.", reply_markup=get_admin_keyboard())
        return
    context.user_data.clear()
    admin_db = get_user(query.from_user.id)
    admin_id = admin_db["id"] if admin_db else None
    # تسوية موضعية فورية لهذه السلعة: أي منتظر غطاه المخزون الحالي يُشعَر الآن
    healed = None
    try:
        healed = await asyncio.to_thread(auto_reconcile_product, product_id, 0, admin_id)
        if healed and healed["fulfilled"]:
            await _send_fulfilled_notifications(context.bot, healed["fulfilled"])
    except Exception as heal_err:
        logging.error("[PENDING] Provide self-heal failed: " + str(heal_err))
    unit_ar = _get_unit_ar(product["name"])
    # من ينتظر الآن بعد التسوية؟
    pendings = [p for p in get_all_pending_deliveries() if p["product_id"] == product_id]
    if not pendings:
        msg = "✅ لا يوجد زبائن منتظرون لهذه السلعة."
        if healed and healed["fulfilled"]:
            waiters_covered = []
            for f in healed["fulfilled"]:
                waiters_covered.append((f.get("user_name") or "زبون") + " (" + str(f["qty"]) + ")")
            msg = ("🎉 تمت تغطية كل المنتظرين وإشعارهم تلقائياً:\n"
                   "🔔 " + "، ".join(waiters_covered))
        msg += "\n\n📊 المخزون الحالي: " + str(product["quantity"]) + " " + unit_ar
        await query.message.reply_text(msg, reply_markup=get_admin_keyboard())
        return
    # ما زال هناك عجز حقيقي — اعرض الوضع بوضوح دون مطالبة بكتابة كمية
    total_pending = sum(p["pending_qty"] for p in pendings)
    stock_qty = product["quantity"]
    waiters = []
    for p in pendings:
        waiters.append((p.get("user_fname") or p.get("user_name") or "مجهول") + " " + str(p["pending_qty"]))
    real_deficit = max(0, total_pending - max(0, stock_qty))
    lines = [
        "📦 " + product["name"],
        "📊 المخزون الحالي: " + str(stock_qty) + " " + unit_ar,
        "⏳ زبائن منتظرون (" + str(len(pendings)) + "): " + "، ".join(waiters),
        "⚠️ العجز الحقيقي: " + str(real_deficit) + " " + unit_ar + " — المخزون لا يكفيهم بعد",
        "",
        "💡 عند وصول البضاعة أضِفها من زر «➕ إضافة كمية»",
        "🔔 فور تغطية الكمية للمنتظرين سيتم إشعارهم تلقائياً",
    ]
    await query.message.reply_text("\n".join(lines), reply_markup=get_admin_keyboard())


async def _show_pending_admin(update, context):
    """عرض كل السلع ذات العجز مع أزرار توفير مباشرة — للمدير."""
    # شفاء ذاتي: أي طلب معلق غطته الكمية الحالية يُزال فوراً عند فتح
    # اللوحة (يشمل الصفوف العالقة القديمة) مع إشعار الزبائن المعنيين.
    healed_note = ""
    try:
        healed = await asyncio.to_thread(auto_reconcile_all)
        if healed and healed["fulfilled"]:
            await _send_fulfilled_notifications(context.bot, healed["fulfilled"])
            healed_note = ("🔄 تمت تسوية " + str(healed["total_covered"])
                           + " وحدة كانت مغطاة بالمخزون وتأخرت إزالتها من القائمة")
    except Exception as heal_err:
        logging.error("[PENDING] Self-heal failed: " + str(heal_err))
    pendings = get_all_pending_deliveries()
    if not pendings:
        empty_txt = "✅ لا توجد سلع ذات عجز حالياً."
        if healed_note:
            empty_txt = "🎉 " + healed_note + chr(10) + chr(10) + empty_txt
        await update.message.reply_text(
            empty_txt,
            reply_markup=get_admin_keyboard(),
        )
        return
    # Group by product
    from collections import OrderedDict
    grouped = OrderedDict()
    for p in pendings:
        pid = p["product_id"]
        if pid not in grouped:
            grouped[pid] = {
                "name": p["product_name"],
                "product_qty": p["product_qty"],
                "product_id": pid,
                "customers": [],
            }
        cust_name = p.get("user_fname") or p.get("user_name") or "مجهول"
        grouped[pid]["customers"].append({
            "name": cust_name,
            "qty": p["pending_qty"],
            "user_tid": p.get("user_tid"),
            "date": p["created_at"].strftime("%d/%m %H:%M") if p.get("created_at") else "",
        })
    # Build inline buttons for each product: [توفير الآن]
    provide_buttons = []
    for pid, info in grouped.items():
        total_pending = sum(c["qty"] for c in info["customers"])
        label = "📦 " + info["name"] + " (" + str(total_pending) + ")"
        provide_buttons.append([InlineKeyboardButton(label, callback_data="provide_" + str(pid))])
    # Back to main keyboard
    provide_buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_done")])
    # Build text
    lines = ["🔴 السلع المطلوبة للزبائن المنتظرين (" + str(len(grouped)) + ")\n"]
    if healed_note:
        lines.append(healed_note)
    for pid, info in grouped.items():
        total_pending = sum(c["qty"] for c in info["customers"])
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        stock_qty = info["product_qty"]
        unit_ar = _get_unit_ar(info["name"])
        if stock_qty < 0:
            lines.append(
                "📦 " + info["name"]
                + " — ⚠️ العجز الحقيقي: " + str(-stock_qty) + " " + unit_ar
                + " | ⏳ منتظرون: " + str(total_pending) + " " + unit_ar
            )
        else:
            lines.append(
                "📦 " + info["name"]
                + " — 📊 المخزون: " + str(stock_qty) + " " + unit_ar
                + " | ⏳ منتظرون: " + str(total_pending) + " " + unit_ar
            )
        for c in info["customers"]:
            lines.append("   ├─ 👤 " + c["name"] + ": منتظر " + str(c["qty"]) + " وحدة  ⏰ " + c["date"])
    lines.append("")
    lines.append("💡 اضغط زر السلعة لعرض المنتظرين — وعند توفر البضاعة يُشعَرون تلقائياً")
    text = "\n".join(lines)
    # Split if too long for Telegram
    if len(text) > 4000:
        chunks = [lines[0] + "\n"]
        for ln in lines[1:]:
            if len("\n".join(chunks[-1])) + len("\n" + ln) > 3900:
                chunks.append(["🔴 تابع السلع المطلوبة\n"])
            chunks[-1].append(ln)
        for chunk in chunks:
            await update.message.reply_text("\n".join(chunk))
        await update.message.reply_text(
            "📤 " + str(len(grouped)) + " سلعة ذات عجز — اختر سلعة للتوفير:",
            reply_markup=InlineKeyboardMarkup(provide_buttons),
        )
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(provide_buttons))


async def _show_pending_customer(update, context):
    """عرض طلبات الزبون المعلقة فقط — للزبون."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        return
    # شفاء ذاتي: إذا كانت لديه طلبات معلقة غطاها المخزون فعلاً وتأخرت
    # إزالتها، تُزال هنا فوراً ويصله إشعار بالتغطية قبل عرض القائمة.
    try:
        healed = await asyncio.to_thread(auto_reconcile_all)
        if healed and healed["fulfilled"]:
            mine = [f for f in healed["fulfilled"] if f.get("user_tid") == user.id]
            if mine:
                await _send_fulfilled_notifications(context.bot, mine)
    except Exception as heal_err:
        logging.error("[PENDING] Self-heal failed: " + str(heal_err))
    pendings = get_user_pending_deliveries(db_user["id"])
    if not pendings:
        await update.message.reply_text(
            "✅ لا توجد طلبات معلقة حالياً.",
            reply_markup=get_customer_keyboard(),
        )
        return
    lines = ["⏳ طلباتي المعلقة (" + str(len(pendings)) + ")\n"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    for p in pendings:
        unit = _get_unit_ar(p["product_name"])
        date_str = p["created_at"].strftime("%d/%m %H:%M") if p.get("created_at") else ""
        lines.append(
            "📌 " + p["product_name"]
            + "\n   └─ منتظر: " + str(p["pending_qty"]) + " " + unit
            + "  |  ⏰ " + date_str
        )
    lines.append("")
    lines.append("💡 سيتم إشعارك تلقائياً عند توفر البضاعة")
    await update.message.reply_text("\n".join(lines), reply_markup=get_customer_keyboard())


# ═══════════════════════════════════════════════════════════
#  INCOMING ORDERS DISPLAY & CALLBACKS
# ═══════════════════════════════════════════════════════════

def _incoming_order_keyboard(order_id):
    """أزرار «جاهز / غير جاهز» لطلب توريد — واحدة للقائمة ولإشعارات الوصول
    (صورة أو نص) حتى يعامل كل طلب بالطريقة نفسها لحظة وصوله."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ جاهز", callback_data="io_ready_" + str(order_id)),
            InlineKeyboardButton("⏳ غير جاهز", callback_data="io_notready_" + str(order_id)),
        ]
    ])


async def _show_admin_incoming_orders(update, context):
    """عرض طلبات التوريد - كل طلب برسالة منفصلة: صورة + أزرار جاهز/غير جاهز."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""\
                SELECT io.*, u.first_name, u.username, u.telegram_id
                FROM incoming_orders io
                LEFT JOIN users u ON u.id = io.requester_id
                WHERE io.status IN ('pending', 'received')
                ORDER BY io.created_at ASC
                """)
            all_orders = [dict(r) for r in cur.fetchall()]
        conn.commit()
    except Exception:
        conn.rollback()
        all_orders = []
    finally:
        release_connection(conn)

    if not all_orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=get_admin_keyboard())
        return

    bot = context.bot
    for o in all_orders:
        order_id = o["id"]
        req_name = o.get("first_name") or o.get("username") or "مستخدم"
        caption = (
            "🚚 طلب #" + str(order_id) +
            "  |  👤 " + req_name +
            "  |  📦 " + o["product_name"]
        )
        keyboard = _incoming_order_keyboard(order_id)
        if o.get("photo_file_id"):
            try:
                await bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=o["photo_file_id"],
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception:
                await update.message.reply_text(caption, reply_markup=keyboard)
        else:
            await update.message.reply_text(caption + "\n\n⚠️ بدون صورة وصل", reply_markup=keyboard)

    await update.message.reply_text("📤 عدد الطلبات: " + str(len(all_orders)), reply_markup=get_admin_keyboard())


async def _show_customer_orders(update, context):
    """عرض طلبات التوريد المعلّقة + قيد التجهيز للمستخدم."""
    user = update.effective_user
    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        return
    orders = get_user_pending_orders(db_user["id"])
    if not orders:
        await update.message.reply_text("📭 لا توجد طلبات توريد حالياً.", reply_markup=get_customer_keyboard())
        return

    lines = ["📋 طلباتك (" + str(len(orders)) + ")\n"]
    for o in orders:
        created = o["created_at"].strftime("%H:%M %d/%m") if o.get("created_at") else ""
        status_txt = "⏳ قيد التوريد" if o["status"] == "pending" else "🔧 جاري التجهيز"
        photo_icon = "  📷" if o.get("photo_file_id") else ""
        lines.append(
            "📋 #" + str(o["id"]) +
            "  |  📦 " + o["product_name"] +
            "  |  🔢 " + str(o["quantity"]) +
            "  |  " + status_txt +
            "  |  ⏰ " + created + photo_icon
        )
    lines.append("\n📡 سيتم إشعارك تلقائياً بأي تحديث.")
    await update.message.reply_text("\n".join(lines), reply_markup=get_customer_keyboard())


async def handle_receipt_photo(update, context):
    """معالجة صورة الوصل - إنشاء طلب جديد مباشرة."""
    user = update.effective_user
    state = context.user_data.get("state")
    photo = update.message.photo
    if not photo:
        return
    file_id = photo[-1].file_id
    req_name = html.escape(user.first_name or user.username or str(user.id))

    if state not in ("waiting_order_photo", "waiting_order_input", "waiting_order_name"):
        return

    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
        context.user_data.clear()
        return
    try:
        order = create_incoming_order(db_user["id"], "طلب بوصل صورة", 1, photo_file_id=file_id)
        order_id = order["id"]
        caption = (
            "🚚 طلب توريد جديد!\n\n"
            "👤 من: <b>" + req_name + "</b>\n"
            "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
            "📷 وصل مرفق"
        )
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=_incoming_order_keyboard(order_id),
                )
            except Exception:
                pass
        await update.message.reply_text(
            "✅ تم تسجيل طلب التوريد!\n\n"
            "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
            "⏳ الحالة: <b>قيد التوريد</b>\n\n"
            "📡 سيتم إشعارك تلقائياً عند التحديث.\n"
            "تابع طلبك من 📋 طلباتي",
            parse_mode=ParseMode.HTML,
            reply_markup=get_customer_keyboard(),
        )
        logging.info("[INCOMING] Photo order #" + str(order_id) + " by user " + str(user.id))
    except Exception as e:
        logging.error("[INCOMING] Failed: " + str(e))
        await update.message.reply_text("❌ حدث خطأ.", reply_markup=get_customer_keyboard())
    context.user_data.clear()


async def callback_incoming_order_handler(update, context):
    """معالجة أزرار المدير: جاهز / غير جاهز."""
    query = update.callback_query
    await query.answer()
    data = query.data
    bot = context.bot

    # ── 1. طلب جاهز في المخزن ──
    if data.startswith("io_ready_"):
        order_id = int(data.split("_")[-1])
        order = complete_incoming_order(order_id)
        if not order:
            # لا يوجد رفض هنا: غالباً الطلب عولج مسبقاً من نسخة إشعار أخرى
            try:
                await query.edit_message_caption(caption="ℹ️ تمت معالجة هذا الطلب مسبقاً أو لم يعد متاحاً.")
            except Exception:
                try:
                    await query.edit_message_text("ℹ️ تمت معالجة هذا الطلب مسبقاً أو لم يعد متاحاً.")
                except Exception:
                    pass
            return

        tg_id = _get_telegram_id(order["requester_id"])
        if tg_id:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="✅ طلبك جاهز في المخزن الآن!\n\n"
                         "📦 السلعة: <b>" + order["product_name"] + "</b>\n"
                         "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n\n"
                         "🏃 تقدر تيجان تأخذه!",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        # Edit caption (photo) or text depending on message type
        try:
            await query.edit_message_caption(
                caption="✅ تم تأكيد - الطلب #" + str(order_id) + " جاهز في المخزن\n"
                "📦 " + order["product_name"] +
                "\n\n📱 تم إشعار صاحب الطلب.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            try:
                await query.edit_message_text(
                    "✅ تم تأكيد - الطلب #" + str(order_id) + " جاهز في المخزن\n"
                    "📦 " + order["product_name"] +
                    "\n\n📱 تم إشعار صاحب الطلب."
                )
            except Exception:
                pass
        logging.info("[INCOMING] Ready order #" + str(order_id))
        return

    # ── 2. طلب ليس جاهز بعد ──
    if data.startswith("io_notready_"):
        order_id = int(data.split("_")[-1])

        tg_id = None
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT io.*, u.telegram_id FROM incoming_orders io LEFT JOIN users u ON u.id = io.requester_id WHERE io.id = %s", (order_id,))
                order = cur.fetchone()
                if order:
                    tg_id = order.get("telegram_id")
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)

        if tg_id:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text="⏳ طلبك ليس جاهزاً بعد.\n\n"
                         "📋 رقم الطلب: <b>#" + str(order_id) + "</b>\n"
                         "سيتم إشعارك فور الجاهزية.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

        if order:
            try:
                await query.edit_message_caption(
                    caption="⏳ الطلب #" + str(order_id) + " ليس جاهزاً بعد.\n"
                    "📦 " + dict(order).get("product_name", "") +
                    "\n\n📱 تم إشعار المستخدم.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                try:
                    await query.edit_message_text(
                        "⏳ الطلب #" + str(order_id) + " ليس جاهزاً بعد.\n"
                        "📦 " + dict(order).get("product_name", "") +
                        "\n\n📱 تم إشعار المستخدم."
                    )
                except Exception:
                    pass
        else:
            try:
                await query.edit_message_caption(caption="❌ الطلب غير موجود.")
            except Exception:
                try:
                    await query.edit_message_text("❌ الطلب غير موجود.")
                except Exception:
                    pass
        logging.info("[INCOMING] Not ready - order #" + str(order_id))
        return

def _get_telegram_id(user_db_id):
    """جلب telegram_id من user_db_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE id = %s", (user_db_id,))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


# ═══════════ v72: حارس التجمّد — إعادة تشغيل ذاتية عند أي تجمّد ═══════════
_loop_hb = {"t": 0.0, "got": False}


def _loop_heartbeat():
    _loop_hb["t"] = time.time()
    _loop_hb["got"] = True


async def _loop_heartbeat_task():
    """نبضة على حلقة الأحداث كل 30 ث — إن تجمدت الحلقة يتوقف هذا السجل فوراً."""
    loop = asyncio.get_running_loop()
    while True:
        loop.call_soon_threadsafe(_loop_heartbeat)
        await asyncio.sleep(30)


def _freeze_watchdog():
    """خيط خارجي مستقل عن الحلقة: إذا لم تنبض حلقة الأحداث ≥ 4 دقائق فالعملية
    مجمّدة تماماً (لا تلغرام ولا PWA ولا مراقبة) → قتل ذاتي os._exit(1)
    → Render يعيد التشغيل تلقائياً خلال دقائق بدل انتظار ريستارت يدوي."""
    def _run():
        while True:
            time.sleep(60)
            try:
                if not _loop_hb["got"]:
                    continue
                _age = time.time() - _loop_hb["t"]
                if _age > 240:
                    logging.error(
                        "[WATCHDOG] Event loop frozen for "
                        + str(int(_age)) + "s — self-restarting (v72)"
                    )
                    os._exit(1)
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True, name="freeze-watchdog").start()


async def _on_app_post_init(application):
    """Start background tasks after the app is initialized (works for both webhook and polling)."""
    # v76: تنبيه التوقف غير النظيف — لو العلم كان 0 عند الإقلاع فالتشغيل السابق مات قسرياً (تجمد/قتل/كراش)
    try:
        _prev = await asyncio.to_thread(_read_bot_setting, "clean_shutdown")
        if _prev == "0":
            for _aid in list(ADMIN_IDS)[:5]:
                try:
                    await application.bot.send_message(
                        chat_id=_aid,
                        text="⚠️ أعيد تشغيل البوت بعد توقف غير نظيف (تجمد أو قتل عملية).\n"
                             "🧾 أرسل /errors لرؤية آخر الأخطاء الحقيقية،\n"
                             "📋 والسجلات التقنية للتفاصيل الكاملة.",
                    )
                except Exception:
                    pass
        await asyncio.to_thread(_write_bot_setting, "clean_shutdown", "0")
    except Exception:
        pass
    asyncio.create_task(_offline_retry_loop(application.bot))
    asyncio.create_task(_oil_monitor_loop(application.bot))
    _freeze_watchdog()  # v72: حارس التجمّد الذاتي — ريستارت تلقائي عند أي تجمّد
    asyncio.create_task(_loop_heartbeat_task())  # v72: نبضة الحلقة للحارس
    logging.info("[BOT] Offline retry task started via post_init")
    logging.info("[BOT] Freeze watchdog armed via post_init (v72)")
    logging.info("[BOT] Oil monitor task started via post_init")


def build_application():
    async def _on_app_post_shutdown(application):
        # v76: علامة إغلاق نظيف — غيابها في الإقلاع القادم يعني موتاً قسرياً
        try:
            await asyncio.to_thread(_write_bot_setting, "clean_shutdown", "1")
        except Exception:
            pass
    app = Application.builder().token(BOT_TOKEN).post_init(_on_app_post_init).post_shutdown(_on_app_post_shutdown).build()

    # ── تنظيف الشاشة: تتبع رسائل البوت الصادرة + رسائل المستخدم (تُحذف عند العودة للقائمة) ──
    _install_sweep_tracking(app.bot)
    app.add_handler(MessageHandler(filters.ALL, _sweep_user_tracker, block=True), group=-2)

    # ── Middleware: تحديث بيانات المستخدم تلقائياً عند أي تفاعل ──
    async def auto_sync_user(update, context):
        user = update.effective_user
        if user:
            try:
                await asyncio.to_thread(sync_user_info, user)
            except Exception as e:
                logging.warning("[SYNC] Failed to sync user " + str(user.id) + ": " + str(e))

    # يشغل sync_user_info قبل أي handler آخر (non-blocking)
    app.add_handler(MessageHandler(filters.ALL, auto_sync_user, block=False), group=-1)
    app.add_handler(CallbackQueryHandler(auto_sync_user, block=False), group=-1)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", route_message))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CallbackQueryHandler(callback_addtype_handler, pattern="^addtype_"))
    app.add_handler(CallbackQueryHandler(callback_add_handler, pattern="^add_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(callback_approval_handler, pattern="^(approve|reject)_"))
    # 🎯 نظام الفئات: اختيار فئة المستخدم الجديد + التفعيل بالفئة + إدارة الفئات من اللوحة
    app.add_handler(CallbackQueryHandler(callback_pickrole_handler, pattern="^pickrole_"))
    app.add_handler(CallbackQueryHandler(callback_approval_handler, pattern="^approveas_"))
    app.add_handler(CallbackQueryHandler(callback_role_menu_handler, pattern="^askrole_"))
    app.add_handler(CallbackQueryHandler(callback_role_set_handler, pattern="^setrole_"))
    # v34: أزرار الموافقة/الحذف على طلبات انضمام التطبيق (للمدير)
    app.add_handler(CallbackQueryHandler(callback_pwreg_handler, pattern="^pwreg_(ok|del)_"))
    app.add_handler(CallbackQueryHandler(callback_pwreset_handler, pattern="^pwreset_gen_"))   # v39
    # 🔐 نظام الصلاحيات الديناميكي: لوحة إدارة صلاحيات الفئات من داخل البوت
    app.add_handler(CallbackQueryHandler(callback_perms_handler, pattern="^(permsrole_|permstgl_|permsdone)"))
    # 🚫 بوابة الطرد: أول معالج على كل تحديث — المطرود لا يصلحه شيء (v23)
    app.add_handler(TypeHandler(Update, kicked_gate), group=-2)
    # خطوة التأكيد قبل تغيير أي دور + أدوات لوحة المستخدمين
    app.add_handler(CallbackQueryHandler(callback_users_ask_handler, pattern="^(askpromote|askdemote|askkick)_"))
    app.add_handler(CallbackQueryHandler(callback_users_cancel_handler, pattern="^users_cancel$"))
    app.add_handler(CallbackQueryHandler(callback_users_refresh_handler, pattern="^users_refresh$"))
    app.add_handler(CallbackQueryHandler(callback_users_close_handler, pattern="^users_close$"))
    app.add_handler(CallbackQueryHandler(callback_promote_handler, pattern="^promote_"))
    app.add_handler(CallbackQueryHandler(callback_promote_handler, pattern="^demote_"))
    # 🚫 تنفيذ الطرد/إعادة التنشيط (v23)
    app.add_handler(CallbackQueryHandler(callback_kick_handler, pattern="^kick_"))
    app.add_handler(CallbackQueryHandler(callback_kick_handler, pattern="^unban_"))
    # v53: دمج الحسابات المكررة — قائمة المرشحين ثم تأكيد ثم تنفيذ
    app.add_handler(CallbackQueryHandler(callback_merge_list_handler, pattern="^askmerge_"))
    app.add_handler(CallbackQueryHandler(callback_merge_confirm_handler, pattern="^mergesel_"))
    app.add_handler(CallbackQueryHandler(callback_merge_go_handler, pattern="^mergego_"))
    # Restore handlers - specific patterns first, then generic
    app.add_handler(CallbackQueryHandler(callback_restore_from_channel_handler, pattern="^restore_from_channel$"))
    app.add_handler(CallbackQueryHandler(callback_add_notify_handler, pattern="^addnotify_"))
    app.add_handler(CallbackQueryHandler(callback_alert_pick_handler, pattern=r"^alertmode_|^alertu_|^alertpg_"))  # v83: التنبيه عام+خاص
    app.add_handler(CallbackQueryHandler(callback_rem_notify_handler, pattern="^remnotify_"))
    app.add_handler(CallbackQueryHandler(callback_restore_last_handler, pattern="^restore_last$"))
    app.add_handler(CallbackQueryHandler(callback_restore_last_confirm_handler, pattern="^last_restore_"))
    app.add_handler(CallbackQueryHandler(callback_restore_handler, pattern="^restore_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(callback_admin_done_handler, pattern="^admin_done$"))
    app.add_handler(CallbackQueryHandler(callback_provide_handler, pattern="^provide_"))
    app.add_handler(CallbackQueryHandler(callback_product_select_handler, pattern="^psel_"))
    app.add_handler(CallbackQueryHandler(callback_product_action_handler, pattern="^pact_"))
    app.add_handler(CallbackQueryHandler(callback_extract_approve_handler, pattern="^ext_approve_"))
    app.add_handler(CallbackQueryHandler(callback_extract_approve_handler, pattern="^ext_reject_"))
    app.add_handler(CallbackQueryHandler(callback_incoming_order_handler, pattern="^io_ready_"))
    app.add_handler(CallbackQueryHandler(callback_incoming_order_handler, pattern="^io_notready_"))
    app.add_handler(CallbackQueryHandler(callback_go_start_handler, pattern="^go_start$"))
    app.add_handler(CallbackQueryHandler(callback_mov_product_handler, pattern=r"^movp_\d+$"))
    app.add_handler(CallbackQueryHandler(callback_mov_date_handler, pattern="^movd_"))
    app.add_handler(CallbackQueryHandler(callback_mov_delete_handler, pattern="^movp_del_"))
    # الفواتير: الأنماط الأخص أولاً حتى لا يبتلعها النمط العام ^invoice_
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_back_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_remove_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invoice_(?!back_|remove_)"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_worker_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^invsel_"))
    app.add_handler(CallbackQueryHandler(callback_invoice_handler, pattern="^inv_plen_"))
    # 🔧 صيانة الرافعة: إدارة الزيت (مدراء فقط) + زر تأكيد العامل
    app.add_handler(CallbackQueryHandler(callback_oil_handler, pattern="^oil_"))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("backup_test", backup_test_command))
    app.add_handler(CommandHandler("testmail", testmail_command))  # v36: اختبار البريد
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("webpass", webpass_command))
    app.add_handler(CommandHandler("audit", audit_command))
    app.add_handler(CommandHandler("errors", errors_command))  # v76: سجل الأخطاء الحي — المالك فقط
    app.add_handler(MessageHandler(filters.Document.ALL, handle_backup_document))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.UpdateType.EDITED, handle_receipt_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)
    return app


# ═══════════════════════════════════════════════════════════
#  DAILY AUTO-BACKUP
# ═══════════════════════════════════════════════════════════


def _send_backup_email(backup_bytes, filename):
    """Send backup JSON to configured email. Returns True on success, False otherwise."""
    if not SMTP_EMAIL or not SMTP_PASSWORD or not BACKUP_EMAIL:
        return False
    if not SMTP_HOST:
        logging.error("[BACKUP] SMTP_HOST not configured")
        return False
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = BACKUP_EMAIL
        msg["Subject"] = "\U0001f4be \u0646\u0633\u062e\u0629 \u0627\u062d\u062a\u064a\u0627\u0637\u064a\u0629 - \u0627\u0644\u0645\u062e\u0632\u0646 " + datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
        body = ("\u0645\u0631\u062d\u0628\u0627\n"
                + "\n"
                + "\u0647\u0630\u0627 \u0645\u0644\u0641 \u0627\u0644\u0646\u0633\u062e \u0627\u0644\u0627\u062d\u062a\u064a\u0627\u0637\u064a \u0627\u0644\u064a\u0648\u0645\u064a \u0644\u0644\u0645\u062e\u0632\u0646.\n"
                + "\u0627\u062d\u0641\u0638\u0647 \u0641\u064a \u0645\u0643\u0627\u0646 \u0622\u0645\u0646.\n"
                + "\n"
                + "\u0644\u0625\u0633\u062a\u064a\u0631\u0627\u062f \u0627\u0644\u0628\u064a\u0627\u0646\u0627\u062a: \u0623\u0631\u0633\u0644 \u0647\u0630\u0627 \u0627\u0644\u0645\u0644\u0641 \u0644\u0644\u0628\u0648\u062a \u0641\u064a \u062a\u064a\u0644\u064a\u063a\u0631\u0627\u0645.")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        part = MIMEBase("application", "json")
        part.set_payload(backup_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
        tls_ctx = ssl.create_default_context()  # verifies cert + hostname
        if SMTP_PORT == 465:
            server_ctx = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=tls_ctx)
        else:
            server_ctx = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        with server_ctx as server:
            if SMTP_PORT != 465:
                server.starttls(context=tls_ctx)
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, BACKUP_EMAIL, msg.as_string())
        logging.info("[BACKUP] Email sent to " + BACKUP_EMAIL)
        return True
    except Exception as e:
        logging.error("[BACKUP] Email failed: " + str(e))
        return False


def _send_backup_email_with_retry(backup_bytes, filename, max_attempts=3, delay_sec=300):
    """Retry email send up to max_attempts times with delay_sec between attempts.
    Synchronous — must be called from a thread (use asyncio.to_thread).
    """
    for attempt in range(1, max_attempts + 1):
        if _send_backup_email(backup_bytes, filename):
            return True
        if attempt < max_attempts:
            logging.warning("[BACKUP] Email attempt " + str(attempt) + "/" + str(max_attempts) + " failed, retrying in " + str(delay_sec) + "s...")
            time.sleep(delay_sec)
        else:
            logging.error("[BACKUP] All " + str(max_attempts) + " email attempts failed")
    return False


def _save_backup_to_disk(backup_bytes):
    """Save backup to local disk as a fallback. Returns True on success."""
    try:
        with open(_BACKUP_DISK_PATH, "wb") as f:
            f.write(backup_bytes)
        logging.info("[BACKUP] Saved to disk: " + _BACKUP_DISK_PATH)
        return True
    except Exception as e:
        logging.warning("[BACKUP] Disk save failed: " + str(e))
        return False


def _load_backup_from_disk():
    """Load last backup from disk into memory. Used at startup."""
    try:
        if os.path.exists(_BACKUP_DISK_PATH):
            with open(_BACKUP_DISK_PATH, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
            logging.info("[BACKUP] Loaded backup from disk (" + str(data.get("date", "?")) + ")")
            return data
    except Exception as e:
        logging.warning("[BACKUP] Disk load failed: " + str(e))
    return None


def _rss_mb():
    """v75: ذاكرة العملية الحالية بالميغابايت (Linux /proc مع بديل resource)."""
    try:
        with open("/proc/self/status", "r") as _fh:
            for _line in _fh:
                if _line.startswith("VmRSS:"):
                    return round(int(_line.split()[1]) / 1024, 1)
    except Exception:
        pass
    try:
        import resource as _res
        return round(_res.getrusage(_res.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        return -1


async def _send_backup_to_channel(bot, file_bytes, filename, label="auto"):
    """Send backup JSON file to the configured Telegram channel.
    Returns True on success, False otherwise.
    """
    if not BACKUP_CHANNEL_ID:
        return False
    # v75: حارس الحجم — حد تليغرام 50MB؛ نتجنب المحاولة المستحيلة ونسجل السبب بوضوح
    if len(file_bytes) > 48 * 1024 * 1024:
        logging.error("[BACKUP] Channel send SKIPPED: %s bytes > Telegram 50MB limit", format(len(file_bytes), ","))
        return False
    try:
        from io import BytesIO
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d %H:%M")
        caption = (
            "\U0001f4be نسخة احتياطية (" + label + ")\n"
            "\U0001f4c5 " + now_str + "\n"
            "\U0001f4e6 " + str(len(file_bytes)) + " bytes"
        )
        await bot.send_document(
            chat_id=BACKUP_CHANNEL_ID,
            document=BytesIO(file_bytes),
            filename=filename,
            caption=caption,
        )
        logging.info("[BACKUP] Sent to channel " + BACKUP_CHANNEL_ID)
        return True
    except Exception as e:
        logging.error("[BACKUP] Channel send failed: " + str(e))
        return False


async def _daily_backup(bot, label="auto"):
    """Take a full backup. Save to memory, disk, and email (with retry).
    `label` is used in logs ("auto" for scheduled, "test" for /backup_test).
    Returns dict {"memory": bool, "disk": bool, "email": bool, "bytes": int}."""
    global _last_backup_data
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d")
    result = {"memory": False, "disk": False, "email": False, "channel": False, "bytes": 0, "error": None}
    try:
        logging.info("[BACKUP] %s export start (RSS %s MB)", label, _rss_mb())
        backup = await asyncio.to_thread(db_export_backup)  # v72: خارج حلقة الأحداث
        _last_backup_data = backup
        result["memory"] = True
        # v75: بناء JSON في خيط منفصل وبلا indent — لا تجميد وملف أصغر (الاستيراد لا يهتم بالتنسيق)
        json_str = await asyncio.to_thread(
            lambda: json.dumps(backup, ensure_ascii=False, default=str)
        )
        file_bytes = json_str.encode("utf-8")
        del json_str
        result["bytes"] = len(file_bytes)
        logging.info("[BACKUP] %s export done: %s bytes (RSS %s MB)", label, format(result["bytes"], ","), _rss_mb())
        now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y-%m-%d_%H-%M")
        filename = "backup_" + now_str + ".json"
        # 1) Save to disk as fallback (always, even if email succeeds)
        result["disk"] = _save_backup_to_disk(file_bytes)
        # 2) Send to Telegram channel (works on Render free tier)
        result["channel"] = await _send_backup_to_channel(bot, file_bytes, filename, label)
        # 3) Send email with retry (3 attempts, 5 min apart) — runs in thread to avoid blocking loop
        if SMTP_EMAIL and SMTP_PASSWORD and BACKUP_EMAIL and SMTP_HOST:
            result["email"] = await asyncio.to_thread(
                _send_backup_email_with_retry, file_bytes, filename
            )
        else:
            logging.warning("[BACKUP] Email not configured (SMTP_HOST/SMTP_EMAIL/SMTP_PASSWORD/BACKUP_EMAIL missing) — backup saved to memory + disk + channel only")
        logging.info("[BACKUP] " + label + " backup done: memory=1 disk=" + str(int(result["disk"])) + " channel=" + str(int(result["channel"])) + " email=" + str(int(result["email"])) + " (" + str(result["bytes"]) + " bytes, " + today + ")")
    except Exception as e:
        result["error"] = str(e)
        logging.error("[BACKUP] " + label + " backup failed: " + str(e))
    return result


# ═══════════════════════════════════════════════════════════
#  DAILY AUTO-BACKUP SCHEDULE (نسخة واحدة يومياً)
# ═══════════════════════════════════════════════════════════

def _try_claim_auto_backup(slot_key):
    """احتكار فتحة النسخة التلقائية ذرياً عبر قاعدة البيانات.

    تكتب العلامة في bot_settings بـ INSERT ... ON CONFLICT DO UPDATE ...
    WHERE value <> جديد، وترجع True فقط للعملية التي كسبت المطالبة.
    هكذا لا تُرسل النسخة مرتين حتى لو أُعيد تشغيل البوت داخل الساعة نفسها
    أو تشغّل أكثر من نسخة على Render — لأن آخر موعد ناجح محفوظ في DB
    وليس في الذاكرة.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value "
                "WHERE bot_settings.value <> EXCLUDED.value "
                "RETURNING key",
                ("auto_backup_slot", slot_key),
            )
            won = cur.fetchone() is not None
        conn.commit()
        return won
    except Exception as e:
        conn.rollback()
        logging.error("[BACKUP] Claim slot failed: " + str(e), exc_info=True)
        return False  # آمن: لا ترسل إلا والمطالبة حاسمة


async def _daily_backup_loop(bot):
    """نسخة احتياطية تلقائية واحدة كل يوم الساعة 17:00 (توقيت الجزائر).

    - الموعد قابل للتغيير من متغير البيئة AUTO_BACKUP_HOUR (0-23).
    - النافذة كامل الساعة لمقاومة الانقطاعات القصيرة.
    - منع التكرار نهائي عبر _try_claim_auto_backup: العلامة محفوظة في
      قاعدة البيانات ("2026-08-27_17") فلا يعيد إعادة التشغيل إرسالها.
    """
    try:
        backup_hour = int(os.getenv("AUTO_BACKUP_HOUR", "").strip() or 17)
    except ValueError:
        backup_hour = 17
    if not (0 <= backup_hour <= 23):
        backup_hour = 17
    logging.info("[BACKUP] Daily auto-backup scheduled at %02d:00 Algeria time", backup_hour)
    while True:
        try:
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=1)))
            if now.hour == backup_hour:
                slot_key = now.strftime("%Y-%m-%d") + "_%02d" % backup_hour
                if _try_claim_auto_backup(slot_key):
                    logging.info("[BACKUP] Starting scheduled daily backup (%02d:00)...", backup_hour)
                    await _daily_backup(bot, label="daily")
        except Exception as e:
            logging.error("[BACKUP] Loop error: " + str(e))
        await asyncio.sleep(60)


async def _keep_alive(app):
    """v87: نبضة HTTP لخدمة Render فقط — صفر استعلامات للقاعدة (Scale-to-Zero يعود).
    التنبيهات مبنية على الأخطاء الحقيقية: get_connection يرفع الأعلام عند فشل
    اتصال فعلي، وهنا تُرسل إشعارات تلغرام للمالك (هبوط/عودة) مرة لكل حادثة."""
    global _db_down_alert_pending, _db_was_down, _db_up_alert_pending
    external_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else ""
    if not external_url:
        port = int(os.getenv("PORT", 10000))
        external_url = "http://127.0.0.1:" + str(port) + "/"
    ping_url = external_url.rstrip("/") + "/"
    logging.info("[KEEP-ALIVE] Target: " + ping_url + " (HTTP فقط — بلا نبضة DB منذ v87)")
    # Reuse one session instead of creating new each time
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    try:
        while True:
            try:
                # Ping Render health endpoint
                async with session.get(ping_url) as r:
                    if r.status != 200:
                        logging.warning("[KEEP-ALIVE] Render ping status " + str(r.status))
                # v87: صفر استعلامات دورية — التنبيه من أخطاء الاستخدام الحقيقية فقط
                if _db_down_alert_pending:
                    _db_down_alert_pending = False
                    _db_was_down = True
                    for _oid in (list(OWNER_IDS)[:3] or list(ADMIN_IDS)[:3]):
                        try:
                            await app.bot.send_message(
                                chat_id=_oid,
                                text=("🚨 قاعدة البيانات غير متاحة الآن\n"
                                      "فشلت كل محاولات الاتصال أثناء استخدام حقيقي.\n"
                                      "🛡 العمليات تُحفظ في الطابور وتُنفَّذ تلقائياً عند عودة القاعدة.\n"
                                      "🔎 الأسباب المحتملة: نفاد حصة Neon الشهرية أو انقطاع الخدمة."))
                        except Exception:
                            pass
                elif _db_up_alert_pending:
                    _db_up_alert_pending = False
                    for _oid in (list(OWNER_IDS)[:3] or list(ADMIN_IDS)[:3]):
                        try:
                            await app.bot.send_message(
                                chat_id=_oid,
                                text=("✅ قاعدة البيانات عادت للعمل\n"
                                      "الطابور الأوفلاين سيعيد تنفيذ العمليات المؤجلة تلقائياً."))
                        except Exception:
                            pass
            except Exception as e:
                logging.warning("[KEEP-ALIVE] Ping failed: " + str(e))
            await asyncio.sleep(180)
    finally:
        await session.close()


async def _health_handler(request):
    """Shared aiohttp health endpoint (webhook mode + Render polling mode)."""
    qcount = get_offline_queue_count()
    status = "degraded" if qcount > 0 else "ok"
    return web.Response(text='{"status":"' + status + '","pending_ops":' + str(qcount) + '}',
                        content_type="application/json")


# ═══════════════════════════════════════════════════════════
#  📱 PWA WEB — v24: تطبيق ويب مثبّت من المتصفح داخل bot.py
#  نفس أسلوب «main.py + pwa.html»: البوت يخدم صفحة pwa.html
#  من مجلده + مسارات API محمية بالجلسات، على نفس منفذ Render
#  ($PORT) في وضعي التشغيل (webhook / polling+health).
#  المصادقة: جدولا pwa_auth + pwa_sessions (scrypt + كوكيز
#  httpOnly) — بلا أي تأثير على جداول البوت الموجودة.
#  الصلاحيات: نفس نظام role_permissions المشترك مع البوت،
#  والطرد (active=False) يمنع الدخول من الويب أيضاً.
# ═══════════════════════════════════════════════════════════
import functools as _pwa_functools
import secrets as _pwa_secrets
import hmac as _pwa_hmac
import decimal as _pwa_decimal

PWA_COOKIE = "pwa_sess"
PWA_SESSION_DAYS = 30
PWA_VERSION = "v88.1"  # v88.1 = سجل الفواتير الكامل + تحميل PDF محصّن (رسائل خطأ ظاهرة) + عرض سطح المكتب
_PWA_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # v41: فلاتر التواريخ

# أسباب عملية جاهزة بنقرة واحدة — موحّدة بين البوت والتطبيق (تُرسل عبر /api/status)
PWA_REASONS = {
    "add":    ["🚚 وصول شاحنة", "↩️ إرجاع من زبون", "📝 تصحيح مخزون", "📦 جرد أولي"],
    "remove": ["💰 بيع لرابح", "💰 بيع لحسان", "👤 بيع لزبون", "📝 تصحيح مخزون"],
    "reconcile": [],
}
# مرجع تطبيق تلغرام الحي — لإشعارات الويب (تغطية المعلق، طلبات التوريد)
TG_APP = None
PWA_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pwa.html")

PWA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pwa_auth (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    username   VARCHAR(100) UNIQUE NOT NULL,
    pass_hash  TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pwa_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pwa_sessions_user ON pwa_sessions(user_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id        SERIAL PRIMARY KEY,
    ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id   INTEGER,
    username  VARCHAR(200),
    action    VARCHAR(60) NOT NULL,
    details   VARCHAR(500),
    ip        VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);
-- ═══ v32: الهوية (إيميل/هاتف) + التسجيل الذاتي + الرمز السري ═══
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255);
    ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32);
    ALTER TABLE users ADD COLUMN IF NOT EXISTS identity_verified BOOLEAN NOT NULL DEFAULT FALSE;
    -- حسابات التسجيل من التطبيق تولد بلا تلغرام قبل تأكيد الرمز عبر البوت
    ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uniq ON users (email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS users_phone_uniq ON users (phone) WHERE phone IS NOT NULL;
CREATE TABLE IF NOT EXISTS pwa_registrations (
    id           SERIAL PRIMARY KEY,
    code         CHAR(6) UNIQUE NOT NULL,
    full_name    VARCHAR(255) NOT NULL,
    email        VARCHAR(255),
    phone        VARCHAR(32),
    role         VARCHAR(20) NOT NULL CHECK (role IN ('stock', 'seller')),
    status       VARCHAR(12) NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'confirmed', 'cancelled')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ip           VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_pwa_reg_status ON pwa_registrations (status, created_at DESC);
-- ═══ v34: رقم سري يختاره العميل عند التسجيل — يُحفظ في القاعدة عند موافقة المدير ═══
ALTER TABLE pwa_registrations ADD COLUMN IF NOT EXISTS password_hash TEXT;
-- ═══ v39: اسم دخول من اختيار العميل — بلا بريد نهائياً (اسم + رقم سر + موافقة الإدارة) ═══
ALTER TABLE pwa_registrations ADD COLUMN IF NOT EXISTS username VARCHAR(100);
CREATE UNIQUE INDEX IF NOT EXISTS pwa_reg_username_uniq
    ON pwa_registrations (LOWER(username)) WHERE status = 'waiting' AND username IS NOT NULL;
-- ═══ v39: طلبات استعادة الرقم السري — الموظف يطلب والإدارة تولّد رقماً مؤقتاً بضغطة ═══
CREATE TABLE IF NOT EXISTS pwa_pass_resets (
    id         SERIAL PRIMARY KEY,
    username   VARCHAR(100) NOT NULL,
    status     VARCHAR(10) NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'done', 'cancelled')),
    temp_pass  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    done_at    TIMESTAMPTZ,
    ip         VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_pwa_resets_status ON pwa_pass_resets (status, created_at DESC);
CREATE TABLE IF NOT EXISTS pwa_otp (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    ip         VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pwa_otp_user ON pwa_otp (user_id, created_at DESC);
-- ═══ v41: الملف الشخصي (اسم معروض + صورة) + حذف ناعم للسلع (السجل محفوظ) ═══
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);
    ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar TEXT;
EXCEPTION WHEN OTHERS THEN NULL; END $$;
ALTER TABLE products ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE;
-- ═══ v53: وسم الحسابات المدموجة merged_into (الفيديو أسفل بعد إنشاء جدول الرسائل) ═══
DO $$ BEGIN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS merged_into INTEGER;
EXCEPTION WHEN OTHERS THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_transactions_product_date
    ON transactions (product_id, date DESC);
-- ═══ v50: الرسائل — محادثات فردية بين فريق العمل (نص/صورة/ملف/صوت) ═══
CREATE TABLE IF NOT EXISTS chat_messages (
    id         SERIAL PRIMARY KEY,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recip_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       VARCHAR(10) NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'image', 'file', 'audio', 'video')),
    body       TEXT DEFAULT '',
    media      BYTEA,
    media_name VARCHAR(255),
    media_mime VARCHAR(120),
    media_size INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_chat_pair ON chat_messages (sender_id, recip_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_chat_unread ON chat_messages (recip_id) WHERE read_at IS NULL;
-- ═══ v48: رمز «تذكّرني» — دخول بضغطة واحدة من نفس الجهاز ═══
CREATE TABLE IF NOT EXISTS pwa_remember (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pwa_remember_user ON pwa_remember (user_id);
-- ═══ v53: فيديو الرسائل — توسيع قيمة kind لقواعد البيانات القديمة ═══
DO $$ BEGIN
    ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_kind_check;
    ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_kind_check
        CHECK (kind IN ('text', 'image', 'file', 'audio', 'video'));
EXCEPTION WHEN OTHERS THEN NULL; END $$;
-- ═══ v61: الرد على رسالة محددة — الاقتباس داخل الفقاعة مثل واتساب ═══
DO $$ BEGIN
    ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS reply_to INTEGER
        REFERENCES chat_messages(id) ON DELETE SET NULL;
EXCEPTION WHEN OTHERS THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_chat_reply ON chat_messages (reply_to);
-- ═══ v83: مجموعات الدردشة — مثل تطبيقات المراسلة (قائمة + أعضاء + رسائل) ═══
CREATE TABLE IF NOT EXISTS chat_groups (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(80) NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS chat_group_members (
    group_id     INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_admin     BOOLEAN NOT NULL DEFAULT FALSE,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS chat_group_messages (
    id         SERIAL PRIMARY KEY,
    group_id   INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       VARCHAR(10) NOT NULL DEFAULT 'text',
    body       TEXT DEFAULT '',
    media      BYTEA,
    media_name VARCHAR(255),
    media_mime VARCHAR(120),
    media_size INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reply_to   INTEGER REFERENCES chat_group_messages(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_grp_msg ON chat_group_messages (group_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_chat_grp_member ON chat_group_members (user_id);
DO $$ BEGIN
    ALTER TABLE chat_group_messages DROP CONSTRAINT IF EXISTS chat_group_messages_kind_check;
    ALTER TABLE chat_group_messages ADD CONSTRAINT chat_group_messages_kind_check
        CHECK (kind IN ('text', 'image', 'file', 'audio', 'video'));
EXCEPTION WHEN OTHERS THEN NULL; END $$;
"""

_pwa_html_cache = {"mtime": None, "body": None}
_pwa_icon_cache = None


def _pwa_json_default(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.strftime("%Y-%m-%d %H:%M")
    if isinstance(o, _pwa_decimal.Decimal):
        return float(o)
    return str(o)


def _pwa_jr(data, status=200):
    return web.json_response(
        data, status=status,
        dumps=lambda o: json.dumps(o, ensure_ascii=False, default=_pwa_json_default))


def _pwa_err(status, msg, code=None):
    d = {"ok": False, "error": msg}
    if code:
        d["code"] = str(code)
    return _pwa_jr(d, status=status)


# ═══ v79: قتل حلقة إشعار «يوجد تحديث جديد» — الإصلاح كله من bot.py بلا تغيير pwa.html ═══
# الجذر: pwa.html المقدَّم ما زال APP_VER="v74" بينما PWA_VERSION ترقّى مع كل إصدار يُرفع
# bot.py وحده → شرط «يوجد تحديث» يتحقق في كل فحص، وزر «لاحقاً» يُحفظ بالذاكرة فقط فيموت
# مع كل إعادة تحميل، وزر «نعم» يعيد تحميل نفس الصفحة → حلقة لا تنتهي + مسح كاش وإعادة
# تحميل كاملة عند كل ضغطة = ثقل ملحوظ في التطبيق.
# ═══ v83: مجموعات الدردشة — كود الواجهة يُحقن في pwa.html وقت التقديم ═══
# (المنهجية نفسها: pwa.html على القرص لا يتغير — الرقعة تعمل مع أي نسخة قديمة)
_PWA_GROUPS_JS = r"""/* ═══ v83: مجموعات الدردشة — يُحقن من الخادم (bot.py) ═══ */
(function () {
  if (window.__v83groups) return;
  window.__v83groups = true;
  var G = { g: null, timer: null, lastId: 0, busy: false, msgs: [],
            meAdmin: false, isOwner: false };

  function st(s) { try { if (window.toast) toast(s); } catch (e) {} }
  function sterr(s) { try { if (window.toast) toast(s, true); } catch (e) {} }
  function stop() { if (G.timer) { clearTimeout(G.timer); G.timer = null; } }
  function inHome() {
    var t = document.getElementById("chatThread");
    return t && !t.classList.contains("on");
  }
  function q(id) { return document.getElementById(id); }

  /* اسم المرسل فوق فقاعة الوارد — خاصية المجموعات */
  function gAppend(m) {
    var el = (typeof appendMsg === "function") ? appendMsg(m) : null;
    if (el && !m.mine && m.sender_name) {
      var lb = document.createElement("div");
      lb.style.cssText = "font-size:11px;font-weight:700;opacity:.65;margin:0 0 2px";
      lb.textContent = m.sender_name;
      el.insertBefore(lb, el.firstChild);
    }
    return el;
  }

  function gBtns(on) {
    var d = q("chatDel"), s = q("chatSrchB");
    if (d) d.style.display = on ? "none" : "";
    if (s) s.style.display = on ? "none" : "";
  }

  async function gOpen(id, name) {
    if (!S.user) return;
    stop(); chatStopTimers();
    if (typeof chatMsgSheetClose === "function") { try { chatMsgSheetClose(); } catch (e) {} }
    G.g = { id: id, name: name || ("مجموعة #" + id) };
    G.lastId = 0; G.busy = false; G.msgs = [];
    G.meAdmin = false; G.isOwner = false;
    S.chat.peer = { id: -1, name: "👥 " + G.g.name, role: "" };
    S.chat.lastId = 0; S.chat.readUpto = 0; S.chat.busy = false; S.chat.sending = false;
    S.chat.firstId = 0; S.chat.hasMore = false; S.chat.loadingOlder = false;
    S.chat.reply = null; S.chat.byId = {}; S.chat.lastDateKey = "";
    S.chat.typing = false; S.chat.searchMode = false; S.chat.searchQ = "";
    S.chat.hiddenIds = (typeof chatHiddenGet === "function") ? chatHiddenGet() : [];
    var rb = q("chatReply"); if (rb) rb.style.display = "none";
    var sb = q("chatSBar"); if (sb) sb.classList.remove("on");
    var sq = q("chatSrchQ"); if (sq) sq.value = "";
    var sn = q("chatSrchN"); if (sn) sn.textContent = "";
    Object.keys(S.chat.urls).forEach(function (k) {
      try { URL.revokeObjectURL(S.chat.urls[k]); } catch (e) {}
    });
    S.chat.urls = {};
    q("chatHome").style.display = "none";
    q("chatThread").classList.add("on");
    q("chatPeerName").textContent = "👥 " + G.g.name;
    q("chatPeerAv").textContent = "👥";
    q("chatPeerRole").innerHTML = "مجموعة";
    q("chatTxt").value = "";
    var box = q("chatMsgs"); if (box) box.innerHTML = "";
    var ob = q("chatOlder"); if (ob) ob.classList.remove("show");
    gBtns(true); gInfoBtn(true);
    try {
      var d = await api("/api/chat/gmsg?g=" + id + "&after=0",
                        { noCache: true, timeout: 30000 });
      if (!G.g || G.g.id !== id) return;
      (d.messages || []).forEach(function (m) {
        if (m.id > G.lastId) { G.lastId = m.id; G.msgs.push(m); gAppend(m); }
      });
      if (d.members_count != null)
        q("chatPeerRole").innerHTML = "مجموعة · " + d.members_count + " عضو";
      scrollChat(true);
      gPoll();
    } catch (e) { sterr(e.message || "تعذر فتح المجموعة"); }
  }

  function gPoll() {
    if (G.timer) clearTimeout(G.timer);
    G.timer = setTimeout(async function () {
      G.timer = null;
      if (!G.g || !S.user) return;
      var vc = q("v-chat");
      if (!vc || !vc.classList.contains("on")) return;
      if (G.busy || document.hidden) { gPoll(); return; }
      G.busy = true;
      try {
        var d = await api("/api/chat/gmsg?g=" + G.g.id + "&after=" + G.lastId,
                          { noCache: true, timeout: 20000 });
        if (G.g && d.messages && d.messages.length) {
          var inc = false;
          d.messages.forEach(function (m) {
            if (m.id > G.lastId) {
              G.lastId = m.id; G.msgs.push(m); gAppend(m);
              if (!m.mine) inc = true;
            }
          });
          if (inc && typeof chatNotifyRecv === "function") chatNotifyRecv();
          scrollChat(false);
        }
        if (G.g && typeof d.read_upto === "number" && d.read_upto > S.chat.readUpto) {
          S.chat.readUpto = d.read_upto;
          if (typeof updateTicks === "function") updateTicks();
        }
      } catch (e) { /* انقطاع مؤقت — الدورة القادمة تعيد المحاولة */ }
      finally { G.busy = false; if (G.g && S.user) gPoll(); }
    }, (typeof _CHAT_POLL_MSG === "number") ? _CHAT_POLL_MSG : 3500);
  }

  /* زر معلومات المجموعة في رأس المحادثة */
  function gInfoBtn(on) {
    var old = q("g83info"); if (old) old.remove();
    if (!on) return;
    var hdr = q("chatThread").querySelector(".thead");
    if (!hdr) return;
    var b = document.createElement("button");
    b.id = "g83info"; b.type = "button"; b.className = "cbtn";
    b.title = "أعضاء المجموعة"; b.textContent = "👥";
    b.addEventListener("click", gMembersDlg);
    var ref = q("chatSrchB") || null;
    if (ref) hdr.insertBefore(b, ref); else hdr.appendChild(b);
  }

  function overlay() {
    var o = document.createElement("div");
    o.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);"
      + "z-index:9999;display:flex;align-items:center;justify-content:center";
    o.addEventListener("click", function (e) { if (e.target === o) o.remove(); });
    document.body.appendChild(o);
    return o;
  }
  function cardBase(title) {
    var o = overlay();
    var c = document.createElement("div");
    c.style.cssText = "background:var(--bg,#fff);color:var(--tx,#111);"
      + "width:min(92vw,380px);max-height:80vh;overflow:auto;border-radius:16px;padding:14px";
    o.appendChild(c);
    return { o: o, c: c, x: title };
  }

  async function gMembersDlg() {
    if (!G.g) return;
    var d;
    try { d = await api("/api/chat/gmembers?g=" + G.g.id, { noCache: true }); }
    catch (e) { sterr(e.message || "تعذر التحميل"); return; }
    G.meAdmin = !!d.me_is_admin; G.isOwner = !!d.me_is_owner;
    var ui = cardBase();
    var ms = d.members || [];
    var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
      + '<b>👥 ' + esc(G.g.name) + '</b>'
      + '<button class="g83x" type="button" style="border:none;background:transparent;font-size:18px">✕</button></div>'
      + '<div style="font-size:12px;opacity:.6;margin-bottom:8px">' + ms.length + ' عضو</div>';
    ms.forEach(function (m) {
      html += '<div style="display:flex;align-items:center;gap:8px;padding:6px 0">'
        + '<div class="cav" style="width:34px;height:34px">' + esc(chatAva(m.name)) + '</div>'
        + '<div style="flex:1;min-width:0"><b style="font-size:14px">' + esc(m.name) + '</b>'
        + '<div style="font-size:11px;opacity:.6">' + esc(m.role_label || "")
        + (m.is_admin ? " · مدير المجموعة" : "") + '</div></div></div>';
    });
    html += '<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">';
    if (G.meAdmin || G.isOwner)
      html += '<button id="g83addm" type="button" style="flex:1;border:none;border-radius:10px;padding:9px;background:#2563eb;color:#fff;font-weight:700">＋ إضافة أعضاء</button>';
    if (G.isOwner)
      html += '<button id="g83delg" type="button" style="flex:1;border:none;border-radius:10px;padding:9px;background:#dc2626;color:#fff;font-weight:700">🗑 حذف المجموعة</button>';
    html += '<button id="g83leave" type="button" style="flex:1;border:none;border-radius:10px;padding:9px;background:#e5e7eb;color:#111;font-weight:700">🚪 مغادرة</button></div>';
    ui.c.innerHTML = html;
    ui.c.querySelector(".g83x").addEventListener("click", function () { ui.o.remove(); });
    var bm = ui.c.querySelector("#g83addm");
    if (bm) bm.addEventListener("click", function () { ui.o.remove(); gAddDlg(); });
    var bl = ui.c.querySelector("#g83leave");
    if (bl) bl.addEventListener("click", async function () {
      if (!confirm("مغادرة المجموعة «" + G.g.name + "»؟")) return;
      try {
        await api("/api/chat/gleave", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ g: G.g.id }) });
        ui.o.remove(); chatReset(false); chatEnter(); st("تمت مغادرة المجموعة");
      } catch (e2) { sterr(e2.message || "فشل"); }
    });
    var bd = ui.c.querySelector("#g83delg");
    if (bd) bd.addEventListener("click", async function () {
      if (!confirm("حذف المجموعة نهائياً مع كل رسائلها؟")) return;
      try {
        await api("/api/chat/gdelete", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ g: G.g.id }) });
        ui.o.remove(); chatReset(false); chatEnter(); st("حُذفت المجموعة");
      } catch (e2) { sterr(e2.message || "فشل"); }
    });
  }

  function pickList(cs, have) {
    var list = "";
    cs.forEach(function (c) {
      if (have && have.has(c.id)) return;
      list += '<label style="display:flex;align-items:center;gap:8px;padding:7px 0;'
        + 'border-bottom:1px solid rgba(128,128,128,.15)">'
        + '<input type="checkbox" class="g83ck" value="' + c.id + '" style="width:18px;height:18px">'
        + '<div style="flex:1;min-width:0"><b style="font-size:14px">' + esc(c.name) + '</b>'
        + (c.tag ? '<span style="font-size:11px;opacity:.6"> ' + esc(c.tag) + '</span>' : "")
        + '</div></label>';
    });
    return list;
  }

  async function gCreateDlg() {
    var d;
    try { d = await api("/api/chat/contacts", { noCache: true }); }
    catch (e) { sterr(e.message || "تعذر تحميل جهات الاتصال"); return; }
    var ui = cardBase();
    var list = pickList(d.contacts || [], null);
    ui.c.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
      + '<b>👥 مجموعة جديدة</b>'
      + '<button class="g83x" type="button" style="border:none;background:transparent;font-size:18px">✕</button></div>'
      + '<input id="g83name" type="text" maxlength="80" placeholder="اسم المجموعة" '
      + 'style="width:100%;box-sizing:border-box;padding:10px;border:1px solid rgba(128,128,128,.35);'
      + 'border-radius:10px;margin-bottom:8px;font-size:14px">'
      + '<div style="max-height:40vh;overflow:auto">'
      + (list || '<p style="opacity:.6;font-size:13px">لا جهات اتصال.</p>') + '</div>'
      + '<button id="g83go" type="button" style="width:100%;margin-top:10px;border:none;border-radius:10px;'
      + 'padding:11px;background:#2563eb;color:#fff;font-weight:800;font-size:15px">إنشاء المجموعة</button>';
    ui.c.querySelector(".g83x").addEventListener("click", function () { ui.o.remove(); });
    ui.c.querySelector("#g83go").addEventListener("click", async function () {
      var nm = ui.c.querySelector("#g83name").value.trim();
      var ids = Array.from(ui.c.querySelectorAll(".g83ck:checked")).map(function (x) { return Number(x.value); });
      if (!nm) { sterr("اكتب اسم المجموعة."); return; }
      if (!ids.length) { sterr("اختر عضواً واحداً على الأقل."); return; }
      var btn = ui.c.querySelector("#g83go");
      btn.textContent = "جارٍ الإنشاء..."; btn.disabled = true;
      try {
        var r = await api("/api/chat/groups/create", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: nm, members: ids }) });
        ui.o.remove(); st("تم إنشاء المجموعة");
        await window.loadChatList(true);
        if (r && r.group) gOpen(r.group.id, r.group.name);
      } catch (e2) {
        btn.textContent = "إنشاء المجموعة"; btn.disabled = false;
        sterr(e2.message || "فشل الإنشاء");
      }
    });
  }

  async function gAddDlg() {
    if (!G.g) return;
    var cd, md;
    try {
      cd = await api("/api/chat/contacts", { noCache: true });
      md = await api("/api/chat/gmembers?g=" + G.g.id, { noCache: true });
    } catch (e) { sterr(e.message || "تعذر التحميل"); return; }
    var have = new Set((md.members || []).map(function (m) { return m.id; }));
    var ui = cardBase();
    var list = pickList(cd.contacts || [], have);
    ui.c.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
      + '<b>＋ إضافة أعضاء — ' + esc(G.g.name) + '</b>'
      + '<button class="g83x" type="button" style="border:none;background:transparent;font-size:18px">✕</button></div>'
      + '<div style="max-height:50vh;overflow:auto">'
      + (list || '<p style="opacity:.6;font-size:13px">لا مستخدمين جدد لإضافتهم.</p>') + '</div>'
      + '<button id="g83go" type="button" style="width:100%;margin-top:10px;border:none;border-radius:10px;'
      + 'padding:11px;background:#2563eb;color:#fff;font-weight:800;font-size:15px">إضافة</button>';
    ui.c.querySelector(".g83x").addEventListener("click", function () { ui.o.remove(); });
    ui.c.querySelector("#g83go").addEventListener("click", async function () {
      var ids = Array.from(ui.c.querySelectorAll(".g83ck:checked")).map(function (x) { return Number(x.value); });
      if (!ids.length) { sterr("اختر عضواً واحداً على الأقل."); return; }
      try {
        await api("/api/chat/gadd", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ g: G.g.id, members: ids }) });
        ui.o.remove(); st("أُضيف الأعضاء");
      } catch (e2) { sterr(e2.message || "فشل"); }
    });
  }

  /* ── تمرير الإرسال والوسائط والقوائم عبر نسخ المجموعات ── */
  var oChatSend = window.chatSend;
  window.chatSend = async function (payload) {
    if (!G.g) return oChatSend.apply(this, arguments);
    if (S.chat.sending) return;
    S.chat.sending = true;
    var btn = q("chatSend");
    btn.textContent = "⏳";
    var replyId = S.chat.reply ? S.chat.reply.id : 0;
    var rInfo = S.chat.reply ? { name: S.chat.reply.name, text: S.chat.reply.text } : null;
    var temp = chatTempBubble(payload, rInfo);
    var gid = G.g.id;
    try {
      var d = await api("/api/chat/gsend", {
        method: "POST", headers: { "Content-Type": "application/json" }, timeout: 60000,
        body: JSON.stringify(Object.assign({ g: gid, reply: replyId }, payload)) });
      var m = d.message;
      if (temp) { try { temp.remove(); } catch (e0) {} }
      if (m && m.id > G.lastId) { G.lastId = m.id; G.msgs.push(m); gAppend(m); scrollChat(true); }
      chatSetReply(null);
      chatBeep("send");
    } catch (e) {
      if (typeof chatTempFail === "function") chatTempFail(temp);
      sterr(e.message || "فشل الإرسال");
    }
    finally { S.chat.sending = false; btn.textContent = "➤"; }
  };

  var oMediaURL = window.chatMediaURL;
  window.chatMediaURL = async function (id) {
    if (!G.g) return oMediaURL.apply(this, arguments);
    var key = "g" + id;
    if (S.chat.urls[key]) return S.chat.urls[key];
    var b = await chatBlob("/api/chat/gmedia?id=" + id);
    var u = URL.createObjectURL(b);
    S.chat.urls[key] = u;
    return u;
  };

  var oChatReset = window.chatReset;
  window.chatReset = function () {
    stop(); G.g = null; gInfoBtn(false); gBtns(false);
    return oChatReset.apply(this, arguments);
  };

  var oOpenChat = window.openChat;
  window.openChat = async function () {
    stop(); G.g = null; gInfoBtn(false); gBtns(false);
    var r = await oOpenChat.apply(this, arguments);
    return r;
  };

  var oLCL = window.loadChatList;
  window.loadChatList = async function () {
    var r = await oLCL.apply(this, arguments);
    try { await gRenderHome(); } catch (e0) {}
    return r;
  };

  async function gRenderHome() {
    if (!S.user) return;
    var box = q("chatList");
    if (!box || !inHome()) return;
    var d = await api("/api/chat/groups", { noCache: true });
    if (!S.user || !inHome()) return;
    box.querySelectorAll(".g83head,.g83grow").forEach(function (x) { x.remove(); });
    var gs = d.groups || [];
    var em = box.querySelector(".empty");
    if (em && gs.length) em.remove();
    var gun = 0;
    var head = document.createElement("div");
    head.className = "g83head";
    head.style.cssText = "display:flex;align-items:center;justify-content:space-between;padding:6px 4px 2px";
    head.innerHTML = '<b style="font-size:12px;opacity:.7">👥 المجموعات</b>'
      + '<button class="g83newbtn" type="button" style="border:none;background:#2563eb;color:#fff;'
      + 'border-radius:999px;padding:6px 12px;font-size:12px;font-weight:700">＋ مجموعة</button>';
    head.querySelector(".g83newbtn").addEventListener("click", gCreateDlg);
    box.insertBefore(head, box.firstChild);
    if (gs.length) {
      var frag = document.createDocumentFragment();
      gs.forEach(function (g) {
        gun += (g.unread || 0);
        var row = document.createElement("div");
        row.className = "chatrow g83grow";
        row.innerHTML = '<div class="cav">👥</div>'
          + '<div class="cinfo"><b>' + esc(g.name) + '</b>'
          + '<span>' + esc(g.preview || ("مجموعة · " + g.members_count + " عضو")) + '</span></div>'
          + '<div class="cside"><span class="ctime">'
          + esc(g.last_at ? fmtChatTime(g.last_at) : "") + '</span>'
          + (g.unread ? '<span class="cunread">' + (g.unread > 99 ? "99+" : g.unread) + '</span>' : "")
          + '</div></div>';
        row.addEventListener("click", function () { gOpen(g.id, g.name); });
        frag.appendChild(row);
      });
      box.insertBefore(frag, head.nextSibling);
    }
    S.chat.total = (S.chat.total || 0) + gun;
    renderChatBadge();
  }
})();
"""


_PWA_UI2_CSS = r"""/* ═══════════════════════════════════════════════════════════════════
   UI 2.0 — v84: الطبقة البصرية الجديدة — تُحقن من bot.py
   هوية LE ROI DE LA CÉRAMIQUE بنسخة معاصرة: زجاج، عمق، حركة، نعومة
   قاعدة ذهبية: لا نحذف أي تنسيق قديم — نتجاوز فقط بأعلى الخصوصية الآمنة
   ═══════════════════════════════════════════════════════════════════ */

/* ── 0) توكنز الطبقة الجديدة ── */
:root{
  --u2-grad:linear-gradient(135deg,var(--brand2),var(--brand));
  --u2-grad-gold:linear-gradient(135deg,#c1913a,#8f6a24);
  --u2-sheet:26px;
  --u2-dockbg:rgba(253,250,242,.86);
  --u2-headbg:rgba(253,250,242,.80);
  --u2-shadow-lg:0 18px 44px rgba(61,43,28,.16);
  --u2-focus:0 0 0 3px var(--goldsoft);
}
html[data-theme="dark"]{
  --u2-dockbg:rgba(33,26,20,.88);
  --u2-headbg:rgba(28,22,16,.82);
  --u2-shadow-lg:0 18px 44px rgba(0,0,0,.5);
}

/* ── 1) الهيدر: زجاج ملكي بعنوان متدرّج وخط ذهبي ── */
#app header{
  background:var(--u2-headbg);
  backdrop-filter:blur(18px) saturate(1.4);
  -webkit-backdrop-filter:blur(18px) saturate(1.4);
  border-bottom:1px solid var(--line);
  box-shadow:0 4px 20px rgba(61,43,28,.07);
  padding:calc(10px + env(safe-area-inset-top)) 16px 10px;
}
html[data-theme="dark"] #app header{box-shadow:0 4px 20px rgba(0,0,0,.35)}
#app header::after{
  content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;
  background:linear-gradient(90deg,transparent,var(--gold),transparent);
  opacity:.55;pointer-events:none;
}
#app header .t b{
  font-size:15.5px;letter-spacing:.2px;
}
@supports ((-webkit-background-clip:text) or (background-clip:text)){
  #app header .t b{
    background:linear-gradient(90deg,var(--brand) 20%,var(--gold));
    -webkit-background-clip:text;background-clip:text;
    -webkit-text-fill-color:transparent;
  }
}
#app header .t span{color:var(--mut)}
#app header button{
  background:var(--brandsoft);color:var(--brand);border:1px solid var(--goldline);
  border-radius:12px;transition:transform .18s ease,box-shadow .18s ease;
}
#app header button:active{transform:scale(.9);box-shadow:0 0 0 6px var(--goldsoft)}
#app header img{border-radius:12px;box-shadow:0 3px 10px rgba(122,58,18,.25)}
#nBadge{
  background:var(--bad);color:#fff;font-size:10px;font-weight:800;
  min-width:17px;height:17px;border-radius:9px;display:flex;align-items:center;
  justify-content:center;padding:0 4px;box-shadow:0 2px 6px rgba(160,44,44,.4);
}

/* ── 2) شريط التنقل: Dock عائم زجاجي بحبة دواء متدرجة ── */
main{padding-bottom:calc(118px + env(safe-area-inset-bottom))}
nav.tabs{
  left:14px;right:14px;bottom:calc(12px + env(safe-area-inset-bottom));
  border-radius:24px;border:1px solid var(--goldline);
  background:var(--u2-dockbg);
  backdrop-filter:blur(22px) saturate(1.5);
  -webkit-backdrop-filter:blur(22px) saturate(1.5);
  box-shadow:0 12px 34px rgba(61,43,28,.18);
  padding:7px 8px calc(7px);
}
html[data-theme="dark"] nav.tabs{box-shadow:0 12px 34px rgba(0,0,0,.5)}
nav.tabs button{
  border-radius:16px;position:relative;
  transition:background .22s ease,color .22s ease,transform .18s ease;
}
nav.tabs button .ic{transition:transform .22s cubic-bezier(.34,1.56,.64,1)}
nav.tabs button:active{transform:scale(.9)}
nav.tabs button.on{
  background:var(--u2-grad);color:#fff;font-weight:800;
  box-shadow:0 6px 16px rgba(122,58,18,.38);
}
html[data-theme="dark"] nav.tabs button.on{box-shadow:0 6px 16px rgba(217,166,72,.25)}
nav.tabs button.on .ic{transform:translateY(-3px) scale(1.15)}
nav.tabs .cnavb{z-index:2;box-shadow:0 0 0 2px var(--card)}

/* ── 3) الانتقال بين الأقسام: تلاشٍ ناعم (opacity فقط — آمن للعناصر fixed) ── */
.view.on{animation:u2view .24s ease}
@keyframes u2view{from{opacity:0}to{opacity:1}}

/* ── 4) النوافذ المنبثقة: Bottom Sheets ذكية على الهاتف ── */
@media (max-width:699px){
  dialog{
    position:fixed;inset:auto 0 0 0;width:100%;max-width:none;margin:0;
    border-radius:var(--u2-sheet) var(--u2-sheet) 0 0;
    max-height:88vh;max-height:88dvh;overflow-y:auto;overscroll-behavior:contain;
    box-shadow:0 -20px 70px rgba(0,0,0,.38);
  }
  /* استثناء: عارض وسائط الشات يبقى ملء الشاشة */
  dialog#chatView{inset:0;width:100%;height:100%;max-height:none;border-radius:0;
    background:rgba(0,0,0,.92);overflow:hidden}
  dialog[open]{animation:u2sheet .34s cubic-bezier(.22,1.15,.36,1)}
  dialog#chatView[open]{animation:none}
  @keyframes u2sheet{from{transform:translateY(55%);opacity:.35}to{transform:translateY(0);opacity:1}}
  dialog .db{padding-bottom:calc(20px + env(safe-area-inset-bottom))}
}
@media (min-width:700px){
  dialog{border-radius:22px;box-shadow:var(--u2-shadow-lg)}
  dialog[open]{animation:u2pop .26s cubic-bezier(.22,1.25,.36,1)}
  @keyframes u2pop{from{transform:scale(.88);opacity:0}to{transform:scale(1);opacity:1}}
}
dialog::backdrop{
  background:rgba(30,20,12,.48);
  backdrop-filter:blur(5px) saturate(.9);
  -webkit-backdrop-filter:blur(5px) saturate(.9);
}
dialog .dh{
  background:transparent;color:var(--brand);
  padding:8px 20px 2px;font-size:15.5px;font-weight:800;
}
dialog .dh::after{
  content:"";display:block;height:3px;width:46px;border-radius:99px;
  background:linear-gradient(90deg,var(--gold),var(--brand));
  margin-top:7px;opacity:.9;
}
dialog .db{padding:14px 20px 18px}
.u2grab{
  width:46px;height:5px;border-radius:99px;background:var(--line);
  margin:12px auto 4px;flex:none;
}
html[data-theme="dark"] .u2grab{background:#4a3b2a}

/* ── 5) البطاقات والقوائم: عمق ناعم ولمسة ضغط ── */
.card,.row,.stat,.chatrow,.oilcard,.skl{border-radius:18px}
.card{box-shadow:var(--shadow)}
.row{
  transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease;
}
.row:active{transform:scale(.985);box-shadow:0 2px 8px rgba(61,43,28,.1)}
.stat{
  background:linear-gradient(165deg,var(--card) 55%,var(--brandsoft));
  border:1px solid var(--goldline);position:relative;overflow:hidden;
  transition:transform .16s ease;
}
.stat::before{
  content:"";position:absolute;top:-46px;inset-inline-end:-38px;
  width:110px;height:110px;border-radius:50%;
  background:radial-gradient(circle,var(--goldsoft),transparent 68%);
  pointer-events:none;
}
.stat:active{transform:scale(.94)}
.stat b{font-size:26px;letter-spacing:-.5px}

/* ── 6) الأزرار: تدرّج ملكي + إحساس ضغط ملموس ── */
.btn{
  border-radius:14px;font-weight:800;position:relative;overflow:hidden;
  transition:transform .16s ease,box-shadow .16s ease,filter .16s ease;
  box-shadow:0 5px 14px rgba(122,58,18,.22);
}
html[data-theme="dark"] .btn{box-shadow:0 5px 14px rgba(0,0,0,.35)}
.btn:active{transform:scale(.955);box-shadow:0 2px 6px rgba(122,58,18,.2)}
.btn.gray,.btn.red,.gbtn{box-shadow:none}
.btn.gold{background:var(--u2-grad-gold);box-shadow:0 5px 14px rgba(169,126,47,.3)}
.btn.red{background:linear-gradient(135deg,#c0392b,#8f2015)}
.mbtn{border-radius:13px;transition:transform .15s ease}
.u2rip{
  position:absolute;border-radius:50%;transform:scale(0);
  background:rgba(255,255,255,.42);pointer-events:none;
  animation:u2rip .55s ease-out forwards;
}
.u2rip.dark{background:rgba(122,58,18,.16)}
@keyframes u2rip{to{transform:scale(3);opacity:0}}

/* ── 7) الزر الذكي العائم FAB ── */
.u2fab{
  position:fixed;z-index:8;inset-inline-end:18px;
  bottom:calc(96px + env(safe-area-inset-bottom));
  width:60px;height:60px;border:none;border-radius:20px;cursor:pointer;
  background:var(--u2-grad);color:#fff;font-size:28px;line-height:1;
  display:none;align-items:center;justify-content:center;
  box-shadow:0 12px 30px rgba(122,58,18,.45),0 2px 8px rgba(122,58,18,.3);
  transition:transform .2s cubic-bezier(.34,1.56,.64,1);
  padding:0;
}
html[data-theme="dark"] .u2fab{box-shadow:0 12px 30px rgba(0,0,0,.55),0 0 0 1px var(--goldline)}
.u2fab.show{display:flex;animation:u2fab .32s cubic-bezier(.34,1.56,.64,1)}
.u2fab:active{transform:scale(.88) rotate(90deg)}
@keyframes u2fab{from{transform:scale(0) rotate(-120deg);opacity:0}to{transform:scale(1) rotate(0);opacity:1}}

/* ── 8) الحقول والبحث: حلقات تركيز ذهبية ── */
.search{border-radius:16px;box-shadow:var(--shadow);transition:border-color .2s,box-shadow .2s}
.fld input,.fld select,.fld textarea,#chatSrchQ{transition:border-color .2s,box-shadow .2s}
.search:focus,.fld input:focus,.fld select:focus,.fld textarea:focus,#chatSrchQ:focus{
  outline:none;border-color:var(--gold);box-shadow:var(--u2-focus)}
.seg{border-radius:14px;padding:4px}
.seg button{border-radius:11px;transition:all .2s ease}
.seg button.on{
  background:var(--card);color:var(--brand);
  box-shadow:0 2px 8px rgba(61,43,28,.14),0 0 0 1px var(--goldline);
}
html[data-theme="dark"] .seg button.on{background:#3a2e20;color:var(--gold)}
.chip,.rchip,.fchip{transition:transform .15s ease,background .2s,border-color .2s}
.chip:active,.rchip:active,.fchip:active{transform:scale(.93)}

/* ── 9) التوست: حبة زجاجية عائمة ── */
#toast{
  bottom:calc(104px + env(safe-area-inset-bottom));
  border-radius:99px;padding:12px 22px;font-weight:700;
  background:rgba(43,30,19,.9);color:#fdfaf2;
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  box-shadow:0 12px 34px rgba(0,0,0,.35);
  animation:u2toast .3s cubic-bezier(.34,1.4,.64,1);
}
html[data-theme="dark"] #toast{background:rgba(58,46,32,.95);color:#f3ead9}
@keyframes u2toast{from{opacity:0;transform:translateX(-50%) translateY(14px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}

/* ── 10) شاشة الدخول: مدخل ملكي فخم ── */
#login{
  background:
    radial-gradient(1100px 540px at 50% -12%, var(--goldsoft), transparent 62%),
    radial-gradient(900px 460px at 108% 112%, var(--brandsoft), transparent 55%),
    var(--bg);
}
#login .logo{
  border-radius:26px;
  box-shadow:0 18px 44px rgba(122,58,18,.3),0 0 0 7px var(--goldsoft);
}
#login .card{border-radius:24px;box-shadow:var(--u2-shadow-lg);border-color:var(--goldline)}

/* ── 11) الشات: لمسات خفيفة فقط — لا مساس بالمنطق ── */
.cbar{
  border-radius:20px;
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  box-shadow:0 6px 20px rgba(61,43,28,.1);
}
.cbtn.go{background:var(--u2-grad);box-shadow:0 4px 12px rgba(122,58,18,.35)}
.chatrow{transition:transform .16s ease}
.chatrow:active{transform:scale(.985)}
.cav{border-radius:50%;box-shadow:0 2px 8px rgba(61,43,28,.18)}

/* ── 12) بطاقات الرئيسية الملهمة (طلبات/زيت) والدرجات ── */
.oilcard{box-shadow:0 10px 26px rgba(21,128,61,.25);border-radius:20px}
.oilcard.warn{box-shadow:0 10px 26px rgba(180,83,9,.28)}
.oilcard.red{box-shadow:0 10px 26px rgba(185,28,28,.3)}

/* ── 13) تفاصيل عامة: تمرير أنيق + انتقال ثيم سلس ── */
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-thumb{background:var(--line);border-radius:99px}
::-webkit-scrollbar-track{background:transparent}
body,.card,.row,header,nav.tabs,dialog,.seg button,.chip{
  transition-property:background-color,border-color;
  transition-duration:.25s;transition-timing-function:ease;
}
button,.btn,.row,.chatrow{-webkit-tap-highlight-color:transparent}
.badge{border-radius:99px}
img{-webkit-user-drag:none;user-select:none}"""

_PWA_UI2_JS = r"""/* ═══════════════════════════════════════════════════════════════════
   UI 2.0 — v84: طبقة الذكاء والسلاسة — تُحقن من bot.py بعد كل السكربتات
   المبادئ: لا نستبدل أي دالة قائمة إلا بتغليف آمن يستدعي الأصل،
            كل شيء داخل IIFE + try/catch — أي عطل يبقى معزولاً بلا أثر.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  function safe(fn) { try { fn(); } catch (e) { /* معزول بلا أثر */ } }
  function $(s) { return document.querySelector(s); }

  /* ── 1) ترقية النوافذ المنبثقة: مقبض سحب + إغلاق بلمس الخلفية + سحب للأسفل ──
     (14 حواراً بالجرد — استثنٍ فقط chatView: عارض وسائط ملء شاشة له زر إغلاقه) ── */
  var DIALOG_IDS = ["dlg", "dlgPass", "dlgRole", "dlgApprove", "dlgNew",
    "dlgEdit", "dlgUser", "dlgDetail", "dlgPhoto", "dlgOrd",
    "dlgMenu", "dlgNotif", "dlgUpd"];

  function upgradeDialog(d) {
    if (!d || d._u2) return;
    d._u2 = true;
    /* مقبض السحب أعلى الورقة */
    var h = document.createElement("div");
    h.className = "u2grab";
    d.insertBefore(h, d.firstChild);
    /* لمس الخلفية (backdrop) يغلق — نفس سلوك زر Esc الأصلي */
    d.addEventListener("click", function (e) {
      if (e.target === d) safe(function () { d.close(); });
    });
    /* سحب المقبض للأسفل يغلق — إغلاق فقط، لا منطق إلغاء مكرر */
    var startY = null, dy = 0;
    h.addEventListener("touchstart", function (e) {
      startY = e.touches[0].clientY; dy = 0;
    }, { passive: true });
    h.addEventListener("touchmove", function (e) {
      if (startY === null) return;
      dy = e.touches[0].clientY - startY;
      if (dy > 0) {
        d.style.transition = "none";
        d.style.transform = "translateY(" + dy + "px)";
      }
    }, { passive: true });
    h.addEventListener("touchend", function () {
      if (startY === null) return;
      d.style.transition = "transform .25s ease";
      d.style.transform = "";
      if (dy > 110) safe(function () { d.close(); });
      startY = null; dy = 0;
    }, { passive: true });
  }
  DIALOG_IDS.forEach(function (id) { upgradeDialog(document.getElementById(id)); });

  /* ── 2) الزر الذكي العائم FAB — يشغّل الأزرار الحقيقية القائمة (بلا منطق مكرر) ── */
  var FAB_TARGET = { "v-inv": "btnAddProd", "v-ord": "btnNewOrd", "v-chat": "btnNewChat" };
  var FAB_LABEL = { "v-inv": "إضافة سلعة جديدة", "v-ord": "طلب توريد جديد", "v-chat": "محادثة جديدة" };
  var curView = "v-home";
  var fab = document.createElement("button");
  fab.type = "button";
  fab.className = "u2fab";
  fab.innerHTML = "＋";
  document.body.appendChild(fab);

  function fabTargetOk() {
    var id = FAB_TARGET[curView];
    if (!id) return false;
    var b = document.getElementById(id);
    if (!b || b.disabled) return false;
    /* مخفي بالصلاحيات أو القسم الداخلي؟ offsetParent يكشفه بلا قراءة display يدوياً */
    return b.offsetParent !== null;
  }
  function syncFab() {
    var on = fabTargetOk();
    fab.classList.toggle("show", on);
    if (on) fab.setAttribute("aria-label", FAB_LABEL[curView] || "إجراء سريع");
  }
  fab.addEventListener("click", function () {
    safe(function () {
      var b = document.getElementById(FAB_TARGET[curView]);
      if (b && !b.disabled) b.click();
    });
  });

  /* ── 3) تتبّع القسم الحالي: تغليف switchView — الأصل يُستدعى دائماً ── */
  safe(function () {
    var orig = window.switchView;
    if (typeof orig !== "function") return;
    var active = document.querySelector(".view.on");
    if (active) curView = active.id;
    window.switchView = function (id) {
      var r = orig.apply(this, arguments);
      curView = id;
      safe(syncFab);
      /* القوائم تُبنى غير متزامنة — فحص متأخر لظهور/اختفاء الزر الهدف */
      setTimeout(function () { safe(syncFab); }, 650);
      setTimeout(function () { safe(syncFab); }, 1400);
      return r;
    };
  });
  safe(syncFab);

  /* ── 4) أثر الموجة (Ripple) عند اللمس — تفويض عام، بلا تدخل في الأحداث القائمة ── */
  document.addEventListener("pointerdown", function (e) {
    safe(function () {
      var b = e.target.closest(".btn,.tbtn,.chip,.rchip,.fchip,nav.tabs button");
      if (!b || b.disabled) return;
      var r = b.getBoundingClientRect();
      var sz = Math.max(r.width, r.height);
      var s = document.createElement("span");
      var dark = !/btn$/.test(b.className || "") || b.classList.contains("gray") || b.classList.contains("rchip");
      s.className = "u2rip" + (dark ? " dark" : "");
      s.style.width = s.style.height = sz + "px";
      s.style.left = (e.clientX - r.left - sz / 2) + "px";
      s.style.top = (e.clientY - r.top - sz / 2) + "px";
      if (getComputedStyle(b).position === "static") b.style.position = "relative";
      b.appendChild(s);
      setTimeout(function () { s.remove(); }, 560);
    });
  }, { passive: true });

  /* ── 5) لون شريط النظام يتبع الثيم الزجاجي الجديد ── */
  safe(function () {
    var orig = window.applyTheme;
    if (typeof orig !== "function") return;
    window.applyTheme = function (mode, save) {
      var r = orig.apply(this, arguments);
      safe(function () {
        var m = document.querySelector('meta[name="theme-color"]');
        if (m) m.setAttribute("content", mode === "dark" ? "#151110" : "#f5ecdc");
      });
      return r;
    };
  });

  /* ── 6) إصدار الرئيسية يعرض القيمة الحية بدل v74 المجمّدة ── */
  safe(function () {
    var p = document.querySelector("#v-home .hint");
    if (p && typeof APP_VER !== "undefined") p.textContent = "الإصدار " + APP_VER + " — واجهة UI 2.0";
  });

  /* ── 7) مزامنة أولية متأخرة (بعد أول تحميل قوائم) ── */
  setTimeout(function () { safe(syncFab); }, 1200);
})();"""

_PWA_INVOICE_CSS = r"""/* ═══ v88: شاشة الفاتورة ═══ */
#v-oil .invCard{margin-bottom:12px;padding:14px}
#v-oil .invTitle{font-weight:700;font-size:14px;margin-bottom:10px;color:var(--ink)}
#v-oil .invTitle .cnt{color:var(--mut);font-weight:400;font-size:12px}
#v-oil .invRow{display:flex;align-items:center;gap:10px;padding:9px 4px;border-bottom:1px dashed var(--line)}
#v-oil .invRow:last-child{border-bottom:none}
#v-oil .invRow .info{flex:1;min-width:0}
#v-oil .invRow .info b{display:block;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#v-oil .invRow .info span{font-size:12px;color:var(--mut)}
#v-oil .invTotalBar{position:sticky;bottom:64px;z-index:5;display:flex;gap:10px;align-items:center;background:var(--card);border:1px solid var(--goldline);border-radius:14px;padding:10px 12px;box-shadow:0 6px 18px rgba(0,0,0,.14);margin-bottom:12px}
#v-oil .invTotalBar .tot span{font-size:11.5px;color:var(--mut);display:block}
#v-oil .invTotalBar .tot b{font-size:17px;color:var(--brand)}
#v-oil .invTotalBar .btn{flex:1;padding:12px}
#v-oil .invDone{text-align:center;border:1.5px solid var(--okline);background:var(--oksoft)}
#v-oil .invDone .big{font-size:20px;font-weight:800;color:var(--ok);margin:6px 0}
#v-oil .invMini{font-size:12.5px;color:var(--ink);line-height:2}
#v-oil .invDone .acts{display:flex;gap:8px;justify-content:center;margin-top:10px;flex-wrap:wrap}
#v-oil .invDone .acts .btn,#v-oil .invDone .acts a{min-width:130px}
#v-oil .invGrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
#v-oil .invGrid label{font-size:11.5px;color:var(--mut);display:block;margin-bottom:3px}
#v-oil .invGrid input{width:100%;padding:10px 12px;border:1.5px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font-size:15px;box-sizing:border-box}
#v-oil .invPrev{font-size:13px;color:var(--brand);font-weight:700;margin-top:8px;min-height:18px}
#v-oil .invRec{display:flex;align-items:center;gap:8px;padding:8px 4px;border-bottom:1px dashed var(--line);font-size:13px}
#v-oil .invRec:last-child{border-bottom:none}
#v-oil .invRec .info{flex:1;min-width:0}
#v-oil .invRec .info b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#v-oil .invRec .info span{font-size:11.5px;color:var(--mut)}
#v-oil .invRec a{text-decoration:none}
#v-oil .invArchQ{margin-bottom:8px}
#v-oil .invSt{font-size:10px;font-weight:800;padding:1.5px 7px;border-radius:99px;background:#f4e8d7;color:#8a5b00;margin-inline-start:4px;vertical-align:middle;white-space:nowrap}
#v-oil .invRec .btn{white-space:nowrap}"""


_PWA_INVOICE_SECTION_HTML = (
    '<div id="invMain"><div class="card invCard"><input class="search" id="invCust" placeholder="&#128100'
    + '; اسم الزبون أو رقم هاتفه..." maxlength="120"></div><div class="card invCard"><input class="search" '
    + 'id="invQ" placeholder="&#128269; ابحث باسم السلعة لإضافتها..." autocomplete="off"><div id="invResult'
    + 's"><p class="empty">ابحث واختر سلعة للإضافة</p></div></div><div class="card invCard" id="invItemForm'
    + '" style="display:none"><div class="invTitle">&#10133; <span id="invFormTitle"></span></div><div clas'
    + 's="invGrid"><div><label id="invQtyLabel">الكمية</label><input id="invQty" type="number" inputmode="d'
    + 'ecimal" min="0" step="any" placeholder="0"></div><div><label>السعر (د.ج)</label><input id="invPrice"'
    + ' type="number" inputmode="decimal" min="0" step="any" placeholder="0"></div><div id="invBoxWrap"><la'
    + 'bel>مساحة العلبة (m&#178;)</label><input id="invBox" type="number" inputmode="decimal" min="0" step='
    + '"any" value="1.44"></div><div id="invLenWrap"><label>طول القطعة (م)</label><input id="invLen" type="'
    + 'number" inputmode="decimal" min="0" step="any" value="0.45"></div></div><div class="invPrev" id="inv'
    + 'Preview"></div><div style="display:flex;gap:8px;margin-top:10px"><button type="button" class="btn go'
    + 'ld" id="invAddGo" style="flex:1">&#10003; أضف للسلة</button><button type="button" class="btn gray" i'
    + 'd="invCancelItem">إلغاء</button></div></div><div class="card invCard"><div class="invTitle">&#129522'
    + '; السلة <span class="cnt" id="invCount"></span></div><div id="invCart"><p class="empty">أضف سلعاً با'
    + 'لبحث أعلاه</p></div></div><div class="card invCard"><div class="invTitle">&#128104;&#8205;&#128295; '
    + 'العامل المكلّف بالتجهيز</div><select class="search" id="invWorker"><option value="">&#8212; اختر الع'
    + 'امل &#8212;</option></select></div><div class="invTotalBar"><div class="tot"><span>الإجمالي</span><b'
    + ' id="invTotal">0,00 د.ج</b></div><button type="button" class="btn gold" id="invGo">&#129534; إنشاء ا'
    + 'لفاتورة</button></div></div><div class="card invCard" id="invSuccess" style="display:none"></div><di'
    + 'v class="card invCard"><div class="invTitle">&#128337; أحدث الفواتير</div><div id="invRecent"><p cla'
    + 'ss="empty">&#8212;</p></div></div>'
    + '<div class="card invCard"><div class="invTitle">&#128218; سجل الفواتير '
    + '<span class="cnt" id="invArchCnt"></span></div>'
    + '<input class="search invArchQ" id="invArchQ" placeholder="&#128269; ابحث باسم الزبون أو برقم الفاتورة..." autocomplete="off">'
    + '<div id="invArch"><p class="empty">جارِ التحميل...</p></div>'
    + '<button type="button" class="btn gray" id="invArchMore" style="display:none;width:100%;margin-top:8px">&#11015; فواتير أقدم</button></div>'
)


_PWA_INVOICE_JS = r"""/* ═══ v88: شاشة إنشاء الفاتورة (بدل الزيت) ═══ */
(function(){
  var INV = {cart:[], prod:null, workers:[], resMap:{}, searchSeq:0, boxMemo:{}};

  function money(n){
    n = Number(n || 0);
    return n.toLocaleString("fr-FR", {minimumFractionDigits:2, maximumFractionDigits:2}) + " د.ج";
  }
  function unitLabel(pt){
    return pt === "pcs" ? "قطعة" : (pt === "plant" ? "م.ط" : "m²");
  }

  // ── v88.1: تحميل PDF محصّن — fetch ثم تنزيل مباشر مع رسالة خطأ ظاهرة عند أي فشل
  // (الروابط النقية كانت تفشل بصمت داخل PWA/ويبفيو على بعض الهواتف)
  async function dlPdf(id, btn){
    var t = btn ? btn.textContent : "";
    if(btn){ btn.disabled = true; btn.textContent = "\u23F3 جارٍ..."; }
    try{
      var r = await fetch("/api/invoice/pdf?id=" + id, {credentials:"same-origin"});
      if(!r.ok){
        var m = "تعذر التحميل (" + r.status + ")";
        try{ var j = await r.json(); if(j && j.error) m = j.error; }catch(e){}
        throw new Error(m);
      }
      var b = await r.blob();
      if(!b || b.size < 100) throw new Error("ملف PDF فارغ — أعد المحاولة");
      var url = URL.createObjectURL(b);
      var a = document.createElement("a");
      a.href = url; a.download = "facture_" + id + ".pdf";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function(){ try{ URL.revokeObjectURL(url); }catch(e){} }, 5000);
      toast("\u2705 تم تحميل الفاتورة #" + id);
    }catch(err){
      toast(err && err.message ? err.message : "فشل تحميل PDF", 1);
    }finally{
      if(btn){ btn.disabled = false; btn.textContent = t; }
    }
  }
  window.invDlPdf = dlPdf;

  // ── v88.1: صف فاتورة موحّد — زر تنزيل (fetch) + معاينة بالمتصفح
  function invRow(v){
    var st = (v.status && v.status !== "approved")
      ? ' <span class="invSt">' + esc(v.status) + '</span>' : "";
    return '<div class="invRec">'
      + '<div class="info"><b>#' + v.id + ' &#8212; ' + esc(v.customer) + st + '</b>'
      + '<span>' + esc(v.at) + (v.worker ? ' • &#128104; ' + esc(v.worker) : '')
      + ' • &#128176; ' + money(v.total) + '</span></div>'
      + '<button type="button" class="btn mini soft-g" data-invpdf="' + v.id + '">&#11015;&#65039; PDF</button>'
      + '<a class="btn mini gray" href="/api/invoice/pdf?id=' + v.id + '&disp=inline" target="_blank" rel="noopener" title="معاينة">&#128065;</a></div>';
  }

  // ── v88.1: سجل الفواتير الكامل — بحث + «فواتير أقدم» (ترقيم بالمؤشر)
  var archTimer = null, archSeq = 0, archBefore = 0;
  async function loadArch(reset){
    var box = $("#invArch"), more = $("#invArchMore");
    if(!box) return;
    var q = $("#invArchQ").value.trim();
    if(reset) archBefore = 0;
    var my = ++archSeq;
    try{
      var d = await api("/api/invoice/list?limit=15"
        + (q ? "&q=" + encodeURIComponent(q) : "")
        + (archBefore ? "&before=" + archBefore : ""), {noCache:true});
      if(my !== archSeq) return;
      var list = d.invoices || [];
      var html = list.map(invRow).join("");
      if(reset){
        box.innerHTML = list.length ? html
          : '<p class="empty">' + (q ? "لا نتائج مطابقة" : "السجل فارغ") + '</p>';
      } else if(list.length){
        box.innerHTML += html;
      }
      if(list.length) archBefore = list[list.length - 1].id;
      if(more) more.style.display = d.has_more ? "" : "none";
    }catch(e){
      if(my === archSeq) box.innerHTML = '<p class="empty">' + esc(e.message) + '</p>';
    }
  }

  async function loadWorkers(){
    try{
      var d = await api("/api/invoice/workers", {noCache:true});
      INV.workers = d.workers || [];
    }catch(e){ INV.workers = []; }
    var sel = $("#invWorker");
    sel.innerHTML = '<option value="">&#8212; اختر العامل المكلّف &#8212;</option>'
      + INV.workers.map(function(w){
          return '<option value="' + w.id + '">' + esc(w.name) + '</option>';
        }).join("");
  }

  async function loadRecent(){
    var box = $("#invRecent");
    try{
      var d = await api("/api/invoice/recent", {noCache:true});
      var list = d.invoices || [];
      if(!list.length){ box.innerHTML = '<p class="empty">لا فواتير بعد — الأولى ستظهر هنا</p>'; return; }
      box.innerHTML = list.map(invRow).join("");
    }catch(e){ box.innerHTML = '<p class="empty">' + esc(e.message) + '</p>'; }
  }

  async function loadInvoice(){
    await Promise.all([loadWorkers(), loadRecent(), loadArch(true)]);
  }

  var searchTimer = null;
  function onSearch(){
    clearTimeout(searchTimer);
    searchTimer = setTimeout(doSearch, 280);
  }
  async function doSearch(){
    var q = $("#invQ").value.trim();
    var my = ++INV.searchSeq;
    try{
      var d = await api("/api/invoice/products?q=" + encodeURIComponent(q));
      if(my !== INV.searchSeq) return;
      renderResults(d.products || []);
    }catch(e){
      if(my === INV.searchSeq) $("#invResults").innerHTML = '<p class="empty">' + esc(e.message) + '</p>';
    }
  }
  function renderResults(list){
    INV.resMap = {};
    list.forEach(function(p){ INV.resMap[p.id] = p; });
    var box = $("#invResults");
    if(!list.length){
      box.innerHTML = '<p class="empty">لا نتائج — أضف السلعة للمخزون أولاً</p>';
      return;
    }
    box.innerHTML = list.map(function(p){
      var pr = (p.price != null && p.price !== "") ? money(p.price) : "بلا سعر محفوظ";
      return '<div class="invRow">'
        + '<div class="info"><b>' + esc(p.name) + '</b>'
        + '<span>&#128230; ' + (p.qty || 0) + ' • &#128176; ' + pr
        + ' • بالـ' + unitLabel(p.ptype) + '</span></div>'
        + '<button type="button" class="btn mini gold" data-add="' + p.id + '">&#65291; إضافة</button></div>';
    }).join("");
  }

  function openForm(pid){
    var p = INV.resMap[pid];
    if(!p) return;
    INV.prod = p;
    $("#invFormTitle").textContent = p.name;
    $("#invQtyLabel").textContent = p.ptype === "pcs" ? "عدد القطع"
      : (p.ptype === "plant" ? "الطول بالمتر (م.ط)" : "المساحة (m²)");
    $("#invQty").value = "";
    $("#invPrice").value = (p.price != null && p.price !== "") ? p.price : "";
    var memo = INV.boxMemo[pid] || 1.44;
    $("#invBoxWrap").style.display = (p.ptype === "m2") ? "" : "none";
    $("#invBox").value = memo;
    $("#invLenWrap").style.display = (p.ptype === "plant") ? "" : "none";
    $("#invLen").value = "0.45";
    $("#invPreview").textContent = "";
    $("#invItemForm").style.display = "";
    try{ $("#invItemForm").scrollIntoView({behavior:"smooth", block:"center"}); }catch(e){}
    try{ $("#invQty").focus(); }catch(e){}
  }

  function calcPreview(){
    var p = INV.prod;
    if(!p) return null;
    var qty = parseFloat($("#invQty").value);
    var price = parseFloat($("#invPrice").value);
    if(!(qty > 0) || !(price >= 0)) return null;
    var o = {pid:p.id, name:p.name, ptype:p.ptype, qty:qty, price:price};
    if(p.ptype === "m2"){
      var ba = parseFloat($("#invBox").value);
      if(!(ba > 0)) ba = 1.44;
      o.box_area = ba;
      o.boxes = Math.ceil(qty / ba);
    }
    if(p.ptype === "plant"){
      var ln = parseFloat($("#invLen").value);
      if(!(ln > 0)) ln = 0.45;
      o.length_m = ln;
      o.pieces = Math.ceil(qty / ln);
    }
    return o;
  }
  function showPreview(){
    var o = calcPreview();
    var el = $("#invPreview");
    if(!o){ el.textContent = ""; return; }
    var sub = o.qty * o.price;
    var extra = "";
    if(o.ptype === "m2") extra = " &#8592; " + o.boxes + " علبة";
    if(o.ptype === "plant") extra = " &#8592; " + o.pieces + " قطعة";
    el.innerHTML = "&#128208; " + o.qty + " " + unitLabel(o.ptype) + extra
      + " &#215; " + money(o.price) + " = " + money(sub);
  }

  function confirmAdd(){
    var o = calcPreview();
    if(!o){ toast("أدخل الكمية والسعر أولاً", 1); return; }
    if(o.box_area) INV.boxMemo[o.pid] = o.box_area;
    var same = INV.cart.filter(function(i){ return i.pid === o.pid && i.price === o.price; })[0];
    if(same){ same.qty = Math.round((same.qty + o.qty) * 1000) / 1000; }
    else{ INV.cart.push(o); }
    renderCart();
    $("#invItemForm").style.display = "none";
    INV.prod = null;
    $("#invQ").value = "";
    $("#invResults").innerHTML = "";
    toast("\u2705 أُضيفت للسلة (" + INV.cart.length + " عناصر)");
  }

  function renderCart(){
    var box = $("#invCart");
    $("#invCount").textContent = INV.cart.length ? "(" + INV.cart.length + ")" : "";
    if(!INV.cart.length){
      box.innerHTML = '<p class="empty">أضف سلعاً بالبحث أعلاه</p>';
      $("#invTotal").textContent = money(0);
      return;
    }
    box.innerHTML = INV.cart.map(function(i, ix){
      var det = i.qty + " " + unitLabel(i.ptype);
      if(i.ptype === "m2") det += " &#8592; " + i.boxes + " علبة";
      if(i.ptype === "plant") det += " &#8592; " + i.pieces + " قطعة";
      return '<div class="invRow">'
        + '<div class="info"><b>' + esc(i.name) + '</b>'
        + '<span>' + det + ' &#215; ' + money(i.price) + ' = ' + money(i.qty * i.price) + '</span></div>'
        + '<button type="button" class="btn mini soft-r" data-del="' + ix + '">&#10005;</button></div>';
    }).join("");
    $("#invTotal").textContent = money(cartTotal());
  }
  function cartTotal(){
    return INV.cart.reduce(function(s, i){ return s + i.qty * i.price; }, 0);
  }

  function showSuccess(d){
    $("#invMain").style.display = "none";
    var sc = $("#invSuccess");
    sc.style.display = "";
    sc.innerHTML = '<div class="big">&#9989; فاتورة #' + d.invoice_id + ' أُنشئت بنجاح</div>'
      + '<div class="invMini">&#128100; ' + esc(d.customer)
      + '<br>&#128176; ' + money(d.total)
      + '<br>&#128104; ' + esc(d.worker_name) + ' (أُشعر في تلغرام)'
      + '<br>&#128206; وصلتك نسخة PDF في البوت أيضاً</div>'
      + '<div class="acts">'
      + '<button type="button" class="btn green" data-invpdf="' + d.invoice_id + '">&#11015;&#65039; تحميل PDF</button>'
      + '<a class="btn gray" href="/api/invoice/pdf?id=' + d.invoice_id + '&disp=inline" target="_blank" rel="noopener">&#128065; معاينة</a>'
      + '<button type="button" class="btn gray" id="invAgain">&#129534; فاتورة جديدة</button></div>';
    $("#invAgain").addEventListener("click", function(){
      sc.style.display = "none";
      $("#invMain").style.display = "";
      loadRecent();
    });
    try{ sc.scrollIntoView({behavior:"smooth", block:"start"}); }catch(e){}
  }

  async function createInv(){
    var cust = $("#invCust").value.trim();
    if(!cust){ toast("اكتب اسم الزبون أولاً", 1); return; }
    if(!INV.cart.length){ toast("السلة فارغة — أضف سلعاً أولاً", 1); return; }
    var wid = $("#invWorker").value;
    if(!wid){ toast("اختر العامل المكلّف بالتجهيز", 1); return; }
    var btn = $("#invGo");
    btn.disabled = true;
    try{
      var d = await api("/api/invoice/create", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          customer: cust, worker_id: Number(wid),
          items: INV.cart.map(function(i){
            var o = {qty: i.qty, price: i.price, ptype: i.ptype};
            if(i.pid) o.product_id = i.pid; else o.name = i.name;
            if(i.ptype === "m2") o.box_area = i.box_area;
            if(i.ptype === "plant") o.length_m = i.length_m || 0.45;
            return o;
          })
        })
      });
      INV.cart = [];
      renderCart();
      $("#invCust").value = "";
      showSuccess(d);
    }catch(e){
      toast(e.message || "فشل إنشاء الفاتورة", 1);
    }finally{
      btn.disabled = false;
    }
  }

  document.addEventListener("click", function(ev){
    var t = ev.target.closest ? ev.target.closest("[data-add],[data-del],#invAddGo,#invCancelItem,#invGo") : null;
    if(!t) return;
    if(t.hasAttribute("data-add")) openForm(Number(t.getAttribute("data-add")));
    else if(t.hasAttribute("data-del")){
      INV.cart.splice(Number(t.getAttribute("data-del")), 1);
      renderCart();
    }
    else if(t.id === "invAddGo") confirmAdd();
    else if(t.id === "invCancelItem"){
      $("#invItemForm").style.display = "none";
      INV.prod = null;
    }
    else if(t.id === "invGo") createInv();
  });
  document.addEventListener("input", function(ev){
    var id = ev.target && ev.target.id;
    if(id === "invQ") onSearch();
    else if(id === "invQty" || id === "invPrice" || id === "invBox" || id === "invLen") showPreview();
  });
  document.addEventListener("keydown", function(ev){
    if(ev.key !== "Enter") return;
    if(ev.target && ev.target.id === "invQty" && $("#invItemForm").style.display !== "none") confirmAdd();
  });
  document.addEventListener("click", function(ev){
    var p = ev.target.closest ? ev.target.closest("[data-invpdf]") : null;
    if(p){ ev.preventDefault(); dlPdf(p.getAttribute("data-invpdf"), p); return; }
    if(ev.target && ev.target.id === "invArchMore") loadArch(false);
  });
  document.addEventListener("input", function(ev){
    if(ev.target && ev.target.id === "invArchQ"){
      clearTimeout(archTimer);
      archTimer = setTimeout(function(){ loadArch(true); }, 300);
    }
  });
  window.loadInvoice = loadInvoice;
  renderCart();
})();"""


_PWA_DESKTOP_CSS = r"""/* ═══ v88.1: عرض سطح المكتب — نفس التطبيق بمساحة أرحب ═══ */
@media (min-width: 900px){
  main{max-width:960px;padding:20px 20px 96px}
  nav.tabs{left:50%;right:auto;transform:translateX(-50%);max-width:780px;
    border:1px solid var(--line);border-bottom:none;border-radius:16px 16px 0 0}
  .grid{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
  button.btn:hover,a.btn:hover{filter:brightness(1.06)}
  nav.tabs button:hover{color:var(--brand)}
  #v-oil .invGrid{grid-template-columns:repeat(4,1fr)}
}
"""

_PWA_HTML_PATCHES = (
    # 1) إصدار الصفحة يُحقن من الخادم وقت كل طلب (ماركر __PWA_VER__) — لا انحراف أبداً
    ('const APP_VER = "v74";',
     'const APP_VER = "__PWA_VER__";'),
    # 2) الرفض يُقرأ من localStorage — ينجو من إعادة التحميل (كان متغير ذاكرة يموت)
    ('S.updDismissed = "";',
     'S.updDismissed = (function(){try{return localStorage.getItem("pwa_upd_seen")||""}catch(e){return ""}})();'),
    # 3) «نعم، حدّث الآن» يحفظ الإصدار قبل إعادة التحميل — لا حلقة حتى لو عادت نسخة قديمة
    ('function doAppUpdate() {\n  toast("جارٍ التحديث…");',
     'function doAppUpdate() {\n  try { localStorage.setItem("pwa_upd_seen", S.serverVersion || ""); } catch (e) {}\n  toast("جارٍ التحديث…");'),
    # 4) «لاحقاً» يحفظ الإصدار في localStorage — إشعار واحد كحد أقصى لكل إصدار لكل جهاز
    ('  S.updDismissed = S.serverVersion || "";',
     '  S.updDismissed = S.serverVersion || "";\n  try { localStorage.setItem("pwa_upd_seen", S.updDismissed); } catch (e) {}'),
    # 5) شاشة الإعدادات تعرض الإصدار الحقيقي بدل «v74» المجمّد
    ('$("#setAbout").textContent = "الإصدار v74" + (S.serverVersion ? " — الخادم " + S.serverVersion : "");',
     '$("#setAbout").textContent = "الإصدار " + APP_VER + (S.serverVersion ? " — الخادم " + S.serverVersion : "");'),
    # 6) v81: تنبيه القائمة المقتطعة — لا اختفاء صامت للسلع بعد سقف العرض
    ('S.invData = d.products || [];',
     'S.invData = d.products || [];\n'
     '    try {\n'
     '      const __tot = d.total | 0, __n = (d.products || []).length;\n'
     '      const __oh = document.getElementById("invTruncHint");\n'
     '      if (__oh) __oh.remove();\n'
     '      if (__tot > __n && __n > 0) {\n'
     '        const h = document.createElement("div");\n'
     '        h.id = "invTruncHint";\n'
     '        h.style.cssText = "background:#fff7e6;border:1px solid #f0c36d;color:#8a5b00;padding:8px 10px;border-radius:10px;margin:0 0 8px;font-size:13px";\n'
     '        h.textContent = "يُعرض أول " + __n + " من أصل " + __tot + " سلعة — استخدم البحث بالاسم للوصول إلى البقية.";\n'
     '        box.insertBefore(h, box.firstChild);\n'
     '      }\n'
     '    } catch (_e) {}'),
    # 7) v83: مجموعات الدردشة — الحقن قبل إغلاق الجسم (الدوال عالمية فتعاد تعريفها بأمان)
    ('</script>\n</body>',
     '</script>\n<script id="v83groups">\n' + _PWA_GROUPS_JS + '</script>\n</body>'),
    # 8) v84: واجهة UI 2.0 — الطبقة البصرية (CSS) بعد إغلاق الستايل الأصلي
    ('</style>',
     '</style>\n<style id="ui2css">' + _PWA_UI2_CSS + '</style>'),
    # 9) v84: واجهة UI 2.0 — طبقة الذكاء (JS) بعد سكربت المجموعات
    ('</script>\n</body>',
     '</script>\n<script id="ui2js">\n' + _PWA_UI2_JS + '</script>\n</body>'),
    # ── v88: زر 🧾 فاتورة بدل 🔧 الزيت — نفس المعرفات (tabOil/v-oil) ──
    #    حتى يبقى التنقل والأمان كما هما، والمحتوى الجديد يُحقن من الخادم
    # 10) نص التاب: الزيت ← فاتورة
    ('<button data-v="v-oil" id="tabOil"><span class="ic">🔧</span>الزيت</button>',
     '<button data-v="v-oil" id="tabOil"><span class="ic">🧾</span>فاتورة</button>'),
    # 11) شاشة الزيت ← شاشة إنشاء الفاتورة الاحترافية (CSS + HTML + JS مضمّنة)
    ('<section class="view" id="v-oil">\n'
     '      <h2 class="vt">زيت الرافعة الشوكية</h2>\n'
     '      <div id="oilBox"><p class="empty">جارِ التحميل...</p></div>\n'
     '    </section>',
     '<section class="view" id="v-oil">'
     '<h2 class="vt">إنشاء فاتورة</h2>'
     '<style>' + _PWA_INVOICE_CSS + '</style>'
     + _PWA_INVOICE_SECTION_HTML
     + '<script>' + _PWA_INVOICE_JS + '</script>'
     + '</section>'),
    # 12) بوابة ظهور التاب: صلاحية الفواتير (مدير/بائع) مع احتياط القديمة
    ('$("#tabOil").style.display = S.perms.oil ? "" : "none";',
     '$("#tabOil").style.display = (S.perms.invoice || S.perms.oil) ? "" : "none";'),
    # 13) فتح الشاشة يحمّل الفاتورة بدل الزيت
    ('if (id === "v-oil") loadOil();',
     'if (id === "v-oil") loadInvoice();'),
    # ── v88.1: عرض مريح على شاشات الحاسوب (نفس الرابط يعمل في كروم/إيدج/فايرفوكس) ──
    # 14) CSS سطح المكتب قبل طبقة ذكاء UI2 (المرساة تولدها الرقعة 9 — الترتيب مهم)
    ('<script id="ui2js">',
     '<style id="dtcss">' + _PWA_DESKTOP_CSS + '</style>\n<script id="ui2js">'),
)


def _pwa_patch_html(body):
    """v79: يطبّق رقع آلية التحديث الداخلي على pwa.html قبل تقديمه للمتصفح.
    الهدف: إصلاح حلقة إشعار «يوجد تحديث جديد» من bot.py وحده — ملف pwa.html على
    القرص لا يحتاج أي تعديل أبداً، وأي نسخة قديمة/جديدة تُقدَّم مصحّحة تلقائياً."""
    out = body
    for old, new in _PWA_HTML_PATCHES:
        ob = old.encode("utf-8")
        nb = new.encode("utf-8")
        n = out.count(ob)
        if n == 1:
            out = out.replace(ob, nb)
        else:
            logging.warning("[PWA][v79] تخطّي رقعة (مرساة x%s): %s", n, old[:50])
    return out


def _pwa_read_html():
    """يقرأ pwa.html من مجلد bot.py مع كاش حسب تاريخ التعديل."""
    global _pwa_html_cache
    try:
        mtime = os.path.getmtime(PWA_HTML_PATH)
        if _pwa_html_cache["mtime"] != mtime or _pwa_html_cache["body"] is None:
            with open(PWA_HTML_PATH, "rb") as f:
                _pwa_html_cache = {"mtime": mtime, "body": _pwa_patch_html(f.read())}
        return _pwa_html_cache["body"].replace(b"__PWA_VER__", PWA_VERSION.encode("utf-8"))
    except Exception as e:
        logging.warning("[PWA] pwa.html missing: " + str(e))
        return ("<!doctype html><html dir=rtl lang=ar><meta charset=utf-8>"
                "<body style='font-family:sans-serif;text-align:center;padding:40px'>"
                "<h2>⚠️ ملف pwa.html غير موجود بجانب bot.py</h2></body>").encode("utf-8")


def _pwa_icon_bytes():
    """v88: أيقونة/غلاف التطبيق من القاعدة (الشعار لم يعد داخل bot.py)."""
    global _pwa_icon_cache
    if _pwa_icon_cache is None:
        try:
            _pwa_icon_cache = _theme_logo_load() or b""
        except Exception:
            _pwa_icon_cache = b""
    return _pwa_icon_cache


# ── كلمات المرور: scrypt بملح عشوائي ──

def _pwa_hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "s1$" + salt.hex() + "$" + dk.hex()


def _pwa_verify_password(password, stored):
    try:
        _, salt_hex, hash_hex = str(stored).split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
        return _pwa_hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# إيموجي/رموز تُشال من أسماء الدخول: أعلام الدول والرموز ومحددات العرض وZWJ —
# أسماء تلغرام تحملها («يوسف 🇩🇿») وإدخالها من لوحة مفاتيح الهاتف شبه مستحيل
_PWA_EMOJI_RX = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F\U0000200D\U000020E3]+")


def _pwa_norm_username(u):
    return re.sub(r"[\s@]+", "", _PWA_EMOJI_RX.sub("", str(u or ""))).lower()


def _pwa_init_tables():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(PWA_SCHEMA_SQL)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    # v52: دمج المكرر عند الإقلاع — نفس الشخص من التطبيق ومن تلجرام = حساب واحد
    try:
        _pwa_merge_duplicate_users()
    except Exception as e:
        logging.error("[PWA][v52] startup merge skipped: %s", e)


def _norm_name_key(value):
    """v53: مفتاح اسم مُطبَّع للمقارنة — مسافة غير قياسية/تطويل/همزات/تاء مربوطة
    تُوحَّد حتى لا يفوت الدمج التلقائي بسبب فرق إملائي بسيط."""
    if not value or not str(value).strip():
        return ""
    s = str(value).replace("\u00a0", " ").replace("\u200f", "").replace("\u200e", "")
    s = s.replace("ـ", "")
    s = " ".join(s.strip().split()).lower()
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        s = s.replace(a, b)
    return s


def _merge_display_name(u):
    """أفضل اسم معروض لصف مستخدم (للعرض والمقارنة)."""
    for v in (u.get("display_name"), u.get("first_name"), u.get("username")):
        if v and str(v).strip():
            return str(v).strip()
    return "#" + str(u.get("id") or "")


def _esc_h(s):
    """تهريب HTML للبطاقات (بوت تلغرام)."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _merge_two_users(dst_id, src_id, delete_src):
    """v53: دمج حساب (المصدر) في حساب آخر (الناجي) — ينقل كل السجلات إليه.
    يعيد قاموس عدّادات ما نُقل، ويرفع استثناء عند أي مانع.
    • delete_src=True → حذف صف المصدر (حسابات التطبيق بلا تلجرام فقط، حارس SQL).
    • delete_src=False → إيقاف المصدر ووسمه merged_into (حساب تلجرام حقيقي
      لا يُحذف حتى لا يعيد /start إنشاءه مكرراً)."""
    dst_id, src_id = int(dst_id), int(src_id)
    if dst_id == src_id:
        raise RuntimeError("لا يمكن دمج الحساب في نفسه")
    conn = get_connection()
    counts = {}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, telegram_id, role, active FROM users WHERE id IN (%s, %s)",
                        (dst_id, src_id))
            found = {int(r["id"]): r for r in cur.fetchall()}
        if dst_id not in found or src_id not in found:
            raise RuntimeError("أحد الحسابين غير موجود")
        for r in found.values():
            if r["role"] == "admin" or _protected_account(r["telegram_id"]):
                raise RuntimeError("لا يمكن دمج حساب إداري/محمي")
        with conn.cursor() as cur:
            # دخول التطبيق: إن للناجي رقم سرّاً أبقيناه وإلا انتقل رقم المدمج
            cur.execute("SELECT 1 FROM pwa_auth WHERE user_id = %s", (dst_id,))
            if cur.fetchone():
                cur.execute("DELETE FROM pwa_auth WHERE user_id = %s", (src_id,))
            else:
                cur.execute("UPDATE pwa_auth SET user_id = %s WHERE user_id = %s", (dst_id, src_id))
            # استكمال بيانات الهوية الناقصة في الناجي من المدموج —
            # تُمسح من المصدر أولاً حتى لا يصطدم فهرس email/phone الفريد
            cur.execute(
                "SELECT email, phone, avatar, identity_verified, display_name "
                "FROM users WHERE id = %s", (src_id,))
            s_email, s_phone, s_avatar, s_verified, s_display = cur.fetchone()
            cur.execute("UPDATE users SET email = NULL, phone = NULL WHERE id = %s", (src_id,))
            cur.execute(
                "UPDATE users SET "
                "email = COALESCE(email, %s), "
                "phone = COALESCE(phone, %s), "
                "avatar = COALESCE(avatar, %s), "
                "identity_verified = (identity_verified OR %s), "
                "display_name = COALESCE(NULLIF(display_name, ''), %s) "
                "WHERE id = %s",
                (s_email, s_phone, s_avatar, bool(s_verified), s_display, dst_id))
            for sql, prm in [
                ("UPDATE pwa_sessions SET user_id = %s WHERE user_id = %s", (dst_id, src_id)),
                ("UPDATE pwa_remember SET user_id = %s WHERE user_id = %s", (dst_id, src_id)),
                ("UPDATE pwa_otp SET user_id = %s WHERE user_id = %s", (dst_id, src_id)),
                ("UPDATE pwa_registrations SET user_id = %s WHERE user_id = %s", (dst_id, src_id)),
                ("UPDATE notification_reads SET user_id = %s WHERE user_id = %s "
                 "AND NOT EXISTS (SELECT 1 FROM notification_reads WHERE user_id = %s)",
                 (dst_id, src_id, dst_id)),
                ("DELETE FROM notification_reads WHERE user_id = %s", (src_id,)),
            ]:
                cur.execute(sql, prm)
            # المحادثات: المرسل ثم المستقبل — عدّاد الرسائل المنقولة
            cur.execute("UPDATE chat_messages SET sender_id = %s WHERE sender_id = %s",
                        (dst_id, src_id))
            n_msg = cur.rowcount
            cur.execute("UPDATE chat_messages SET recip_id = %s WHERE recip_id = %s",
                        (dst_id, src_id))
            counts["messages"] = n_msg + cur.rowcount
            # رسائل صارت من الناجي إلى نفسه (محادثة الحسابين معاً) تُحذف
            cur.execute("DELETE FROM chat_messages WHERE sender_id = %s AND recip_id = %s",
                        (dst_id, dst_id))
            for sql, key, prm in [
                ("UPDATE transactions SET user_id = %s WHERE user_id = %s",
                 "movements", (dst_id, src_id)),
                ("UPDATE extraction_requests SET requester_id = %s WHERE requester_id = %s",
                 "extractions", (dst_id, src_id)),
                ("UPDATE incoming_orders SET requester_id = %s WHERE requester_id = %s",
                 "supply_orders", (dst_id, src_id)),
                ("UPDATE pending_deliveries SET user_id = %s WHERE user_id = %s",
                 None, (dst_id, src_id)),
                ("UPDATE audit_log SET user_id = %s WHERE user_id = %s",
                 None, (dst_id, src_id)),
            ]:
                cur.execute(sql, prm)
                if key:
                    counts[key] = cur.rowcount
            cur.execute("UPDATE invoices SET worker_id = %s WHERE worker_id = %s", (dst_id, src_id))
            n_inv = cur.rowcount
            cur.execute("UPDATE invoices SET admin_id = %s WHERE admin_id = %s", (dst_id, src_id))
            counts["invoices"] = n_inv + cur.rowcount
            if delete_src:
                # حارس صارم: صف حساب التطبيق فقط (بلا تلجرام) يُحذف
                cur.execute("DELETE FROM users WHERE id = %s AND telegram_id IS NULL", (src_id,))
                if cur.rowcount != 1:
                    raise RuntimeError("الحساب المدموج حساب تلجرام — يُوقف ولا يُحذف")
                counts["removed"] = True
            else:
                cur.execute("UPDATE users SET active = FALSE, merged_into = %s WHERE id = %s",
                            (dst_id, src_id))
                counts["removed"] = False
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def _pwa_merge_duplicate_users():
    """v52/v53: دمج حساب التطبيق (telegram_id IS NULL) في حساب تلجرام بنفس الاسم.
    نفس الشخص كان يسجّل من التطبيق ومن تلجرام فيظهر مرتين في الرسائل
    (بلاغ المدير: «تداخل بين مستخدمي بوت تلغرام ومستخدمي التطبيق»).
    القواعد:
    • الناجي دائماً حساب تلجرام (مرساة الهوية الحقيقية) — «حتى ولو موجود في تلجرام».
    • من لا اسم له في تلجرام يبقى كما هو — «إلا إذا كان شخصاً جديداً كلياً».
    • تطابق الاسم مع أكثر من حساب تلجرام → لا دمج تلقائي — يعالجها المدير يدوياً
      بزر «🧬 دمج المكرر» في لوحة المستخدمين (v53).
    • لا دمج أبداً في حساب مدير ولا بين حسابين من تلجرام تلقائياً.
    ينتقل كل شيء إلى الناجي: دخول التطبيق/الجلسات/التذكير/رموز OTP/المحادثات/
    حركات المخزون/طلبات الاستخراج/طلبات التوريد/الفواتير/الإشعارات المقروءة."""
    conn = get_connection()
    moved = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, telegram_id, username, first_name, display_name, role, active FROM users")
            rows = cur.fetchall()

        tg = {}
        for r in rows:
            if r["telegram_id"] is not None and r["active"] and r["role"] != "admin":
                n = _norm_name_key(_merge_display_name(r))
                if n:
                    tg.setdefault(n, []).append(int(r["id"]))
        pairs = []
        for a in rows:
            if a["telegram_id"] is not None or not a["active"]:
                continue
            n = _norm_name_key(_merge_display_name(a))
            cands = tg.get(n) or []
            if n and len(cands) == 1:
                pairs.append((int(a["id"]), cands[0], n))
        for src, dst, nm in pairs:
            try:
                _merge_two_users(dst, src, delete_src=True)
                moved.append((src, dst, nm))
                logging.info("[PWA][v53] merged app user %s into telegram user %s (name=%s)", src, dst, nm)
            except Exception as e:
                conn.rollback()
                logging.error("[PWA][v53] merge failed src=%s dst=%s: %s", src, dst, e)
        return moved
    finally:
        release_connection(conn)


# ═══════════ حزمة الحماية v29: حصار التخمين + سجل تدقيق + تنبيه المالك ═══════════

_PWA_LOGIN_MAX_FAILS = 5     # محاولات فاشلة قبل القفل
_PWA_LOGIN_WINDOW = 600      # نافذة عدّ المحاولات: 10 دقائق
_PWA_LOGIN_LOCKOUT = 900     # مدة القفل: 15 دقيقة
_PWA_LOGIN_FAILS = {}        # مفتاح ip|اسم → {"marks": [ts], "until": ts, "alerted": bool}
_PWA_LOGIN_LOCK = threading.Lock()
_PWA_LOCK_ALERT_TEXT = "تم رصد محاولات دخول فاشلة متكررة على تطبيق الويب — يُحتمل أنها تخمين كلمات مرور:"


class PwaLocked(Exception):
    """يُرفع عند حصار محاولات الدخول — يحمل ثواني الانتظار المتبقية."""
    def __init__(self, retry_after):
        super().__init__("locked")
        self.retry_after = int(retry_after)


def _pwa_login_gate(key):
    """يعيد True إن سُمح بالمحاولة، وإلا يرفع PwaLocked."""
    now = time.time()
    with _PWA_LOGIN_LOCK:
        st = _PWA_LOGIN_FAILS.get(key)
        if st and st.get("until") and now < st["until"]:
            raise PwaLocked(st["until"] - now + 1)
    return True


def _pwa_login_fail(key):
    """يسجّل محاولة فاشلة. يعيد True فقط عند لحظة تفعيل القفل (لتنبيه المالك)."""
    now = time.time()
    with _PWA_LOGIN_LOCK:
        # تنظيف: إسقاط مفاتيح قديمة منتهية
        for k in list(_PWA_LOGIN_FAILS.keys()):
            s = _PWA_LOGIN_FAILS[k]
            if (not s.get("marks") or now - s["marks"][-1] > 3600) and \
               (not s.get("until") or now > s["until"]):
                del _PWA_LOGIN_FAILS[k]
        st = _PWA_LOGIN_FAILS.setdefault(key, {"marks": [], "until": 0, "alerted": False})
        st["marks"] = [m for m in st["marks"] if now - m < _PWA_LOGIN_WINDOW]
        st["marks"].append(now)
        if len(st["marks"]) >= _PWA_LOGIN_MAX_FAILS:
            if now >= st.get("until", 0):
                st["until"] = now + _PWA_LOGIN_LOCKOUT
            if not st.get("alerted"):
                st["alerted"] = True
                return True
    return False


def _pwa_login_clear(key):
    with _PWA_LOGIN_LOCK:
        _PWA_LOGIN_FAILS.pop(key, None)


AUDIT_LABELS = {
    "login": "✅ دخول ناجح", "login_fail": "❌ دخول فاشل",
    "lockout": "🔒 رفض (حساب مقفول)", "brute_force_lock": "🚨 قفل محاولات تخمين",
    "setup": "🛠 إعداد أول مدير", "changepass": "🔑 تغيير كلمة مرور",
    "webpass_set": "🔑 ضبط كلمة موظف", "user_toggle": "🚫 تفعيل/طرد مستخدم",
    "qty_add": "➕ إضافة كمية", "qty_remove": "➖ إخراج كمية", "qty_reconcile": "⚖️ جرد",
    "product_create": "📦 سلعة جديدة", "incoming_create": "🚚 طلب توريد",
    "incoming_advance": "🚚 تحديث توريد",
    # ── v32: الهوية والتسجيل الذاتي والرمز السري ──
    "register": "🆕 طلب انضمام جديد", "register_fail": "❌ طلب انضمام مرفوض",
    "register_delete": "🗑 حذف طلب انضمام", "register_delete_fail": "⚠️ فشل حذف طلب انضمام",
    "register_confirm": "✅ تأكيد انضمام عبر البوت",
    "otp_request": "📲 طلب رمز دخول", "otp_request_fail": "❌ طلب رمز لمعرف غير معروف",
    "google_login": "🔐 دخول بحساب Google", "google_login_fail": "🚫 دخول Google مرفوض",
    "google_join_start": "🆕 بداية انضمام عبر Google", "google_join": "🆕 طلب انضمام (Google)",
    "registration_approve": "✅ تفعيل عضوية من التطبيق", "registration_approve_fail": "❌ فشل تفعيل عضوية",
    "otp_login": "✅ دخول برمز سري", "otp_fail": "❌ رمز سري خاطئ",
    "user_role": "🎯 تغيير فئة مستخدم", "user_role_fail": "⚠️ فشل تغيير فئة",
}


def _audit_log(action, user=None, details="", ip=""):
    """سجل تدقيق مقاوم للأعطال — لا يكسر العملية الأساسية أبداً."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log (user_id, username, action, details, ip) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ((user or {}).get("id"),
                     str((user or {}).get("name") or (user or {}).get("web_username") or "")[:200],
                     str(action)[:60], str(details or "")[:500], str(ip or "")[:64]))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    except Exception:
        pass


def _audit_recent(limit=15):
    """آخر أحداث التدقيق (متزامن)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT ts, username, action, details, ip FROM audit_log "
                "ORDER BY ts DESC LIMIT %s", (max(1, min(int(limit), 50)),))
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        release_connection(conn)


# ═══════════ نظام الإشعارات الموحّد (v30): قاعدة بيانات + بث تلغرام ═══════════
# كل عملية (سلعة جديدة / إضافة / إخراج / مخزون منخفض) من أي طرف
# (بوت تلغرام أو تطبيق الويب) تُسجّل في جدول notifications — تظهر في جرس
# التطبيق للجميع — وتُبثّ عبر تلغرام لكل المستخدمين النشطين: تزامن تام.

def db_insert_notification(kind, title, body, actor="", source="bot"):
    """إدراج إشعار في القاعدة — مقاوم للأعطال مثل _audit_log، لا يرفع استثناءات."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notifications (kind, title, body, actor, source) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (str(kind)[:20], str(title)[:300], str(body or "")[:1000],
                     str(actor or "")[:200], str(source or "bot")[:10]))
                nid = cur.fetchone()[0]
            conn.commit()
            return nid
        except Exception:
            conn.rollback()
            return None
        finally:
            release_connection(conn)
    except Exception:
        return None


def _product_name_fast(pid):
    """اسم السلعة من معرفها — لرسائل الإشعار فقط، مقاوم للأعطال."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM products WHERE id = %s", (pid,))
                r = cur.fetchone()
                return str(r[0]) if r else "سلعة #" + str(pid)
        finally:
            release_connection(conn)
    except Exception:
        return "سلعة #" + str(pid)


def _notify_recipients():
    """معرفات تلغرام لكل المستخدمين النشطين (نفس قاعدة _broadcast_to_all_users)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND role IN "
                        "('admin', 'manager', 'stock', 'seller', 'external', 'customer') "
                        "AND active = TRUE")
            return cur.fetchall()
    except Exception:
        return []
    finally:
        release_connection(conn)


# v66: الحركات الروتينية عبر التطبيق تصلح تلغرام — التنبيه التلغرامي للمميزة فقط
# (↩️ إرجاع من زبون / 🚚 وصول شاحنة / 💰 بيع لرابح / 💰 بيع لحسان).
# الباقي (بيع لزبون، تصحيح مخزون، جرد…) يُسجَّل في نظام الحركة والإشعارات
# الداخلية للتطبيق كالمعتاد — دون إزعاج تلغرام.
def _pwa_tg_worthy_reason(reason):
    _r = str(reason or "")
    return any(k in _r for k in ("شاحنة", "إرجاع", "ارجاع", "رابح", "حسان"))


async def notify_everyone(kind, title, body, actor="", source="web", tg=True):
    """(v30) تسجيل إشعار في القاعدة ثم بثه تلغرامياً للجميع —
    تزامن تام بين تطبيق الويب وبوت تلغرام. آمنة للاستدعاء من أي سياق.
    v66: tg=False يسجّل الإشعار داخلياً (جرس التطبيق) دون بث تلغرامي."""
    try:
        await _pwa_run(db_insert_notification, kind, title, body, actor, source)
    except Exception:
        pass
    if TG_APP is None or not tg:
        return
    text = str(title) + "\n\n" + str(body or "") + \
        "\n\n👤 " + (str(actor) if actor else "موظف") + " • عبر التطبيق 📱"
    try:
        rows = await _pwa_run(_notify_recipients)
        for r in rows or []:
            try:
                await TG_APP.bot.send_message(chat_id=r[0], text=text)
            except Exception:
                pass
            await asyncio.sleep(0.05)
    except Exception as e:
        logging.warning("[NOTIFY] broadcast failed: " + str(e))


async def _pwa_alert_owner(text):
    """تنبيه المالك عبر تلغرام عند الأحداث الأمنية الخطيرة."""
    if TG_APP is None:
        return
    try:
        owner_ids = [int(t) for t in (OWNER_IDS or [])][:3]
    except Exception:
        owner_ids = []
    for tid in owner_ids:
        try:
            await TG_APP.bot.send_message(chat_id=tid, text=text)
        except Exception as e:
            logging.error("[PWA] owner alert failed: " + str(e))


def _pwa_password_ok(p):
    """سياسة الكلمات: 8+ أحرف تجمع حروف وأرقاماً — تعطب القواميس والتخمين."""
    p = str(p or "")
    if len(p) < 8:
        return False, "كلمة المرور يجب أن تكون 8 أحرف على الأقل."
    if not any(ch.isalpha() for ch in p):
        return False, "كلمة المرور يجب أن تحوي حروفاً أبجدية."
    if not any(ch.isdigit() for ch in p):
        return False, "كلمة المرور يجب أن تحوي أرقاماً."
    return True, ""


def pwa_set_password(user_id, username, password):
    """زرع/تحديث كلمة مرور ويب لمستخدم + إلغاء جلساته القديمة."""
    username = _pwa_norm_username(username)
    if not username:
        username = "u" + str(user_id)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # تفرد اسم المستخدم: إن كان محجوزاً لغيره نلحقة بمعرّفه
            cur.execute("SELECT user_id FROM pwa_auth WHERE username = %s", (username,))
            row = cur.fetchone()
            if row and row[0] != user_id:
                username = username + "_" + str(user_id)
            cur.execute(
                "INSERT INTO pwa_auth (user_id, username, pass_hash, updated_at) "
                "VALUES (%s, %s, %s, NOW()) "
                "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, "
                "pass_hash = EXCLUDED.pass_hash, updated_at = NOW()",
                (user_id, username, _pwa_hash_password(password)),
            )
            cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (user_id,))
        conn.commit()
        return username
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


def pwa_bootstrap_admin():
    """زرع اختياري لكلمة مرور المدير أول مرة عبر متغير البيئة PWA_ADMIN_PASSWORD.
    آمن ضد إعادة التشغيل: لا يكتب أبداً فوق مدير لديه كلمة مرور بالفعل —
    والمسار الأساسي الآن هو شاشة «الإعداد الأول» من /api/setup."""
    pwd = os.getenv("PWA_ADMIN_PASSWORD", "").strip()
    if not pwd:
        return
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT u.id, COALESCE(u.username, u.first_name) FROM users u "
                    "WHERE u.role = 'admin' AND u.active "
                    "AND NOT EXISTS (SELECT 1 FROM pwa_auth a WHERE a.user_id = u.id) "
                    "ORDER BY u.id LIMIT 1")
                row = cur.fetchone()
            conn.commit()
        finally:
            release_connection(conn)
        if not row:
            logging.info("[PWA] PWA_ADMIN_PASSWORD: كل المدراء لديهم كلمة مرور — تخطّي (حماية من الكتابة فوقها)")
            return
        uname = pwa_set_password(row[0], row[1] or "admin", pwd)
        logging.info("[PWA] تم زرع كلمة مرور ويب للمدير: " + uname)
    except Exception as e:
        logging.warning("[PWA] bootstrap failed: " + str(e))


def _pwa_create_session(user_id):
    token = _pwa_secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pwa_sessions (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, NOW() + (%s * INTERVAL '1 day'))",
                (token_hash, user_id, PWA_SESSION_DAYS),
            )
            # عناية الجلسات: إسقاط المنتهية وحدّ أعلى 10 جلسات لكل مستخدم
            # (يمنع تضخم الجدول وسيء الاستخدام)
            cur.execute("DELETE FROM pwa_sessions WHERE expires_at < NOW()")
            cur.execute(
                "DELETE FROM pwa_sessions WHERE user_id = %s AND token_hash NOT IN ("
                "SELECT token_hash FROM pwa_sessions WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT 10)", (user_id, user_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return token


def _pwa_kill_sessions(user_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        release_connection(conn)


def _pwa_session_user(request):
    """يقرأ الكوكي ويرجع dict المستخدم الكامل أو None (فحص في executor).
    v58: يحدّث last_seen (بخانقة 45 ثانية) لحالة «متصل الآن» في الرسائل."""
    token = request.cookies.get(PWA_COOKIE, "")
    if not token or len(token) > 128:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.display_name, u.avatar, a.username AS web_username, "
                "u.last_seen "
                "FROM pwa_sessions s JOIN users u ON u.id = s.user_id "
                "LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE s.token_hash = %s AND s.expires_at > NOW()",
                (token_hash,))
            row = cur.fetchone()
            if row:
                # v58: نبض الحضور — كتابة واحدة كحد أقصى كل 45 ثانية لكل مستخدم
                try:
                    ls = row.get("last_seen")
                    _now = datetime.datetime.now(datetime.timezone.utc)
                    if ls and ls.tzinfo is None:
                        ls = ls.replace(tzinfo=datetime.timezone.utc)
                    if not ls or (_now - ls).total_seconds() > 45:
                        cur.execute("UPDATE users SET last_seen = NOW() WHERE id = %s",
                                    (row["id"],))
                        row["last_seen"] = _now
                except Exception:
                    conn.rollback()   # v58: أي خلل في النبض لا يُسقط الجلسة
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        conn.commit()
        if not row:
            return None
        d = dict(row)
        # v41: الاسم المعروض أولاً (يُعدَّل من الإعدادات) ثم اسم تلغرام ثم اسم الدخول
        d["name"] = (d.get("display_name") or d.get("first_name") or d.get("tg_username")
                     or d.get("web_username") or str(d.get("telegram_id")))
        return d
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


# ── منطق API (دوال متزامنة تُستدعى في executor) ──

def _pwa_api_login_email(email, password):
    """v39: دخول بالبريد (حسابات Google والقديمة) + الرقم السري.
    رسائل واضحة: «الرقم السري خاطئ» / لا حساب / بانتظار موافقة الإدارة.
    v39: رُفعت بوابة «تأكيد البريد» نهائياً — لا رموز بريد بعد اليوم."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.identity_verified, a.pass_hash "
                "FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s LIMIT 1", (email,))
            row = cur.fetchone()
            if row is None:
                # صاحب البريد موظف قائم بلا رقم سري محفوظ؟
                cur.execute("SELECT 1 FROM users WHERE lower(email) = %s LIMIT 1", (email,))
                if cur.fetchone():
                    return False, None, ("لا يوجد رقم سري محفوظ لحسابك — "
                                         "اطلب من الإدارة ضبط رقم سري (زر 🔑) أو ادخل بحساب Google.")
                # طلب انضمام (Google) منتظر الموافقة؟
                cur.execute("SELECT 1 FROM pwa_registrations "
                            "WHERE lower(email) = %s AND status = 'waiting' LIMIT 1", (email,))
                if cur.fetchone():
                    return False, None, ("طلب تسجيلك في انتظار موافقة الإدارة — "
                                         "ستدخل التطبيق فور موافقتها.")
                return False, None, ("لا يوجد حساب بهذا البريد الإلكتروني — "
                                     "أنشئ حساباً من «حساب جديد».")
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] email login db error: " + str(e))
        return False, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    # v39: فحص الرقم السري أولاً دائماً — بلا بوابة «تأكيد البريد» (لا رموز بريد بعد اليوم)
    if not _pwa_verify_password(password, row["pass_hash"]):
        time.sleep(0.5)  # إبطاء بسيط لتخشين محاولات التخمين
        return False, None, "الرقم السري خاطئ."
    if not row["active"]:
        return False, None, KICK_NOTICE
    if row["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, None, "لا يوجد وصول ويب لفئتك حالياً — راجع الإدارة."
    token = _pwa_create_session(row["id"])
    user = {"id": row["id"], "telegram_id": row["telegram_id"],
            "name": row["first_name"] or row["tg_username"] or email,
            "role": row["role"], "role_label": ROLE_LABELS.get(row["role"], row["role"])}
    return True, (token, user), None


def _pwa_api_login(username, password):
    username = str(username or "").strip()
    if username.startswith("@"):
        username = username[1:]   # v39: القارئ يكتب @ كعادة تلغرام — اسم الدخول بلا @
    password = str(password or "")
    if not username or not password:
        return False, None, "أدخل اسم الدخول (أو البريد) والرقم السري."
    # v34: الدخول بالبريد الإلكتروني + الرقم السري (المسار الأساسي الجديد)
    if "@" in username:
        return _pwa_api_login_email(username.lower(), password)
    username = _pwa_norm_username(username)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # الحسابات قليلة — نجلبها كلها ونطابق مطابقة ذكية: المباشر أولاً
            # ثم المطبّع (يقبل أسماء قديمة مخزّنة بها إيموجي/مسافات)
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, a.username AS auth_username, a.pass_hash "
                "FROM pwa_auth a JOIN users u ON u.id = a.user_id")
            rows = cur.fetchall()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] login db error: " + str(e))
        return False, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    cands = []
    for r in rows:
        au = str(r.get("auth_username") or "")
        if au.lower() == username:
            cands.insert(0, r)  # تطابق مباشر باسم الدخول المخزّن — أولوية
        elif _pwa_norm_username(au) == username:
            cands.append(r)     # تطابق مطبّع (حساب قديم باسم فيه إيموجي)
    row = None
    for r in cands:
        if _pwa_verify_password(password, r["pass_hash"]):
            row = r
            break
    if row is None:
        # v39: طلب انضمام (اسم + رقم سر) بانتظار موافقة الإدارة؟
        conn2 = get_connection()
        try:
            with conn2.cursor() as cur2:
                cur2.execute("SELECT 1 FROM pwa_registrations "
                             "WHERE lower(username) = %s AND status = 'waiting' LIMIT 1",
                             (username.lower(),))
                if cur2.fetchone():
                    return False, None, ("طلب تسجيلك في انتظار موافقة الإدارة — "
                                         "ستدخل التطبيق فور موافقتها.")
        except Exception:
            pass
        finally:
            release_connection(conn2)
        time.sleep(0.5)  # إبطاء بسيط لتخشين محاولات التخمين
        return False, None, "اسم المستخدم أو كلمة المرور غير صحيحة."
    if not row["active"]:
        return False, None, KICK_NOTICE
    if row["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, None, "لا يوجد وصول ويب لفئتك حالياً — راجع الإدارة."
    user_id = row["id"]
    token = _pwa_create_session(user_id)
    user = {"id": user_id, "telegram_id": row["telegram_id"],
            "name": row["first_name"] or row["tg_username"] or username,
            "role": row["role"], "role_label": ROLE_LABELS.get(row["role"], row["role"])}
    return True, (token, user), None


def _pwa_perm_map(role):
    """خريطة صلاحيات الواجهة للفئة — نفس نظام الصلاحيات المشترك."""
    admin = role in (ROLE_ADMIN, ROLE_MANAGER)
    return {
        "admin": admin,
        "search": admin or role_perm_enabled(role, PERM_SEARCH),
        "add_qty": admin or role_perm_enabled(role, PERM_ADD_QTY),
        "remove": admin or role_perm_enabled(role, PERM_REMOVE),
        "movements": admin or role_perm_enabled(role, PERM_MOVEMENTS),
        "low_stock": admin or role_perm_enabled(role, PERM_LOW_STOCK),
        "oil": admin,
        "users": admin,
        # v88: إنشاء الفواتير — للمدراء والبائعين (نفس بوابة فاتورة تلغرام)
        "invoice": admin or role == ROLE_SELLER,
    }


def _pwa_api_me(user):
    return {"ok": True, "user": {
        "name": user["name"], "role": user["role"],
        "role_label": ROLE_LABELS.get(user["role"], user["role"]),
        "web_username": user.get("web_username"),
        "display_name": user.get("display_name") or "",
        "avatar": user.get("avatar") or "",          # v41: صورة الحساب الشخصي
        "telegram_id": user.get("telegram_id"),
    }, "perms": _pwa_perm_map(user["role"])}


# ═══════════ v39: التسجيل بلا بريد — اسم دخول + رقم سر + موافقة الإدارة ═══════════

_PWA_ROLES_SELF = (ROLE_STOCK, ROLE_SELLER)   # الفئات المتاحة للتسجيل الذاتي
_PWA_REG_EXPIRE_HOURS = 48                    # صلاحية عرض الطلب في لوحة الإدارة بالساعات
_PWA_OTP_EXPIRE_MIN = 10                      # صلاحية رمز الدخول بالدقائق
_PWA_OTP_MAX_ATTEMPTS = 5                     # محاولات خاطئة قبل إبطال الرمز
_PWA_OTP_MAX_PER_10MIN = 3                    # سقف طلبات الرموز لكل مستخدم/10 دقائق


def _pwa_norm_phone(p):
    """تطبيع الهاتف: أرقام فقط، يُسقط + و00 ويوحّد 213xxxxxxxx → 0xxxxxxxxx."""
    p = str(p or "").strip().replace(" ", "").replace("-", "").replace(".", "")
    if p.startswith("+"):
        p = p[1:]
    elif p.startswith("00"):
        p = p[2:]
    digits = "".join(ch for ch in p if ch.isdigit())
    if digits.startswith("213") and len(digits) >= 11:
        digits = "0" + digits[3:]
    return digits


def _pwa_self_register_enabled():
    """التسجيل الذاتي من التطبيق — يُعطّل بـ PWA_SELF_REGISTER=0 على Render."""
    return os.getenv("PWA_SELF_REGISTER", "1").strip().lower() not in ("0", "false", "no", "off")


def _pwa_mask_identifier(ident):
    """تعشية المعرف في الردود: a•••@x.com / 05••••12."""
    ident = str(ident or "").strip()
    if len(ident) < 4:
        return "•••"
    if "@" in ident:
        at = ident.find("@")
        return ident[0] + "•••" + ident[at:]
    return ident[:2] + "••••" + ident[-2:]


def _pwa_api_register(name, username_raw, password, role, ip):
    """v39: التسجيل بلا بريد نهائياً — اسم كامل + اسم دخول من اختيار العميل
    + رقم سر قوي + الفئة. الطلب يُحفظ «بانتظار موافقة الإدارة» ويُشعَر المدراء
    فوراً في تلغرام (أزرار موافقة/حذف) وفي جرس التطبيق 🔔.
    بلا SMTP وبلا Brevo وبلا رموز — إعداد صفري.
    يعيد (ok, payload_or_err). فئة المدير لا تُطلب هنا أبداً."""
    if not _pwa_self_register_enabled():
        return False, None, "التسجيل الذاتي معطّل حالياً — راجع إدارة المؤسسة."
    name = str(name or "").strip()
    if len(name) < 3 or len(name) > 120:
        return False, None, "أدخل الاسم الكامل (3 أحرف على الأقل)."
    role = str(role or "").strip()
    if role not in _PWA_ROLES_SELF:
        return False, None, "الفئة المتاحة: بائع أو عامل مخزون فقط."
    username = str(username_raw or "").strip()
    if username.startswith("@"):
        username = username[1:]
    username = re.sub(r"\s+", " ", username)
    if not (3 <= len(username) <= 40):
        return False, None, "اسم الدخول يجب أن يكون من 3 إلى 40 حرفاً."
    if "@" in username:
        return False, None, "اسم الدخول لا يحتاج @ — اختر اسماً بسيطاً مثل: ahmed"
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        return False, None, pol_msg
    uname_l = username.lower()
    conn = get_connection()
    reused_id = None
    try:
        with conn.cursor() as cur:
            # اسم دخول محجوز لحساب مفعّل؟
            cur.execute(
                "SELECT u.id FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s LIMIT 1", (uname_l,))
            row = cur.fetchone()
            if row:
                return False, None, ("اسم الدخول «" + username + "» محجوز مسبقاً — "
                                     "اختر اسماً آخر أو سجّل الدخول به.")
            # طلب سابق ما زال بانتظار الموافقة بنفس الاسم؟ نحدّث بياناته بدل الازدواج
            cur.execute(
                "SELECT id FROM pwa_registrations "
                "WHERE lower(username) = %s AND status = 'waiting' LIMIT 1", (uname_l,))
            row = cur.fetchone()
            if row:
                reused_id = int(row[0])
                cur.execute(
                    "UPDATE pwa_registrations SET full_name = %s, role = %s, "
                    "password_hash = %s, created_at = NOW() WHERE id = %s",
                    (name, role, _pwa_hash_password(str(password)), reused_id))
                reused = True
            else:
                reused = False
                code = "PW" + _pwa_secrets.token_hex(2).upper()
                cur.execute(
                    "INSERT INTO pwa_registrations "
                    "(code, full_name, username, role, status, password_hash, ip) "
                    "VALUES (%s, %s, %s, %s, 'waiting', %s, %s) RETURNING id",
                    (code, name, username, role, _pwa_hash_password(str(password)), ip))
                reused_id = int(cur.fetchone()[0])
        conn.commit()
    except Exception as e:
        conn.rollback()
        msg = str(e)
        if "pwa_reg_username_uniq" in msg:
            return False, None, ("لديك طلب انضمام بنفس اسم الدخول ما زال بانتظار "
                                 "الموافقة — انتظر قرار الإدارة أو اختر اسماً آخر.")
        logging.error("[PWA] register failed: " + msg)
        return False, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    # إشعار مزدوج بلا أي إعداد: جرس التطبيق 🔔 + رسالة تلغرام بأزرار للمدراء
    db_insert_notification(
        "reg", "🔔 طلب انضمام جديد: " + name,
        "اسم الدخول: " + username + " — الفئة المطلوبة: "
        + ROLE_LABELS.get(role, role) + " — بانتظار موافقة الإدارة.",
        actor=name, source="web")
    # v40: جدولة آمنة من خيط executor — create_task هنا كان يُسقط الطلب بعد نجاح الحفظ
    if TG_APP is not None:
        _pwa_schedule(_notify_admins_pwa_registration(
            reused_id, name, username, ROLE_LABELS.get(role, role)))
    return True, {"username": username, "reg_id": reused_id, "reused": reused}, None


def _pwa_api_registrations_list(user):
    """قائمة طلبات التسجيل المنتظرة (للمدير فقط) — ضمن /api/users.
    v39: تُظهر اسم الدخول أيضاً."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, code, full_name, username, email, phone, role, password_hash, "
                "created_at::text AS created_at, ip FROM pwa_registrations "
                "WHERE status = 'waiting' AND created_at > NOW() - INTERVAL '"
                + str(_PWA_REG_EXPIRE_HOURS) + " hours' ORDER BY created_at DESC LIMIT 50")
            rows = cur.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] registrations list failed: " + str(e))
        return []
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "code": r["code"], "name": r["full_name"],
            "username": r.get("username"), "email": r["email"], "phone": r["phone"],
            "role": r["role"],
            "role_label": ROLE_LABELS.get(r["role"], r["role"]),
            "has_pass": bool(r.get("password_hash")),
            "created_at": (r.get("created_at") or "")[:16], "ip": r.get("ip"),
        })
    return out


def _pwa_api_registration_delete(user, reg_id):
    """حذف طلب تسجيل منتظر (للمدير فقط)."""
    try:
        reg_id = int(reg_id)
    except (TypeError, ValueError):
        return False, "معرّف غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pwa_registrations WHERE id = %s AND status = 'waiting'", (reg_id,))
            n = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] registration delete failed: " + str(e))
        return False, "خطأ في الخادم."
    finally:
        release_connection(conn)
    if not n:
        return False, "الطلب غير موجود أو مؤكّد مسبقاً."
    return True, "تم حذف الطلب."


# ═══════════ v39: نسيت كلمة المرور — طلب الموظف + توليد رقم سر مؤقت بضغطة ═══════════

_PWA_TEMP_PASS_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # بلا حروف ملتبسة (i,l,o,0,1)


def _pwa_generate_temp_password(length=8):
    """رقم سر مؤقت مقروء ومميز — حروف صغيرة وأرقام مع رقم واحد على الأقل.
    يُعرض على المدير مرة واحدة ليعطيه للموظف شفهياً."""
    while True:
        pw = "".join(_pwa_secrets.choice(_PWA_TEMP_PASS_ALPHABET) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _pwa_api_forgot_request(username_raw, ip):
    """v39: طلب استعادة الرقم السري (عام بلا جلسة).
    يعيد دائماً ردّاً موحّداً (لا يفشل الطلب) حتى لا يُكتشف أي أسماء موجودة.
    الطلب يظهر للمدير في لوحة المستخدمين وفي تلغرام بضغطة توليد."""
    username = str(username_raw or "").strip()
    if username.startswith("@"):
        username = username[1:]
    username = re.sub(r"\s+", " ", username)
    if len(username) < 3:
        return False, "أدخل اسم الدخول الخاص بك (3 أحرف على الأقل)."
    uname_l = username.lower()
    conn = get_connection()
    found_uid = None
    new_reset_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.user_id FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s AND u.active LIMIT 1", (uname_l,))
            row = cur.fetchone()
            if row:
                found_uid = int(row[0])
                cur.execute("SELECT 1 FROM pwa_pass_resets "
                            "WHERE lower(username) = %s AND status = 'waiting' LIMIT 1",
                            (uname_l,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO pwa_pass_resets (username, ip) VALUES (%s, %s) "
                        "RETURNING id", (username, ip))
                    new_reset_id = int(cur.fetchone()[0])
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] forgot request failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    if found_uid is not None and new_reset_id is not None:
        db_insert_notification(
            "reg", "🔑 طلب استعادة رقم سر: " + username,
            "طالب باستعادة رقمه السري — ولّد رقماً مؤقتاً له من لوحة المستخدمين.",
            actor=username, source="web")
        # v40: جدولة آمنة من خيط executor — نفس إصلاح التسجيل
        if TG_APP is not None:
            _pwa_schedule(_notify_admins_pwa_reset(new_reset_id, username))
    # ردّ موحّد دائماً — لا نكشف إن كان الاسم موجوداً أم لا (حماية من الاستكشاف)
    return True, None


def _pwa_api_resets_list():
    """v39: قائمة طلبات استعادة الرقم السري المنتظرة (للمدير فقط)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, username, created_at::text AS created_at, ip "
                "FROM pwa_pass_resets WHERE status = 'waiting' "
                "ORDER BY created_at DESC LIMIT 50")
            rows = cur.fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] resets list failed: " + str(e))
        return []
    finally:
        release_connection(conn)
    return [{"id": r["id"], "username": r["username"],
             "created_at": (r.get("created_at") or "")[:16], "ip": r.get("ip")}
            for r in rows]


def _pwa_api_reset_generate(user, reset_id):
    """v39: الموافقة على طلب استعادة — توليد رقم سر مؤقت وحفظه مكان القديم
    (بكوكي scrypt) وإبطال جلسات صاحبه وإظهار الرقم المؤقت للمدير مرة واحدة.
    يعيد (ok, msg_or_password)."""
    try:
        reset_id = int(reset_id)
    except (TypeError, ValueError):
        return False, "معرّف غير صالح.", None
    conn = get_connection()
    uname = None
    uid = None
    temp = _pwa_generate_temp_password()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM pwa_pass_resets "
                        "WHERE id = %s AND status = 'waiting'", (reset_id,))
            row = cur.fetchone()
            if not row:
                return False, "الطلب غير موجود أو مُعالَج مسبقاً.", None
            uname = str(row[0])
            cur.execute(
                "SELECT a.user_id FROM pwa_auth a JOIN users u ON u.id = a.user_id "
                "WHERE lower(a.username) = %s AND u.active LIMIT 1", (uname.lower(),))
            urow = cur.fetchone()
            if not urow:
                cur.execute("UPDATE pwa_pass_resets SET status = 'cancelled', done_at = NOW() "
                            "WHERE id = %s", (reset_id,))
                conn.commit()
                return False, "لا يوجد حساب نشط بهذا الاسم — أُلغي الطلب.", None
            uid = int(urow[0])
            cur.execute("UPDATE pwa_auth SET pass_hash = %s, updated_at = NOW() "
                        "WHERE user_id = %s",
                        (_pwa_hash_password(temp), uid))
            cur.execute("UPDATE pwa_pass_resets SET status = 'done', temp_pass = %s, "
                        "done_at = NOW() WHERE id = %s", (temp, reset_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] reset generate failed: " + str(e))
        return False, "خطأ في الخادم.", None
    finally:
        release_connection(conn)
    # أمان: إبطال جلسات صاحب الحساب (إن كان مسجلاً من جهاز آخر)
    try:
        _pwa_kill_sessions(uid)
    except Exception:
        pass
    return True, uname, temp


# ── v33: قنوات تسليم الرمز — البريد الإلكتروني (SMTP) وSMS عبر TextBee ──
def _pwa_otp_email_ready():
    """هل مرسل البريد مهيأ؟ (نفس إعدادات SMTP_* الخاصة بالنسخة الاحتياطية)"""
    return bool(SMTP_HOST and SMTP_EMAIL and SMTP_PASSWORD)


def _pwa_otp_sms_ready():
    """هل بوابة SMS مهيأة؟ (TextBee — هاتفك كبوابة إرسال، مجاني)"""
    return bool(TEXTBEE_API_KEY and TEXTBEE_DEVICE_ID)


def _pwa_tcp_probe(host, port, timeout=8):
    """v37: فحص TCP خام — هل يمكن فتح اتصال بمضيف:منفذ؟ يعيد (متصل؟، نص الخطأ)."""
    import socket
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        try:
            s.close()
        except Exception:
            pass
        return True, ""
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)[:70]


def _pwa_mask_password(pw):
    """v37: وصف آمن للمفتاح — بلا كشف قيمته."""
    pw = str(pw or "")
    if not pw:
        return "(فارغ ⚠️)"
    if pw.startswith("xkeysib-"):
        return "مفتاح البريد صحيح الشكل (xkeysib-…) ✓"
    return "طوله " + str(len(pw)) + " — ⚠️ لا يبدأ بـ xkeysib- (ليس مفتاح SMTP!)"


async def testmail_command(update, context):
    """v38: /testmail — للمدير فقط: فاحص شامل لإعدادات البريد:
    1) عرض الإعدادات الحالية كما يراها الخادم (كشف الأخطاء المطبعية)
    2) مصفوفة اتصال TCP لمنافذ SMTP: مضيفك + خدمة البريد 587/465 + خادم مرجعي
    3) فحص خدمة البريد HTTPS API (منفذ 443 — الوحيد غير المحجوب في الاستضافة المجانية)
    4) حكم مباشر + إرسال فعلي إلى BACKUP_EMAIL مع السبب عند الفشل."""
    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text("هذا الأمر للمدير فقط.")
        return
    if not _pwa_otp_email_ready() or not BACKUP_EMAIL:
        await update.message.reply_text(
            "⚠️ الإعداد ناقص — المطلوب في متغيرات البيئة (Environment):\n"
            "SMTP_HOST = smtp-relay.brevo.com\n"
            "SMTP_PORT = 587\n"
            "SMTP_EMAIL = بريد تسجيلك في خدمة البريد\n"
            "SMTP_PASSWORD = مفتاح SMTP من خدمة البريد (يبدأ بـ xkeysib-)\n"
            "BACKUP_EMAIL = بريدك لاستقبال التجربة\n"
            "ثم أعد نشر التطبيق.")
        return

    # 1) الإعدادات كما يراها الخادم — هنا تظهر الأخطاء المطبعية فوراً
    pw_desc = _pwa_mask_password(SMTP_PASSWORD)
    email_desc = _pwa_mask_identifier(SMTP_EMAIL) if SMTP_EMAIL else "(فارغ)"
    api_desc = "موجود ✓" if BREVO_API_KEY else "غير موجود"
    await update.message.reply_text(
        "🔧 الإعدادات الحالية كما يراها الخادم:\n"
        "SMTP_HOST = «" + str(SMTP_HOST) + "»\n"
        "SMTP_PORT = " + str(SMTP_PORT) + "\n"
        "SMTP_TLS = «" + (SMTP_TLS or "") + "»\n"
        "SMTP_EMAIL = " + email_desc + "\n"
        "SMTP_PASSWORD = " + pw_desc + "\n"
        "BREVO_API_KEY = " + api_desc + "\n"
        "↩️ قارنها بالقيم المطلوبة — أي اختلاف حرف أو مسافة هو السبب.")

    # 2) مصفوفة اتصال TCP لمنافذ SMTP
    await update.message.reply_text("📡 جارٍ فحص الشبكة…")
    probes = [
        ("إعداداتك (" + str(SMTP_HOST) + ":" + str(SMTP_PORT) + ")",
         SMTP_HOST, SMTP_PORT),
        ("خدمة البريد 587", "smtp-relay.brevo.com", 587),
        ("خدمة البريد 465", "smtp-relay.brevo.com", 465),
        ("خادم مرجعي 465", "smtp.gmail.com", 465),
    ]
    lines = []
    mine_ok = False
    others_ok = False
    for label, h, p in probes:
        ok, err = await asyncio.to_thread(_pwa_tcp_probe, h, p)
        if label.startswith("إعداداتك"):
            mine_ok = ok
        else:
            others_ok = others_ok or ok
        lines.append(("✅ " if ok else "❌ ") + label
                     + ("" if ok else " — " + err))
    await update.message.reply_text(
        "📡 منافذ SMTP:\n" + "\n".join(lines)
        + "\n(ملاحظة: الاستضافات السحابية المجانية تحجب منافذ البريد "
          "25/465/587 — هذا ليس خطأك)")

    # 3) فحص Brevo HTTPS API — منفذ 443 (نفس منفذ تلغرام الذي يعمل لديك)
    api_ok = False
    api_keys = _pwa_brevo_api_keys()
    if api_keys:
        for k in api_keys:
            ok, status, info = await asyncio.to_thread(
                _pwa_brevo_api_request, k, "GET", "account")
            tail = ("…" + k[-4:]) if len(k) > 8 else "؟"
            if ok:
                api_ok = True
                await update.message.reply_text(
                    "🔑 API خدمة البريد عبر HTTPS: ✅ يعمل — المفتاح " + tail + " صالح.")
                break
            if status in (401, 403):
                await update.message.reply_text(
                    "🔑 API خدمة البريد عبر HTTPS: المنفذ مفتوح لكن المفتاح " + tail
                    + " مرفوض (401).\nهذا مفتاح SMTP وليس مفتاح API — أنشئ مفتاح "
                      "API من: لوحة خدمة البريد ← SMTP & API ← تبويب API ← Generate new "
                      "key، ثم أضفه كمتغير BREVO_API_KEY في متغيرات البيئة.")
            else:
                await update.message.reply_text(
                    "🔑 API خدمة البريد عبر HTTPS: استجابة غير متوقعة — HTTP "
                    + str(status) + " " + str(info)[:90])
    else:
        await update.message.reply_text(
            "🔑 لا يوجد متغير BREVO_API_KEY — إذا تبيّن أن منافذ SMTP محجوبة "
            "ستجد خطوات إنشائه أدناه.")

    # 4) الحكم النهائي — يفرّق بين خطأ القيم وحجب المنافذ في الاستضافة
    if not mine_ok and others_ok:
        await update.message.reply_text(
            "🎯 التشخيص: إعداداتك لا تفتح اتصالاً بينما خدمة البريد نفسها متاحة!\n"
            "→ الخطأ في SMTP_HOST أو SMTP_PORT (حرف أو مسافة زائدة).\n"
            "القيم الصحيحة: SMTP_HOST = smtp-relay.brevo.com و SMTP_PORT = 587\n"
            "عدّلها ثم أعد نشر التطبيق وأعد /testmail.")
        return
    if not mine_ok and not others_ok:
        if not api_ok:
            await update.message.reply_text(
                "🎯 التشخيص: إعداداتك صحيحة لكن استضافتك تحجب كل منافذ البريد "
                "الصادرة (25/465/587) — سياسة الاستضافات السحابية المجانية، "
                "وليست مشكلة منك إطلاقاً.\nالحل: مفتاح API لخدمة البريد عبر HTTPS:\n"
                "1) لوحة خدمة البريد ← SMTP & API ← تبويب API ← Generate new key "
                "(يبدأ بـ xkeysib-)\n"
                "2) أضفه في متغيرات البيئة كمتغير جديد: BREVO_API_KEY\n"
                "3) أعد نشر التطبيق ثم أعد /testmail.")
            return
        await update.message.reply_text(
            "🎯 التشخيص: إعداداتك صحيحة 100% — لكن استضافتك السحابية المجانية تحجب "
            "كل منافذ البريد الصادرة 25/465/587 منذ 2025، ولهذا فشل SMTP حتى مع "
            "خوادم البريد الخارجية.\n✅ لا مشكلة — النسخة الجديدة سترسل عبر HTTPS API "
            "تلقائياً (نفس منفذ تلغرام الذي يعمل عندك).")

    # 5) إرسال فعلي
    await update.message.reply_text("⏳ جارٍ إرسال بريد تجريبي إلى " + BACKUP_EMAIL + " …")
    ok = await asyncio.to_thread(
        _pwa_send_email_sync, BACKUP_EMAIL,
        "✅ بريد تجريبي — مخزون السيراميك",
        "هذا بريد تجريبي للتأكد من عمل إعدادات SMTP على الخادم.\n"
        "إذا وصلك فالإرسال يعمل، وسيصل رمز التأكيد لكل مسجل جديد مباشرة.\n\n"
        "إن لم يصلك: افحص مجلد Spam ثم راجع السبب المعروض في البوت.")
    if ok:
        await update.message.reply_text(
            "✅ وصل البريد التجريبي إلى " + BACKUP_EMAIL + "\n"
            "الإعدادات سليمة — رموز التأكيد ستصل للمسجلين الجدد.")
    else:
        await update.message.reply_text(
            "❌ فشل الإرسال — السبب:\n" + (_PWA_SMTP_LAST_ERR or "غير معروف"))


# v36/v38: تشخيص فشل إرسال البريد — آخر خطأ مصنّف بالعربية يُعرض للمدير وللمسجلين
_PWA_SMTP_LAST_ERR = ""
_PWA_SMTP_PROBE_LOG = []  # v38: سجل كل محاولات الإرسال (host, port, mode, ok?, raw_err)
_PWA_BREVO_OK_KEY = ""    # v38: آخر مفتاح Brevo API نجح — يُستعمل أولاً في الإرسالات التالية


def _pwa_smtp_classify(e):
    """v36: تصنيف استثناء SMTP إلى نص عربي واضح (سبب قابل للإصلاح)."""
    s = str(e or "")
    low = s.lower()
    if ("535" in s or "530" in s or "badcredentials" in low
            or "authentic" in low or "username and password" in low
            or "authorization failed" in low or "login failed" in low):
        return ("بيانات دخول البريد مرفوضة — تأكد أن SMTP_PASSWORD هو مفتاح SMTP من "
                "خدمة البريد (يبدأ بـ xkeysib-) وليس كلمة سر الحساب، وأن SMTP_EMAIL "
                "هو بريد تسجيلك في خدمة البريد نفسه.")
    if "getaddrinfo" in low or "name or service not known" in low or "nodename" in low:
        return "تعذر الوصول لخادم البريد — تحقق من صحة SMTP_HOST."
    if ("timed out" in low or "timeout" in low or "refused" in low
            or "unreachable" in low):
        return ("تعذر الاتصال بخوادم البريد — الاستضافات السحابية المجانية تحجب "
                "منافذ البريد الصادرة (25/465/587). الحل: أنشئ مفتاح API لخدمة "
                "البريد (SMTP & API ← تبويب API ← Generate new key) وأضفه كمتغير "
                "BREVO_API_KEY في متغيرات البيئة ثم أعد نشر التطبيق — الإرسال عبر "
                "HTTPS يتجاوز هذا الحجب. أرسل /testmail في البوت لتشخيص فوري.")
    if (("sender" in low and ("reject" in low or "denied" in low
            or "not verified" in low or "unverified" in low)) or "553" in s):
        return ("بريد المُرسِل مرفوض — استعمل SMTP_EMAIL بنفس بريد تسجيل "
                "الحساب (مُرسِل موثَّق تلقائياً) أو فعّل المُرسِل من لوحة خدمة البريد.")
    if "550" in s or "551" in s or "recipient" in low:
        return "البريد المُرسَل إليه مرفوض من خادم البريد — تحقق من صحة البريد."
    return "خطأ غير متوقع أثناء إرسال البريد — حاول مرة أخرى بعد قليل."


def _pwa_brevo_api_keys():
    """v38: مفاتيح Brevo API المرشحة بالترتيب — BREVO_API_KEY أولاً ثم مفتاح SMTP
    إن بدأ بـ xkeysib- (البادئة واحدة لكن المفتاحين مختلفان في Brevo — نجرب المتاحين)."""
    keys = []
    if BREVO_API_KEY:
        keys.append(BREVO_API_KEY)
    if SMTP_PASSWORD and SMTP_PASSWORD.startswith("xkeysib-") and SMTP_PASSWORD not in keys:
        keys.append(SMTP_PASSWORD)
    if _PWA_BREVO_OK_KEY and _PWA_BREVO_OK_KEY not in keys:
        keys.insert(0, _PWA_BREVO_OK_KEY)
    return keys


def _pwa_brevo_api_request(api_key, method, path, payload=None):
    """v38: طلب HTTPS إلى Brevo API v3 (منفذ 443 — غير محجوب في الاستضافة المجانية).
    يعيد (ok؟، http_status، نص الاستجابة)."""
    import json as _json
    import urllib.request
    import urllib.error
    url = "https://api.brevo.com/v3/" + str(path).lstrip("/")
    data = _json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=str(method).upper(),
                                 headers={"api-key": str(api_key),
                                          "content-type": "application/json",
                                          "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            return True, r.status, body
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")[:220]
        except Exception:
            pass
        return False, e.code, raw
    except Exception as e:
        return False, 0, type(e).__name__ + ": " + str(e)[:120]


def _pwa_brevo_api_send_sync(api_key, to_email, subject, body):
    """v38: إرسال بريد عبر Brevo HTTPS API — يعيد (ok؟، status، raw)."""
    payload = {"sender": {"name": "تطبيق المخزون", "email": SMTP_EMAIL},
               "to": [{"email": str(to_email)}],
               "subject": str(subject),
               "textContent": str(body)}
    return _pwa_brevo_api_request(api_key, "POST", "smtp/email", payload)


def _pwa_email_final_err(last_raw):
    """v38/v77: السبب النهائي المعروض — أولوية لأخطاء مفتاح API (المسار الأساسي عبر HTTPS)."""
    api_fails = [err for (_h, _p, m, ok, err) in _PWA_SMTP_PROBE_LOG
                 if m == "api" and not ok]
    if any(("401" in err or "403" in err) for err in api_fails):
        return ("مفتاح خدمة البريد مرفوض — أنشئ مفتاح API من لوحة خدمة البريد "
                "(SMTP & API ← تبويب API ← Generate new key، يبدأ بـ xkeysib-) "
                "وضعه في متغير جديد: BREVO_API_KEY ثم أعد نشر التطبيق. "
                "ملاحظة: مفتاح SMTP ومفتاح API مفتاحان مختلفان.")
    if any("sender" in err.lower() for err in api_fails):
        return ("بريد المُرسِل غير موثّق في خدمة البريد — استعمل SMTP_EMAIL بنفس بريد "
                "تسجيلك أو فعّل المُرسِل من لوحة الخدمة (Senders).")
    return _pwa_smtp_classify(last_raw)


def _pwa_send_email_sync(to_email, subject, body):
    """v34: إرسال بريد عام (متزامن — يُستدعى في executor). يعيد True عند النجاح.
    v38: المسار الأساسي Brevo HTTPS API (منفذ 443 — يعمل رغم حجب منافذ SMTP
    في Render المجاني منذ 2025)، والـ SMTP احتياط. كل محاولة تُسجّل في
    _PWA_SMTP_PROBE_LOG والسبب النهائي في _PWA_SMTP_LAST_ERR."""
    global _PWA_SMTP_LAST_ERR, _PWA_SMTP_PROBE_LOG, _PWA_BREVO_OK_KEY
    import smtplib
    import ssl as _ssl
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr
    probe = []
    last_raw = ""
    # 1) Brevo HTTPS API أولاً — منفذ 443 غير محجوب
    for k in _pwa_brevo_api_keys():
        ok, status, raw = _pwa_brevo_api_send_sync(k, to_email, subject, body)
        if ok:
            _PWA_BREVO_OK_KEY = k
            _PWA_SMTP_PROBE_LOG = probe + [("Brevo API HTTPS", 443, "api", True, "")]
            logging.info("[PWA] email sent via Brevo API to " + str(to_email))
            _PWA_SMTP_LAST_ERR = ""
            return True
        err = ("HTTP " + str(status) + " " + raw) if status else raw
        probe.append(("Brevo API HTTPS", 443, "api", False, err))
        logging.error("[PWA] brevo api attempt failed: " + err)
        if status not in (401, 403):
            break  # خطأ غير متعلق بالمفتاح — لا فائدة من تجربة مفتاح آخر
    # 2) SMTP احتياطاً (يعمل خارج Render أو إذا رُفع الحجب)
    try:
        msg = MIMEText(str(body), "plain", "utf-8")
        msg["Subject"] = Header(str(subject), "utf-8")
        msg["From"] = formataddr((str(Header("تطبيق المخزون", "utf-8")), SMTP_EMAIL))
        msg["To"] = to_email
        ctx = _ssl.create_default_context()
        if SMTP_TLS == "none":
            server_ctx = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        elif SMTP_PORT == 465 or SMTP_TLS == "ssl":
            server_ctx = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=ctx)
        else:
            server_ctx = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        with server_ctx as server:
            if SMTP_TLS != "none" and SMTP_PORT != 465 and SMTP_TLS != "ssl":
                server.starttls(context=ctx)
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
        mode = "ssl" if (SMTP_PORT == 465 or SMTP_TLS == "ssl") else "starttls"
        _PWA_SMTP_PROBE_LOG = probe + [(SMTP_HOST, SMTP_PORT, mode, True, "")]
        logging.info("[PWA] email sent via SMTP to " + str(to_email))
        _PWA_SMTP_LAST_ERR = ""
        return True
    except Exception as e:
        last_raw = type(e).__name__ + ": " + str(e)[:150]
        mode = "ssl" if (SMTP_PORT == 465 or SMTP_TLS == "ssl") else "starttls"
        probe.append((SMTP_HOST, SMTP_PORT, mode, False, last_raw))
        logging.error("[PWA] smtp fallback failed: " + last_raw)
    _PWA_SMTP_PROBE_LOG = probe
    _PWA_SMTP_LAST_ERR = _pwa_email_final_err(last_raw)
    logging.error("[PWA] email failed | السبب: " + _PWA_SMTP_LAST_ERR)
    return False


def _pwa_send_otp_email_sync(to_email, code):
    """إرسال رمز الدخول إلى البريد الإلكتروني (متزامن — يُستدعى عبر asyncio.to_thread).
    يعيد True عند النجاح؛ عند الفشل يتحول المسار تلقائياً إلى تلغرام في المعالج."""
    body = ("مرحباً،\n\n"
            "رمز الدخول إلى تطبيق مخزون السيراميك:\n\n"
            + code + "\n\n"
            "⏳ صالح " + str(_PWA_OTP_EXPIRE_MIN) + " دقائق — لا تشاركه مع أحد.\n"
            "إن لم تطلب هذا الرمز فتجاهل هذه الرسالة.\n")
    return _pwa_send_email_sync(to_email, "🔑 رمز الدخول: " + code, body)


async def _pwa_send_otp_sms(phone_norm, code):
    """إرسال الرمز رسالة SMS عبر بوابة TextBee (هاتفك الاحتياطي كمُرسل — مجاني).
    يعيد True عند النجاح؛ عند الفشل يتحول المسار تلقائياً إلى تلغرام في المعالج."""
    if not phone_norm:
        return False
    intl = phone_norm
    if intl.startswith("0") and len(intl) >= 10:
        intl = "+213" + intl[1:]  # 0xxxxxxxxx → +213xxxxxxxxx
    elif not intl.startswith("+"):
        intl = "+" + intl
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(
                    TEXTBEE_API_BASE.rstrip("/") + "/gateways/" + TEXTBEE_DEVICE_ID + "/send-sms",
                    headers={"x-api-key": TEXTBEE_API_KEY,
                             "Content-Type": "application/json"},
                    json={"recipients": [intl],
                          "message": "رمز الدخول إلى تطبيق المخزون: " + code
                                     + " (صالح " + str(_PWA_OTP_EXPIRE_MIN)
                                     + " دقائق — لا تشاركه مع أحد)"}) as resp:
                if resp.status < 300:
                    logging.info("[PWA] otp sms sent to " + intl)
                    return True
                txt = await resp.text()
                logging.error("[PWA] otp sms http " + str(resp.status) + ": " + txt[:200])
                return False
    except Exception as e:
        logging.error("[PWA] otp sms failed: " + str(e))
        return False


def _pwa_otp_find_user(identifier):
    """إيميل أو هاتف → صف مستخدم مؤهل للرمز السري (نشط + مربوط بالبوت)."""
    ident = str(identifier or "").strip()
    if not ident:
        return None
    email = ident.lower() if "@" in ident else ""
    phone = _pwa_norm_phone(ident) if "@" not in ident else ""
    if not email and not phone:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.email, u.phone, a.username AS web_username "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE u.active = TRUE AND u.telegram_id IS NOT NULL "
                "AND u.role NOT IN (%s, %s) AND ("
                "(%s <> '' AND u.email = %s) OR (%s <> '' AND u.phone = %s)) LIMIT 1",
                (ROLE_CUSTOMER, ROLE_PENDING, email, email, phone, phone))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] otp find user failed: " + str(e))
        return None
    finally:
        release_connection(conn)


def _pwa_otp_issue(user_id, ip):
    """توليد رمز دخول مخزّن مُهشَّراً (متزامن) — يعيد (ok, code_or_msg)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pwa_otp WHERE user_id = %s "
                        "AND created_at > NOW() - INTERVAL '10 minutes'", (user_id,))
            if cur.fetchone()[0] >= _PWA_OTP_MAX_PER_10MIN:
                return False, "طلبات رموز كثيرة — انتظر نحو 10 دقائق ثم أعد المحاولة."
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            cur.execute(
                "INSERT INTO pwa_otp (user_id, code_hash, ip, expires_at) "
                "VALUES (%s, %s, %s, NOW() + INTERVAL '" + str(_PWA_OTP_EXPIRE_MIN) + " minutes')",
                (user_id, _pwa_hash_password(code), ip))
        conn.commit()
        return True, code
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] otp issue failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_otp_verify(user_id, code):
    """تحقق من الرمز (متزامن) — يعيد (ok, err). يُبطل الرمز بعد المحاولات القصوى."""
    try:
        code = str(code or "").strip()
    except Exception:
        return False, "رمز غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, code_hash, expires_at, used_at, attempts FROM pwa_otp "
                        "WHERE user_id = %s AND used_at IS NULL ORDER BY created_at DESC LIMIT 1",
                        (user_id,))
            row = cur.fetchone()
            if row is None:
                return False, "لا يوجد رمز فعّال — اطلب رمزاً جديداً."
            if row["attempts"] >= _PWA_OTP_MAX_ATTEMPTS:
                return False, "أُبطل الرمز بعد محاولات خاطئة كثيرة — اطلب رمزاً جديداً."
            if row["expires_at"] <= datetime.datetime.now(datetime.timezone.utc):
                return False, "انتهت صلاحية الرمز — اطلب رمزاً جديداً."
            if not _pwa_verify_password(code, row["code_hash"]):
                cur.execute("UPDATE pwa_otp SET attempts = attempts + 1 WHERE id = %s", (row["id"],))
                conn.commit()
                left = _PWA_OTP_MAX_ATTEMPTS - (row["attempts"] + 1)
                return False, "رمز خاطئ." + (" تبقّت " + str(left) + " محاولات." if left > 0 else "")
            cur.execute("UPDATE pwa_otp SET used_at = NOW() WHERE id = %s", (row["id"],))
        conn.commit()
        return True, None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] otp verify failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_user_role(user, target_tid, role):
    """تغيير فئة مستخدم من التطبيق (للمدير فقط) — نفس نظام البوت."""
    if role not in ROLE_LABELS:
        return False, "فئة غير معروفة."
    if target_tid == user["telegram_id"]:
        return False, "لا يمكنك تغيير فئتك بنفسك — استخدم حساب مدير آخر احتياطاً."
    if _protected_account(target_tid):
        return False, "🔒 هذا الحساب محمي (المالك/متغيرات البيئة) — لا يمكن تعديل فئته."
    try:
        changed = set_user_role(target_tid, role)
    except Exception as e:
        return False, "خطأ في تغيير الفئة: " + str(e)
    if not changed:
        return False, "المستخدم غير موجود."
    return True, "تم تعيين الفئة: " + ROLE_LABELS[role]


def _pwa_api_stats(user):
    perms = _pwa_perm_map(user["role"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products WHERE NOT archived")
            n_products = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE quantity <= minimum_stock AND NOT archived")
            n_low = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM transactions WHERE date >= CURRENT_DATE")
            n_today = cur.fetchone()[0]
            if perms["admin"]:
                cur.execute("SELECT COUNT(*) FROM pending_deliveries WHERE pending_qty > 0")
            else:
                cur.execute("SELECT COUNT(*) FROM pending_deliveries "
                            "WHERE pending_qty > 0 AND user_id = %s", (user["id"],))
            n_pend = cur.fetchone()[0]
            if perms["admin"]:
                cur.execute("SELECT COUNT(*) FROM incoming_orders "
                            "WHERE status IN ('pending', 'received')")
            else:
                cur.execute("SELECT COUNT(*) FROM incoming_orders "
                            "WHERE status IN ('pending', 'received') AND requester_id = %s",
                            (user["id"],))
            n_inc = cur.fetchone()[0]
        conn.commit()
    finally:
        release_connection(conn)
    oil = None
    if perms["oil"]:
        st = oil_ensure_row()
        if st.get("has_cycle"):
            base = float(st.get("baseline_hours") or 0)
            cur_h = float(st.get("current_hours") or base)
            since = max(0.0, cur_h - base)
            remaining = float(OIL_H_LIMIT) - since
            oil = {"since": round(since, 1), "limit": float(OIL_H_LIMIT),
                   "remaining": round(remaining, 1),
                   "level": "red" if since >= float(OIL_H_RED) else ("warn" if remaining <= 0 else "ok")}
    return {"ok": True, "stats": {
        "products": n_products, "low": n_low, "today": n_today,
        "pending": n_pend, "incoming": n_inc}, "oil": oil}


def _pwa_api_products(user, q, archived=False, low=False):
    role = user["role"]
    external_only = role == ROLE_EXTERNAL
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            asql = "archived" if archived else "NOT archived"   # v41: قسمة المحذوفات
            lsql = " AND (COALESCE(quantity,0) <= COALESCE(minimum_stock,0))" if low else ""   # v43: فلتر المنخفضة
            # v81: السقف 200→500 + العدد الكلي — قديماً كانت القوائم الطويلة تُقتطع
            # صامتة عند 200 فتبدو السلع «غير موجودة» مع أنها حية (تظهر بالبحث فقط)
            if q:
                cur.execute(
                    "SELECT * FROM products WHERE " + asql + lsql + " AND name ILIKE %s "
                    "ORDER BY LOWER(name) LIMIT 500",
                    ("%" + q + "%",))
            else:
                cur.execute("SELECT * FROM products WHERE " + asql + lsql +
                            " ORDER BY LOWER(name) LIMIT 500")
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS cnt FROM products WHERE " + asql + lsql +
                        (" AND name ILIKE %s" if q else ""),
                        ("%" + q + "%",) if q else ())
            total = int(cur.fetchone()["cnt"])
        conn.commit()
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        if external_only:
            out.append({"id": r["id"], "name": r["name"],
                        "available": (r["quantity"] or 0) > 0})
        else:
            out.append({"id": r["id"], "name": r["name"],
                        "qty": r["quantity"], "min": r["minimum_stock"],
                        "location": r.get("location") or "",
                        "price": r.get("selling_price"),
                        "unit": _get_unit_ar(r["name"]),
                        "low": (r["quantity"] or 0) <= (r["minimum_stock"] or 0)})
    return {"ok": True, "products": out, "total": total, "limited": external_only, "archived": bool(archived), "low": bool(low)}


def _pwa_api_product_detail(user, product_id):
    """📋 تفاصيل السلعة + سجل حركاتها (v31) — نفس ما يعرضه البوت:
    الخارجي يرى التوفر فقط، والباقي يرى الكميات والحركة بالسبب والفاعل."""
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return None, "معرّف غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
            prod = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not prod:
        return None, "السلعة غير موجودة."
    p = dict(prod)
    if user["role"] == ROLE_EXTERNAL:
        return {"limited": True, "id": p["id"], "name": p["name"],
                "available": (p["quantity"] or 0) > 0}, None
    txns = []
    try:
        txns = _filter_test_pairs(get_product_transactions(product_id, limit=30)) or []
    except Exception:
        txns = []
    hist = []
    for t in txns:
        t = dict(t)
        hist.append({
            "t": t.get("tx_type"), "q": t.get("quantity"),
            "date": t["date"].strftime("%Y-%m-%d %H:%M") if t.get("date") else "",
            "op": t.get("operator_name") or "مجهول",
            "reason": t.get("reason") or "",
        })
    return {
        "limited": False,
        "id": p["id"], "name": p["name"], "qty": p["quantity"],
        "min": p["minimum_stock"], "location": p.get("location") or "",
        "price": p.get("selling_price"), "unit": _get_unit_ar(p["name"]),
        "low": (p["quantity"] or 0) <= (p["minimum_stock"] or 0),
        "archived": bool(p.get("archived")),   # v41: شارة المحذوفة + إتاحة الاستعادة
        "created": p["created_at"].strftime("%Y-%m-%d") if p.get("created_at") else "",
        "history": hist,
    }, None


def _pwa_api_qty(user, product_id, qtype, quantity, reason):
    role = user["role"]
    if qtype == "add":
        if not role_perm_enabled(role, PERM_ADD_QTY) and role not in (ROLE_ADMIN, ROLE_MANAGER):
            return False, "ليست لديك صلاحية إضافة الكميات."
    elif qtype == "remove":
        if not role_perm_enabled(role, PERM_REMOVE) and role not in (ROLE_ADMIN, ROLE_MANAGER):
            return False, "ليست لديك صلاحية إخراج الكميات."
    elif qtype == "reconcile":
        if role not in (ROLE_ADMIN, ROLE_MANAGER):
            return False, "الجرد للمدراء فقط."
    else:
        return False, "نوع عملية غير معروف."
    if not isinstance(quantity, int) or quantity < 0:
        return False, "أدخل كمية صحيحة."
    if qtype in ("add", "remove") and quantity <= 0:
        return False, "أدخل كمية أكبر من صفر."
    reason = (str(reason or "").strip())[:200]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM products WHERE id = %s FOR UPDATE", (product_id,))
            prod = cur.fetchone()
            if not prod:
                conn.rollback()
                return False, "السلعة غير موجودة."
            if prod.get("archived"):
                conn.rollback()   # v41: سلعة محذوفة ناعماً — لا حركة عليها
                return False, "السلعة محذوفة — اطلب من المدير استعادتها أولاً."
            old_qty = prod["quantity"] or 0
            if qtype == "add":
                new_qty = old_qty + quantity
                cur.execute("UPDATE products SET quantity = %s WHERE id = %s", (new_qty, product_id))
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'add', %s, %s, %s)",
                    (product_id, quantity, user["id"], reason or None))
            elif qtype == "remove":
                if old_qty < quantity:
                    conn.rollback()
                    return False, "الكمية المتوفرة (" + str(old_qty) + ") أقل من المطلوب."
                new_qty = old_qty - quantity
                cur.execute("UPDATE products SET quantity = %s WHERE id = %s", (new_qty, product_id))
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'remove', %s, %s, %s)",
                    (product_id, quantity, user["id"], reason or None))
            else:  # reconcile → جرد: ضبط الكمية على قيمة معلومة
                new_qty = quantity
                delta = abs(new_qty - old_qty)
                cur.execute("UPDATE products SET quantity = %s WHERE id = %s", (new_qty, product_id))
                if delta > 0:
                    cur.execute(
                        "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                        "VALUES (%s, 'reconcile', %s, %s, %s)",
                        (product_id, delta, user["id"],
                         ("جرد: من " + str(old_qty) + " إلى " + str(new_qty) +
                          (" — " + reason if reason else ""))))
        conn.commit()
        return True, "تمت العملية بنجاح."
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] qty failed: " + str(e))
        return False, "فشلت العملية، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_add_product(user, name, qty, min_stock, price, location):
    """＋ إضافة سلعة جديدة (مدراء فقط) — مثل زر «إضافة سلعة» في البوت."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, "إضافة سلعة للمدراء فقط."
    name = str(name or "").strip()
    if len(name) < 2:
        return False, "أدخل اسم السلعة (حرفان على الأقل)."
    if len(name) > 500:
        return False, "اسم السلعة طويل جداً."
    try:
        qty = int(qty)
        min_stock = int(min_stock)
    except (TypeError, ValueError):
        return False, "أدخل كميات صحيحة."
    if qty < 0 or min_stock < 0:
        return False, "الكميات لا تقبل قيماً سالبة."
    try:
        price = round(float(str(price or 0).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return False, "السعر غير صحيح."
    if price < 0:
        return False, "السعر لا يقبل قيماً سالبة."
    location = str(location or "").strip()[:200]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) "
                        "AND NOT archived LIMIT 1", (name,))
            if cur.fetchone():
                return False, "توجد سلعة بنفس الاسم بالفعل — ابحث عنها وأضف كمياتها."
            # v41: نفس الاسم موجود بين المحذوفات؟ الاستعادة أفضل من الازدواج
            cur.execute("SELECT id FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) "
                        "AND archived LIMIT 1", (name,))
            if cur.fetchone():
                return False, ("توجد سلعة محذوفة بنفس الاسم — استعدها من قسم «المحذوفة» "
                               "في المخزون بدل إنشاء سلعة مكررة.")
            cur.execute(
                "INSERT INTO products (name, quantity, location, minimum_stock, selling_price) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (name, qty, location, min_stock, price))
            pid = cur.fetchone()[0]
            if qty > 0:
                cur.execute(
                    "INSERT INTO transactions (product_id, type, quantity, user_id, reason) "
                    "VALUES (%s, 'add', %s, %s, 'إضافة سلعة جديدة')",
                    (pid, qty, user["id"]))
        conn.commit()
        return True, "تمت إضافة السلعة ✅"
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] add product failed: " + str(e))
        return False, "فشلت الإضافة، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_product_edit(user, product_id, name, location=None, price=None,
                          minimum_stock=None):
    """✏️ v41: تعديل سلعة (مدراء فقط) — الاسم إلزامي، والحقول الأخرى اختيارية
    (None = تبقى كما هي). التعديل لا يمسّ سجل الحركة إطلاقاً."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, None, "تعديل السلع للمدراء فقط."
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return False, None, "معرّف غير صالح."
    name = str(name or "").strip()
    if len(name) < 2:
        return False, None, "أدخل اسم السلعة (حرفان على الأقل)."
    if len(name) > 500:
        return False, None, "اسم السلعة طويل جداً."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, name FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
            if not row:
                return False, None, "السلعة غير موجودة."
            old_name = row["name"]
            if name != old_name:
                cur.execute(
                    "SELECT id FROM products WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s)) "
                    "AND id <> %s LIMIT 1", (name, product_id))
                if cur.fetchone():
                    conn.rollback()
                    return False, None, "توجد سلعة أخرى بنفس الاسم."
            sets = ["name = %s"]
            params = [name]
            if location is not None:
                sets.append("location = %s")
                params.append(str(location).strip()[:200])
            if price is not None and str(price).strip() != "":
                try:
                    pv = round(float(str(price).replace(",", ".")), 2)
                    if pv < 0:
                        return False, None, "السعر لا يقبل قيماً سالبة."
                    sets.append("selling_price = %s")
                    params.append(pv)
                except (TypeError, ValueError):
                    return False, None, "السعر غير صحيح."
            if minimum_stock is not None and str(minimum_stock).strip() != "":
                try:
                    mv = int(minimum_stock)
                    if mv < 0:
                        return False, None, "الحد الأدنى لا يقبل قيماً سالبة."
                    sets.append("minimum_stock = %s")
                    params.append(mv)
                except (TypeError, ValueError):
                    return False, None, "الحد الأدنى غير صحيح."
            params.append(product_id)
            cur.execute("UPDATE products SET " + ", ".join(sets) + " WHERE id = %s",
                        tuple(params))
        conn.commit()
        return True, {"old_name": old_name, "new_name": name}, None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] product edit failed: " + str(e))
        return False, None, "فشل التعديل، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_product_archive(user, product_id, archived=True):
    """🗑 v41: حذف ناعم للسلعة (مدراء فقط) — تُخفى من كل القوائم بينما يبقى
    سجل حركتها كاملاً محفوظاً (قيد RESTRICT محترم)، ويمكن استعادتها لاحقاً."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, None, "حذف السلع للمدراء فقط."
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return False, None, "معرّف غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE products SET archived = %s WHERE id = %s "
                "RETURNING name, quantity",
                (bool(archived), product_id))
            row = cur.fetchone()
            if not row:
                return False, None, "السلعة غير موجودة."
            if not archived:
                # استعادة: لا نسمح باسم مكرر مع سلعة حية أخرى
                cur.execute(
                    "SELECT id FROM products WHERE LOWER(TRIM(name)) = "
                    "(SELECT LOWER(TRIM(name)) FROM products WHERE id = %s) "
                    "AND id <> %s AND NOT archived LIMIT 1", (product_id, product_id))
                if cur.fetchone():
                    conn.rollback()   # ↩️ نتراجع عن UPDATE الاستعادة قبل الرفض
                    return False, None, ("لا يمكن الاستعادة: توجد سلعة حية بنفس الاسم "
                                         "— عدّل الاسم أولاً من تفاصيل السلعة.")
        conn.commit()
        return True, dict(row), None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] product archive failed: " + str(e))
        return False, None, "فشلت العملية، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_pending(user):
    """🔴 الطلبات المعلقة (عجز المخزون السالب): المدير يراها كلها
    والموظف يرى طلباته فقط — نفس فلسفة البوت."""
    admin = _pwa_perm_map(user["role"])["admin"]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            base = (
                "SELECT pd.id, pd.pending_qty, pd.created_at, "
                "p.name AS product_name, p.quantity AS product_qty, "
                "COALESCE(u.first_name, u.username, '#' || u.telegram_id::text) AS user_name "
                "FROM pending_deliveries pd "
                "JOIN products p ON p.id = pd.product_id "
                "LEFT JOIN users u ON u.id = pd.user_id "
                "WHERE pd.pending_qty > 0 ")
            if admin:
                cur.execute(base + "ORDER BY pd.created_at ASC LIMIT 200")
            else:
                cur.execute(base + "AND pd.user_id = %s ORDER BY pd.created_at ASC LIMIT 200",
                            (user["id"],))
            rows = cur.fetchall()
        conn.commit()
        return {"ok": True, "pending": [dict(r) for r in rows], "admin": admin}
    finally:
        release_connection(conn)


def _pwa_api_incoming_list(user):
    """🚚 طلبات التوريد المفتوحة: المدير يراها كلها والموظف يرى طلباته.
    (v31) تضيف has_photo لزر «تصوير الوصل» و mine لتحديد من يملك الطلب."""
    admin = _pwa_perm_map(user["role"])["admin"]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            base = (
                "SELECT io.id, io.product_name, io.quantity, io.status, io.created_at, "
                "io.requester_id, (io.web_photo IS NOT NULL) AS has_photo, "
                "COALESCE(u.first_name, u.username, '#' || u.telegram_id::text) AS user_name "
                "FROM incoming_orders io LEFT JOIN users u ON u.id = io.requester_id "
                "WHERE io.status IN ('pending', 'received') ")
            if admin:
                cur.execute(base + "ORDER BY io.created_at ASC LIMIT 200")
            else:
                cur.execute(base + "AND io.requester_id = %s ORDER BY io.created_at ASC LIMIT 200",
                            (user["id"],))
            rows = cur.fetchall()
        conn.commit()
        out = []
        for r in rows:
            d = dict(r)
            d["mine"] = (d.get("requester_id") == user["id"])
            out.append(d)
        return {"ok": True, "orders": out, "admin": admin}
    finally:
        release_connection(conn)


_PWA_PHOTO_MAX = 3 * 1024 * 1024  # 3MB بعد فك الترميز (الصورة تُضغط في المتصفح قبله)
_PWA_PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}


def _pwa_receipt_allowed(user, order_row):
    """المدير/المدير العام — أو صاحب الطلب نفسه."""
    return _pwa_perm_map(user["role"])["admin"] or (order_row and order_row.get("requester_id") == user["id"])


def _pwa_api_receipt_upload(user, order_id, photo_b64, mime):
    """📸 رفع/استبدال صورة وصل التوريد من التطبيق (v31)."""
    admin = _pwa_perm_map(user["role"])["admin"]
    if not admin and user["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, "لا يوجد وصول لك حالياً — راجع الإدارة."
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return False, "طلب غير صالح."
    photo_b64 = str(photo_b64 or "")
    if "," in photo_b64 and photo_b64.strip().startswith("data:"):
        photo_b64 = photo_b64.split(",", 1)[1]  # تقبل data URL أو base64 خام
    try:
        import base64 as _b64
        data = _b64.b64decode(photo_b64, validate=False)
    except Exception:
        return False, "صورة غير صالحة."
    if not data:
        return False, "الصورة فارغة."
    if len(data) > _PWA_PHOTO_MAX:
        return False, "الصورة كبيرة جداً (الحد 3 ميغابايت)."
    mime = str(mime or "image/jpeg").split(";")[0].strip().lower()
    if mime not in _PWA_PHOTO_MIMES:
        mime = "image/jpeg"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT requester_id FROM incoming_orders WHERE id = %s", (order_id,))
            row = cur.fetchone()
            if not row:
                return False, "الطلب غير موجود."
            order = {"requester_id": row[0]}
            if not _pwa_receipt_allowed(user, order):
                return False, "يمكنك رفع وصل طلباتك فقط."
            cur.execute(
                "UPDATE incoming_orders SET web_photo = %s, web_photo_mime = %s, "
                "web_photo_at = NOW(), web_photo_by = %s WHERE id = %s",
                (psycopg2.Binary(data), mime, user["id"], order_id))
        conn.commit()
        return True, "تم حفظ صورة الوصل ✅"
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] receipt upload failed: " + str(e))
        return False, "فشل الحفظ، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_receipt_get(user, order_id):
    """🖼️ قراءة صورة الوصل — المدير الكل، وصاحب الطلب طلبه فقط. يعيد (bytes, mime) أو (None, رسالة)."""
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return None, None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT requester_id, web_photo, web_photo_mime FROM incoming_orders WHERE id = %s",
                        (order_id,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row:
        return None, None, "الطلب غير موجود."
    order = {"requester_id": row[0]}
    if not _pwa_receipt_allowed(user, order):
        return None, None, "لا تملك صلاحية عرض هذا الوصل."
    if not row[1]:
        return None, None, "لا توجد صورة وصل لهذا الطلب بعد."
    return bytes(row[1]), (row[2] or "image/jpeg"), None


def _pwa_api_incoming_create(user, product_name, quantity):
    """🚚 تسجيل طلب توريد جديد من الويب (بدون صورة — تُرسل من البوت إن لزم)."""
    if user["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
        return False, "لا يوجد وصول لك حالياً — راجع الإدارة."
    product_name = str(product_name or "").strip()
    if len(product_name) < 2:
        return False, "أدخل اسم السلعة المطلوبة (حرفان على الأقل)."
    if len(product_name) > 500:
        return False, "الاسم طويل جداً."
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return False, "أدخل كمية صحيحة."
    if quantity < 1 or quantity > 100000:
        return False, "الكمية يجب أن تكون بين 1 و 100000."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO incoming_orders (requester_id, product_name, quantity) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user["id"], product_name, quantity))
            oid = cur.fetchone()[0]
        conn.commit()
        return True, {"id": oid}
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] incoming create failed: " + str(e))
        return False, "فشل تسجيل الطلب، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_incoming_advance(user, order_id, action):
    """إدارة طلب توريد (مدراء فقط): receive = استُلم من المورد، complete = جاهز."""
    if not _pwa_perm_map(user["role"])["admin"]:
        return False, None, "إدارة طلبات التوريد للمدراء فقط."
    if action not in ("receive", "complete"):
        return False, None, "إجراء غير معروف."
    if action == "receive":
        row = receive_incoming_order(order_id)
        if not row:
            return False, None, "الطلب غير موجود أو تم تغيير حالته."
        return True, row, None
    row = complete_incoming_order(order_id)
    if not row:
        return False, None, "الطلب غير موجود أو تم إنجازه سابقاً."
    return True, row, None


def _pwa_incoming_requester_tid(requester_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_id FROM users WHERE id = %s", (requester_id,))
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    finally:
        release_connection(conn)


def _pwa_api_movements(user, limit, date_from=None, date_to=None,
                       product_id=None, exclude_friday=False, q=None,
                       mov_type=None):
    """📋 v41: سجل الحركة بفلاتر كاملة — من تاريخ/إلى تاريخ + سلعة محددة
    + استثناء يوم الجمعة (ISODOW=5) + بحث بالاسم + ملخص إجمالي للفترة."""
    if not _pwa_perm_map(user["role"])["movements"]:
        return None
    limit = max(1, min(int(limit or 300), 500))
    where = ["1=1"]
    params = []
    if date_from:
        if not _PWA_DATE_RE.match(str(date_from)):
            return {"ok": False, "error": "تاريخ «من» غير صالح — الصيغة YYYY-MM-DD"}
        where.append("t.date >= %s::date")
        params.append(str(date_from))
    if date_to:
        if not _PWA_DATE_RE.match(str(date_to)):
            return {"ok": False, "error": "تاريخ «إلى» غير صالح — الصيغة YYYY-MM-DD"}
        where.append("t.date < (%s::date + INTERVAL '1 day')")  # شامل يوم «إلى»
        params.append(str(date_to))
    if product_id:
        try:
            where.append("t.product_id = %s")
            params.append(int(product_id))
        except (TypeError, ValueError):
            return {"ok": False, "error": "معرّف السلعة غير صالح."}
    if mov_type:
        if mov_type not in ("add", "remove", "reconcile"):
            return {"ok": False, "error": "نوع الحركة غير صالح."}
        # v69: تصنيف حقيقي لأزرار الإضافة/الإخراج/الجرد — كان العميل يرسل type منذ v48 والخادم يتجاهله
        where.append("t.type = %s")
        params.append(mov_type)
    if exclude_friday:
        # الأسبوع الجزائري: الجمعة راحة — ISODOW: الاثنين=1 … الجمعة=5
        where.append("EXTRACT(ISODOW FROM t.date) <> 5")
    if q:
        where.append("p.name ILIKE %s")
        params.append("%" + str(q).strip()[:80] + "%")
    wsql = " AND ".join(where)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id, t.type, t.quantity, t.date, t.reason, "
                "t.product_id, p.name AS product_name, "
                "COALESCE(u.display_name, u.first_name, u.username, "
                "'#' || u.telegram_id::text) AS user_name "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE " + wsql + " "
                "ORDER BY t.date DESC LIMIT %s", (params + [limit]))
            rows = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) AS cnt, "
                "COALESCE(SUM(t.quantity) FILTER (WHERE t.type = 'add'), 0) AS add_qty, "
                "COALESCE(SUM(t.quantity) FILTER (WHERE t.type = 'remove'), 0) AS remove_qty, "
                "COUNT(*) FILTER (WHERE t.type = 'add') AS add_cnt, "
                "COUNT(*) FILTER (WHERE t.type = 'remove') AS remove_cnt, "
                "COUNT(*) FILTER (WHERE t.type = 'reconcile') AS rec_cnt "
                "FROM transactions t JOIN products p ON p.id = t.product_id "
                "WHERE " + wsql, tuple(params))
            s = cur.fetchone()
        conn.commit()
        return {"ok": True, "movements": [dict(r) for r in rows],
                "summary": {"count": s["cnt"], "add_qty": s["add_qty"],
                            "remove_qty": s["remove_qty"], "add_cnt": s["add_cnt"],
                            "remove_cnt": s["remove_cnt"], "rec_cnt": s["rec_cnt"]}}
    finally:
        release_connection(conn)


def _pwa_api_low(user):
    if not _pwa_perm_map(user["role"])["low_stock"]:
        return None
    rows = get_low_stock_products()
    return {"ok": True, "products": [
        {"id": r["id"], "name": r["name"], "qty": r["quantity"],
         "min": r["minimum_stock"]} for r in rows]}


def _pwa_api_oil(user):
    if not _pwa_perm_map(user["role"])["oil"]:
        return None
    st = oil_ensure_row()
    readings = oil_recent_readings(6)
    rate = oil_compute_rate(readings)
    base = float(st.get("baseline_hours") or 0)
    cur_h = float(st.get("current_hours") or base)
    since = max(0.0, cur_h - base)
    return {"ok": True, "oil": {
        "has_cycle": bool(st.get("has_cycle")),
        "baseline": base, "current": cur_h, "since": round(since, 1),
        "limit": float(OIL_H_LIMIT), "red": float(OIL_H_RED),
        "rate": round(float(rate), 2) if rate else None,
        "last_change": st.get("last_change_at"),
        "readings": [{"reading": float(r[0]), "at": r[1]} for r in reversed(readings[-8:])],
    }}


# ═════ v88: الفواتير من التطبيق — نفس مسار فاتورة تلغرام حرفياً ═════
#  (نفس الجداول، نفس الخصم من المخزون، نفس إشعار العامل، نفس PDF)
#  الزبون ← السلع (بحث) ← السلة ← العامل ← إنشاء → PDF جاهز للتحميل.

_PWA_INV_MAX_ITEMS = 60          # سقف عناصر الفاتورة الواحدة
_PWA_INV_MAX_QTY = 100000.0      # سقف الكمية/المساحة
_PWA_INV_MAX_PRICE = 10000000.0  # سقف السعر


def _pwa_api_invoice_products(user, q):
    """بحث سلع للفاتورة: الاسم والسعر والكمية والنوع (لعرض الحقل المناسب)."""
    if not _pwa_perm_map(user["role"])["invoice"]:
        return None
    q = str(q or "").strip()[:80]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if q:
                cur.execute(
                    "SELECT id, name, quantity, selling_price FROM products "
                    "WHERE NOT archived AND name ILIKE %s "
                    "ORDER BY LOWER(name) LIMIT 25",
                    ("%" + q + "%",))
            else:
                cur.execute(
                    "SELECT id, name, quantity, selling_price FROM products "
                    "WHERE NOT archived ORDER BY LOWER(name) LIMIT 25")
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "name": r["name"],
            "qty": r["quantity"], "price": r.get("selling_price"),
            "ptype": _invoice_product_type(r["name"]),
        })
    return {"ok": True, "products": out}


def _pwa_api_invoice_workers(user):
    """قائمة العمال المتاحين لتجهيز الفاتورة — نفس استعلام فاتورة تلغرام."""
    if not _pwa_perm_map(user["role"])["invoice"]:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, telegram_id, COALESCE(first_name, username, '#' || id) AS name "
                "FROM users WHERE role IN ('admin', 'manager', 'stock', 'seller', 'external', 'customer') "
                "AND active = TRUE ORDER BY name")
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    return {"ok": True, "workers": [
        {"id": r[0], "telegram_id": r[1], "name": r[2]} for r in rows]}


def _pwa_invoice_build_items(items_raw, cur):
    """تحقق صارم وبناء عناصر الفاتورة — الأساسيات تُحسب في الخادم دائماً.
    يرجع (items, err). سلعة بالمعرف ← الاسم والنوع من القاعدة (لا ثقة بالعميل)،
    وسلعة يدوية بلا معرف مسموحة مثل البوت (لا خصم مخزون لها)."""
    import math as _inv_math
    if not isinstance(items_raw, list) or not (1 <= len(items_raw) <= _PWA_INV_MAX_ITEMS):
        return None, "عدد عناصر الفاتورة غير صالح (1 إلى " + str(_PWA_INV_MAX_ITEMS) + ")."
    # أسماء السلع ذات المعرفات تُجلب دفعة واحدة
    pids = []
    for it in items_raw:
        if not isinstance(it, dict):
            return None, "عنصر غير صالح في الفاتورة."
        try:
            pid = int(it.get("product_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            pids.append(pid)
    pname = {}
    if pids:
        cur.execute(
            "SELECT id, name, selling_price FROM products WHERE id = ANY(%s)",
            (pids,))
        for r in cur.fetchall():
            pname[r[0]] = (r[1], r[2])
    items = []
    for it in items_raw:
        try:
            pid = int(it.get("product_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            if pid not in pname:
                return None, "السلعة #" + str(pid) + " لم تعد موجودة في المخزون."
            name, _db_price = pname[pid]
        else:
            name = str(it.get("name") or "").strip()[:160]
            if not name:
                return None, "سلعة يدوية بلا اسم — اكتب اسمها."
        ptype = _invoice_product_type(name)
        try:
            qty = float(it.get("qty"))
            price = float(it.get("price"))
        except (TypeError, ValueError):
            return None, "كمية أو سعر غير صالح في «" + name + "»."
        if not (0 < qty <= _PWA_INV_MAX_QTY):
            return None, "كمية غير منطقية في «" + name + "»."
        if not (0 <= price <= _PWA_INV_MAX_PRICE):
            return None, "سعر غير منطقي في «" + name + "»."
        item = {"product_id": pid or None, "product_name": name, "ptype": ptype,
                "unit_price": price, "subtotal": 0.0}
        if ptype == "m2":
            try:
                box_area = float(it.get("box_area"))
            except (TypeError, ValueError):
                box_area = 0.0
            if not (0 < box_area <= 100):
                box_area = 1.44
            item["qty_m2"] = round(qty, 2)
            item["box_area"] = box_area
            item["num_boxes"] = _inv_math.ceil(qty / box_area)
            item["quantity"] = item["qty_m2"]
            item["subtotal"] = item["qty_m2"] * price
        elif ptype == "pcs":
            item["quantity"] = max(1, int(qty))
            item["subtotal"] = item["quantity"] * price
        else:  # plant
            try:
                length_m = float(it.get("length_m"))
            except (TypeError, ValueError):
                length_m = 0.45
            if not (0 < length_m <= 20):
                length_m = 0.45
            item["length_m"] = length_m
            item["qty_m"] = round(qty, 2)
            item["num_pieces"] = _inv_math.ceil(qty / length_m)
            item["quantity"] = item["qty_m"]
            item["subtotal"] = item["qty_m"] * price
        items.append(item)
    return items, None


def _pwa_api_invoice_create(user, customer, items_raw, worker_id):
    """إنشاء فاتورة من التطبيق — نفس منطق _invoice_finalize بالحرف:
    حفظ + خصم مخزون (تحذير دون رفض عند العجز كما في البوت) + سجل حركات.
    الإشعارات وPDF تجري في المُعالج غير المتزامن بعد نجاح الحفظ."""
    if not _pwa_perm_map(user["role"])["invoice"]:
        return None
    customer = str(customer or "").strip()[:120]
    if not customer:
        return False, "اكتب اسم الزبون أولاً."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            items, err = _pwa_invoice_build_items(items_raw, cur)
            if err:
                return False, err
            worker_id = int(worker_id or 0)
            cur.execute(
                "SELECT id, telegram_id, COALESCE(first_name, username, '#' || id) "
                "FROM users WHERE id = %s AND active = TRUE AND role IN "
                "('admin', 'manager', 'stock', 'seller', 'external', 'customer')",
                (worker_id,))
            wrow = cur.fetchone()
            if not wrow:
                return False, "اختر عاملاً صالحاً لتجهيز الفاتورة."
            total = round(sum(i["subtotal"] for i in items), 2)
            cur.execute(
                "INSERT INTO invoices (customer_name, worker_id, admin_id, total_amount, status, approved_at) "
                "VALUES (%s, %s, %s, %s, 'approved', NOW()) RETURNING id",
                (customer, wrow[0], user["id"], total))
            invoice_id = cur.fetchone()[0]
            for item in items:
                cur.execute(
                    "INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price, subtotal) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (invoice_id, item["product_id"], item["product_name"],
                     item["quantity"], item["unit_price"], item["subtotal"]))
                if item["product_id"]:
                    # نفس سلوك البوت: خصم بشرط الكفاية — عند العجز تحذير دون رفض
                    stock_qty = (item.get("num_boxes", item["quantity"])
                                 if item["ptype"] == "m2" else item["quantity"])
                    cur.execute(
                        "UPDATE products SET quantity = quantity - %s "
                        "WHERE id = %s AND quantity >= %s",
                        (stock_qty, item["product_id"], stock_qty))
                    if cur.rowcount == 0:
                        logging.warning("[INVOICE-PWA] مخزون غير كافٍ للسلعة #"
                                        + str(item["product_id"]) + " (المطلوب "
                                        + str(stock_qty) + ") — الفاتورة استمرت كبوت تلغرام")
                    cur.execute(
                        "INSERT INTO transactions (product_id, type, quantity, user_id) "
                        "VALUES (%s, 'remove', %s, %s)",
                        (item["product_id"], stock_qty, user["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return True, {
        "ok": True, "invoice_id": invoice_id, "total": total,
        "customer": customer, "worker_id": wrow[0],
        "worker_name": wrow[2], "worker_tid": wrow[1],
        "creator_tid": user.get("telegram_id"),
        "items": items, "pdf": "/api/invoice/pdf?id=" + str(invoice_id),
    }


def _pwa_api_invoice_recent(user, limit=8):
    """آخر الفواتير (لإعادة تحميل PDF أو مراجعة سريعة) — البائع يرى فواتيره فقط."""
    if not _pwa_perm_map(user["role"])["invoice"]:
        return None
    limit = max(1, min(int(limit or 8), 20))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            where = ""
            params = []
            if user["role"] == ROLE_SELLER:
                where = " WHERE v.admin_id = %s"
                params.append(user["id"])
            cur.execute(
                "SELECT v.id, v.customer_name, v.total_amount, v.status, v.created_at, "
                "COALESCE(u.first_name, u.username, '') FROM invoices v "
                "LEFT JOIN users u ON u.id = v.worker_id" + where +
                " ORDER BY v.id DESC LIMIT " + str(limit),
                params)
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        out.append({
            "id": r[0], "customer": r[1], "total": float(r[2] or 0),
            "status": r[3],
            "at": r[4].strftime("%Y/%m/%d %H:%M") if r[4] else "",
            "worker": r[5] or "",
        })
    return {"ok": True, "invoices": out}


def _pwa_api_invoice_list(user, q="", before_id=0, limit=15):
    """v88.1: سجل الفواتير الكامل — بحث بالزبون أو رقم الفاتورة + ترقيم بالمؤشر
    (before = آخر معرف ظهر) للوصول إلى أي فاتورة سابقة وتحميل PDF منها.
    البائع يرى فواتيره فقط."""
    if not _pwa_perm_map(user["role"])["invoice"]:
        return None
    try:
        limit = max(1, min(int(limit or 15), 30))
    except (TypeError, ValueError):
        limit = 15
    try:
        before_id = int(before_id or 0)
    except (TypeError, ValueError):
        before_id = 0
    q = str(q or "").strip()[:80]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            where = []
            params = []
            if user["role"] == ROLE_SELLER:
                where.append("v.admin_id = %s")
                params.append(user["id"])
            if q:
                cond = "v.customer_name ILIKE %s"
                params.append("%" + q + "%")
                if q.isdigit():
                    cond += " OR v.id = %s"
                    params.append(int(q))
                where.append("(" + cond + ")")
            if before_id > 0:
                where.append("v.id < %s")
                params.append(before_id)
            wsql = (" WHERE " + " AND ".join(where)) if where else ""
            cur.execute(
                "SELECT v.id, v.customer_name, v.total_amount, v.status, v.created_at, "
                "COALESCE(u.first_name, u.username, '') FROM invoices v "
                "LEFT JOIN users u ON u.id = v.worker_id" + wsql +
                " ORDER BY v.id DESC LIMIT " + str(limit + 1),
                params)
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    # صف إضافي واحد = يوجد المزيد (أرخص من COUNT على قاعدة حصة محدودة)
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = []
    for r in rows:
        out.append({
            "id": r[0], "customer": r[1], "total": float(r[2] or 0),
            "status": r[3],
            "at": r[4].strftime("%Y/%m/%d %H:%M") if r[4] else "",
            "worker": r[5] or "",
        })
    return {"ok": True, "invoices": out, "has_more": has_more}


def _pwa_api_invoice_get(user, invoice_id):
    """جلب فاتورة مع عناصرها — للمدير أي فاتورة، وللبائع فواتيره فقط."""
    if not _pwa_perm_map(user["role"])["invoice"]:
        return None
    try:
        invoice_id = int(invoice_id or 0)
    except (TypeError, ValueError):
        return False, "رقم فاتورة غير صالح."
    if invoice_id <= 0:
        return False, "رقم فاتورة غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT v.id, v.customer_name, v.total_amount, v.status, v.created_at, v.admin_id, "
                "COALESCE(u.first_name, u.username, '') AS worker_name "
                "FROM invoices v LEFT JOIN users u ON u.id = v.worker_id WHERE v.id = %s",
                (invoice_id,))
            inv = cur.fetchone()
            if not inv:
                return False, "الفاتورة غير موجودة."
            if user["role"] == ROLE_SELLER and inv["admin_id"] != user["id"]:
                return False, "هذه الفاتورة ليس من إنشائك."
            cur.execute(
                "SELECT product_id, product_name, quantity, unit_price, subtotal "
                "FROM invoice_items WHERE invoice_id = %s ORDER BY id",
                (invoice_id,))
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    items = []
    for r in rows:
        it = dict(r)
        it["ptype"] = _invoice_product_type(r["product_name"])
        items.append(it)
    return True, {
        "ok": True, "invoice_id": inv["id"], "customer": inv["customer_name"],
        "total": float(inv["total_amount"] or 0), "status": inv["status"],
        "at": inv["created_at"].strftime("%Y/%m/%d %H:%M") if inv["created_at"] else "",
        "worker_name": inv["worker_name"], "items": items,
    }


def _pwa_api_users(user):
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, u.email, u.phone, u.identity_verified, u.last_seen, "
                "a.username AS web_username "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "ORDER BY u.active DESC, u.role, LOWER(COALESCE(u.first_name, u.username, ''))")
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    out = []
    for r in rows:
        out.append({
            "telegram_id": r["telegram_id"], "name": r["first_name"] or r["tg_username"] or str(r["telegram_id"]),
            "role": r["role"], "role_label": ROLE_LABELS.get(r["role"], r["role"]),
            "active": bool(r["active"]), "web": bool(r["web_username"]),
            "web_username": r.get("web_username"),   # v65: كان ينقص — كان يظهر «اسم الدخول: undefined»
            "email": r.get("email"), "phone": r.get("phone"),
            "verified": bool(r.get("identity_verified")),
            "last_seen": r["last_seen"].isoformat() if r.get("last_seen") else "",   # v65: «متصل الآن»
            "protected": bool(_protected_account(r["telegram_id"])),
        })
    counts = {"all": len(out), "kicked": sum(1 for u in out if not u["active"]),
              "web": sum(1 for u in out if u["web"])}
    return {"ok": True, "users": out, "counts": counts}


def _pwa_api_toggle(user, target_tid, active):
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    if target_tid == user["telegram_id"]:
        return False, "لا يمكنك تنفيذ هذا الإجراء على نفسك."
    if not active and _protected_account(target_tid):
        return False, "لا يمكن إيقاف حساب محمي (مالك/بيئة)."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM users WHERE telegram_id = %s", (target_tid,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row:
        return False, "المستخدم غير موجود."
    if not active and row[0] == ROLE_ADMIN:
        return False, "لا يمكن إيقاف حساب بفئة إدارية — خفّض فئته أولاً."
    set_user_active(target_tid, bool(active))
    if not active:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE telegram_id = %s", (target_tid,))
                r2 = cur.fetchone()
                if r2:
                    cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (r2[0],))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            release_connection(conn)
    return True, ("تم إيقاف المستخدم." if not active else "تم إعادة تنشيط المستخدم.")


def _pwa_api_webpass(user, target_tid, password):
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    password = str(password or "")
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        return False, pol_msg
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, COALESCE(username, first_name) FROM users WHERE telegram_id = %s", (target_tid,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row:
        return False, "المستخدم غير موجود."
    uname = pwa_set_password(row[0], row[1] or ("u" + str(target_tid)), password)
    return True, "تم ضبط كلمة مرور الويب. اسم الدخول: " + uname


def _pwa_has_accounts():
    """هل توجد أي حسابات ويب؟ (يحدد ظهور شاشة الإعداد الأول)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pwa_auth")
            n = cur.fetchone()[0]
        conn.commit()
        return n > 0
    finally:
        release_connection(conn)


def _pwa_api_setup(name, password, confirm):
    """الإعداد الأول: أول من يفتح الرابط ينشئ حساب المدير بنفسه —
    بدون متغيرات بيئة ولا سكربتات. يعمل فقط ما دامت لا توجد أي
    حسابات ويب (pwa_auth فارغ)، وبعدها يُغلق للأبد.
    يربط الحساب بمدير فعلي من جدول users (يُفضّل المالك OWNER_IDS)."""
    name = str(name or "").strip()
    password = str(password or "")
    confirm = str(confirm or "")
    if _pwa_has_accounts():
        return False, None, "يوجد حساب مدير بالفعل — سجّل الدخول من الشاشة السابقة."
    if len(name) < 2:
        return False, None, "أدخل اسم مستخدم (حرفان على الأقل)."
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        return False, None, pol_msg
    if confirm and confirm != password:
        return False, None, "تأكيد كلمة المرور غير مطابق."
    try:
        owner_ids = [int(t) for t in (OWNER_IDS or [])]
    except Exception:
        owner_ids = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, COALESCE(username, first_name) FROM users "
                "WHERE role = 'admin' AND active "
                "ORDER BY (telegram_id = ANY(%s)) DESC, id LIMIT 1",
                (owner_ids,))
            row = cur.fetchone()
    finally:
        release_connection(conn)
    if not row:
        return False, None, "لا يوجد مدير في النظام بعد — افتح بوت تلغرام أولاً واضغط /start."
    try:
        uname = pwa_set_password(row[0], name or row[1] or "admin", password)
    except Exception as e:
        logging.error("[PWA] setup failed: " + str(e))
        return False, None, "فشل الإنشاء، أعد المحاولة."
    logging.info("[PWA] الإعداد الأول: أُنشئ حساب المدير «" + uname + "» من الويب")
    token = _pwa_create_session(row[0])
    return True, (token, uname), None


def _pwa_api_changepass(user, old_password, new_password):
    """تغيير كلمة مرور الويب الخاصة بالمستخدم الحالي نفسه."""
    old_password = str(old_password or "")
    new_password = str(new_password or "")
    pol_ok, pol_msg = _pwa_password_ok(new_password)
    if not pol_ok:
        return False, pol_msg
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pass_hash, username FROM pwa_auth WHERE user_id = %s",
                        (user["id"],))
            row = cur.fetchone()
    finally:
        release_connection(conn)
    if not row or not _pwa_verify_password(old_password, row[0]):
        time.sleep(0.4)
        return False, "كلمة المرور الحالية غير صحيحة."
    uname = pwa_set_password(user["id"], row[1] or user["name"], new_password)
    return True, "تم تغيير كلمة مرورك. اسم الدخول: " + uname


_PWA_AVATAR_RE = re.compile(r"^data:image/(jpeg|jpg|png|webp);base64,[A-Za-z0-9+/=\s]+$")
_PWA_AVATAR_MAX = 400_000          # ~300KB صورة بعد التشفير — حماية من التضخم
_PWA_UNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


def _pwa_api_profile(user, name=None, avatar=None):
    """👤 v41: تحديث الملف الشخصي — الاسم المعروض و/أو صورة الحساب.
    لا يمسّ اسم تلغرام ولا كلمة المرور. avatar="" = إزالة الصورة."""
    sets, params = [], []
    if name is not None:
        name = str(name).strip()
        if len(name) < 2:
            return False, "الاسم قصير — حرفان على الأقل."
        if len(name) > 60:
            return False, "الاسم طويل — 60 حرفاً كحد أقصى."
        sets.append("display_name = %s")
        params.append(name)
    if avatar is not None:
        avatar = str(avatar or "").strip()
        if avatar and (len(avatar) > _PWA_AVATAR_MAX or not _PWA_AVATAR_RE.match(avatar)):
            return False, "الصورة غير مدعومة أو كبيرة جداً — جرّب صورة أخرى."
        sets.append("avatar = %s")
        params.append(avatar or None)
    if not sets:
        return False, "لا يوجد ما يُحفظ."
    params.append(user["id"])
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET " + ", ".join(sets) + " WHERE id = %s",
                        tuple(params))
        conn.commit()
        return True, "تم حفظ ملفك الشخصي ✅"
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] profile failed: " + str(e))
        return False, "فشل الحفظ، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_profile_username(user, username, password):
    """🔑 v41: تغيير اسم الدخول — يتطلب تأكيد كلمة المرور الحالية
    (حماية احترافية من سرقة الحساب)، ويحافظ على تفرد الأسماء."""
    username = str(username or "").strip().lstrip("@")
    password = str(password or "")
    if not _PWA_UNAME_RE.match(username):
        return False, ("اسم الدخول: 3 إلى 40 حرفاً لاتينياً/رقماً "
                       "(يُسمح بالنقطة والشرطة والشرطة السفلية).")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pass_hash FROM pwa_auth WHERE user_id = %s", (user["id"],))
            row = cur.fetchone()
    finally:
        release_connection(conn)
    if not row or not _pwa_verify_password(password, row[0]):
        time.sleep(0.4)
        return False, "كلمة المرور الحالية غير صحيحة."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM pwa_auth WHERE LOWER(username) = LOWER(%s) "
                "AND user_id <> %s LIMIT 1", (username, user["id"]))
            if cur.fetchone():
                conn.rollback()
                return False, "اسم الدخول محجوز لمستخدم آخر — اختر غيره."
            cur.execute(
                "UPDATE pwa_auth SET username = %s, updated_at = NOW() WHERE user_id = %s",
                (username, user["id"]))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] profile username failed: " + str(e))
        return False, "فشل التغيير، أعد المحاولة."
    finally:
        release_connection(conn)
    return True, "تم تغيير اسم دخولك إلى «" + username + "» ✅"


# ── معالجات aiohttp ──

# ── v40: جدولة آمنة من أي خيط ──
# درس v40 المؤلم: الدوال المتزامنة (تسجيل/نسيت كلمة المرور) تُنفَّذ عبر _pwa_run
# في خيط executor — وasyncio.create_task من ذلك الخيط يرفع
# «RuntimeError: no running event loop» فيسقط الطلب كلياً برسالة 500
# رغم أن الحفظ في القاعدة تمّ قبله! (اختبارات v39 لم تلتقطه لأن TG_APP
# كان None عندها، وفي الإنتاج موجود). الحل: مرجع للحلقة الرئيسية + جدولة آمنة.
_PWA_MAIN_LOOP = None


def _pwa_set_main_loop():
    """تثبيت مرجع حلقة الأحداث الرئيسية — يُستدعى من داخل اللوب فقط."""
    global _PWA_MAIN_LOOP
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _PWA_MAIN_LOOP is not loop:
        _PWA_MAIN_LOOP = loop


def _pwa_schedule(coro):
    """جدولة كوروتين من أي سياق — بديل asyncio.create_task داخل الدوال
    المتزامنة التي تُنفَّذ في خيط executor. تعمل من اللوب ومن الخيوط معاً.
    يعيد Task (من اللوب) أو concurrent Future (من خيط) أو None عند الفشل."""
    if coro is None:
        return None
    try:
        asyncio.get_running_loop()
        return asyncio.create_task(coro)  # أنا داخل اللوب أصلاً
    except RuntimeError:
        pass
    loop = _PWA_MAIN_LOOP
    if loop is not None and not loop.is_closed():
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as e:
            logging.error("[PWA] schedule threadsafe failed: " + str(e))
    try:
        coro.close()
    except Exception:
        pass
    logging.error("[PWA] schedule failed: لا توجد حلقة أحداث رئيسية — أسقطت الكوروتين")
    return None


async def _pwa_run(fn, *a, **kw):
    _pwa_set_main_loop()  # v40: التقاط الحلقة الرئيسية تلقائياً لأول طلب
    return await asyncio.get_running_loop().run_in_executor(
        None, _pwa_functools.partial(fn, *a, **kw))


async def _pwa_auth_user(request):
    """يعيد المستخدم من الجلسة أو None (مع فحص الجلسة في executor)."""
    return await _pwa_run(_pwa_session_user, request)


async def pwa_page_handler(request):
    if request.path == "/sw.js":
        return web.Response(text=_PWA_SW_JS, content_type="application/javascript",
                            charset="utf-8", headers={"Cache-Control": "no-cache"})
    return web.Response(body=_pwa_read_html(), content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-cache"})


async def pwa_manifest_handler(request):
    manifest = {
        "name": "مخزون السيراميك",
        "short_name": "المخزون",
        "dir": "rtl", "lang": "ar",
        "start_url": "/", "scope": "/",
        "display": "standalone",
        "background_color": "#fdf6f0",     # v42: هوية السيراميك
        "theme_color": "#b45309",
        "icons": [{"src": "/icon.png", "sizes": "512x512",
                   "type": "image/jpeg", "purpose": "any"},
                  {"src": "/icon.png", "sizes": "512x512",
                   "type": "image/jpeg", "purpose": "maskable"}],
    }
    return web.json_response(manifest, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def pwa_icon_handler(request):
    # v88: الشعار من القاعدة — to_thread حتى لا يحجب أول نداء حلقة الأحداث
    body = await asyncio.to_thread(_pwa_icon_bytes)
    if not body:
        raise web.HTTPNotFound()
    # v41: الأيقونة الجديدة JPEG كامل الامتلاء (تُدوَّر بصرياً عبر CSS)
    return web.Response(body=body, content_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


async def pwa_login_handler(request):
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    username_raw = str(body.get("username", "")).strip()
    ip = request.remote or "؟"
    key = ip + "|" + username_raw.lower()
    # 🔒 حارس التخمين: قفل مؤقت بعد محاولات فاشلة متكررة (IP + اسم)
    try:
        await _pwa_run(_pwa_login_gate, key)
    except PwaLocked as lk:
        await _pwa_run(_audit_log, "lockout", None,
                       details="اسم: " + username_raw, ip=ip)
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة خاطئة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    ok, result, err = await _pwa_run(_pwa_api_login,
                                     username_raw, str(body.get("password", "")))
    if not ok:
        alerted = await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "login_fail", None,
                       details="اسم: " + username_raw, ip=ip)
        if alerted:
            # 🚨 لحظة تفعيل القفل: سجّل وأبلغ المالك عبر تلغرام
            await _pwa_run(_audit_log, "brute_force_lock", None,
                           details="اسم: " + username_raw, ip=ip)
            asyncio.create_task(_pwa_alert_owner(
                "🚨 تنبيه أمني\n" + _PWA_LOCK_ALERT_TEXT
                + "\n👤 الاسم المُجرّب: " + username_raw
                + "\n🌐 IP: " + ip
                + "\n⏳ تم قفل المحاولات 15 دقيقة."))
        # v35: من لم يؤكد بريده بعد يُوجّه لشاشة الرمز (الكود يميّزه للواجهة) — أُلغي في v39
        return _pwa_err(401, err)
    token, user = result
    await _pwa_run(_pwa_login_clear, key)
    await _pwa_run(_audit_log, "login", user, ip=ip)
    # v48: رمز «تذكّرني» — يولد مع كل دخول ناجح لدخول بضغطة لاحقاً
    remember = ""
    try:
        remember = await _pwa_run(_pwa_create_remember, user["id"])
    except Exception:
        remember = ""
    resp = _pwa_jr({"ok": True, "user": user, "remember": remember})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


async def pwa_logout_handler(request):
    user = await _pwa_auth_user(request)
    if user:
        await _pwa_run(_pwa_kill_sessions, user["id"])
    resp = _pwa_jr({"ok": True})
    resp.del_cookie(PWA_COOKIE, path="/")
    return resp


# ═══════════ v39: التسجيل بلا بريد — اسم دخول + رقم سر + موافقة الإدارة ═══════════

async def pwa_register_handler(request):
    """v39: طلب انضمام جديد بلا بريد — اسم كامل + اسم دخول + رقم سر قوي + فئة.
    الطلب يُحفظ «بانتظار موافقة الإدارة» ويُشعَر المدراء فوراً (تلغرام + جرس التطبيق).
    بلا SMTP وبلا رموز — إعداد صفري."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    try:
        ok, result, err = await _pwa_run(_pwa_api_register,
                                         str(body.get("name", "")),
                                         str(body.get("username", "")),
                                         str(body.get("password", "")),
                                         str(body.get("role", "")), ip)
    except Exception as e:
        # v40: أي عطل غير متوقع يعيد JSON واضحاً — لا صفحة 500 HTML تغذّي «خطأ غير متوقع»
        logging.error("[PWA] register handler crashed: " + str(e))
        return _pwa_err(500, "خطأ في الخادم، أعد المحاولة.")
    if not ok:
        await _pwa_run(_audit_log, "register_fail", None,
                       details="دور: " + str(body.get("role", "?")), ip=ip)
        return _pwa_err(400, err)
    await _pwa_run(_audit_log, "register", None,
                   details="اسم الدخول: " + str(result["username"])
                           + " فئة: " + str(body.get("role", "?"))
                           + (" (تحديث طلب معلّق)" if result.get("reused") else ""), ip=ip)
    return _pwa_jr({"ok": True, "username": result["username"],
                    "message": "وصل طلبك إلى إدارة المؤسسة — بانتظار الموافقة.\n"
                               "ستدخل التطبيق باسم دخولك ورقمك السري فور موافقتها."})


async def pwa_forgot_handler(request):
    """v39: نسيت كلمة المرور (عام بلا جلسة) — طلب استعادة يظهر للإدارة
    في تلغرام ولوحة المستخدمين، والتوليد بضغطة واحدة."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    # 🔒 حارس التخمين حتى هنا — يمنع إغراق الطلبات
    try:
        await _pwa_run(_pwa_login_gate, "forgot|" + ip)
    except PwaLocked as lk:
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    try:
        ok, err = await _pwa_run(_pwa_api_forgot_request,
                                 str(body.get("username", "")), ip)
    except Exception as e:
        # v40: عطل غير متوقع يعيد JSON واضحاً — لا 500 HTML
        logging.error("[PWA] forgot handler crashed: " + str(e))
        return _pwa_err(500, "خطأ في الخادم، أعد المحاولة.")
    if not ok:
        return _pwa_err(400, err)
    await _pwa_run(_audit_log, "forgot_request", None,
                   details="اسم: " + str(body.get("username", "")), ip=ip)
    return _pwa_jr({"ok": True,
                    "message": "وصل طلبك إلى الإدارة 🔔 — سيُعطى لك رقم سر مؤقت "
                               "من إدارة المؤسسة، أدخله ثم غيّره من «كلمة مروري»."})


async def pwa_resets_handler(request):
    """v39: قائمة طلبات استعادة الرقم السري (للمدير فقط)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذه الشاشة للمديرين فقط.")
    resets = await _pwa_run(_pwa_api_resets_list)
    return _pwa_jr({"ok": True, "resets": resets})


async def pwa_reset_generate_handler(request):
    """v39: توليد رقم سر مؤقت لطلب استعادة (للمدير فقط) —
    يُعرض الرقم للمدير مرة واحدة ليوصله للموظف شفهياً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        reset_id = int(body.get("id", 0))
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, uname_or_err, temp = await _pwa_run(_pwa_api_reset_generate, user, reset_id)
    if not ok:
        return _pwa_err(400, uname_or_err)
    await _pwa_run(_audit_log, "pwreset_gen", user,
                   details="اسم: " + str(uname_or_err),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "username": uname_or_err, "temp_password": temp,
                    "message": "الرقم المؤقت لـ " + str(uname_or_err)
                               + " — أعطه له شفهياً وسيسجّل الدخول به ثم يغيّره."})


async def pwa_registrations_handler(request):
    """قائمة طلبات التسجيل المنتظرة (للمدير)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذه الشاشة للمديرين فقط.")
    regs = await _pwa_run(_pwa_api_registrations_list, user)
    return _pwa_jr({"ok": True, "registrations": regs})


async def pwa_registration_delete_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        reg_id = int(body.get("id", 0))
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_registration_delete, user, reg_id)
    await _pwa_run(_audit_log, "register_delete" if ok else "register_delete_fail",
                   user, details=str(reg_id), ip=request.remote or "؟")
    if not ok:
        return _pwa_err(400, msg)
    return _pwa_jr({"ok": True, "message": msg})


async def _notify_admins_pwa_registration(reg_id, full_name, username, role_label):
    """v39: إشعار فوري للمدراء في تلغرام بطلب انضمام جديد من التطبيق
    مع أزرار سريعة: تفعيل كبائع / تفعيل كعامل مخزون / حذف الطلب.
    بلا بريد — اسم الدخول والرقم السري محفوظان منذ التسجيل."""
    if TG_APP is None:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 تفعيل كبائع",
                              callback_data="pwreg_ok_" + str(reg_id) + "_seller"),
         InlineKeyboardButton("📦 تفعيل كعامل مخزون",
                              callback_data="pwreg_ok_" + str(reg_id) + "_stock")],
        [InlineKeyboardButton("🗑 حذف الطلب", callback_data="pwreg_del_" + str(reg_id))],
    ])
    lines = [
        "🔔 طلب انضمام جديد من التطبيق:",
        "👤 " + str(full_name or ""),
        "🔑 اسم الدخول: " + str(username or ""),
        "🎯 الفئة المطلوبة: " + str(role_label or ""),
        "",
        "اضغط موافقة ليدخل التطبيق فوراً باسم الدخول ورقمه السري — بلا موافقة يبقى معلقاً.",
    ]
    text = "\n".join(lines)
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text, reply_markup=kb)
        except Exception as e:
            logging.error("[PWA] reg notify failed for " + str(aid) + ": " + str(e))


async def callback_pwreg_handler(update, context):
    """v34: أزرار الموافقة/الحذف على طلبات الانضمام (للمدير في تلغرام).
    الموافقة تُنشئ الحساب بالبريد والرقم السري المحفوظين وتُشعِر صاحبه بالبريد."""
    query = update.callback_query
    # 🔒 AUTH GUARD — المدير وحده
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = str(query.data or "").split("_")   # pwreg_ok_12_seller / pwreg_del_12
    action = parts[1] if len(parts) > 1 else ""
    try:
        reg_id = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        reg_id = 0
    if not reg_id:
        return
    if action == "ok":
        role = parts[3] if len(parts) > 3 else ""
        ok, msg = await asyncio.to_thread(_pwa_api_registration_approve, reg_id, role)
        await asyncio.to_thread(_audit_log, "pwreg_approve" if ok else "pwreg_approve_fail",
                                None, details=str(msg)[:120])
        try:
            await query.edit_message_text(("✅ " if ok else "❌ ") + str(msg))
        except Exception:
            pass
    elif action == "del":
        ok, msg = await asyncio.to_thread(_pwa_api_registration_delete, None, reg_id)
        await asyncio.to_thread(_audit_log, "pwreg_delete" if ok else "pwreg_delete_fail",
                                None, details=str(msg)[:120])
        try:
            await query.edit_message_text(("🗑 " if ok else "❌ ") + str(msg))
        except Exception:
            pass


async def _notify_admins_pwa_reset(reset_id, username):
    """v39: إشعار فوري للمدراء في تلغرام بطلب استعادة رقم سر —
    زر واحد يولّد رقماً مؤقتاً بضغطة."""
    if TG_APP is None:
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 توليد رقم سر مؤقت",
                              callback_data="pwreset_gen_" + str(reset_id))],
    ])
    text = ("🔑 طلب استعادة كلمة المرور من التطبيق:\n"
            "👤 اسم الدخول: " + str(username or "") + "\n\n"
            "اضغط الزر لتوليد رقم سر مؤقت — أعطه له شفهياً "
            "وسيغيّره بنفسه من «كلمة مروري» بعد الدخول.")
    seen = set()
    for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
        try:
            aid = int(admin_id)
        except (TypeError, ValueError):
            continue
        if aid in seen:
            continue
        seen.add(aid)
        try:
            await TG_APP.bot.send_message(chat_id=aid, text=text, reply_markup=kb)
        except Exception as e:
            logging.error("[PWA] reset notify failed for " + str(aid) + ": " + str(e))


async def callback_pwreset_handler(update, context):
    """v39: زر «توليد رقم سر مؤقت» على طلبات الاستعادة (للمدير في تلغرام).
    يولّد رقماً عشوائياً، يحفظه مكان القديم، يبطل جلسات صاحبه، ويعرضه هنا مرة واحدة."""
    query = update.callback_query
    # 🔒 AUTH GUARD — المدير وحده
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا الإجراء للمديرين فقط.", show_alert=True)
        return
    await query.answer()
    parts = str(query.data or "").split("_")   # pwreset_gen_12
    action = parts[1] if len(parts) > 1 else ""
    try:
        reset_id = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        reset_id = 0
    if action != "gen" or not reset_id:
        return
    ok, uname, temp = await asyncio.to_thread(_pwa_api_reset_generate, None, reset_id)
    await asyncio.to_thread(_audit_log,
                            "pwreset_gen" if ok else "pwreset_gen_fail",
                            None, details=str(uname)[:120])
    if ok:
        text = ("✅ تم توليد رقم سر مؤقت لـ: " + str(uname) + "\n\n"
                "🔑 الرقم المؤقت: <code>" + str(temp) + "</code>\n\n"
                "أعطه له شفهياً الآن — سيغيّره بنفسه من «كلمة مروري» بعد الدخول.\n"
                "⚠️ لن يظهر هذا الرقم مرة أخرى.")
    else:
        text = "❌ " + str(uname)
    try:
        await query.edit_message_text(text, parse_mode="HTML")
    except Exception:
        pass


async def pwa_otp_request_handler(request):
    """طلب رمز دخول سري (عام): إيميل/هاتف → يُسلَّم عبر تلغرام."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    identifier = str(body.get("identifier", "")).strip()
    ip = request.remote or "؟"
    key = ip + "|otp:" + identifier.lower()
    try:
        await _pwa_run(_pwa_login_gate, key)
    except PwaLocked as lk:
        await _pwa_run(_audit_log, "lockout", None, details="otp", ip=ip)
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة خاطئة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    found = await _pwa_run(_pwa_otp_find_user, identifier)
    if not found:
        alerted = await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "otp_request_fail", None, ip=ip)
        if alerted:
            await _pwa_run(_audit_log, "brute_force_lock", None, details="otp", ip=ip)
            asyncio.create_task(_pwa_alert_owner(
                "🚨 تنبيه أمني\nمحاولات تخمين متكررة لرموز الدخول (otp)\n🌐 IP: " + ip))
        return _pwa_err(401, "لا يوجد حساب مؤهل بهذه البيانات "
                             "(أو الحساب غير مربوط بالبوت / موقوف).")
    ok, code = await _pwa_run(_pwa_otp_issue, found["id"], ip)
    if not ok:
        return _pwa_err(429, code)
    # v33: القناة الأفضل أولاً — بريد حقيقي للإيميل / SMS للهاتف — وتلغرام شبكة أمان
    is_email = "@" in identifier
    channel = "telegram"
    sent = False
    if is_email and _pwa_otp_email_ready():
        sent = await asyncio.to_thread(
            _pwa_send_otp_email_sync, found.get("email") or identifier.lower(), code)
        if sent:
            channel = "email"
    elif not is_email and _pwa_otp_sms_ready():
        sent = await _pwa_send_otp_sms(found.get("phone"), code)
        if sent:
            channel = "sms"
    if not sent and TG_APP is not None:
        try:
            await TG_APP.bot.send_message(
                chat_id=found["telegram_id"],
                text="🔑 رمز الدخول إلى تطبيق المخزون:\n\n"
                     "<b>" + code + "</b>\n\n"
                     "⏳ صالح " + str(_PWA_OTP_EXPIRE_MIN) + " دقائق — لا تشاركه مع أحد.\n"
                     "إن لم تطلب هذا الرمز فتجاهل الرسالة.",
                parse_mode=ParseMode.HTML)
            sent = True
            channel = "telegram"
        except Exception as e:
            logging.error("[PWA] otp send failed: " + str(e))
    if not sent:
        await _pwa_run(_pwa_login_clear, key)
        return _pwa_err(503, "تعذّر إرسال الرمز — تأكد من إعدادات البريد/SMS على الخادم، "
                             "أو افتح بوت المؤسسة واضغط «ابدأ» ثم أعد المحاولة.")
    await _pwa_run(_audit_log, "otp_request", {"id": found["id"], "name": found["first_name"] or ""},
                   details=channel, ip=ip)
    return _pwa_jr({"ok": True, "masked": _pwa_mask_identifier(identifier), "channel": channel})


async def pwa_otp_verify_handler(request):
    """تحقق من الرمز وإنشاء الجلسة (عام)."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    identifier = str(body.get("identifier", "")).strip()
    code = str(body.get("code", "")).strip()
    ip = request.remote or "؟"
    key = ip + "|otp:" + identifier.lower()
    try:
        await _pwa_run(_pwa_login_gate, key)
    except PwaLocked as lk:
        await _pwa_run(_audit_log, "lockout", None, details="otp", ip=ip)
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة خاطئة — انتظر نحو " + str(mins)
                        + " دقيقة ثم أعد المحاولة.")
    found = await _pwa_run(_pwa_otp_find_user, identifier)
    if not found:
        await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "otp_fail", None, details="مستخدم غير معروف", ip=ip)
        return _pwa_err(401, "لا يوجد حساب مؤهل بهذه البيانات.")
    ok, err = await _pwa_run(_pwa_otp_verify, found["id"], code)
    if not ok:
        alerted = await _pwa_run(_pwa_login_fail, key)
        await _pwa_run(_audit_log, "otp_fail", {"id": found["id"], "name": found["first_name"] or ""}, ip=ip)
        if alerted:
            await _pwa_run(_audit_log, "brute_force_lock", None, details="otp", ip=ip)
            asyncio.create_task(_pwa_alert_owner(
                "🚨 تنبيه أمني\nمحاولات رموز خاطئة متكررة (otp)\n🌐 IP: " + ip))
        return _pwa_err(401, err)
    token = await _pwa_run(_pwa_create_session, found["id"])
    user = {"id": found["id"], "telegram_id": found["telegram_id"],
            "name": found["first_name"] or found["tg_username"] or identifier,
            "role": found["role"],
            "role_label": ROLE_LABELS.get(found["role"], found["role"]),
            "web_username": found.get("web_username")}
    await _pwa_run(_pwa_login_clear, key)
    await _pwa_run(_audit_log, "otp_login", user, ip=ip)
    resp = _pwa_jr({"ok": True, "user": user})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


# ═══════════ v33: دخول بحساب Google (OAuth 2.0 — مجاني) ═══════════
# ملاحظة APK: جوجل يحجب نافذة الدخول داخل WebView — التطبيق يفتحها في المتصفح الخارجي
# ثم يعود عبر الرابط العميق ceramicstock://auth?next=... (معالَج في MainActivity).
import base64 as _b64
import urllib.parse as _uparse


def _pwa_base_url(request):
    """العنوان الأساسي للتطبيق: RENDER_EXTERNAL_URL على Render، وإلا عنوان الطلب نفسه."""
    env = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if env:
        return env
    try:
        return str(request.url.origin())   # scheme://host[:port] — جذر التطبيق
    except Exception:
        return "http://" + str(getattr(request, "host", "localhost"))

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_STATE_TTL = 600          # صلاحية حالة CSRF بالثواني
_GOOGLE_OT_TTL = 900             # صلاحية رمز العودة/الانضمام بالثواني
_google_oauth_states = {}        # state → {"exp": epoch, "apk": bool}
_google_ots = {}                 # ot → {"exp": epoch, "kind": "session"|"join", ...}


def _google_redirect_uri():
    """عنوان العودة المسجّل في Google Console — يُضبط بمتغير GOOGLE_REDIRECT_URI
    أو يُشتق من RENDER_EXTERNAL_URL تلقائياً."""
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    base = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if base:
        return base + "/api/auth/google/callback"
    return ""


def _google_state_new(apk):
    state = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _google_oauth_states.items() if v["exp"] < now]:
        _google_oauth_states.pop(k, None)
    _google_oauth_states[state] = {"exp": now + _GOOGLE_STATE_TTL, "apk": apk}
    return state


def _google_state_take(state):
    rec = _google_oauth_states.pop(str(state or ""), None)
    if not rec or rec["exp"] < time.time():
        return None
    return rec


def _google_ot_new(kind, data):
    ot = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _google_ots.items() if v["exp"] < now]:
        _google_ots.pop(k, None)
    rec = {"exp": now + _GOOGLE_OT_TTL, "kind": kind}
    rec.update(data)
    _google_ots[ot] = rec
    return ot


def _google_ot_peek(ot):
    rec = _google_ots.get(str(ot or ""))
    if not rec or rec["exp"] < time.time():
        return None
    return rec


def _google_ot_take(ot):
    rec = _google_ot_peek(ot)
    if rec:
        _google_ots.pop(str(ot or ""), None)
    return rec


async def _google_exchange_code(code, redirect_uri):
    """استبدال كود التفويض بحِمْل id_token من خوادم جوجل مباشرة (TLS خادم←خادم).
    يعيد dict الحمولة (email/name/...) أو يرفع استثناء."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as sess:
        async with sess.post(GOOGLE_TOKEN_URL, data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or not isinstance(data, dict) or not data.get("id_token"):
                raise RuntimeError("google token http " + str(resp.status)
                                   + ": " + json.dumps(data, ensure_ascii=False)[:200])
    idt = str(data["id_token"]).split(".")
    if len(idt) < 2:
        raise RuntimeError("google id_token malformed")
    pad = "=" * (-len(idt[1]) % 4)
    payload = json.loads(_b64.urlsafe_b64decode(idt[1] + pad).decode("utf-8"))
    if payload.get("aud") != GOOGLE_CLIENT_ID:
        raise RuntimeError("google aud mismatch")
    if payload.get("exp", 0) < time.time():
        raise RuntimeError("google id_token expired")
    if not payload.get("email_verified"):
        raise RuntimeError("google email not verified")
    return payload


def _pwa_google_find_user(email):
    """بريد Google → مستخدم فريق نشط (غير زبون/منتظر) — بلا اشتراط تلغرام."""
    email = str(email or "").strip().lower()
    if not email:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.role, u.active, a.username AS web_username "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE u.active = TRUE AND u.role NOT IN (%s, %s) "
                "AND lower(u.email) = %s LIMIT 1",
                (ROLE_CUSTOMER, ROLE_PENDING, email))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] google find user failed: " + str(e))
        return None
    finally:
        release_connection(conn)


def _pwa_google_find_any_user(email):
    """بريد Google → أي صف مستخدم بهذا البريد (لعرض حالة الانتظار/التوقف)."""
    email = str(email or "").strip().lower()
    if not email:
        return None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, role, active FROM users WHERE lower(email) = %s LIMIT 1",
                        (email,))
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        release_connection(conn)


def _pwa_google_add_registration(email, name, role, ip):
    """طلب انضمام عبر Google — إن وُجد طلب منتظر بنفس البريد نحدّث فئته بدل التكرار."""
    email = str(email or "").strip().lower()
    name = str(name or "").strip()[:120] or email.split("@")[0]
    if not email:
        return False, "بريد غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM pwa_registrations "
                        "WHERE status = 'waiting' AND lower(email) = %s LIMIT 1", (email,))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE pwa_registrations SET full_name = %s, role = %s "
                            "WHERE id = %s RETURNING id", (name, role, row[0]))
                reg_id = cur.fetchone()[0]
            else:
                # عمود code = CHAR(6) — بادئة GO تميّز انضمام Google عن الطلبات الأخرى
                reg_id = None
                for _ in range(3):
                    try:
                        cur.execute(
                            "INSERT INTO pwa_registrations (code, full_name, email, phone, role, ip) "
                            "VALUES (%s, %s, %s, '', %s, %s) RETURNING id",
                            ("GO" + secrets.token_hex(2).upper(), name, email, role, ip))
                        reg_id = cur.fetchone()[0]
                        break
                    except Exception:
                        conn.rollback()
                        cur = conn.cursor()
                else:
                    raise RuntimeError("registration code collision")
        conn.commit()
        return True, int(reg_id)
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] google add registration failed: " + str(e))
        return False, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)


def _pwa_api_registration_approve(reg_id, role):
    """تفعيل طلب انضمام من التطبيق (للمدير): إنشاء/تحديث حساب المستخدم وتأكيد الطلب.
    v39: بلا بريد — اسم الدخول والرقم السري اللذان اختارهما العميل يُحفظان في
    pwa_auth ويدخل بهما فوراً بعد الموافقة. بلا إشعارات SMTP إطلاقاً.
    يعيد (ok, msg)."""
    try:
        reg_id = int(reg_id)
    except (TypeError, ValueError):
        return False, "معرّف غير صالح."
    if role not in ROLE_LABELS or role == ROLE_PENDING:
        return False, "فئة غير صالحة."
    conn = get_connection()
    reg_pass = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT full_name, email, username, role, password_hash "
                        "FROM pwa_registrations WHERE id = %s AND status = 'waiting'", (reg_id,))
            reg = cur.fetchone()
            if not reg:
                return False, "الطلب غير موجود أو مُفعَّل مسبقاً."
            full_name, reg_email, reg_username, reg_role, reg_pass = \
                reg[0], (reg[1] or "").strip().lower(), (reg[2] or "").strip(), reg[3], reg[4]
            final_role = role if role != ROLE_CUSTOMER else reg_role
            # v52: هل الاسم مطابق لحساب تلجرام موجود؟ اربط به بدل إنشاء صف مكرر
            # (حساب واحد لكل شخص — لا دمج في حساب مدير حمايةً من خداع الصلاحيات)
            name_key = " ".join((full_name or "").split()).lower()
            uid = None
            _applied_role = final_role   # v78: الفئة المطبقة فعلياً (قد تُبقى القديمة)
            _role_note = ""
            if name_key:
                cur.execute(
                    "SELECT id FROM users WHERE active = TRUE AND telegram_id IS NOT NULL "
                    "AND role <> 'admin' "
                    "AND LOWER(TRIM(COALESCE(NULLIF(display_name, ''), first_name, username, ''))) = %s "
                    "ORDER BY id", (name_key,))
                cands = cur.fetchall()
                # v52: نربط فقط عند مرشح تلجرام واحد — التعدد يُترك للمدير يدوياً
                if len(cands) == 1:
                    uid = cands[0][0]
            if uid is not None:
                # v78: الموظف القائم بفئة حقيقية لا تُكتَب فوق فئته عند ربط تسجيل بالاسم —
                # الربط يملأ الهوية فقط، وتغيير الفئة يبقى من زر «🎯 الفئة» الصريح.
                cur.execute("SELECT role FROM users WHERE id = %s", (uid,))
                _erow = cur.fetchone()
                _erole = _erow[0] if _erow else None
                _keep_role = _erole not in (None, ROLE_PENDING, ROLE_CUSTOMER)
                cur.execute(
                    "UPDATE users SET role = %s, active = TRUE, identity_verified = TRUE, "
                    "first_name = COALESCE(NULLIF(first_name, ''), %s), "
                    "email = COALESCE(email, %s) WHERE id = %s",
                    (_erole if _keep_role else final_role, full_name, (reg_email or None), uid))
                if _keep_role:
                    _applied_role = _erole
                    _role_note = (" — أُبقيت فئته الحالية («" + ROLE_LABELS.get(_erole, _erole)
                                  + "»)، لتغييرها استخدم زر 🎯 الفئة")
            elif reg_email:
                # v78: نفس الحماية عند الربط بالبريد — لا كتابة فوق فئة موظف قائم
                cur.execute("SELECT id, role FROM users WHERE lower(email) = %s "
                            "ORDER BY id LIMIT 1", (reg_email,))
                _ematch = cur.fetchone()
                _erole2 = _ematch[1] if _ematch else None
                _keep2 = _erole2 not in (None, ROLE_PENDING, ROLE_CUSTOMER)
                cur.execute("UPDATE users SET role = %s, active = TRUE, identity_verified = TRUE, "
                            "first_name = COALESCE(NULLIF(first_name, ''), %s) "
                            "WHERE lower(email) = %s RETURNING id",
                            (_erole2 if _keep2 else final_role, full_name, reg_email))
                if _keep2:
                    _applied_role = _erole2
                    _role_note = (" — أُبقيت فئته الحالية («" + ROLE_LABELS.get(_erole2, _erole2)
                                  + "»)، لتغييرها استخدم زر 🎯 الفئة")
                uid = cur.fetchone()
                if not uid:
                    cur.execute(
                        "INSERT INTO users (first_name, email, role, active, identity_verified) "
                        "VALUES (%s, %s, %s, TRUE, TRUE) RETURNING id",
                        (full_name, reg_email, final_role))
                    uid = cur.fetchone()
            else:
                cur.execute(
                    "INSERT INTO users (first_name, role, active, identity_verified) "
                    "VALUES (%s, %s, TRUE, TRUE) RETURNING id", (full_name, final_role))
                uid = cur.fetchone()
            uid = int(uid[0] if isinstance(uid, (tuple, list)) else uid)
            # v39: اسم الدخول من اختيار العميل (احتياطاً: البريد أو u<id> للحسابات القديمة)
            if reg_pass:
                auth_name = reg_username or reg_email or ("u" + str(uid))
                cur.execute("SELECT user_id FROM pwa_auth WHERE lower(username) = %s "
                            "AND user_id <> %s LIMIT 1", (auth_name.lower(), uid))
                taken = cur.fetchone()
                if taken:
                    auth_name = "u" + str(uid)
                cur.execute(
                    "INSERT INTO pwa_auth (user_id, username, pass_hash, updated_at) "
                    "VALUES (%s, %s, %s, NOW()) "
                    "ON CONFLICT (user_id) DO UPDATE SET pass_hash = EXCLUDED.pass_hash, "
                    "username = EXCLUDED.username, updated_at = NOW()",
                    (uid, auth_name, reg_pass))
            cur.execute("UPDATE pwa_registrations SET status = 'confirmed', confirmed_at = NOW() "
                        "WHERE id = %s", (reg_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] registration approve failed: " + str(e))
        return False, "خطأ في الخادم."
    finally:
        release_connection(conn)
    return True, ("✅ تمت الموافقة: " + str(full_name)
                  + " ← " + ROLE_LABELS.get(_applied_role, _applied_role)
                  + _role_note
                  + " — يدخل الآن باسم دخوله ورقمه السري.")


def _google_return_redirect(base, apk, next_url):
    """عودة ما بعد الموافقة: متصفح عادي → توجيه مباشر؛ APK → رابط عميق للمتصفح."""
    if apk:
        raise web.HTTPFound("ceramicstock://auth?next=" + _uparse.quote(next_url, safe=""))
    raise web.HTTPFound(next_url)


async def pwa_google_start_handler(request):
    """زر «الدخول بحساب Google» → نافذة اختيار الحسابات في جوجل."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return _pwa_err(503, "دخول Google غير مهيأ على الخادم.")
    ip = request.remote or "؟"
    try:
        await _pwa_run(_pwa_login_gate, "gstart|" + ip)
    except PwaLocked as lk:
        return _pwa_err(429, "محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة.")
    redirect_uri = _google_redirect_uri()
    if not redirect_uri:
        return _pwa_err(503, "لم يُضبط عنوان العودة (GOOGLE_REDIRECT_URI / RENDER_EXTERNAL_URL).")
    apk = request.query.get("app", "") == "1"
    state = _google_state_new(apk)
    url = (_GOOGLE_AUTH_URL + "?client_id=" + _uparse.quote(GOOGLE_CLIENT_ID)
           + "&redirect_uri=" + _uparse.quote(redirect_uri)
           + "&response_type=code&scope=" + _uparse.quote("openid email profile")
           + "&state=" + _uparse.quote(state) + "&prompt=select_account")
    raise web.HTTPFound(url)


async def pwa_google_callback_handler(request):
    """عودة جوجل بعد الموافقة: جلسة كاملة لأعضاء الفريق، أو طلب انضمام لبريد جديد."""
    ip = request.remote or "؟"
    try:
        await _pwa_run(_pwa_login_gate, "gcb|" + ip)
    except PwaLocked as lk:
        return _pwa_err(429, "محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة.")
    st = _google_state_take(request.query.get("state", ""))
    if not st:
        return _pwa_err(400, "جلسة Google منتهية أو مستهلكة — اضغط زر الدخول من جديد.")
    code = request.query.get("code", "")
    if not code:
        return _pwa_err(400, "لم تصل موافقة من Google — أعد المحاولة.")
    try:
        payload = await _google_exchange_code(code, _google_redirect_uri())
    except Exception as e:
        logging.error("[PWA] google callback failed: " + str(e))
        return _pwa_err(502, "تعذّر إتمام الدخول مع Google — أعد المحاولة.")
    email = str(payload.get("email") or "").strip().lower()
    if not email:
        return _pwa_err(502, "لم يُرجع Google بريداً صالحاً.")
    name = str(payload.get("name") or email.split("@")[0])[:120]
    base = _pwa_base_url(request)
    apk = bool(st.get("apk"))
    found = await _pwa_run(_pwa_google_find_user, email)
    if found:
        token = await _pwa_run(_pwa_create_session, found["id"])
        user = {"id": found["id"], "name": found["first_name"] or found["tg_username"] or name,
                "role": found["role"]}
        await _pwa_run(_audit_log, "google_login", user, details=email, ip=ip)
        if apk:
            ot = _google_ot_new("session", {"token": token})
            _google_return_redirect(base, True, base + "/api/auth/google/exchange?ot=" + _uparse.quote(ot))
        secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
        resp = web.HTTPFound(base + "/")
        resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                        httponly=True, samesite="Lax", secure=secure_cookie, path="/")
        raise resp
    # بريد غير معروف → عضوية قيد التفعيل؟
    any_row = await _pwa_run(_pwa_google_find_any_user, email)
    if any_row:
        # موجود لكنه منتظر/موقوف/زبون
        await _pwa_run(_audit_log, "google_login_fail", None,
                       details=email + " — الفئة: " + str(any_row.get("role")), ip=ip)
        _google_return_redirect(base, apk, base + "/?google=wait")
    if not _pwa_self_register_enabled():
        return _pwa_err(403, "التسجيل الذاتي معطّل حالياً — راجع إدارة المؤسسة.")
    ot = _google_ot_new("join", {"email": email, "name": name})
    await _pwa_run(_audit_log, "google_join_start", None, details=email, ip=ip)
    if apk:
        _google_return_redirect(base, True, base + "/api/auth/google/exchange?ot=" + _uparse.quote(ot))
    _google_return_redirect(base, False, base + "/?google=join&ot=" + _uparse.quote(ot)
                            + "&name=" + _uparse.quote(name) + "&email=" + _uparse.quote(email))


async def pwa_google_exchange_handler(request):
    """APK: تحويل رمز العودة إلى جلسة داخل WebView (كوكي) أو شاشة انضمام."""
    ot = request.query.get("ot", "")
    rec = _google_ot_take(ot)
    base = _pwa_base_url(request)
    if not rec:
        raise web.HTTPFound(base + "/?google=expired")
    if rec["kind"] == "session":
        secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
        resp = web.HTTPFound(base + "/")
        resp.set_cookie(PWA_COOKIE, rec["token"], max_age=PWA_SESSION_DAYS * 86400,
                        httponly=True, samesite="Lax", secure=secure_cookie, path="/")
        raise resp
    if rec["kind"] == "join":
        # لا نستهلك الرمز هنا — يُستهلك عند إرسال الفئة فعلياً
        ot2 = _google_ot_new("join", {"email": rec["email"], "name": rec.get("name", "")})
        raise web.HTTPFound(base + "/?google=join&ot=" + _uparse.quote(ot2)
                            + "&name=" + _uparse.quote(rec.get("name", ""))
                            + "&email=" + _uparse.quote(rec.get("email", "")))
    raise web.HTTPFound(base + "/")


async def pwa_google_join_handler(request):
    """إرسال الفئة المختارة لطلب انضمام Google (عام — بمفتاح ot)."""
    try:
        body = await request.json()
        ot = str(body.get("ot", "")).strip()
        role = str(body.get("role", "")).strip()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    try:
        await _pwa_run(_pwa_login_gate, "gjoin|" + ip)
    except PwaLocked as lk:
        return _pwa_err(429, "محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة.")
    rec = _google_ot_peek(ot)
    if not rec or rec.get("kind") != "join":
        return _pwa_err(400, "انتهت صلاحية طلب الانضمام — سجّل الدخول بجوجل من جديد.")
    if role not in _PWA_ROLES_SELF:
        return _pwa_err(400, "الفئة المتاحة: بائع أو عامل مخزون فقط.")
    _google_ot_take(ot)   # الرمز يُستهلك فقط بعد نجاح التحقق من الفئة
    ok, res = await _pwa_run(_pwa_google_add_registration, rec["email"], rec.get("name", ""), role, ip)
    if not ok:
        return _pwa_err(500, res or "خطأ في الخادم.")
    await _pwa_run(_audit_log, "google_join",
                   {"name": rec.get("name", "")}, details=rec["email"] + " ← " + role, ip=ip)
    # v34: إشعار فوري للمدراء في تلغرام بأزرار الموافقة/الحذف
    if isinstance(res, int):
        asyncio.create_task(_notify_admins_pwa_registration(
            res, rec.get("name", ""), rec["email"], ROLE_LABELS.get(role, role)))
    return _pwa_jr({"ok": True,
                    "message": "تم استلام طلبك — عضويتك في انتظار تفعيل الإدارة. "
                               "سجّل الدخول بجوجل مجدداً بعد التفعيل."})


async def pwa_registration_approve_handler(request):
    """تفعيل عضوية منتظرة من التطبيق (للمدير) — دخول Google بلا تلغرام."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        reg_id = body.get("id")
        role = str(body.get("role", "")).strip()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_registration_approve, reg_id, role)
    await _pwa_run(_audit_log, "registration_approve" if ok else "registration_approve_fail",
                   user, details=str(msg)[:120], ip=request.remote or "؟")
    if not ok:
        return _pwa_err(400, msg)
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_users_role_handler(request):
    """تغيير فئة مستخدم من التطبيق (للمدير) — ترقية المدير من هنا أيضاً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["users"]:
        return _pwa_err(403, "هذا الإجراء للمديرين فقط.")
    try:
        body = await request.json()
        target_tid = int(body.get("telegram_id", 0))
        role = str(body.get("role", "")).strip()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_user_role, user, target_tid, role)
    await _pwa_run(_audit_log, "user_role" if ok else "user_role_fail", user,
                   details="الهدف: " + str(target_tid) + " ← " + str(role),
                   ip=request.remote or "؟")
    if not ok:
        return _pwa_err(400, msg)
    # إشعار المستخدم في تلغرام إن أمكن (مثل نظام البوت)
    if TG_APP is not None:
        try:
            await TG_APP.bot.send_message(
                chat_id=target_tid,
                text="🎯 حدّثت إدارة المؤسسة فئتك في نظام المخزون:\n"
                     "<b>" + ROLE_LABELS.get(role, role) + "</b>\n"
                     "📱 افتح التطبيق وستُطبَّق صلاحياتك الجديدة تلقائياً.",
                parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error("[PWA] role notify failed: " + str(e))
    return _pwa_jr({"ok": True, "message": msg})


def _pwa_guard(user, need):
    """فحص موحّد: جلسة صالحة + نشط + صلاحية مطلوبة. يرجع (user, error_response)."""
    if user is None:
        return None, _pwa_err(401, "انتهت الجلسة — سجّل الدخول من جديد.")
    if not user["active"]:
        return None, _pwa_err(403, KICK_NOTICE)
    if need and not _pwa_perm_map(user["role"])[need]:
        return None, _pwa_err(403, "ليست لديك صلاحية لهذه الشاشة.")
    return user, None


async def pwa_me_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    return _pwa_jr(await _pwa_run(_pwa_api_me, user))


async def pwa_stats_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    return _pwa_jr(await _pwa_run(_pwa_api_stats, user))


async def pwa_products_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["search"]:
        return _pwa_err(403, "ليست لديك صلاحية البحث.")
    q = (request.query.get("q") or "").strip()[:120]
    show_archived = (request.query.get("archived") in ("1", "true"))
    show_low = (request.query.get("low") in ("1", "true"))   # v43: فلتر المنخفضة
    return _pwa_jr(await _pwa_run(_pwa_api_products, user, q, show_archived, show_low))


async def pwa_qty_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    try:
        pid = int(body.get("product_id"))
        qty = int(body.get("quantity", 0))
    except (TypeError, ValueError):
        return _pwa_err(400, "قيم غير صحيحة.")
    qtype = str(body.get("type", ""))
    ok, msg = await _pwa_run(_pwa_api_qty, user, pid, qtype, qty, body.get("reason"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "qty_" + qtype, user,
                   details="سلعة #" + str(pid) + " × " + str(qty)
                           + (" — سبب: " + str(body.get("reason", "")).strip()[:150]
                              if str(body.get("reason", "")).strip() else ""),
                   ip=request.remote or "؟")
    # التسوية الذاتية مثل البوت: كل زيادة كمية تغطي العجز المعلق FIFO
    rec = None
    if qtype in ("add", "reconcile"):
        rec = await _pwa_run(auto_reconcile_product, pid, qty, user["id"])
    if rec and rec.get("fulfilled") and TG_APP is not None:
        asyncio.create_task(_send_fulfilled_notifications(TG_APP.bot, rec["fulfilled"]))
    # (v30) إشعار الجميع: جرس تطبيق الويب + بث تلغرام — تزامن تام بين الطرفين
    try:
        _reason = str(body.get("reason") or "").strip()
        _pname = await _pwa_run(_product_name_fast, pid)
        if qtype == "add":
            _title = ("🚚 وصول شاحنة" if "شاحنة" in _reason
                      else ("↩️ إرجاع من زبون" if ("إرجاع" in _reason or "ارجاع" in _reason)
                            else "📥 إضافة كمية"))
            _line = "➕ الكمية: " + str(qty)
        elif qtype == "remove":
            _title = ("💰 " + _reason) if _reason else "📤 إخراج كمية"
            _line = "➖ الكمية: " + str(qty)
        else:
            _title, _line = "⚖️ جرد المخزون", "⚖️ الكمية بعد الجرد: " + str(qty)
        _body = "📦 " + _pname + "\n" + _line + (("\n📌 " + _reason) if _reason else "")
        # v66: البث التلغرامي للحركات المميزة فقط (إرجاع/شاحنة/رابح/حسان) —
        # وبيع لزبون والتصحيح والجرد يبقيان مسجّلين داخلياً بلا إزعاج تلغرام
        asyncio.create_task(notify_everyone(qtype, _title, _body,
                                            str(user.get("name") or ""), "web",
                                            tg=_pwa_tg_worthy_reason(_reason)))
    except Exception as _ne:
        logging.warning("[PWA] notify failed: " + str(_ne))
    return _pwa_jr({"ok": True, "message": msg,
                    "reconciled": (rec or {}).get("total_covered", 0)})


async def pwa_products_create_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "إضافة سلعة للمدراء فقط.")
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(
        _pwa_api_add_product, user, body.get("name"), body.get("quantity", 0),
        body.get("minimum_stock", 5), body.get("price", 0), body.get("location", ""))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "product_create", user,
                   details="اسم: " + str(body.get("name", ""))[:150],
                   ip=request.remote or "؟")
    # (v30) إشعار الجميع بسلعة جديدة — جرس التطبيق + تلغرام
    try:
        _nqty = int(body.get("quantity", 0) or 0)
        _nloc = str(body.get("location") or "").strip()
        _nbody = "📦 " + str(body.get("name", ""))[:200] + "\n➕ الكمية الابتدائية: " + str(_nqty)
        if _nloc:
            _nbody += "\n📍 الموقع: " + _nloc[:100]
        asyncio.create_task(notify_everyone(
            "new_item", "🆕 سلعة جديدة!", _nbody, str(user.get("name") or ""), "web"))
    except Exception:
        pass
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_products_edit_handler(request):
    """✏️ v41: تعديل سلعة — اسم + (موقع/سعر/حد أدنى اختياري)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "تعديل السلع للمدراء فقط.")
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, info, emsg = await _pwa_run(
        _pwa_api_product_edit, user, body.get("id"), body.get("name"),
        location=body.get("location"), price=body.get("price"),
        minimum_stock=body.get("minimum_stock"))
    if not ok:
        return _pwa_err(400, emsg)
    await _pwa_run(_audit_log, "product_edit", user,
                   details=("سلعة #" + str(body.get("id")) + ": "
                            + str((info or {}).get("old_name", "")) + " ← "
                            + str((info or {}).get("new_name", ""))),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": "تم حفظ التعديلات ✅"})


async def pwa_products_archive_handler(request):
    """🗑 v41: حذف ناعم / استعادة سلعة — السجل محفوظ دائماً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "حذف السلع للمدراء فقط.")
    try:
        body = await request.json()
        pid = int(body.get("id"))
        archived = bool(body.get("archived", True))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, info, emsg = await _pwa_run(_pwa_api_product_archive, user, pid, archived)
    if not ok:
        return _pwa_err(400, emsg)
    await _pwa_run(_audit_log, "product_restore" if not archived else "product_archive",
                   user, details="سلعة #" + str(pid) + " — " + str((info or {}).get("name", "")),
                   ip=request.remote or "؟")
    try:
        _pname = str((info or {}).get("name", ""))[:200]
        if archived:
            asyncio.create_task(notify_everyone(
                "info", "🗑 حُذفت سلعة",
                "📦 " + _pname + "\nأُخفيت من المخزون — سجل حركتها محفوظ كاملاً.",
                str(user.get("name") or ""), "web"))
        else:
            asyncio.create_task(notify_everyone(
                "info", "♻️ أُعيدت سلعة",
                "📦 " + _pname + "\nعادت إلى قوائم المخزون.",
                str(user.get("name") or ""), "web"))
    except Exception:
        pass
    return _pwa_jr({"ok": True, "message":
                    ("تم حذف السلعة — سجل حركتها محفوظ ويمكن استعادتها." if archived
                     else "تمت استعادة السلعة ✅")})


async def pwa_profile_handler(request):
    """👤 v41: تحديث الاسم المعروض و/أو صورة الحساب الشخصي."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_profile, user,
                             name=body.get("name"), avatar=body.get("avatar"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "profile_update", user, ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_profile_username_handler(request):
    """🔑 v41: تغيير اسم الدخول — بتأكيد كلمة المرور."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_profile_username, user,
                             body.get("username"), body.get("password"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "profile_username", user, details=str(body.get("username", ""))[:60],
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg, "username": str(body.get("username", "")).lstrip("@")})


async def pwa_pending_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_pending, user)
    return _pwa_jr(data)


async def pwa_incoming_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_incoming_list, user)
    return _pwa_jr(data)


async def pwa_receipt_upload_handler(request):
    """📸 (v31) رفع صورة وصل التوريد من التطبيق — base64 في JSON."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_receipt_upload, user,
                             body.get("id"), body.get("photo"), body.get("mime"))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "receipt_upload", user,
                   details="وصل طلب توريد #" + str(body.get("id")),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_receipt_get_handler(request):
    """🖼️ (v31) عرض صورة الوصل — رد ثنائي بصورة المايم المناسبة."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        oid = int(request.match_info["oid"])
    except (KeyError, TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    data, mime, err2 = await _pwa_run(_pwa_api_receipt_get, user, oid)
    if err2:
        return _pwa_err(404, err2)
    return web.Response(body=data, content_type=(mime or "image/jpeg"),
                        headers={"Cache-Control": "no-store"})


async def pwa_product_detail_handler(request):
    """📋 (v31) تفاصيل السلعة + سجل حركاتها."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data, err2 = await _pwa_run(_pwa_api_product_detail, user,
                                request.match_info.get("pid"))
    if err2:
        return _pwa_err(404, err2)
    return _pwa_jr({"ok": True, "product": data})


async def pwa_incoming_create_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, res = await _pwa_run(_pwa_api_incoming_create, user,
                             body.get("product_name"), body.get("quantity", 1))
    if not ok:
        return _pwa_err(400, res)
    await _pwa_run(_audit_log, "incoming_create", user,
                   details="سلعة: " + str(body.get("product_name", ""))[:150]
                           + " × " + str(body.get("quantity", 1)),
                   ip=request.remote or "؟")
    if TG_APP is not None:
        note = ("🚚 طلب توريد جديد من تطبيق الويب\n\n"
                "📦 السلعة: " + str(body.get("product_name", ""))[:200] + "\n"
                "🔢 الكمية: " + str(body.get("quantity", 1)) + "\n"
                "👤 الطالب: " + str(user.get("name", "?")))
        for aid in ADMIN_IDS:
            try:
                asyncio.create_task(TG_APP.bot.send_message(chat_id=aid, text=note))
            except Exception:
                pass
    return _pwa_jr({"ok": True, "message": "تم تسجيل طلب التوريد ✅ — وصل للمدير فوراً."})


async def pwa_incoming_advance_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    if not _pwa_perm_map(user["role"])["admin"]:
        return _pwa_err(403, "إدارة طلبات التوريد للمدراء فقط.")
    try:
        body = await request.json()
        oid = int(body.get("id"))
        action = str(body.get("action", ""))
    except (TypeError, ValueError):
        return _pwa_err(400, "قيم غير صحيحة.")
    ok, row, msg = await _pwa_run(_pwa_api_incoming_advance, user, oid, action)
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "incoming_advance", user,
                   details="طلب #" + str(oid) + " ← " + action,
                   ip=request.remote or "؟")
    if action == "complete" and TG_APP is not None and row:
        tid = await _pwa_run(_pwa_incoming_requester_tid, row.get("requester_id"))
        if tid:
            note = ("✅ طلب التوريد الذي طلبته أصبح جاهزاً\n\n"
                    "📦 " + str(row.get("product_name", ""))[:200] + "\n"
                    "🔢 الكمية: " + str(row.get("quantity", "?")))
            try:
                asyncio.create_task(TG_APP.bot.send_message(chat_id=tid, text=note))
            except Exception:
                pass
    return _pwa_jr({"ok": True,
                    "message": "تم الاستلام — الطلب قيد التجهيز." if action == "receive"
                    else "تم تعليم الطلب جاهزاً ✅"})


async def pwa_movements_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(
        _pwa_api_movements, user, request.query.get("limit"),
        date_from=request.query.get("from"),
        date_to=request.query.get("to"),
        product_id=request.query.get("product"),
        exclude_friday=(request.query.get("nofriday") in ("1", "true", "yes")),
        q=request.query.get("q"),
        mov_type=request.query.get("type"))   # v69: فلتر النوع
    if data is None:
        return _pwa_err(403, "ليست لديك صلاحية الحركة.")
    if isinstance(data, dict) and data.get("ok") is False:
        return _pwa_err(400, data.get("error", "فلاتر غير صالحة."))
    return _pwa_jr(data)


async def pwa_low_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_low, user)
    if data is None:
        return _pwa_err(403, "ليست لديك صلاحية المخزون المنخفض.")
    return _pwa_jr(data)


async def pwa_oil_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_oil, user)
    if data is None:
        return _pwa_err(403, "شاشة الزيت للمدراء فقط.")
    return _pwa_jr(data)


# ── v88: معالجات الفواتير من التطبيق ──

async def pwa_invoice_products_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    q = request.query.get("q", "")
    data = await _pwa_run(_pwa_api_invoice_products, user, q)
    if data is None:
        return _pwa_err(403, "الفواتير للمدراء والبائعين فقط.")
    return _pwa_jr(data)


async def pwa_invoice_workers_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_invoice_workers, user)
    if data is None:
        return _pwa_err(403, "الفواتير للمدراء والبائعين فقط.")
    return _pwa_jr(data)


async def pwa_invoice_create_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    result = await _pwa_run(_pwa_api_invoice_create, user,
                            str(body.get("customer", "")),
                            body.get("items"), body.get("worker_id"))
    if result is None:
        return _pwa_err(403, "الفواتير للمدراء والبائعين فقط.")
    ok, payload = result
    if not ok:
        return _pwa_err(400, payload)
    # إشعارات تلغرام (أفضل جهد — لا تفشل عملية الإنشاء إن تعذّر الإرسال)
    r = payload
    bot = TG_APP.bot if TG_APP is not None else None
    if bot is not None:
        now_str = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=1))).strftime("%Y/%m/%d %H:%M")
        worker_msg = ("🧾 فاتورة جديدة للتجهيز!" + chr(10) + chr(10)
                      + "👤 الزبون: " + r["customer"] + chr(10)
                      + "📅 " + now_str + chr(10) + "━" * 15 + chr(10))
        for i, item in enumerate(r["items"], 1):
            pt = item["ptype"]
            if pt == "pcs":
                worker_msg += (str(i) + ". " + item["product_name"] + " — "
                               + str(item["quantity"]) + " قطعة" + chr(10))
            elif pt == "plant":
                worker_msg += (str(i) + ". " + item["product_name"] + " — "
                               + str(item["qty_m"]) + " م.ط" + chr(10))
            else:
                worker_msg += (str(i) + ". " + item["product_name"] + " — "
                               + str(item["qty_m2"]) + "m² → "
                               + str(item["num_boxes"]) + " علبة" + chr(10))
        worker_msg += ("━" * 15 + chr(10) + "💰 الإجمالي: "
                       + _fmt_price(r["total"]) + " د.ج" + chr(10) + chr(10)
                       + "⚙️ يرجى تجهيز البضاعة فوراً")
        try:
            if r.get("worker_tid"):
                await bot.send_message(chat_id=int(r["worker_tid"]), text=worker_msg)
        except Exception as w_err:
            logging.warning("[INVOICE-PWA] worker notify failed: " + str(w_err))
        # PDF للمنشئ — نفس تجربة فاتورة تلغرام
        try:
            if r.get("creator_tid"):
                pdf_path = await asyncio.to_thread(
                    _generate_invoice_pdf, r["invoice_id"], r["customer"],
                    r["items"], r["total"], r["worker_name"])
                if pdf_path:
                    with open(pdf_path, "rb") as f:
                        await bot.send_document(
                            chat_id=int(r["creator_tid"]), document=f,
                            filename="invoice_" + str(r["invoice_id"]) + ".pdf",
                            caption=("🧾 فاتورة #" + str(r["invoice_id"]) + " - " + r["customer"]
                                     + chr(10) + "💰 الإجمالي: " + _fmt_price(r["total"]) + " د.ج"
                                     + chr(10) + "👷 العامل: " + r["worker_name"]
                                     + chr(10) + "📱 من التطبيق"))
                    os.remove(pdf_path)
        except Exception as pdf_err:
            logging.warning("[INVOICE-PWA] PDF send failed: " + str(pdf_err))
    await _pwa_run(_audit_log, "invoice_create", user,
                   details="فاتورة #" + str(r["invoice_id"]) + " — " + r["customer"]
                           + " — " + _fmt_price(r["total"]) + " د.ج — العامل: " + r["worker_name"],
                   ip=request.remote or "؟")
    return _pwa_jr(r)


async def pwa_invoice_recent_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        limit = int(request.query.get("limit", "8"))
    except ValueError:
        limit = 8
    data = await _pwa_run(_pwa_api_invoice_recent, user, limit)
    if data is None:
        return _pwa_err(403, "الفواتير للمدراء والبائعين فقط.")
    return _pwa_jr(data)


async def pwa_invoice_list_handler(request):
    """v88.1: سجل الفواتير الكامل — q للبحث وbefore للمؤشر وlimit للحجم."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    q = request.query.get("q", "")
    try:
        before_id = int(request.query.get("before", "0"))
    except ValueError:
        before_id = 0
    try:
        limit = int(request.query.get("limit", "15"))
    except ValueError:
        limit = 15
    data = await _pwa_run(_pwa_api_invoice_list, user, q, before_id, limit)
    if data is None:
        return _pwa_err(403, "الفواتير للمدراء والبائعين فقط.")
    return _pwa_jr(data)


async def pwa_invoice_pdf_handler(request):
    """تحميل PDF الفاتورة — يُبنى لحظياً من القاعدة بنفس مولّد البوت."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        inv_id = int(request.query.get("id", "0"))
    except ValueError:
        return _pwa_err(400, "رقم فاتورة غير صالح.")
    data = await _pwa_run(_pwa_api_invoice_get, user, inv_id)
    if data is None:
        return _pwa_err(403, "الفواتير للمدراء والبائعين فقط.")
    ok, inv = data
    if not ok:
        return _pwa_err(404, inv)
    pdf_path = await asyncio.to_thread(
        _generate_invoice_pdf, inv["invoice_id"], inv["customer"],
        inv["items"], inv["total"], inv["worker_name"])
    if not pdf_path:
        return _pwa_err(500, "تعذر توليد PDF — أعد المحاولة.")
    try:
        with open(pdf_path, "rb") as f:
            body = f.read()
    finally:
        try:
            os.remove(pdf_path)
        except Exception:
            pass
    fname = "invoice_" + str(inv["invoice_id"]) + ".pdf"
    # v88.1: disp=inline يفتح PDF بالمتصفح للمعاينة — الافتراضي تنزيل مرفق
    disp = request.query.get("disp", "attachment")
    if disp not in ("attachment", "inline"):
        disp = "attachment"
    return web.Response(
        body=body, content_type="application/pdf",
        headers={"Content-Disposition": disp + '; filename="' + fname + '"'})


async def pwa_users_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_users, user)
    if data is None:
        return _pwa_err(403, "شاشة المستخدمين للمدراء فقط.")
    return _pwa_jr(data)


async def pwa_users_toggle_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        tid = int(body.get("telegram_id"))
        active = bool(body.get("active"))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_toggle, user, tid, active)
    if ok is None:
        return _pwa_err(403, "للمدراء فقط.")
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "user_toggle", user,
                   details=("تفعيل" if active else "طرد") + " المستخدم #" + str(tid),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


def _pwa_api_delweb(user, target_tid):
    """v64: حذف حساب موظف من التطبيق فقط — دون المساس بحسابه في البوت.
    يُلغي: اسم الدخول والرقم السري (pwa_auth) + الجلسات + رموز التذكّر
    + بريد Google المرتبط (يمنع إعادة إنشاء الحساب تلقائياً عبر جوجل).
    يُبقي صف users كاملاً: الفئة والإيقاف وكل بيانات تلغرام كما هي."""
    if not _pwa_perm_map(user["role"])["users"]:
        return None
    if target_tid == user["telegram_id"]:
        return False, "لا يمكنك حذف حسابك من التطبيق — استخدم حساب مدير آخر."
    if _protected_account(target_tid):
        return False, "🔒 هذا الحساب محمي (المالك/متغيرات البيئة) — لا يمكن حذف حسابه."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE telegram_id = %s", (target_tid,))
            row = cur.fetchone()
            if not row:
                return False, "المستخدم غير موجود."
            uid = row[0]
            cur.execute("DELETE FROM pwa_auth WHERE user_id = %s", (uid,))
            n_auth = cur.rowcount
            cur.execute("DELETE FROM pwa_sessions WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM pwa_remember WHERE user_id = %s", (uid,))
            cur.execute("UPDATE users SET email = NULL WHERE id = %s AND email IS NOT NULL", (uid,))
        conn.commit()
        if not n_auth:
            return False, "لا يوجد حساب تطبيق لهذا المستخدم أصلاً — لا شيء يحذف."
        return True, "تم حذف حسابه من التطبيق — حسابه في البوت لم يُمس."
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


async def pwa_users_delweb_handler(request):
    """v64: حذف حساب التطبيق لموظف (للمدير فقط) — البوت يبقى كما هو."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        tid = int(body.get("telegram_id"))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_delweb, user, tid)
    if ok is None:
        return _pwa_err(403, "للمدراء فقط.")
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "user_delweb", user,
                   details="حذف حساب التطبيق للمستخدم #" + str(tid) + " (البوت كما هو)",
                   ip=request.remote or "؟")
    if TG_APP is not None:
        try:
            await TG_APP.bot.send_message(
                chat_id=tid,
                text="🗑️ ألغت إدارة المؤسسة حسابك في تطبيق المخزون.\n"
                     "✅ حسابك في البوت لم يُمس ويعمل كالمعتاد.",
            )
        except Exception as e:
            logging.error("[PWA] delweb notify failed: " + str(e))
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_users_webpass_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        tid = int(body.get("telegram_id"))
    except (TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_webpass, user, tid, str(body.get("password", "")))
    if ok is None:
        return _pwa_err(403, "للمدراء فقط.")
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "webpass_set", user,
                   details="للمستخدم #" + str(tid),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


# ═══════════ (v30) مركز إشعارات التطبيق: جرس + عدّاد غير المقروء + تعليم كمقروء ═══════════

def _pwa_api_notifications(user, after, limit):
    """قائمة إشعارات المستخدم + عدد غير المقروء + آخر معرف (متزامن)."""
    try:
        after = max(0, int(after))
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        after, limit = 0, 30
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) AS last_id FROM notifications")
            last_id = int(cur.fetchone()["last_id"])
            cur.execute("SELECT last_read_id FROM notification_reads WHERE user_id = %s",
                        (user["id"],))
            rr = cur.fetchone()
            read_upto = int(rr["last_read_id"]) if rr else 0
            cur.execute("SELECT COUNT(*) AS c FROM notifications WHERE id > %s", (read_upto,))
            unread = min(int(cur.fetchone()["c"]), 99)
            # v50: إجمالي رسائل الشات غير المقروءة — يُرسل مع دورية الإشعارات (لا حلقة جديدة)
            try:
                cur.execute("SELECT COUNT(*) AS c FROM chat_messages "
                            "WHERE recip_id = %s AND read_at IS NULL", (user["id"],))
                chat_unread = min(int(cur.fetchone()["c"]), 99)
            except Exception:
                chat_unread = 0
            cur.execute(
                "SELECT id, kind, title, body, actor, source, created_at "
                "FROM notifications WHERE id > %s ORDER BY id DESC LIMIT %s",
                (after, limit))
            items = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("created_at"):
                    try:
                        d["ts"] = d["created_at"].isoformat()
                        d["created_at"] = d["created_at"].strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        d["ts"] = str(d["created_at"])
                else:
                    d["ts"] = ""
                items.append(d)
        return {"ok": True, "items": items, "unread": unread, "last_id": last_id,
                "chat_unread": chat_unread}
    except Exception as e:
        logging.error("[PWA] notifications failed: " + str(e))
        return {"ok": True, "items": [], "unread": 0, "last_id": 0, "chat_unread": 0}
    finally:
        release_connection(conn)


def _pwa_api_notifications_read(user, last_id):
    """تعليم إشعارات المستخدم كمقروء حتى معرف معيّن (متزامن)."""
    try:
        last_id = max(0, int(last_id))
    except (TypeError, ValueError):
        last_id = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notification_reads (user_id, last_read_id) VALUES (%s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET last_read_id = "
                "GREATEST(notification_reads.last_read_id, EXCLUDED.last_read_id)",
                (user["id"], last_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] notif read failed: " + str(e))
        return False
    finally:
        release_connection(conn)


async def pwa_notifications_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    return _pwa_jr(await _pwa_run(
        _pwa_api_notifications, user,
        request.query.get("after", 0), request.query.get("limit", 30)))


async def pwa_notifications_read_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
        last_id = int(body.get("last_id", 0))
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    await _pwa_run(_pwa_api_notifications_read, user, last_id)
    return _pwa_jr({"ok": True})


async def pwa_status_handler(request):
    """عام: هل يحتاج التطبيق إلى إعداد أول مدير؟ + رقم الإصدار للتشخيص."""
    try:
        has = await _pwa_run(_pwa_has_accounts)
    except Exception:
        has = True  # عند خطأ القاعدة لا نُظهر شاشة الإعداد
    return _pwa_jr({"ok": True, "setup": not has, "version": PWA_VERSION,
                    "self_register": _pwa_self_register_enabled(),
                    "smtp_ready": _pwa_otp_email_ready(),  # للنسخ الاحتياطي البريدي فقط — التسجيل بلا بريد (v39)
                    "smtp_last_err": _PWA_SMTP_LAST_ERR,   # v36: سبب آخر فشل إرسال (تشخيص)
                    "google_login": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),  # v33
                    "reasons": PWA_REASONS})


async def pwa_setup_handler(request):
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ip = request.remote or "؟"
    # 🔒 حتى الإعداد الأول محمي من التخمين العشوائي
    try:
        await _pwa_run(_pwa_login_gate, "setup|" + ip)
    except PwaLocked as lk:
        mins = max(1, int(lk.retry_after // 60) + 1)
        return _pwa_err(429, "محاولات كثيرة — انتظر نحو " + str(mins) + " دقيقة.")
    try:
        ok, result, err = await _pwa_run(
            _pwa_api_setup, str(body.get("name", "")),
            str(body.get("password", "")), str(body.get("confirm", "")))
    except Exception:
        return _pwa_err(500, "خطأ في الخادم، أعد المحاولة.")
    if not ok:
        await _pwa_run(_pwa_login_fail, "setup|" + ip)
        await _pwa_run(_audit_log, "login_fail", None,
                       details="setup: " + str(body.get("name", "")), ip=ip)
        return _pwa_err(400, err)
    token, uname = result
    await _pwa_run(_pwa_login_clear, "setup|" + ip)
    await _pwa_run(_audit_log, "setup", None, details="اسم: " + str(uname), ip=ip)
    resp = _pwa_jr({"ok": True, "username": uname})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, token, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


async def pwa_changepass_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    ok, msg = await _pwa_run(_pwa_api_changepass, user,
                             str(body.get("old_password", "")),
                             str(body.get("new_password", "")))
    if not ok:
        return _pwa_err(400, msg)
    await _pwa_run(_audit_log, "changepass", user,
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


# ── أمر تلغرام /webpass: استعادة/ضبط كلمة مرور الويب من داخل البوت ──

def _webpass_set(telegram_id, password):
    """متزامن: يجد مستخدم البوت ويضبط كلمة ويبه. يرجع (username, name) أو None."""
    user = get_user(telegram_id)
    if not user:
        return None
    name = user.get("first_name") or user.get("username") or str(telegram_id)
    uname = pwa_set_password(user["id"], name, password)
    return uname, name


async def webpass_command(update, context):
    """🔑 /webpass <كلمة جديدة> — للمدراء فقط: ضبط كلمة مرور الويب
    الخاصة بحسابك من تلغرام مباشرة (استعادة عند نسيان الكلمة)."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("⛔ هذا الأمر للمدراء فقط.")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "🔑 ضبط كلمة مرور التطبيق (الويب) الخاصة بك\n\n"
            "اكتب:\n"
            "/webpass كلمة123\n\n"
            "(8 أحرف على الأقل مع حروف وأرقام — ستدخل بها التطبيق من المتصفح)")
        return
    password = str(args[0]).strip()
    pol_ok, pol_msg = _pwa_password_ok(password)
    if not pol_ok:
        await update.effective_message.reply_text("⚠️ " + pol_msg)
        return
    try:
        row = await asyncio.get_running_loop().run_in_executor(
            None, _webpass_set, uid, password)
    except Exception as e:
        logging.error("[WEBPASS] failed: " + str(e))
        await update.effective_message.reply_text("⚠️ فشل الضبط، أعد المحاولة.")
        return
    if row is None:
        await update.effective_message.reply_text(
            "⚠️ حسابك غير مسجل في النظام بعد — افتح البوت بالضغط على /start أولاً.")
        return
    uname, name = row
    await update.effective_message.reply_text(
        "✅ تم ضبط كلمة مرور التطبيق\n\n"
        "👤 الاسم: " + name + "\n"
        "🔖 اسم الدخول: " + uname + "\n"
        "🔑 كلمة المرور: " + password + "\n\n"
        "ادخل الآن من رابط التطبيق بهذين البينين.\n"
        "💡 وللموظفين: من شاشة المستخدمين في التطبيق زر 🔑 أمام كل موظف.")
    logging.info("[WEBPASS] ضبط المدير " + uname + " كلمة مرور ويب من تلغرام")


async def audit_command(update, context):
    """🔒 /audit — للمدراء فقط: آخر أحداث التدقيق الأمني (دخول، كلمات، كميات...)."""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("⛔ هذا الأمر للمدراء فقط.")
        return
    rows = await asyncio.get_running_loop().run_in_executor(None, _audit_recent, 15)
    if not rows:
        await update.effective_message.reply_text(
            "🔒 لا توجد أحداث تدقيق بعد.\n"
            "كل عملية دخول/كلمة/كمية/توريد ستُسجَّل هنا تلقائياً.")
        return
    lines = ["🔒 آخر " + str(len(rows)) + " أحداث أمنية:\n"]
    for r in rows:
        ts = r["ts"].strftime("%m-%d %H:%M") if r.get("ts") else "؟"
        who = r.get("username") or "؟"
        act = AUDIT_LABELS.get(r.get("action"), r.get("action") or "؟")
        det = (" — " + str(r["details"])) if r.get("details") else ""
        ip = (" (" + str(r["ip"]) + ")") if r.get("ip") and r["ip"] != "؟" else ""
        lines.append("• " + ts + " " + act + " — " + str(who) + det + ip)
    await update.effective_message.reply_text("\n".join(lines)[:4000])


_PWA_SW_JS = """// Service Worker — مخزون السيراميك (الصفحات شبكة أولاً + غلاف للأصول)
const CACHE = "ceram-pwa-v14";
const SHELL = ["/", "/icon.png", "/manifest.webmanifest"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ok: false, error: "لا يوجد اتصال — أعد المحاولة عند رجوع الشبكة."}),
        {status: 503, headers: {"Content-Type": "application/json"}})));
    return;
  }
  // صفحات التطبيق: شبكة أولاً دائماً — التحديثات تظهر من أول فتح،
  // وعند انقطاع الشبكة نرجع لآخر نسخة مخزنة.
  if (e.request.mode === "navigate" || url.pathname === "/"
      || url.pathname === "/pwa" || url.pathname === "/app") {
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res && res.status === 200 && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request).then((h) => h || caches.match("/")))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res && res.status === 200 && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
"""


@web.middleware
async def _pwa_sec_middleware(request, handler):
    """ترويسات أمنية على كل ردود الخادم (صفحة + API)."""
    resp = await handler(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    return resp


# ═══════════ v50: الرسائل — محادثات فردية بين فريق العمل ═══════════
# شات شبيه بواتساب داخل التطبيق: محادثة فردية مع أي موظف/مدير، أكثر من محادثة
# في نفس الوقت، نص + صور + ملفات + رسائل صوتية. الوسائط في القاعدة (BYTEA).

_CHAT_MEDIA_MAX = 25 * 1024 * 1024        # 25 ميغابايت بعد فك الترميز (v53: دعم الفيديو)
_CHAT_TEXT_MAX = 4000
_CHAT_RATE = {}                            # user_id -> [window_start, count]
_CHAT_PING = {}                            # recipient telegram_id -> last ts
_CHAT_TYPING = {}                          # v61: (sender_id, recip_id) -> ts «يكتب الآن»
_MOV_CSV_LABELS = {"add": "إضافة", "remove": "خصم", "reconcile": "جرد"}


def _chat_msg_dict(r, me_id):
    """توحيد شكل رسالة الشات للواجهة (RealDictRow → dict نظيف).
    v61: reply_to رقم الرسالة المُردَّد عليها + rtext/rname مقتطف الاقتباس (حلها الخادم)."""
    created = r.get("created_at")
    try:
        rid = int(r.get("reply_to") or 0)
    except (TypeError, ValueError):
        rid = 0
    return {
        "id": int(r["id"]),
        "mine": r["sender_id"] == me_id,
        "kind": r["kind"],
        "body": r.get("body") or "",
        "media": r["kind"] != "text",
        "media_name": r.get("media_name") or "",
        "media_mime": r.get("media_mime") or "",
        "media_size": int(r.get("media_size") or 0),
        "sender_name": r.get("sender_name") or "",
        "read": bool(r.get("read_at")),
        "reply_to": rid,
        "rtext": str(r.get("rtext") or ""),
        "rname": str(r.get("rname") or ""),
        "created_at": created.isoformat() if created else "",
    }


def _chat_reply_preview_row(t, me_id):
    """v61: من صف الرسالة المُردَّد عليها → (مقتطف للعرض، اسم المرسل)."""
    kind = t.get("kind") or "text"
    if kind == "text":
        prev = (t.get("body") or "").strip()[:90]
        if not prev:
            prev = "رسالة"
    elif kind == "image":
        prev = "🖼️ صورة"
    elif kind == "audio":
        prev = "🎤 رسالة صوتية"
    elif kind == "video":
        prev = "🎬 فيديو"
    else:
        prev = "📎 " + ((t.get("media_name") or "ملف")[:60])
    nm = str(t.get("rname") or "")
    if t.get("sender_id") == me_id:
        nm = (nm + " — أنت").strip(" —")
    return prev, nm


def _chat_resolve_replies(rows, me_id):
    """v61: يشرح الاقتباسات — يجمع reply_to من الصفوف ويجلب نصوصها وأسماء مرسليها
    بجلب واحد بدل استعلام لكل رسالة. يعدّل الصفوف في مكانها (rtext/rname)."""
    ids = set()
    for r in rows:
        try:
            v = int(r.get("reply_to") or 0)
        except (TypeError, ValueError):
            v = 0
        if v:
            ids.add(v)
    if not ids:
        return
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, "
                "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS rname "
                "FROM chat_messages m LEFT JOIN users u ON u.id = m.sender_id "
                "WHERE m.id = ANY(%s)", (sorted(ids),))
            src = {x["id"]: x for x in cur.fetchall()}
        conn.commit()
    finally:
        release_connection(conn)
    for r in rows:
        try:
            v = int(r.get("reply_to") or 0)
        except (TypeError, ValueError):
            v = 0
        t = src.get(v)
        if not t:
            continue
        r["rtext"], r["rname"] = _chat_reply_preview_row(t, me_id)


def _pwa_api_chat_contacts(user):
    """💬 جهات الاتصال v58: كل المستخدمين النشطين (المديرون والموظفون والعملاء) بلا المعلّق.
    يعيد لكل جهة: المصدر (تطبيق به حساب ويب / تلغرام فقط) + @username + آخر ظهور
    + وسم تمييز للأسماء المتشابهة (@username وإلا #telegram_id)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.role, u.username, u.telegram_id, u.last_seen, "
                "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                "'#' || u.id::text) AS name, "
                "(a.user_id IS NOT NULL) AS has_web "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE u.active = TRUE AND u.id <> %s "
                "AND (u.role IS NULL OR u.role <> %s) "
                "ORDER BY name", (user["id"], ROLE_PENDING))
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    name_counts = {}
    for r in rows:
        key = (r["name"] or "").strip().lower()
        name_counts[key] = name_counts.get(key, 0) + 1
    contacts = []
    for r in rows:
        key = (r["name"] or "").strip().lower()
        dup = name_counts.get(key, 0) > 1
        tag = ""
        if r.get("username"):
            tag = "@" + r["username"]            # v58: @اسم تلغرام يظهر دائماً إن وجد
        elif dup and r.get("telegram_id"):
            tag = "#" + str(r["telegram_id"])
        elif dup:
            tag = "#" + str(r["id"])
        contacts.append({"id": r["id"], "name": r["name"], "role": r["role"],
                         "role_label": ROLE_LABELS.get(r["role"], r["role"]),
                         "tag": tag,
                         "has_web": bool(r["has_web"]),
                         "last_seen": r["last_seen"].isoformat() if r.get("last_seen") else ""})
    return {"ok": True, "me": {"id": user["id"], "name": user.get("name") or ""},
            "contacts": contacts}


def _pwa_api_chat_list(user):
    """💬 قائمة المحادثات: آخر رسالة مع كل شريك + عدد غير المقروء منه."""
    me = user["id"]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT partner, MAX(id) AS last_id, "
                "COUNT(*) FILTER (WHERE recip_id = %s AND read_at IS NULL) AS unread "
                "FROM (SELECT CASE WHEN sender_id = %s THEN recip_id ELSE sender_id END AS partner, "
                "sender_id, recip_id, id, read_at FROM chat_messages "
                "WHERE sender_id = %s OR recip_id = %s) t "
                "GROUP BY partner ORDER BY last_id DESC LIMIT 80", (me, me, me, me))
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    if not rows:
        return {"ok": True, "chats": []}
    partners = [r["partner"] for r in rows]
    last_ids = [int(r["last_id"]) for r in rows]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, sender_id, kind, body, media_name, media_mime, media_size, "
                "created_at, read_at FROM chat_messages WHERE id = ANY(%s)", (last_ids,))
            msgs = {r["id"]: r for r in cur.fetchall()}
            cur.execute(
                "SELECT u.id, u.role, u.active, u.username, u.telegram_id, u.last_seen, "
                "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                "'#' || u.id::text) AS name, "
                "(a.user_id IS NOT NULL) AS has_web "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE u.id = ANY(%s)", (partners,))
            users = {r["id"]: r for r in cur.fetchall()}
            # v51: أسماء زملاء العمل النشطين كافة — لاكتشاف المتشابهة ووسمها بمعرّف
            cur.execute(
                "SELECT COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                "'#' || u.id::text) AS name "
                "FROM users u WHERE u.active = TRUE AND u.id <> %s "
                "AND (u.role IS NULL OR u.role NOT IN (%s, %s))",
                (me, ROLE_CUSTOMER, ROLE_PENDING))
            name_counts = {}
            for nrow in cur.fetchall():
                nkey = (nrow["name"] or "").strip().lower()
                name_counts[nkey] = name_counts.get(nkey, 0) + 1
        conn.commit()
    finally:
        release_connection(conn)
    chats = []
    for r in rows:
        u = users.get(r["partner"])
        m = msgs.get(int(r["last_id"]))
        if not u or not m:
            continue
        nkey = (u["name"] or "").strip().lower()
        tag = ""
        if name_counts.get(nkey, 0) > 1:
            tag = ("@" + u["username"]) if u.get("username") \
                else ("#" + str(u.get("telegram_id") or u["id"]))
        chats.append({
            "user": {"id": u["id"], "name": u["name"], "role": u["role"],
                     "role_label": ROLE_LABELS.get(u["role"], u["role"]),
                     "active": bool(u["active"]), "tag": tag,
                     "has_web": bool(u.get("has_web")),
                     "last_seen": u["last_seen"].isoformat() if u.get("last_seen") else ""},
            "unread": int(r["unread"]),
            "last": _chat_msg_dict(m, me),
        })
    return {"ok": True, "chats": chats}


def _pwa_api_chat_messages(user, other, after, before=0):
    """💬 رسائل محادثة مع شريك محدد. after=0 → آخر 100 (تصاعدياً)، after>0 → الجديد فقط.
    v58: before>0 → 100 رسالة أقدم من before (ترقيم للخلف — التاريخ الكامل متاح).
    يعيد (messages, read_upto, has_more, error) — فتح المحادثة يعتبر قراءة.
    v61: يشرح الاقتباسات (rtext/rname) ويعيد حالة الشريك في العنصر الخامس:
    {peer_typing, peer_seen, peer_has_web} — «يكتب الآن» + «متصل الآن» في رأس المحادثة."""
    me = user["id"]
    try:
        other = int(other)
        after = max(0, int(after or 0))
        before = max(0, int(before or 0))
    except (TypeError, ValueError):
        return None, 0, False, "طلب غير صالح.", None
    if other == me:
        return None, 0, False, "لا يمكنك محادثة نفسك.", None
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.active, u.last_seen, "
                "(a.user_id IS NOT NULL) AS has_web "
                "FROM users u LEFT JOIN pwa_auth a ON a.user_id = u.id WHERE u.id = %s",
                (other,))
            peer = cur.fetchone()
            if not peer or not peer["active"]:
                return None, 0, False, "المستخدم غير متاح للمحادثة.", None
            if after:
                cur.execute(
                    "SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, m.media_mime, "
                    "m.media_size, m.created_at, m.read_at, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS sender_name "
                    "FROM chat_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE ((m.sender_id = %s AND m.recip_id = %s) "
                    "OR (m.sender_id = %s AND m.recip_id = %s)) AND m.id > %s "
                    "ORDER BY m.id ASC LIMIT 200", (me, other, other, me, after))
                rows = list(cur.fetchall())
            elif before:
                # v58: صفحة أقدم — آخر 100 رسالة قبل before تصاعدياً + هل توجد أقدم؟
                cur.execute(
                    "SELECT * FROM (SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, "
                    "m.media_mime, m.media_size, m.created_at, m.read_at, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS sender_name "
                    "FROM chat_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE ((m.sender_id = %s AND m.recip_id = %s) "
                    "OR (m.sender_id = %s AND m.recip_id = %s)) AND m.id < %s "
                    "ORDER BY m.id DESC LIMIT 100) t ORDER BY t.id ASC",
                    (me, other, other, me, before))
                rows = list(cur.fetchall())
                cur.execute(
                    "SELECT 1 FROM chat_messages WHERE ((sender_id = %s AND recip_id = %s) "
                    "OR (sender_id = %s AND recip_id = %s)) AND id < %s LIMIT 1",
                    (me, other, other, me, rows[0]["id"] if rows else before))
                has_more = cur.fetchone() is not None
            else:
                cur.execute(
                    "SELECT * FROM (SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, "
                    "m.media_mime, m.media_size, m.created_at, m.read_at, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS sender_name "
                    "FROM chat_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE (m.sender_id = %s AND m.recip_id = %s) "
                    "OR (m.sender_id = %s AND m.recip_id = %s) "
                    "ORDER BY m.id DESC LIMIT 100) t ORDER BY t.id ASC",
                    (me, other, other, me))
                rows = list(cur.fetchall())
                # v58: هل توجد رسائل أقدم من أول صفحة؟ (لزر «الرسائل الأقدم»)
                cur.execute(
                    "SELECT 1 FROM chat_messages WHERE ((sender_id = %s AND recip_id = %s) "
                    "OR (sender_id = %s AND recip_id = %s)) AND id < %s LIMIT 1",
                    (me, other, other, me, rows[0]["id"] if rows else 0))
                has_more = cur.fetchone() is not None
            # فتح المحادثة = قراءة رسائل الشريك (لا يحدث في جلب الصفحات الأقدم)
            if not before:
                cur.execute("UPDATE chat_messages SET read_at = NOW() "
                            "WHERE sender_id = %s AND recip_id = %s AND read_at IS NULL",
                            (other, me))
                # v50: أقصى رسالة من رسائلي قرأها الشريك (لعلامتي ✓✓ في الواجهة)
                cur.execute("SELECT COALESCE(MAX(id), 0) AS mx FROM chat_messages "
                            "WHERE sender_id = %s AND recip_id = %s AND read_at IS NOT NULL",
                            (me, other))
                read_upto = int(cur.fetchone()["mx"])
            else:
                read_upto = 0
        conn.commit()
        now = datetime.datetime.now()
        for r in rows:
            if r["sender_id"] == other and not r["read_at"]:
                r["read_at"] = now
        _chat_resolve_replies(rows, me)
        # v61: حالة الشريك — يكتب الآن؟ متصل الآن؟ تطبيق أم تلغرام فقط؟
        now_t = time.time()
        tp = _CHAT_TYPING.get((other, me), 0)
        if len(_CHAT_TYPING) > 400:
            for k in [k2 for k2, v2 in _CHAT_TYPING.items() if now_t - v2 > 60]:
                _CHAT_TYPING.pop(k2, None)
        extra = {"peer_typing": bool(now_t - tp < 6),
                 "peer_seen": peer["last_seen"].isoformat() if peer.get("last_seen") else "",
                 "peer_has_web": bool(peer.get("has_web"))}
        return [_chat_msg_dict(r, me) for r in rows], read_upto, has_more, None, extra
    finally:
        release_connection(conn)


def _pwa_api_chat_send(user, to, kind, text, name, mime, data_b64, reply_to=0):
    """💬 إرسال رسالة (نص/صورة/ملف/صوت). يعيد (msg_dict, peer_telegram_id, peer_has_web, error).
    v61: reply_to — الرد على رسالة محددة داخل نفس المحادثة (اقتباس داخل الفقاعة)،
    و peer_has_web ليعرض التطبيق ✓✓ فوراً لمستلمي تلغرام (الوصول = نجاح الإرسال)."""
    me = user["id"]
    try:
        to = int(to)
    except (TypeError, ValueError):
        return None, None, "طلب غير صالح."
    if to == me:
        return None, None, "لا يمكنك محادثة نفسك."
    # حارس إغراق بسيط: 25 رسالة كحد أقصى في الدقيقة لكل مستخدم
    now = time.time()
    # v58: تنظيف دوري للقواميس كي لا تنمو بلا حدود
    if len(_CHAT_RATE) > 400:
        for k in [k2 for k2, v2 in _CHAT_RATE.items() if now - v2[0] > 600]:
            _CHAT_RATE.pop(k, None)
    if len(_CHAT_PING) > 400:
        for k2 in [k3 for k3, v3 in _CHAT_PING.items() if now - v3 > 600]:
            _CHAT_PING.pop(k2, None)
    win = _CHAT_RATE.get(me)
    if not win or now - win[0] > 60:
        _CHAT_RATE[me] = [now, 1]
    else:
        win[1] += 1
        if win[1] > 25:
            return None, None, "أرسلت رسائل كثيرة — انتظر قليلاً ثم أعد المحاولة."
    kind = str(kind or "text").strip().lower()
    if kind not in ("text", "image", "file", "audio", "video"):
        kind = "text"
    body, media, mname, mmime, msize = "", None, "", "", 0
    if kind == "text":
        body = str(text or "").strip()
        if not body:
            return None, None, "اكتب نص الرسالة أولاً."
        body = body[:_CHAT_TEXT_MAX]
    else:
        raw = str(data_b64 or "")
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            media = base64.b64decode(raw, validate=False)
        except Exception:
            return None, None, "المرفق غير صالح."
        if not media:
            return None, None, "المرفق فارغ."
        if len(media) > _CHAT_MEDIA_MAX:
            return None, None, "الملف كبير جداً — الحد 25 ميغابايت."
        mname = str(name or "ملف").strip().replace("\r", " ").replace("\n", " ")[:200]
        mmime = str(mime or "application/octet-stream").split(";")[0].strip().lower()[:100]
        if kind == "image" and not mmime.startswith("image/"):
            mmime = "image/jpeg"
        if kind == "audio" and not (mmime.startswith("audio/") or mmime.startswith("video/webm")):
            mmime = "audio/webm"
        if kind == "video" and not mmime.startswith("video/"):
            mmime = "video/mp4"
        msize = len(media)
    conn = get_connection()
    try:
        # v61: RealDictCursor — صف الرد يمر على _chat_reply_preview_row التي تقرأ حقولاً بالاسم
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT role, active, telegram_id, "
                "(SELECT COUNT(*) FROM pwa_auth a WHERE a.user_id = users.id) AS has_web "
                "FROM users WHERE id = %s", (to,))
            peer = cur.fetchone()
            # v58: العملاء النشطون محادثون الآن (طلبات المدير) — المستبعد الوحيد «قيد المراجعة»
            if not peer or not peer["active"] or peer["role"] == ROLE_PENDING:
                return None, None, False, "المستخدم غير متاح للمحادثة."
            # v61: التحقق من الرد — يجب أن تكون الرسالة المرجعية داخل هذه المحادثة
            try:
                r_id = int(reply_to or 0)
            except (TypeError, ValueError):
                r_id = 0
            rrow = None
            if r_id:
                cur.execute(
                    "SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS rname "
                    "FROM chat_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE m.id = %s AND ((m.sender_id = %s AND m.recip_id = %s) "
                    "OR (m.sender_id = %s AND m.recip_id = %s))",
                    (r_id, me, to, to, me))
                rrow = cur.fetchone()
                if not rrow:
                    r_id = 0
            cur.execute(
                "INSERT INTO chat_messages (sender_id, recip_id, kind, body, media, "
                "media_name, media_mime, media_size, reply_to) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (me, to, kind, body, psycopg2.Binary(media) if media else None,
                 mname, mmime, msize, r_id or None))
            row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] chat send failed: " + str(e))
        return None, None, False, "فشل الإرسال — أعد المحاولة."
    finally:
        release_connection(conn)
    rtext, rname = "", ""
    if rrow:
        rtext, rname = _chat_reply_preview_row(rrow, me)
    msg = {"id": int(row["id"]), "mine": True, "kind": kind, "body": body,
           "media": kind != "text", "media_name": mname, "media_mime": mmime,
           "media_size": msize, "sender_name": user.get("name") or "",
           "read": False, "reply_to": r_id, "rtext": rtext, "rname": rname,
           "created_at": row["created_at"].isoformat() if row["created_at"] else ""}
    return msg, (peer["telegram_id"] if peer["active"] else None), bool(peer["has_web"]), None


def _pwa_api_chat_read(user, other):
    """💬 تعليم رسائل شريك معين كمقروءة. يعيد الإجمالي الجديد غير المقروء."""
    me = user["id"]
    try:
        other = int(other)
    except (TypeError, ValueError):
        return {"ok": True, "total": 0}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE chat_messages SET read_at = NOW() "
                        "WHERE sender_id = %s AND recip_id = %s AND read_at IS NULL",
                        (other, me))
            cur.execute("SELECT COUNT(*) FROM chat_messages "
                        "WHERE recip_id = %s AND read_at IS NULL", (me,))
            total = int(cur.fetchone()[0])
        conn.commit()
        return {"ok": True, "total": min(total, 99)}
    finally:
        release_connection(conn)


def _pwa_api_chat_typing(user, other):
    """v61: إشارة «يكتب الآن» — يخزن لحظة آخر ضغطة لمستخدم على شريك معين.
    القراءة تحدث في _pwa_api_chat_messages (أقل من 6 ثوانٍ = يكتب الآن).
    بين مستخدمي التطبيق فقط — تلغرام لا يرسل مؤشر الكتابة للبوتات أصلاً."""
    me = user["id"]
    try:
        other = int(other)
    except (TypeError, ValueError):
        return {"ok": False}
    if other == me:
        return {"ok": False}
    now = time.time()
    if len(_CHAT_TYPING) > 400:
        for k in [k2 for k2, v2 in _CHAT_TYPING.items() if now - v2 > 60]:
            _CHAT_TYPING.pop(k2, None)
    _CHAT_TYPING[(me, other)] = now
    return {"ok": True}


def _pwa_api_chat_search(user, other, q):
    """v61: بحث داخل محادثة واحدة — نص الرسائل وأسماء الملفات.
    يعيد (messages تصاعدياً، العدد الكلي، خطأ). حد 60 نتيجة لعرض سريع."""
    me = user["id"]
    try:
        other = int(other)
    except (TypeError, ValueError):
        return [], 0, "طلب غير صالح."
    if other == me:
        return [], 0, "لا يمكنك محادثة نفسك."
    q = str(q or "").strip()[:80]
    if not q:
        return [], 0, None
    pat = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, active FROM users WHERE id = %s", (other,))
            peer = cur.fetchone()
            if not peer or not peer["active"]:
                return [], 0, "المستخدم غير متاح للمحادثة."
            cur.execute(
                "SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, m.media_mime, "
                "m.media_size, m.created_at, m.read_at, m.reply_to, "
                "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS sender_name "
                "FROM chat_messages m LEFT JOIN users u ON u.id = m.sender_id "
                "WHERE ((m.sender_id = %s AND m.recip_id = %s) "
                "OR (m.sender_id = %s AND m.recip_id = %s)) "
                "AND (m.body ILIKE %s OR m.media_name ILIKE %s) "
                "ORDER BY m.id DESC LIMIT 60",
                (me, other, other, me, pat, pat))
            rows = list(cur.fetchall())
            cur.execute(
                "SELECT COUNT(*) AS c FROM chat_messages "
                "WHERE ((sender_id = %s AND recip_id = %s) OR (sender_id = %s AND recip_id = %s)) "
                "AND (body ILIKE %s OR media_name ILIKE %s)",
                (me, other, other, me, pat, pat))
            total = int(cur.fetchone()["c"])
        conn.commit()
        rows.reverse()
        _chat_resolve_replies(rows, me)
        return [_chat_msg_dict(r, me) for r in rows], total, None
    finally:
        release_connection(conn)


def _pwa_api_chat_delete_conversation(user, other):
    """v53: حذف محادثة بالكامل (رسائل الاتجاهين) — للمشارك فيها فقط.
    يعيد (عدد المحذوف، خطأ)."""
    me = user["id"]
    try:
        other = int(other)
    except (TypeError, ValueError):
        return 0, "طلب غير صالح."
    if other == me:
        return 0, "لا يمكنك محادثة نفسك."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_messages WHERE "
                "(sender_id = %s AND recip_id = %s) OR (sender_id = %s AND recip_id = %s)",
                (me, other, other, me))
            n = cur.rowcount
        conn.commit()
        return n, None
    finally:
        release_connection(conn)


# ═══════════ v83: مجموعات الدردشة — قائمة/إنشاء/رسائل/إرسال/أعضاء ═══════════

def _chat_group_member_row(cur, gid, uid):
    """صف عضوية المستخدم في مجموعة — أو None إن لم يكن عضواً."""
    cur.execute(
        "SELECT is_admin FROM chat_group_members WHERE group_id = %s AND user_id = %s",
        (gid, uid))
    return cur.fetchone()


def _chat_group_reply_preview(kind, body, media_name):
    """مقتطف اقتباس الرد — نفس دلالات المحادثة الفردية."""
    k = kind or "text"
    if k == "text":
        return (body or "").strip()[:90] or "رسالة"
    if k == "image":
        return "🖼️ صورة"
    if k == "audio":
        return "🎤 رسالة صوتية"
    if k == "video":
        return "🎬 فيديو"
    return "📎 " + ((media_name or "ملف")[:60])


def _group_msg_dict(m, me):
    """صف رسالة مجموعة → dict الواجهة (نفس حقول المحادثة الفردية + اسم المرسل)."""
    return {
        "id": int(m["id"]), "mine": m["sender_id"] == me,
        "kind": m["kind"], "body": m["body"] or "",
        "media": (m["kind"] or "text") != "text",
        "media_name": m.get("media_name") or "",
        "media_mime": m.get("media_mime") or "",
        "media_size": int(m.get("media_size") or 0),
        "sender_name": m.get("sender_name") or "",
        "sender_id": int(m["sender_id"]),
        "read": bool(m.get("isread")),
        "reply_to": int(m["reply_to"]) if m.get("reply_to") else 0,
        "rtext": m.get("rtext") or "", "rname": m.get("rname") or "",
        "created_at": m["created_at"].isoformat() if m.get("created_at") else "",
    }


def _pwa_api_chat_groups_list(user):
    """v83: مجموعاتي — آخر رسالة + عدد غير المقروء + عدد الأعضاء."""
    me = user["id"]
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT g.id, g.name, g.created_by, "
                "(SELECT COUNT(*) FROM chat_group_members gm2 "
                "WHERE gm2.group_id = g.id) AS members_count, "
                "(SELECT MAX(id) FROM chat_group_messages m2 "
                "WHERE m2.group_id = g.id) AS last_id, "
                "(SELECT COUNT(*) FROM chat_group_messages m3 "
                "WHERE m3.group_id = g.id AND m3.id > gm.last_read_id "
                "AND m3.sender_id <> %s) AS unread "
                "FROM chat_groups g JOIN chat_group_members gm "
                "ON gm.group_id = g.id AND gm.user_id = %s "
                "ORDER BY last_id DESC NULLS LAST LIMIT 60", (me, me))
            rows = cur.fetchall()
            last_ids = [int(r["last_id"]) for r in rows if r["last_id"]]
            previews = {}
            if last_ids:
                cur.execute(
                    "SELECT m.id, m.kind, m.body, m.media_name, m.created_at, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                    "'#' || u.id::text) AS sname "
                    "FROM chat_group_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE m.id = ANY(%s)", (last_ids,))
                for p in cur.fetchall():
                    previews[int(p["id"])] = p
        conn.commit()
    finally:
        release_connection(conn)
    groups = []
    for r in rows:
        lid = int(r["last_id"]) if r["last_id"] else 0
        p = previews.get(lid)
        pv, last_at = "", ""
        if p:
            k = p["kind"] or "text"
            if k == "text":
                pv = (p["body"] or "").strip()[:60] or "رسالة"
            elif k == "image":
                pv = "🖼️ صورة"
            elif k == "audio":
                pv = "🎤 رسالة صوتية"
            elif k == "video":
                pv = "🎬 فيديو"
            else:
                pv = "📎 " + ((p.get("media_name") or "ملف")[:40])
            pv = (p.get("sname") or "") + ": " + pv
            last_at = p["created_at"].isoformat() if p.get("created_at") else ""
        groups.append({"id": int(r["id"]), "name": r["name"],
                       "members_count": int(r["members_count"]),
                       "is_owner": r["created_by"] == me,
                       "unread": int(r["unread"] or 0),
                       "preview": pv, "last_at": last_at})
    return {"ok": True, "groups": groups}


def _pwa_api_chat_group_create(user, name, member_ids):
    """v83: إنشاء مجموعة — المنشئ مديرها تلقائياً، والأعضاء نشطون غير معلقين."""
    me = user["id"]
    name = str(name or "").strip()[:80]
    if not name:
        return None, "اكتب اسم المجموعة."
    ids, seen = [], set()
    for x in (member_ids or []):
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i != me and i not in seen:
            seen.add(i)
            ids.append(i)
    if len(ids) > 50:
        return None, "الحد الأقصى 50 عضواً بالمجموعة."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            valid = [me]
            if ids:
                cur.execute(
                    "SELECT id FROM users WHERE id = ANY(%s) AND active = TRUE "
                    "AND (role IS NULL OR role <> %s)", (ids, ROLE_PENDING))
                valid += [int(r[0]) for r in cur.fetchall()]
            cur.execute(
                "INSERT INTO chat_groups (name, created_by) VALUES (%s, %s) RETURNING id",
                (name, me))
            gid = int(cur.fetchone()[0])
            vals = [(gid, i, i == me) for i in valid]
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO chat_group_members (group_id, user_id, is_admin) VALUES %s",
                vals)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return {"id": gid, "name": name, "members_count": len(valid)}, None


def _pwa_api_chat_group_messages(user, gid, after, before=0):
    """v83: رسائل مجموعة — نفس دلالات المحادثة الفردية + اسم المرسل لكل فقاعة.
    after=0 → آخر 100 تصاعدياً، after>0 → الجديد فقط، before>0 → أقدم (للمستقبل).
    الجلب يعتبر قراءة — last_read_id يتقدم لأكبر رسالة معادة."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, 0, False, "طلب غير صالح.", None
    try:
        after = int(after or 0)
        before = int(before or 0)
    except (TypeError, ValueError):
        after, before = 0, 0
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            mem = _chat_group_member_row(cur, gid, me)
            if not mem:
                return None, 0, False, "لست عضواً في هذه المجموعة.", None
            if after > 0:
                cur.execute(
                    "SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, "
                    "m.media_mime, m.media_size, m.created_at, m.reply_to, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                    "'#' || u.id::text) AS sender_name "
                    "FROM chat_group_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE m.group_id = %s AND m.id > %s ORDER BY m.id ASC LIMIT 200",
                    (gid, after))
                rows = list(cur.fetchall())
                has_more = False
            else:
                cur.execute(
                    "SELECT m.id, m.sender_id, m.kind, m.body, m.media_name, "
                    "m.media_mime, m.media_size, m.created_at, m.reply_to, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                    "'#' || u.id::text) AS sender_name "
                    "FROM chat_group_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE m.group_id = %s AND (%s = 0 OR m.id < %s) "
                    "ORDER BY m.id DESC LIMIT 100", (gid, before, before))
                rows = list(cur.fetchall())
                rows.reverse()
                has_more = len(rows) == 100
            read_upto = 0
            if rows:
                max_id = int(rows[-1]["id"])
                min_id = int(rows[0]["id"])
                cur.execute(
                    "UPDATE chat_group_members SET last_read_id = "
                    "GREATEST(last_read_id, %s) WHERE group_id = %s AND user_id = %s "
                    "RETURNING last_read_id", (max_id, gid, me))
                r = cur.fetchone()
                read_upto = int(r["last_read_id"]) if r else max_id
                cur.execute(
                    "SELECT m.id FROM chat_group_messages m "
                    "JOIN chat_group_members gm ON gm.group_id = m.group_id "
                    "AND gm.user_id <> m.sender_id AND gm.last_read_id >= m.id "
                    "WHERE m.group_id = %s AND m.sender_id = %s "
                    "AND m.id >= %s AND m.id <= %s", (gid, me, min_id, max_id))
                readset = {int(x["id"]) for x in cur.fetchall()}
                rids = list({int(m["reply_to"]) for m in rows if m.get("reply_to")})
                rq = {}
                if rids:
                    cur.execute(
                        "SELECT m.id, m.kind, m.body, m.media_name, "
                        "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                        "'') AS sname "
                        "FROM chat_group_messages m LEFT JOIN users u ON u.id = m.sender_id "
                        "WHERE m.id = ANY(%s)", (rids,))
                    for qrow in cur.fetchall():
                        rq[int(qrow["id"])] = (
                            _chat_group_reply_preview(qrow["kind"], qrow["body"],
                                                      qrow.get("media_name")),
                            qrow["sname"] or "رد")
                for m in rows:
                    if m.get("reply_to") and int(m["reply_to"]) in rq:
                        m["rtext"], m["rname"] = rq[int(m["reply_to"])]
                    m["isread"] = (int(m["id"]) in readset) if m["sender_id"] == me else False
            cur.execute("SELECT name FROM chat_groups WHERE id = %s", (gid,))
            g = cur.fetchone()
            gname = (g["name"] if g else "") or ""
            cur.execute("SELECT COUNT(*) AS count FROM chat_group_members WHERE group_id = %s",
                        (gid,))
            cnt = int(cur.fetchone()["count"])
        conn.commit()
    finally:
        release_connection(conn)
    msgs = [_group_msg_dict(m, me) for m in rows]
    extra = {"members_count": cnt, "group_name": gname}
    return msgs, read_upto, has_more, None, extra


def _pwa_api_chat_group_send(user, gid, kind, text, name, mime, data_b64, reply_to=0):
    """v83: إرسال إلى مجموعة — نفس تحقق الوسائط وحد الإغراق المشترك.
    يعيد (msg_dict, [(telegram_id, has_web)] للمستلمين عدا المرسل, اسم المجموعة, خطأ)."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, None, None, "طلب غير صالح."
    now = time.time()
    if len(_CHAT_RATE) > 400:
        for k in [k2 for k2, v2 in _CHAT_RATE.items() if now - v2[0] > 600]:
            _CHAT_RATE.pop(k, None)
    win = _CHAT_RATE.get(me)
    if not win or now - win[0] > 60:
        _CHAT_RATE[me] = [now, 1]
    else:
        win[1] += 1
        if win[1] > 25:
            return None, None, None, "أرسلت رسائل كثيرة — انتظر قليلاً ثم أعد المحاولة."
    kind = str(kind or "text").strip().lower()
    if kind not in ("text", "image", "file", "audio", "video"):
        kind = "text"
    body, media, mname, mmime, msize = "", None, "", "", 0
    if kind == "text":
        body = str(text or "").strip()
        if not body:
            return None, None, None, "اكتب نص الرسالة أولاً."
        body = body[:_CHAT_TEXT_MAX]
    else:
        raw = str(data_b64 or "")
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            media = base64.b64decode(raw, validate=False)
        except Exception:
            return None, None, None, "المرفق غير صالح."
        if not media:
            return None, None, None, "المرفق فارغ."
        if len(media) > _CHAT_MEDIA_MAX:
            return None, None, None, "الملف كبير جداً — الحد 25 ميغابايت."
        mname = str(name or "ملف").strip().replace("\r", " ").replace("\n", " ")[:200]
        mmime = str(mime or "application/octet-stream").split(";")[0].strip().lower()[:100]
        if kind == "image" and not mmime.startswith("image/"):
            mmime = "image/jpeg"
        if kind == "audio" and not (mmime.startswith("audio/") or mmime.startswith("video/webm")):
            mmime = "audio/webm"
        if kind == "video" and not mmime.startswith("video/"):
            mmime = "video/mp4"
        msize = len(media)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            mem = _chat_group_member_row(cur, gid, me)
            if not mem:
                return None, None, None, "لست عضواً في هذه المجموعة."
            try:
                r_id = int(reply_to or 0)
            except (TypeError, ValueError):
                r_id = 0
            rrow = None
            if r_id:
                cur.execute(
                    "SELECT m.id, m.kind, m.body, m.media_name, "
                    "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, '') AS rname "
                    "FROM chat_group_messages m LEFT JOIN users u ON u.id = m.sender_id "
                    "WHERE m.id = %s AND m.group_id = %s", (r_id, gid))
                rrow = cur.fetchone()
                if not rrow:
                    r_id = 0
            cur.execute(
                "INSERT INTO chat_group_messages (group_id, sender_id, kind, body, media, "
                "media_name, media_mime, media_size, reply_to) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (gid, me, kind, body, psycopg2.Binary(media) if media else None,
                 mname, mmime, msize, r_id or None))
            row = cur.fetchone()
            cur.execute(
                "UPDATE chat_group_members SET last_read_id = GREATEST(last_read_id, %s) "
                "WHERE group_id = %s AND user_id = %s", (int(row["id"]), gid, me))
            cur.execute(
                "SELECT u.telegram_id, (a.user_id IS NOT NULL) AS has_web "
                "FROM chat_group_members gm JOIN users u ON u.id = gm.user_id "
                "LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE gm.group_id = %s AND gm.user_id <> %s "
                "AND u.active = TRUE AND u.telegram_id IS NOT NULL", (gid, me))
            recips = [(int(x["telegram_id"]), bool(x["has_web"])) for x in cur.fetchall()]
            cur.execute("SELECT name FROM chat_groups WHERE id = %s", (gid,))
            g2 = cur.fetchone()
            gname = (g2["name"] if g2 else "") or ""
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] group send failed: " + str(e))
        return None, None, None, "فشل الإرسال — أعد المحاولة."
    finally:
        release_connection(conn)
    rtext, rname2 = "", ""
    if rrow:
        rtext = _chat_group_reply_preview(rrow["kind"], rrow["body"], rrow.get("media_name"))
        rname2 = rrow.get("rname") or "رد"
    msg = {"id": int(row["id"]), "mine": True, "kind": kind, "body": body,
           "media": kind != "text", "media_name": mname, "media_mime": mmime,
           "media_size": msize, "sender_name": user.get("name") or "",
           "sender_id": me, "read": False, "reply_to": r_id,
           "rtext": rtext, "rname": rname2,
           "created_at": row["created_at"].isoformat() if row["created_at"] else ""}
    return msg, recips, gname, None


def _chat_unread_total_raw(cur, me):
    """إجمالي غير المقروء (v83): المحادثات الفردية + كل مجموعاتي."""
    cur.execute("SELECT COUNT(*) FROM chat_messages "
                "WHERE recip_id = %s AND read_at IS NULL", (me,))
    n = int(cur.fetchone()[0])
    cur.execute(
        "SELECT COALESCE(SUM(c), 0) FROM ("
        "SELECT COUNT(*) AS c FROM chat_group_messages m "
        "JOIN chat_group_members gm ON gm.group_id = m.group_id "
        "WHERE gm.user_id = %s AND m.id > gm.last_read_id AND m.sender_id <> %s "
        "GROUP BY m.group_id) t", (me, me))
    n += int(cur.fetchone()[0])
    return n


def _pwa_api_chat_group_read(user, gid):
    """v83: تعليم رسائل المجموعة مقروءة. يعيد إجمالي غير المقروء (فردي + مجموعات)."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return {"ok": False, "total": 0}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_group_members SET last_read_id = "
                "GREATEST(last_read_id, COALESCE((SELECT MAX(id) FROM chat_group_messages "
                "WHERE group_id = %s), 0)) WHERE group_id = %s AND user_id = %s",
                (gid, gid, me))
            n = _chat_unread_total_raw(cur, me)
        conn.commit()
        return {"ok": True, "total": min(n, 99)}
    finally:
        release_connection(conn)


def _pwa_api_chat_group_members(user, gid):
    """v83: أعضاء المجموعة — للعرض وإدارة الإضافة. للأعضاء فقط."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            mem = _chat_group_member_row(cur, gid, me)
            if not mem:
                return None, "لست عضواً في هذه المجموعة."
            cur.execute(
                "SELECT g.name, g.created_by FROM chat_groups g WHERE g.id = %s", (gid,))
            g = cur.fetchone()
            if not g:
                return None, "المجموعة غير موجودة."
            cur.execute(
                "SELECT gm.user_id, gm.is_admin, u.role, u.last_seen, "
                "COALESCE(NULLIF(u.display_name, ''), u.first_name, u.username, "
                "'#' || u.id::text) AS name "
                "FROM chat_group_members gm JOIN users u ON u.id = gm.user_id "
                "WHERE gm.group_id = %s ORDER BY gm.is_admin DESC, name", (gid,))
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)
    members = []
    for r in rows:
        members.append({"id": int(r["user_id"]), "name": r["name"],
                        "role_label": ROLE_LABELS.get(r["role"], r["role"]),
                        "is_admin": bool(r["is_admin"]),
                        "last_seen": r["last_seen"].isoformat() if r.get("last_seen") else ""})
    me_row = next((m for m in members if m["id"] == me), None)
    info = {"ok": True, "name": g["name"], "created_by": int(g["created_by"]),
            "me_is_admin": bool(mem["is_admin"]),
            "me_is_owner": int(g["created_by"]) == me,
            "members": members}
    return info, None


def _pwa_api_chat_group_add_members(user, gid, member_ids):
    """v83: إضافة أعضاء — مدير المجموعة أو مدير النظام فقط."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, "طلب غير صالح."
    ids, seen = [], set()
    for x in (member_ids or []):
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i not in seen:
            seen.add(i)
            ids.append(i)
    if not ids:
        return None, "اختر عضواً واحداً على الأقل."
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            mem = _chat_group_member_row(cur, gid, me)
            if not mem:
                return None, "لست عضواً في هذه المجموعة."
            cur.execute("SELECT created_by FROM chat_groups WHERE id = %s", (gid,))
            g = cur.fetchone()
            if not g:
                return None, "المجموعة غير موجودة."
            if not (mem["is_admin"] or int(g["created_by"]) == me
                    or user.get("role") in ("admin", "manager")):
                return None, "إضافة الأعضاء لمديري المجموعة فقط."
            cur.execute(
                "SELECT id FROM users WHERE id = ANY(%s) AND active = TRUE "
                "AND (role IS NULL OR role <> %s)", (ids, ROLE_PENDING))
            valid = [int(x[0]) for x in cur.fetchall()]
            added = 0
            if valid:
                vals = [(gid, i, False) for i in valid]
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO chat_group_members (group_id, user_id, is_admin) "
                    "VALUES %s ON CONFLICT (group_id, user_id) DO NOTHING", vals)
                added = len(valid)
            cur.execute("SELECT COUNT(*) AS count FROM chat_group_members WHERE group_id = %s",
                        (gid,))
            cnt = int(cur.fetchone()["count"])
        conn.commit()
    finally:
        release_connection(conn)
    return {"ok": True, "added": added, "members_count": cnt}, None


def _pwa_api_chat_group_leave(user, gid):
    """v83: مغادرة مجموعة — وإن انفردت كلياً تُحذف تلقائياً."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_group_members WHERE group_id = %s AND user_id = %s "
                "RETURNING user_id", (gid, me))
            if not cur.fetchone():
                conn.rollback()
                return None, "لست عضواً في هذه المجموعة."
            cur.execute(
                "DELETE FROM chat_groups WHERE id = %s AND NOT EXISTS ("
                "SELECT 1 FROM chat_group_members WHERE group_id = %s)", (gid, gid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return {"ok": True}, None


def _pwa_api_chat_group_delete(user, gid):
    """v83: حذف المجموعة كلياً برسائلها — المنشئ أو مدير النظام."""
    me = user["id"]
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        return None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT created_by FROM chat_groups WHERE id = %s", (gid,))
            g = cur.fetchone()
            if not g:
                return None, "المجموعة غير موجودة."
            if int(g[0]) != me and user.get("role") not in ("admin", "manager"):
                return None, "الحذف لمنشئ المجموعة فقط."
            cur.execute("DELETE FROM chat_groups WHERE id = %s", (gid,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return {"ok": True}, None


def _pwa_api_chat_group_media(user, msg_id):
    """v83: مرفق رسالة مجموعة — للأعضاء فقط. يعيد (bytes, mime, name, خطأ)."""
    me = user["id"]
    try:
        msg_id = int(msg_id)
    except (TypeError, ValueError):
        return None, None, None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT m.media, m.media_mime, m.media_name FROM chat_group_messages m "
                "JOIN chat_group_members gm ON gm.group_id = m.group_id AND gm.user_id = %s "
                "WHERE m.id = %s", (me, msg_id))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row or not row[0]:
        return None, None, None, "المرفق غير موجود."
    return row[0], (row[1] or "application/octet-stream"), (row[2] or "ملف"), None


def _pwa_api_chat_unread_total(user):
    """v83: إجمالي غير المقروء يشمل المجموعات أيضاً."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            n = _chat_unread_total_raw(cur, user["id"])
        conn.commit()
        return {"ok": True, "total": min(n, 99)}
    finally:
        release_connection(conn)


def _pwa_api_chat_media(user, msg_id):
    """💬 مرفق رسالة — للمُرسل والمستقبل فقط. يعيد (bytes, mime, name, err)."""
    try:
        msg_id = int(msg_id)
    except (TypeError, ValueError):
        return None, None, None, "طلب غير صالح."
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT sender_id, recip_id, media, media_mime, media_name "
                        "FROM chat_messages WHERE id = %s", (msg_id,))
            row = cur.fetchone()
        conn.commit()
    finally:
        release_connection(conn)
    if not row or not row[2]:
        return None, None, None, "المرفق غير موجود."
    if user["id"] not in (row[0], row[1]):
        return None, None, None, "لا تملك صلاحية عرض هذا المرفق."
    return bytes(row[2]), (row[3] or "application/octet-stream"), (row[4] or "file"), None


# ─── v48: دخول بضغطة واحدة (رمز التذكّر) ───

def _pwa_create_remember(user_id):
    """رمز «تذكّرني» طويل الأمد (180 يوماً) — يُخزّن مجزّراً ويُدار لكل مستخدم (5 كحد أقصى)."""
    token = _pwa_secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pwa_remember (token_hash, user_id, expires_at) "
                        "VALUES (%s, %s, NOW() + INTERVAL '180 days')",
                        (token_hash, user_id))
            cur.execute("DELETE FROM pwa_remember WHERE expires_at < NOW()")
            cur.execute("DELETE FROM pwa_remember WHERE user_id = %s AND token_hash NOT IN "
                        "(SELECT token_hash FROM pwa_remember WHERE user_id = %s "
                        "ORDER BY created_at DESC LIMIT 5)", (user_id, user_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)
    return token


def _pwa_api_remember_login(token):
    """دخول بضغطة من جهاز تم التذكّر فيه — الرمز يدور مع كل استخدام."""
    token = str(token or "").strip()
    if not token or len(token) > 128:
        return None, None, None, "رمز التذكّر غير صالح — سجّل الدخول بكلمتك السرية."
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = get_connection()
    row = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT u.id, u.telegram_id, u.username AS tg_username, u.first_name, "
                "u.display_name, u.role, u.active, a.username AS web_username "
                "FROM pwa_remember r JOIN users u ON u.id = r.user_id "
                "LEFT JOIN pwa_auth a ON a.user_id = u.id "
                "WHERE r.token_hash = %s AND r.expires_at > NOW()", (token_hash,))
            row = cur.fetchone()
            if not row:
                return None, None, None, "انتهت صلاحية التذكّر — سجّل الدخول بكلمتك السرية."
            if not row["active"]:
                return None, None, None, KICK_NOTICE
            if row["role"] in (ROLE_CUSTOMER, ROLE_PENDING):
                return None, None, None, "لا يوجد وصول ويب لفئتك حالياً — راجع الإدارة."
            cur.execute("DELETE FROM pwa_remember WHERE token_hash = %s", (token_hash,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("[PWA] remember login failed: " + str(e))
        return None, None, None, "خطأ في الخادم، أعد المحاولة."
    finally:
        release_connection(conn)
    uid = row["id"]
    new_remember = _pwa_create_remember(uid)
    sess = _pwa_create_session(uid)
    user = {"id": uid, "telegram_id": row["telegram_id"],
            "name": (row.get("display_name") or row.get("first_name")
                     or row.get("tg_username") or row.get("web_username") or str(uid)),
            "role": row["role"],
            "role_label": ROLE_LABELS.get(row["role"], row["role"])}
    return sess, new_remember, user, None


# ─── v48: تصدير CSV كامل لحركة المخزون (بلا حدّ 300) ───

def _pwa_api_export_movements(user, date_from, date_to, exclude_friday,
                              product_id, mov_type):
    """نفس فلاتر سجل الحركة لكن بلا حدّ — يعيد CSV جاهزاً + عدد العمليات."""
    if not _pwa_perm_map(user["role"])["movements"]:
        return None, "ليست لديك صلاحية سجل الحركة."
    where = ["1=1"]
    params = []
    if date_from:
        if not _PWA_DATE_RE.match(str(date_from)):
            return None, "تاريخ «من» غير صالح — الصيغة YYYY-MM-DD"
        where.append("t.date >= %s::date")
        params.append(str(date_from))
    if date_to:
        if not _PWA_DATE_RE.match(str(date_to)):
            return None, "تاريخ «إلى» غير صالح — الصيغة YYYY-MM-DD"
        where.append("t.date < (%s::date + INTERVAL '1 day')")
        params.append(str(date_to))
    if product_id:
        try:
            where.append("t.product_id = %s")
            params.append(int(product_id))
        except (TypeError, ValueError):
            return None, "معرّف السلعة غير صالح."
    if exclude_friday:
        where.append("EXTRACT(ISODOW FROM t.date) <> 5")
    if mov_type:
        if str(mov_type) not in _MOV_CSV_LABELS:
            return None, "نوع الحركة غير صالح."
        where.append("t.type = %s")
        params.append(str(mov_type))
    wsql = " AND ".join(where)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT t.id, t.type, t.quantity, t.date, t.reason, "
                "p.name AS product_name, "
                "COALESCE(u.display_name, u.first_name, u.username, "
                "'#' || u.telegram_id::text) AS user_name "
                "FROM transactions t "
                "JOIN products p ON p.id = t.product_id "
                "LEFT JOIN users u ON u.id = t.user_id "
                "WHERE " + wsql + " ORDER BY t.date DESC LIMIT 100000",
                tuple(params))
            rows = cur.fetchall()
        conn.commit()
    finally:
        release_connection(conn)

    def cell(v):
        s = str(v if v is not None else "")
        return '"' + s.replace('"', '""') + '"'

    lines = ["التاريخ,النوع,السلعة,الكمية,السبب,المستخدم"]
    for r in rows:
        lines.append(",".join([
            cell(str(r["date"])[:16]),
            cell(_MOV_CSV_LABELS.get(r["type"], r["type"])),
            cell(r["product_name"]), cell(r["quantity"]),
            cell(r["reason"]), cell(r["user_name"])]))
    csv_text = "\ufeff" + "\n".join(lines)   # BOM ليفتح صح في Excel
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    return {"count": len(rows), "csv": csv_text,
            "filename": "movements_" + stamp + ".csv",
            "telegram_id": user.get("telegram_id")}, None


def _pwa_api_receipt_thumb(user, order_id):
    """مصغّرة صورة الوصل (v48) — نفس صلاحيات عرض الوصل، تصغير إلى 260px."""
    data, mime, err = _pwa_api_receipt_get(user, order_id)
    if err:
        return None, err
    if not data:
        return None, "لا توجد صورة وصل لهذا الطلب بعد."
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((260, 260))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=72)
        return out.getvalue(), None
    except Exception:
        return data, None   # تعذر التصغير → الأصل كما هو


# ─── معالجات الشات (async) ───

# ─── v83: معالجات مجموعات الدردشة (async) ───

async def pwa_chat_groups_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_chat_groups_list, user)
    return _pwa_jr(data)


async def pwa_chat_group_create_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    g, err2 = await _pwa_run(_pwa_api_chat_group_create, user,
                             body.get("name"), body.get("members") or [])
    if err2:
        return _pwa_err(400, err2)
    await _pwa_run(_audit_log, "group_create", user,
                   details="مجموعة: " + str(g["name"]) + " — أعضاء: "
                           + str(g["members_count"]),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "group": g})


async def pwa_chat_gmsg_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    msgs, read_upto, has_more, err2, extra = await _pwa_run(
        _pwa_api_chat_group_messages, user,
        request.query.get("g"),
        request.query.get("after", 0),
        request.query.get("before", 0))
    if err2:
        return _pwa_err(400, err2)
    d = {"ok": True, "messages": msgs, "read_upto": read_upto, "has_more": has_more}
    if extra:
        d["members_count"] = extra.get("members_count")
        d["group_name"] = extra.get("group_name")
    return _pwa_jr(d)


async def pwa_chat_gsend_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    msg, recips, gname, err2 = await _pwa_run(
        _pwa_api_chat_group_send, user, body.get("g"), body.get("kind"),
        body.get("text"), body.get("name"), body.get("mime"), body.get("data"),
        body.get("reply"))
    if err2:
        return _pwa_err(400, err2)
    # إشعار تلغرام لأعضاء المجموعة عدا المرسل — 30 ثانية كحد أقصى لكل مستخدم
    if TG_APP is not None and recips:
        try:
            if msg["kind"] == "text":
                content = msg["body"][:300]
            elif msg["kind"] == "image":
                content = "🖼️ صورة مرفقة"
            elif msg["kind"] == "audio":
                content = "🎤 رسالة صوتية"
            elif msg["kind"] == "video":
                content = "🎬 فيديو مرفق"
            else:
                content = "📎 ملف: " + (msg["media_name"] or "")
            note = ("💬 رسالة جديدة في مجموعة «" + str(gname or "") + "»\n"
                    "من: " + str(user.get("name") or "?") + "\n\n" + content)
            now = time.time()
            for tid, _hw in recips:
                if now - _CHAT_PING.get(tid, 0) >= 30:
                    _CHAT_PING[tid] = now
                    asyncio.create_task(_chat_ping_deliver(tid, note))
        except Exception:
            pass
    await _pwa_run(_audit_log, "group_send", user,
                   details="مجموعة: " + str(gname or body.get("g"))
                           + " — نوع: " + msg["kind"],
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg})


async def pwa_chat_gread_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    data = await _pwa_run(_pwa_api_chat_group_read, user, body.get("g"))
    return _pwa_jr(data)


async def pwa_chat_gmembers_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    info, err2 = await _pwa_run(_pwa_api_chat_group_members, user,
                                request.query.get("g"))
    if err2:
        return _pwa_err(400, err2)
    return _pwa_jr(info)


async def pwa_chat_gadd_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    data, err2 = await _pwa_run(_pwa_api_chat_group_add_members, user,
                                body.get("g"), body.get("members") or [])
    if err2:
        return _pwa_err(400, err2)
    await _pwa_run(_audit_log, "group_add", user,
                   details="أضاف " + str(data.get("added")) + " عضواً للمجموعة #"
                           + str(body.get("g")),
                   ip=request.remote or "؟")
    return _pwa_jr(data)


async def pwa_chat_gleave_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    data, err2 = await _pwa_run(_pwa_api_chat_group_leave, user, body.get("g"))
    if err2:
        return _pwa_err(400, err2)
    await _pwa_run(_audit_log, "group_leave", user,
                   details="غادر المجموعة #" + str(body.get("g")),
                   ip=request.remote or "؟")
    return _pwa_jr(data)


async def pwa_chat_gdelete_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    data, err2 = await _pwa_run(_pwa_api_chat_group_delete, user, body.get("g"))
    if err2:
        return _pwa_err(400, err2)
    await _pwa_run(_audit_log, "group_delete", user,
                   details="حذف المجموعة #" + str(body.get("g")),
                   ip=request.remote or "؟")
    return _pwa_jr(data)


async def pwa_chat_gmedia_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data, mime, name, err2 = await _pwa_run(
        _pwa_api_chat_group_media, user, request.query.get("id"))
    if err2:
        return _pwa_err(404, err2)
    safe_name = str(name or "file").replace('"', "")[:120]
    return web.Response(body=data, content_type=(mime or "application/octet-stream"),
                        headers={"Cache-Control": "private, max-age=604800",
                                 "Content-Disposition":
                                     "inline; filename=\"" + safe_name + "\""})


async def pwa_chat_contacts_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_chat_contacts, user)
    return _pwa_jr(data)


async def pwa_chat_list_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_chat_list, user)
    return _pwa_jr(data)


async def pwa_chat_msg_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    msgs, read_upto, has_more, err2, extra = await _pwa_run(
        _pwa_api_chat_messages, user,
        request.query.get("user"),
        request.query.get("after", 0),
        request.query.get("before", 0))
    if err2:
        return _pwa_err(400, err2)
    d = {"ok": True, "messages": msgs, "read_upto": read_upto,
         "has_more": has_more}
    if extra:
        d["peer_typing"] = bool(extra.get("peer_typing"))
        d["peer_seen"] = str(extra.get("peer_seen") or "")
        d["peer_has_web"] = bool(extra.get("peer_has_web"))
    return _pwa_jr(d)


async def pwa_chat_send_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    msg, peer_tid, peer_has_web, err2 = await _pwa_run(
        _pwa_api_chat_send, user, body.get("to"), body.get("kind"),
        body.get("text"), body.get("name"), body.get("mime"), body.get("data"),
        body.get("reply"))
    if err2:
        return _pwa_err(400, err2)
    # إشعار تلغرام للمستلم — مرة كل 30 ثانية كحد أقصى (لا إغراق في المحادثة الحية)
    if TG_APP is not None and peer_tid:
        try:
            key = int(peer_tid)
            now = time.time()
            if now - _CHAT_PING.get(key, 0) >= 30:
                _CHAT_PING[key] = now
                if msg["kind"] == "text":
                    content = msg["body"][:300]
                elif msg["kind"] == "image":
                    content = "🖼️ صورة مرفقة"
                elif msg["kind"] == "audio":
                    content = "🎤 رسالة صوتية"
                elif msg["kind"] == "video":
                    content = "🎬 فيديو مرفق"
                else:
                    content = "📎 ملف: " + (msg["media_name"] or "")
                note = ("💬 رسالة جديدة في تطبيق المخزون\n"
                        "من: " + str(user.get("name") or "?") + "\n\n" + content)
                asyncio.create_task(_chat_ping_deliver(int(peer_tid), note))
        except Exception:
            pass
    await _pwa_run(_audit_log, "chat_send", user,
                   details="إلى #" + str(body.get("to")) + " نوع: " + msg["kind"],
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "message": msg, "peer_has_web": bool(peer_has_web)})


async def _chat_ping_deliver(tid, text):
    try:
        await TG_APP.bot.send_message(chat_id=tid, text=text)
    except Exception:
        pass


async def pwa_chat_read_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    data = await _pwa_run(_pwa_api_chat_read, user, body.get("user"))
    return _pwa_jr(data)


async def pwa_chat_typing_handler(request):
    """v61: إشارة «يكتب الآن» — نبضة من حقل الكتابة يقرأها الطرف الآخر في الاستقصاء."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    data = await _pwa_run(_pwa_api_chat_typing, user, body.get("user"))
    return _pwa_jr(data)


async def pwa_chat_search_handler(request):
    """v61: بحث داخل المحادثة — نص الرسائل وأسماء الملفات، حتى 60 نتيجة."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    msgs, total, err2 = await _pwa_run(
        _pwa_api_chat_search, user,
        request.query.get("user"), request.query.get("q", ""))
    if err2:
        return _pwa_err(400, err2)
    return _pwa_jr({"ok": True, "messages": msgs, "total": total})


async def pwa_chat_delete_handler(request):
    """v53: حذف المحادثة بالكامل (الاتجاهين) — طلب المشارك نفسه."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    n, err2 = await _pwa_run(_pwa_api_chat_delete_conversation, user, body.get("user"))
    if err2:
        return _pwa_err(400, err2)
    await _pwa_run(_audit_log, "chat_delete_conv", user,
                   details="حذف محادثة كاملة مع #" + str(body.get("user")),
                   ip=request.remote or "؟")
    return _pwa_jr({"ok": True, "deleted": int(n or 0)})


_MICLOG_LAST = {}   # v56: تقييد تحويل تقارير الميكروفون إلى تلغرام — رسالة كل 60 ثانية لكل نوع خطأ


async def pwa_miclog_handler(request):
    """v56: تقرير تشخيصي صامت عند فشل بدء الميكروفون داخل التطبيق.
    v57: حقل src (mic/cam) لتقارير الكاميرا أيضاً + att عدد محاولات سلم إعادة المحاولة.
    v59: تشخيص عميق — perm حالة الإذن (Permissions API) + devs عدد الميكروفونات المكتشفة
    (enumerateDevices) + fails عداد الفشل الدائم + labels أسماء الأجهزة. هذه البيانات
    تحسم السبب الحقيقي بدل التخمين، وحقل name=DIAG يعني فحصاً يدوياً من لوحة v59.
    v61: حقل env — بيئة التشغيل (webview نافذة مدمجة / chrome مستقل) لحسم نقاش
    «المشكلة في التطبيق أم الهاتف» من أول تقرير."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    name = str(body.get("name", "") or "")[:60]
    msg = str(body.get("msg", "") or "")[:200]
    ver = str(body.get("ver", "") or "")[:12]
    mime = str(body.get("mime", "") or "")[:60]
    ua = str(body.get("ua", "") or "")[:180]
    att = str(body.get("att", "") or "")[:24]
    src = "cam" if str(body.get("src", "") or "")[:12] == "cam" else "mic"
    perm = str(body.get("perm", "") or "")[:16]
    devs = str(body.get("devs", "") or "")[:12]
    fails = str(body.get("fails", "") or "")[:12]
    labels = str(body.get("labels", "") or "")[:80]
    env = str(body.get("env", "") or "")[:12]
    env_map = {"webview": "WebView مدمج ⚠️", "chrome": "Chrome مستقل ✅"}
    env_txt = env_map.get(env, env if env else "—")
    who = ""
    if isinstance(user, dict):
        who = str(user.get("name") or "") or ("#" + str(user.get("id") or "؟"))
    if not who:
        who = "؟"
    logging.info("[MICLOG] " + src + " " + name + " | " + msg + " | " + who + " | " + ua)
    now = time.time()
    key = src + ":" + name
    if TG_APP is not None and (now - _MICLOG_LAST.get(key, 0)) > 60:
        _MICLOG_LAST[key] = now
        if src == "cam":
            head = "📷 تقرير كاميرا — فشل الفتح"
        elif name == "DIAG":
            head = "🔬 فحص ميكروفون — تقرير تشخيصي من لوحة v59"
        else:
            head = "🎤 تقرير ميكروفون — فشل بدء التسجيل"
        att_line = ("🔁 المحاولات: " + att + "\n") if att else ""
        # v59: بيانات تحسم السبب — إذن محجوب؟ لا جهاز صوت؟ أم جهاز موجود والنظام يرفض فتحه؟
        perm_map = {"granted": "ممنوح", "denied": "محجوب", "prompt": "لم يُطلب بعد",
                    "nq": "غير متاح للاستعلام"}
        perm_txt = perm_map.get(perm, perm if perm else "—")
        dg_lines = ("🔐 الإذن: " + perm_txt + "\n"
                    "🖥️ بيئة التشغيل: " + env_txt + "\n"
                    "🎙️ ميكروفونات مكتشفة: " + (devs if devs else "—")
                    + ((" (" + labels + ")") if labels else "") + "\n"
                    "📊 مرات فشل مسجلة على الجهاز: " + (fails if fails else "0") + "\n")
        text = (head + "\n"
                "❌ الخطأ: " + (name or "غير معروف") + "\n"
                "📝 التفصيل: " + (msg or "—") + "\n" + att_line + dg_lines +
                "👤 المستخدم: " + who + "\n"
                "🌐 الإصدار: " + (ver or "؟") + " — الصيغة: " + (mime or "افتراضية") + "\n"
                "📱 الجهاز: " + (ua or "—"))
        seen = set()
        for admin_id in list(ADMIN_IDS) + list(OWNER_IDS or []):
            try:
                aid = int(admin_id)
            except (TypeError, ValueError):
                continue
            if aid in seen:
                continue
            seen.add(aid)
            try:
                await TG_APP.bot.send_message(chat_id=aid, text=text)
            except Exception as e:
                logging.error("[MICLOG] notify failed for " + str(aid) + ": " + str(e))
    return _pwa_jr({"ok": True})


async def pwa_chat_unread_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data = await _pwa_run(_pwa_api_chat_unread_total, user)
    return _pwa_jr(data)


async def pwa_chat_media_handler(request):
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    data, mime, name, err2 = await _pwa_run(
        _pwa_api_chat_media, user, request.query.get("id"))
    if err2:
        return _pwa_err(404, err2)
    safe_name = str(name or "file").replace('"', "")[:120]
    return web.Response(body=data, content_type=(mime or "application/octet-stream"),
                        headers={"Cache-Control": "private, max-age=604800",
                                 "Content-Disposition":
                                     "inline; filename=\"" + safe_name + "\""})


async def pwa_remember_login_handler(request):
    """v48: دخول بضغطة واحدة برمز التذكّر المحفوظ في الجهاز."""
    try:
        body = await request.json()
    except Exception:
        return _pwa_err(400, "طلب غير صالح.")
    sess, remember, user, err = await _pwa_run(_pwa_api_remember_login,
                                               body.get("token"))
    if err:
        return _pwa_err(401, err)
    await _pwa_run(_audit_log, "remember_login", user, ip=request.remote or "؟")
    resp = _pwa_jr({"ok": True, "remember": remember, "user": user})
    secure_cookie = bool(os.getenv("RENDER_EXTERNAL_URL", "")) or request.secure
    resp.set_cookie(PWA_COOKIE, sess, max_age=PWA_SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure_cookie, path="/")
    return resp


async def pwa_export_movements_handler(request):
    """v48: تصدير CSV كامل للحركة — الملف يُرسل عبر تلجرام، وإن تعذر أُعيد نصياً."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    res, err2 = await _pwa_run(
        _pwa_api_export_movements, user, body.get("from"), body.get("to"),
        bool(body.get("nofriday")), body.get("product"), body.get("type"))
    if err2:
        return _pwa_err(400, err2)
    await _pwa_run(_audit_log, "export_movements", user,
                   details="عمليات: " + str(res["count"]), ip=request.remote or "؟")
    out = {"ok": True, "sent": False, "count": res["count"],
           "filename": res["filename"]}
    sent = False
    if TG_APP is not None and res.get("telegram_id"):
        try:
            await TG_APP.bot.send_document(
                chat_id=res["telegram_id"],
                document=io.BytesIO(res["csv"].encode("utf-8")),
                filename=res["filename"])
            sent = True
        except Exception as e:
            logging.warning("[PWA] export TG send failed: " + str(e))
    out["sent"] = sent
    if not sent:
        out["csv"] = res["csv"]   # نسخة احتياطية نصية داخل التطبيق
    return _pwa_jr(out)


async def pwa_receipt_thumb_handler(request):
    """v48: مصغّرة صورة وصل التوريد (تُعرض في بطاقة الطلب)."""
    user, err = _pwa_guard(await _pwa_auth_user(request), None)
    if err:
        return err
    try:
        oid = int(request.match_info["oid"])
    except (KeyError, TypeError, ValueError):
        return _pwa_err(400, "طلب غير صالح.")
    data, err2 = await _pwa_run(_pwa_api_receipt_thumb, user, oid)
    if err2 or not data:
        return _pwa_err(404, err2 or "لا توجد صورة وصل لهذا الطلب بعد.")
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


def register_pwa_routes(app):
    """تركيب مسارات PWA على خادم aiohttp الموجود (webhook أو polling+health).
    آمنة إضافياً بالكامل: لا تمس مسارات البوت ولا جداوله."""
    _pwa_init_tables()
    pwa_bootstrap_admin()
    app.router.add_get("/", pwa_page_handler)
    app.router.add_get("/pwa", pwa_page_handler)
    app.router.add_get("/app", pwa_page_handler)
    app.router.add_get("/sw.js", pwa_page_handler)
    app.router.add_get("/manifest.webmanifest", pwa_manifest_handler)
    app.router.add_get("/icon.png", pwa_icon_handler)
    app.router.add_post("/api/login", pwa_login_handler)
    # ── v32: التسجيل الذاتي + الرمز السري + إدارة الفئات ──
    app.router.add_post("/api/register", pwa_register_handler)
    app.router.add_post("/api/forgot", pwa_forgot_handler)               # v39: طلب استعادة الرقم السري
    app.router.add_get("/api/resets", pwa_resets_handler)                # v39: قائمة الطلبات (للمدير)
    app.router.add_post("/api/resets/generate", pwa_reset_generate_handler)  # v39: توليد رقم سر مؤقت
    app.router.add_post("/api/login/otp/request", pwa_otp_request_handler)
    app.router.add_post("/api/login/otp/verify", pwa_otp_verify_handler)
    # ── v33: دخول Google + قنوات OTP الحقيقية + تفعيل العضويات ──
    app.router.add_get("/api/auth/google/start", pwa_google_start_handler)
    app.router.add_get("/api/auth/google/callback", pwa_google_callback_handler)
    app.router.add_get("/api/auth/google/exchange", pwa_google_exchange_handler)
    app.router.add_post("/api/auth/google/join", pwa_google_join_handler)
    app.router.add_post("/api/registrations/approve", pwa_registration_approve_handler)
    app.router.add_get("/api/registrations", pwa_registrations_handler)
    app.router.add_post("/api/registrations/delete", pwa_registration_delete_handler)
    app.router.add_post("/api/users/role", pwa_users_role_handler)
    app.router.add_post("/api/logout", pwa_logout_handler)
    app.router.add_get("/api/status", pwa_status_handler)
    app.router.add_post("/api/setup", pwa_setup_handler)
    app.router.add_post("/api/changepass", pwa_changepass_handler)
    app.router.add_get("/api/me", pwa_me_handler)
    app.router.add_get("/api/stats", pwa_stats_handler)
    app.router.add_get("/api/products", pwa_products_handler)
    app.router.add_post("/api/qty", pwa_qty_handler)
    app.router.add_get("/api/movements", pwa_movements_handler)
    app.router.add_get("/api/low", pwa_low_handler)
    # (v30) مركز الإشعارات
    app.router.add_get("/api/notifications", pwa_notifications_handler)
    app.router.add_post("/api/notifications/read", pwa_notifications_read_handler)
    app.router.add_get("/api/oil", pwa_oil_handler)
    # ── v88: الفواتير من التطبيق ──
    app.router.add_get("/api/invoice/products", pwa_invoice_products_handler)
    app.router.add_get("/api/invoice/workers", pwa_invoice_workers_handler)
    app.router.add_post("/api/invoice/create", pwa_invoice_create_handler)
    app.router.add_get("/api/invoice/recent", pwa_invoice_recent_handler)
    app.router.add_get("/api/invoice/list", pwa_invoice_list_handler)
    app.router.add_get("/api/invoice/pdf", pwa_invoice_pdf_handler)
    app.router.add_get("/api/users", pwa_users_handler)
    app.router.add_post("/api/users/toggle", pwa_users_toggle_handler)
    app.router.add_post("/api/users/delweb", pwa_users_delweb_handler)   # v64: حذف حساب التطبيق دون البوت
    app.router.add_post("/api/users/webpass", pwa_users_webpass_handler)
    app.router.add_post("/api/products/create", pwa_products_create_handler)
    app.router.add_post("/api/products/edit", pwa_products_edit_handler)        # v41: تعديل سلعة
    app.router.add_post("/api/products/archive", pwa_products_archive_handler)  # v41: حذف/استعادة ناعمة
    app.router.add_post("/api/profile", pwa_profile_handler)                    # v41: الاسم + الصورة
    app.router.add_post("/api/profile/username", pwa_profile_username_handler)  # v41: تغيير اسم الدخول
    app.router.add_get("/api/pending", pwa_pending_handler)
    app.router.add_get("/api/incoming", pwa_incoming_handler)
    # (v31) الوصل + تفاصيل السلعة — المسارات الديناميكية بعد الثابتة
    app.router.add_post("/api/incoming/receipt", pwa_receipt_upload_handler)
    app.router.add_get("/api/incoming/{oid}/receipt", pwa_receipt_get_handler)
    app.router.add_get("/api/product/{pid}", pwa_product_detail_handler)
    app.router.add_post("/api/incoming/create", pwa_incoming_create_handler)
    app.router.add_post("/api/incoming/advance", pwa_incoming_advance_handler)
    # ── v48: رمز التذكّر + تصدير CSV + مصغّرة الوصل ──
    app.router.add_post("/api/remember/login", pwa_remember_login_handler)
    app.router.add_post("/api/export/movements", pwa_export_movements_handler)
    app.router.add_get("/api/incoming/{oid}/thumb", pwa_receipt_thumb_handler)
    # ── v50: الرسائل — محادثات فردية بين فريق العمل ──
    app.router.add_get("/api/chat/contacts", pwa_chat_contacts_handler)
    app.router.add_get("/api/chat/list", pwa_chat_list_handler)
    app.router.add_get("/api/chat/msg", pwa_chat_msg_handler)
    app.router.add_post("/api/chat/send", pwa_chat_send_handler)
    app.router.add_post("/api/chat/read", pwa_chat_read_handler)
    app.router.add_post("/api/chat/delete", pwa_chat_delete_handler)
    app.router.add_get("/api/chat/unread", pwa_chat_unread_handler)
    app.router.add_get("/api/chat/media", pwa_chat_media_handler)
    # ── v61: واجهة المراسلة الاحترافية — يكتب الآن + بحث داخل المحادثة ──
    app.router.add_post("/api/chat/typing", pwa_chat_typing_handler)
    app.router.add_get("/api/chat/search", pwa_chat_search_handler)
    # ── v83: مجموعات الدردشة ──
    app.router.add_get("/api/chat/groups", pwa_chat_groups_handler)
    app.router.add_post("/api/chat/groups/create", pwa_chat_group_create_handler)
    app.router.add_get("/api/chat/gmsg", pwa_chat_gmsg_handler)
    app.router.add_post("/api/chat/gsend", pwa_chat_gsend_handler)
    app.router.add_post("/api/chat/gread", pwa_chat_gread_handler)
    app.router.add_get("/api/chat/gmembers", pwa_chat_gmembers_handler)
    app.router.add_post("/api/chat/gadd", pwa_chat_gadd_handler)
    app.router.add_post("/api/chat/gleave", pwa_chat_gleave_handler)
    app.router.add_post("/api/chat/gdelete", pwa_chat_gdelete_handler)
    app.router.add_get("/api/chat/gmedia", pwa_chat_gmedia_handler)
    # ── v56: تقرير تشخيص الميكروفون (يصل للمدير في تلغرام) ──
    app.router.add_post("/api/miclog", pwa_miclog_handler)
    logging.info("[PWA] مسارات تطبيق الويب مسجلة: / + /api/* (54 مساراً)")


# ═════════════════════ نهاية قسم PWA WEB ═════════════════════


if __name__ == "__main__":
    # v88: بصمة إصدار صريحة في اللوج — تثبت أي نسخة كود تعمل فعلاً بعد الرفع
    # إن لم تظهر هذا السطر في لوج Render فالرفع لم يُطبّق (كود قديم ما زال يعمل)
    logging.info("[STARTUP] ===== BOT " + PWA_VERSION
                 + " (invoice-archive + safe-pdf-dl + desktop) =====")
    # Initialize multi-DB URLs before anything else
    _init_db_urls()
    logging.info("[DB] Active DB: #" + str(_active_db_index + 1) + "/" + str(len(_db_urls)))
    if RESET_STOCK_PIN == "5614":
        logging.warning("[SECURITY] RESET_STOCK_PIN لا يزال على الافتراضي 5614 — غيّره من متغيرات البيئة!")
    # init_db() calls get_connection() which lazily creates the pool via
    # init_pool(), so an explicit init_pool() here would leak the previous
    # pool's connections.
    init_db()
    # v88: تسخين الشعار في الخلفية — بذرة أولى من الرابط إلى القاعدة إن لزم،
    # وإن فشل فالنظام يعمل بلا غلاف بأمان (كل المسارات fallback). لا يحجب الإقلاع.
    threading.Thread(target=_theme_logo_load, daemon=True, name="theme-logo-warm").start()
    _load_offline_queue()
    _init_low_stock_ids()
    # Restore last backup from disk into memory (if any) so /backup → restore_last works
    # immediately after a Render restart, even before the next scheduled backup runs.
    if _last_backup_data is None:
        _loaded = _load_backup_from_disk()
        if _loaded:
            _last_backup_data = _loaded
    # ── استعادة تلقائية عند الإقلاع: قاعدة فارغة + نسخة متاحة = ضياع حقيقي ──
    # تغطي أيضاً حالة الحذف اليدوي: إن وجد البوت القاعدة فارغة تماماً (0 سلعة)
    # ووجد نسخة محفوظة (ذاكرة/قرص) أعادها فوراً دون تدخل، وأشعار المدراء بعد الإقلاع.
    try:
        _conn = get_connection()
        _empty_db = False
        try:
            with _conn.cursor() as _cur:
                _cur.execute("SELECT COUNT(*) FROM products")
                _empty_db = (_cur.fetchone()[0] == 0)
            _conn.commit()
        finally:
            release_connection(_conn)
        if _empty_db:
            _src = _last_backup_data
            if _src and len(_src.get("products", [])) > 0:
                _summary = db_import_backup(_src)
                _bdate = str(_src.get("date", "?"))[:16]
                _nprod = len(_src.get("products", []))
                _ntx = len(_src.get("transactions", []))
                _startup_restore_notice = (
                    "♻️ استعادة تلقائية عند إقلاع البوت\n\n"
                    "كانت قاعدة البيانات فارغة، فاستُعيدت آخر نسخة احتياطية:\n"
                    "📦 السلع: " + str(_nprod) + "\n"
                    "📜 العمليات: " + str(_ntx) + "\n"
                    "📅 تاريخ النسخة: " + _bdate + "\n\n"
                    "إن لم تتوقع هذه الاستعادة راجع النسخ الاحتياطية فوراً."
                )
                logging.info("[STARTUP] Auto-restored backup into empty DB (" + str(_nprod) + " products): " + str(_summary)[:150])
            else:
                logging.info("[STARTUP] DB is empty and no usable backup found — starting fresh.")
    except Exception as e:
        logging.error("[STARTUP] Auto-restore check failed: " + str(e))
    # (v80) قفل حلقة العجز عند الإقلاع: أي سلعة سالبة بلا طلب معلق تُسجل تلقائياً
    try:
        _sync_boot = sync_all_pending_deficits()
        if _sync_boot["created_units"] > 0:
            logging.warning("[STARTUP][v80] deficit sync: " + str(_sync_boot["created_units"])
                            + " unit(s) registered across " + str(_sync_boot["created_products"])
                            + " product(s)")
    except Exception as _sync_boot_err:
        logging.error("[STARTUP][v80] deficit sync failed: " + str(_sync_boot_err))
    tg_app = build_application()
    render_port = int(os.getenv("PORT", 0))
    if WEBHOOK_URL:
        port = render_port or 10000
        webhook_path = "/webhook"
        url = WEBHOOK_URL + webhook_path
        print("[BOT] Webhook mode, port " + str(port) + ", url: " + url)
        webhook_app = web.Application(middlewares=[_pwa_sec_middleware],
                                      client_max_size=64 * 1024 * 1024)
        webhook_app.router.add_get("/health", _health_handler)
        register_pwa_routes(webhook_app)
        # ── Security: Telegram sends X-Telegram-Bot-Api-Secret-Token on every update;
        # PTB validates it against this value and rejects spoofed requests.
        import secrets as _secrets
        secret_token = os.getenv("WEBHOOK_SECRET_TOKEN") or _secrets.token_hex(32)
        logging.info("[BOT] Webhook secret token validation enabled.")
        TG_APP = tg_app  # لإشعارات الويب من مسارات PWA
        tg_app.run_webhook(
            webhook_path=webhook_path, url=url, port=port,
            secret_token=secret_token,
            drop_pending_updates=True, webhook_app=webhook_app)
    elif render_port:
        health_app = web.Application(middlewares=[_pwa_sec_middleware],
                                     client_max_size=64 * 1024 * 1024)
        health_app.router.add_get("/health", _health_handler)
        register_pwa_routes(health_app)
        async def on_startup(aio_app):
            global _startup_restore_notice, TG_APP
            _pwa_set_main_loop()  # v40: تثبيت الحلقة الرئيسية مبكراً للجدولة من الخيوط
            await tg_app.initialize()
            TG_APP = tg_app  # لإشعارات الويب من مسارات PWA
            await tg_app.start()
            try:
                await tg_app.bot.delete_webhook(drop_pending_updates=True)
            except Exception:
                pass
            # إشعار المدراء بنتيجة الاستعادة التلقائية عند الإقلاع (إن حدثت)
            if _startup_restore_notice:
                for _aid in ADMIN_IDS:
                    try:
                        await tg_app.bot.send_message(chat_id=_aid, text=_startup_restore_notice)
                    except Exception:
                        pass
                _startup_restore_notice = None
            await tg_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)
            asyncio.create_task(_keep_alive(aio_app))
            asyncio.create_task(_daily_backup_loop(tg_app.bot))
            print("[BOT] Daily auto-backup enabled (17:00 Algeria time, once per day)")
            print("[BOT] Polling + health + keep-alive on port " + str(render_port))
        async def on_cleanup(aio_app):
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        health_app.on_startup.append(on_startup)
        health_app.on_cleanup.append(on_cleanup)
        web.run_app(health_app, host="0.0.0.0", port=render_port, print=None, access_log=None)
    else:
        print("[BOT] Local polling mode")
        tg_app.run_polling(drop_pending_updates=True)

