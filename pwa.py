"""
PWA (Progressive Web App) for ProduitLeRoi Bot
Serves a mobile-friendly Arabic RTL dashboard with inventory stats.
"""

import json
import psycopg2
import psycopg2.extras

# ═══════════════════════════════════════════════════════════
#  API: /api/stats — returns dashboard statistics as JSON
# ═══════════════════════════════════════════════════════════

def _get_dashboard_stats(get_connection_func, release_connection_func):
    """Fetch dashboard stats from DB. Returns dict."""
    stats = {
        "products_total": 0,
        "products_qty": 0,
        "products_low": 0,
        "users_total": 0,
        "users_pending": 0,
        "expenses_today": 0,
        "expenses_week": 0,
        "transactions_week": 0,
        "incoming_pending": 0,
        "pending_deliveries": 0,
        "top_products": [],
        "low_products": [],
        "recent_transactions": [],
        "recent_expenses": [],
    }
    try:
        conn = get_connection_func()
        try:
            with conn.cursor(psycopg2.extras.RealDictCursor) as cur:
                # Products
                cur.execute("SELECT COUNT(*) as c FROM products")
                stats["products_total"] = cur.fetchone()["c"]
                cur.execute("SELECT COALESCE(SUM(quantity),0) as c FROM products")
                stats["products_qty"] = int(cur.fetchone()["c"])
                cur.execute("SELECT COUNT(*) as c FROM products WHERE quantity <= minimum_stock")
                stats["products_low"] = cur.fetchone()["c"]

                # Users
                cur.execute("SELECT COUNT(*) as c FROM users WHERE active = TRUE")
                stats["users_total"] = cur.fetchone()["c"]
                cur.execute("SELECT COUNT(*) as c FROM users WHERE role = 'pending'")
                stats["users_pending"] = cur.fetchone()["c"]

                # Expenses
                cur.execute(
                    "SELECT COALESCE(SUM(amount),0) as c FROM expenses"
                    " WHERE created_at >= DATE_TRUNC('day', NOW())")
                stats["expenses_today"] = float(cur.fetchone()["c"])
                cur.execute(
                    "SELECT COALESCE(SUM(amount),0) as c FROM expenses"
                    " WHERE created_at >= NOW() - INTERVAL '7 days'")
                stats["expenses_week"] = float(cur.fetchone()["c"])

                # Transactions this week
                cur.execute(
                    "SELECT COUNT(*) as c FROM transactions"
                    " WHERE date >= NOW() - INTERVAL '7 days'")
                stats["transactions_week"] = cur.fetchone()["c"]

                # Incoming orders
                cur.execute("SELECT COUNT(*) as c FROM incoming_orders WHERE status = 'pending'")
                stats["incoming_pending"] = cur.fetchone()["c"]

                # Pending deliveries
                cur.execute(
                    "SELECT COUNT(DISTINCT product_id) as c"
                    " FROM pending_deliveries WHERE pending_qty > 0")
                stats["pending_deliveries"] = cur.fetchone()["c"]

                # Top 5 products
                cur.execute(
                    "SELECT name, quantity FROM products"
                    " ORDER BY quantity DESC LIMIT 5")
                stats["top_products"] = [
                    {"name": r["name"] or "", "qty": r["quantity"]} for r in cur.fetchall()
                ]

                # Low stock products
                cur.execute(
                    "SELECT name, quantity, minimum_stock FROM products"
                    " WHERE quantity <= minimum_stock"
                    " ORDER BY quantity ASC LIMIT 10")
                stats["low_products"] = [
                    {"name": r["name"] or "", "qty": r["quantity"], "min": r["minimum_stock"]}
                    for r in cur.fetchall()
                ]

                # Recent 10 transactions
                cur.execute(
                    "SELECT t.type, t.quantity, t.date, p.name as product_name"
                    " FROM transactions t"
                    " LEFT JOIN products p ON t.product_id = p.id"
                    " ORDER BY t.date DESC LIMIT 10")
                stats["recent_transactions"] = [
                    {
                        "type": r["type"],
                        "qty": r["quantity"],
                        "product": r["product_name"] or "",
                        "date": r["date"].isoformat() if r["date"] else "",
                    }
                    for r in cur.fetchall()
                ]

                # Recent 10 expenses
                cur.execute(
                    "SELECT e.amount, e.reason, e.created_at, u.first_name"
                    " FROM expenses e"
                    " LEFT JOIN users u ON e.user_id = u.id"
                    " ORDER BY e.created_at DESC LIMIT 10")
                stats["recent_expenses"] = [
                    {
                        "amount": float(r["amount"]),
                        "reason": r["reason"] or "",
                        "user": r["first_name"] or "",
                        "date": r["created_at"].isoformat() if r["created_at"] else "",
                    }
                    for r in cur.fetchall()
                ]

            conn.commit()
        except Exception as ex:
            conn.rollback()
            print(f"[PWA] Stats error: {ex}")
        finally:
            release_connection_func(conn)
    except Exception as ex:
        print(f"[PWA] Connection error: {ex}")
    return stats


# ═══════════════════════════════════════════════════════════
#  PWA HTML — Single-page Arabic RTL dashboard
# ═══════════════════════════════════════════════════════════

PWA_HTML = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#1a1a2e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ProduitLeRoi">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<title>ProduitLeRoi - إدارة المخزون</title>
<style>
:root {
  --bg: #0f0f1a;
  --card: #1a1a2e;
  --card2: #16213e;
  --accent: #e94560;
  --accent2: #0f3460;
  --green: #00d2a0;
  --yellow: #ffd700;
  --blue: #4da6ff;
  --text: #e8e8e8;
  --text2: #a0a0b0;
  --radius: 16px;
  --shadow: 0 4px 20px rgba(0,0,0,0.3);
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}
/* Header */
.header {
  background: linear-gradient(135deg, var(--accent2), var(--card));
  padding: 20px 16px 24px;
  text-align: center;
  position: sticky; top: 0; z-index: 100;
  box-shadow: var(--shadow);
}
.header h1 {
  font-size: 1.4rem;
  font-weight: 700;
  margin-bottom: 4px;
}
.header .subtitle {
  font-size: 0.8rem;
  color: var(--text2);
}
.header .status-dot {
  display: inline-block;
  width: 8px; height: 8px;
  background: var(--green);
  border-radius: 50%;
  margin-left: 6px;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
/* Main */
.main { padding: 16px; max-width: 600px; margin: 0 auto; }
/* Stats Grid */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 20px;
}
.stat-card {
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px;
  box-shadow: var(--shadow);
  transition: transform 0.2s;
}
.stat-card:active { transform: scale(0.97); }
.stat-card .icon { font-size: 1.6rem; margin-bottom: 6px; }
.stat-card .value { font-size: 1.5rem; font-weight: 700; }
.stat-card .label { font-size: 0.75rem; color: var(--text2); margin-top: 2px; }
.stat-card.accent .value { color: var(--accent); }
.stat-card.green .value { color: var(--green); }
.stat-card.yellow .value { color: var(--yellow); }
.stat-card.blue .value { color: var(--blue); }
/* Section */
.section {
  background: var(--card);
  border-radius: var(--radius);
  padding: 16px;
  margin-bottom: 16px;
  box-shadow: var(--shadow);
}
.section-title {
  font-size: 1rem;
  font-weight: 700;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title .badge {
  background: var(--accent);
  color: #fff;
  font-size: 0.7rem;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}
/* List items */
.list-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  font-size: 0.85rem;
}
.list-item:last-child { border-bottom: none; }
.list-item .name { flex: 1; }
.list-item .qty {
  font-weight: 700;
  min-width: 50px;
  text-align: center;
}
.list-item .meta {
  color: var(--text2);
  font-size: 0.75rem;
  min-width: 60px;
  text-align: left;
}
.qty.danger { color: var(--accent); }
.qty.warning { color: var(--yellow); }
.qty.ok { color: var(--green); }
/* Tabs */
.tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  overflow-x: auto;
  padding-bottom: 4px;
}
.tab {
  background: var(--card);
  color: var(--text2);
  border: none;
  padding: 8px 16px;
  border-radius: 20px;
  font-size: 0.8rem;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}
.tab.active {
  background: var(--accent);
  color: #fff;
}
/* Telegram Button */
.tg-btn {
  display: block;
  background: linear-gradient(135deg, #0088cc, #005f8f);
  color: #fff;
  text-align: center;
  padding: 14px;
  border-radius: var(--radius);
  text-decoration: none;
  font-weight: 700;
  font-size: 1rem;
  margin: 20px 0;
  box-shadow: var(--shadow);
  transition: transform 0.2s;
}
.tg-btn:active { transform: scale(0.97); }
/* Loading */
.loading {
  text-align: center;
  padding: 40px;
  color: var(--text2);
}
.loading .spinner {
  display: inline-block;
  width: 32px; height: 32px;
  border: 3px solid var(--card2);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
/* Refresh */
.refresh-btn {
  background: var(--card2);
  color: var(--text);
  border: none;
  padding: 6px 14px;
  border-radius: 10px;
  font-size: 0.75rem;
  font-family: inherit;
  cursor: pointer;
  float: left;
  margin-top: -28px;
}
/* Expense item */
.expense-item .amount { color: var(--accent); font-weight: 700; }
.expense-item .reason { color: var(--text2); font-size: 0.8rem; }
/* Footer */
.footer {
  text-align: center;
  padding: 20px;
  color: var(--text2);
  font-size: 0.7rem;
}
</style>
</head>
<body>

<div class="header">
  <h1>🏭 ProduitLeRoi</h1>
  <div class="subtitle">
    إدارة المخزون والمصاريف
    <span class="status-dot"></span>
  </div>
</div>

<div class="main" id="app">
  <div class="loading" id="loading">
    <div class="spinner"></div>
    <p style="margin-top:12px">جاري تحميل البيانات...</p>
  </div>
</div>

<script>
const BOT_USERNAME = '__BOT_USERNAME__';
let currentTab = 'overview';

async function fetchStats() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) throw new Error('Network error');
    return await res.json();
  } catch (e) {
    console.error('Fetch error:', e);
    return null;
  }
}

function formatNum(n) {
  if (n === undefined || n === null) return '0';
  return Number(n).toLocaleString('ar-DZ');
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('ar-DZ', { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' });
}

function renderApp(data) {
  const app = document.getElementById('app');
  const loading = document.getElementById('loading');
  if (loading) loading.remove();

  if (!data) {
    app.innerHTML = `
      <div class="section" style="text-align:center;padding:40px">
        <p style="font-size:2rem">⚠️</p>
        <p>لم يتم الاتصال بقاعدة البيانات</p>
        <button class="tab" onclick="loadData()" style="margin-top:12px">إعادة المحاولة</button>
      </div>`;
    return;
  }

  app.innerHTML = renderTabs() + renderContent(data) + renderTgButton();
}

function renderTabs() {
  const tabs = [
    { id: 'overview', label: '📊 نظرة عامة' },
    { id: 'inventory', label: '📦 المخزون' },
    { id: 'transactions', label: '🔄 الحركة' },
    { id: 'expenses', label: '💸 المصاريف' },
  ];
  return `<div class="tabs">${tabs.map(t =>
    `<button class="tab${t.id===currentTab?' active':''}" onclick="switchTab('${t.id}')">${t.label}</button>`
  ).join('')}</div>`;
}

function renderContent(data) {
  switch(currentTab) {
    case 'inventory': return renderInventory(data);
    case 'transactions': return renderTransactions(data);
    case 'expenses': return renderExpenses(data);
    default: return renderOverview(data);
  }
}

function renderOverview(data) {
  return `
  <div class="stats-grid">
    <div class="stat-card blue">
      <div class="icon">📦</div>
      <div class="value">${formatNum(data.products_total)}</div>
      <div class="label">إجمالي المنتجات</div>
    </div>
    <div class="stat-card green">
      <div class="icon">📊</div>
      <div class="value">${formatNum(data.products_qty)}</div>
      <div class="label">إجمالي الكميات</div>
    </div>
    <div class="stat-card accent">
      <div class="icon">⚠️</div>
      <div class="value">${formatNum(data.products_low)}</div>
      <div class="label">مخزون منخفض</div>
    </div>
    <div class="stat-card yellow">
      <div class="icon">👥</div>
      <div class="value">${formatNum(data.users_total)}</div>
      <div class="label">المستخدمون النشطون</div>
    </div>
  </div>

  <div class="stats-grid">
    <div class="stat-card accent">
      <div class="icon">💸</div>
      <div class="value">${formatNum(data.expenses_today)}</div>
      <div class="label">مصاريف اليوم</div>
    </div>
    <div class="stat-card accent">
      <div class="icon">💰</div>
      <div class="value">${formatNum(data.expenses_week)}</div>
      <div class="label">مصاريف الأسبوع</div>
    </div>
    <div class="stat-card blue">
      <div class="icon">🔄</div>
      <div class="value">${formatNum(data.transactions_week)}</div>
      <div class="label">حركة الأسبوع</div>
    </div>
    <div class="stat-card yellow">
      <div class="icon">🚚</div>
      <div class="value">${formatNum(data.incoming_pending)}</div>
      <div class="label">طلبات توريد معلقة</div>
    </div>
  </div>

  ${data.low_products.length > 0 ? `
  <div class="section">
    <div class="section-title">
      🔴 مخزون منخفض
      <span class="badge">${data.low_products.length}</span>
    </div>
    ${data.low_products.map(p => `
      <div class="list-item">
        <span class="name">${p.name}</span>
        <span class="qty danger">${p.qty}</span>
        <span class="meta">الحد: ${p.min}</span>
      </div>
    `).join('')}
  </div>` : ''}

  ${data.top_products.length > 0 ? `
  <div class="section">
    <div class="section-title">🏆 أكثر المنتجات كمية</div>
    ${data.top_products.map(p => `
      <div class="list-item">
        <span class="name">${p.name}</span>
        <span class="qty ok">${formatNum(p.qty)}</span>
      </div>
    `).join('')}
  </div>` : ''}
  `;
}

function renderInventory(data) {
  return `
  <div class="stats-grid">
    <div class="stat-card blue">
      <div class="icon">📦</div>
      <div class="value">${formatNum(data.products_total)}</div>
      <div class="label">إجمالي المنتجات</div>
    </div>
    <div class="stat-card accent">
      <div class="icon">⚠️</div>
      <div class="value">${formatNum(data.products_low)}</div>
      <div class="label">مخزون منخفض</div>
    </div>
  </div>

  ${data.low_products.length > 0 ? `
  <div class="section">
    <div class="section-title">
      🔴 منتجات منخفضة المخزون
      <span class="badge">${data.low_products.length}</span>
    </div>
    ${data.low_products.map(p => `
      <div class="list-item">
        <span class="name">${p.name}</span>
        <span class="qty ${p.qty === 0 ? 'danger' : 'warning'}">${p.qty}</span>
        <span class="meta">الحد: ${p.min}</span>
      </div>
    `).join('')}
  </div>` : `
  <div class="section" style="text-align:center;padding:30px">
    <p style="font-size:2rem">✅</p>
    <p>جميع المنتجات فوق الحد الأدنى</p>
  </div>`}

  ${data.top_products.length > 0 ? `
  <div class="section">
    <div class="section-title">🏆 أكثر 5 منتجات كمية</div>
    ${data.top_products.map(p => `
      <div class="list-item">
        <span class="name">${p.name}</span>
        <span class="qty ok">${formatNum(p.qty)}</span>
      </div>
    `).join('')}
  </div>` : ''}
  `;
}

function renderTransactions(data) {
  if (!data.recent_transactions || data.recent_transactions.length === 0) {
    return `<div class="section" style="text-align:center;padding:30px">
      <p style="font-size:2rem">📋</p>
      <p>لا توجد حركات حديثة</p>
    </div>`;
  }
  return `
  <div class="stat-card blue" style="margin-bottom:16px">
    <div class="icon">🔄</div>
    <div class="value">${formatNum(data.transactions_week)}</div>
    <div class="label">حركة هذا الأسبوع</div>
  </div>
  <div class="section">
    <div class="section-title">📋 آخر الحركات</div>
    ${data.recent_transactions.map(t => `
      <div class="list-item">
        <span class="name">${t.type === 'add' ? '➕' : '➖'} ${t.product}</span>
        <span class="qty ${t.type === 'add' ? 'ok' : 'danger'}">${t.type === 'add' ? '+' : '-'}${t.qty}</span>
        <span class="meta">${formatDate(t.date)}</span>
      </div>
    `).join('')}
  </div>`;
}

function renderExpenses(data) {
  if (!data.recent_expenses || data.recent_expenses.length === 0) {
    return `<div class="section" style="text-align:center;padding:30px">
      <p style="font-size:2rem">💸</p>
      <p>لا توجد مصاريف مسجلة</p>
    </div>`;
  }
  return `
  <div class="stats-grid">
    <div class="stat-card accent">
      <div class="icon">💸</div>
      <div class="value">${formatNum(data.expenses_today)}</div>
      <div class="label">مصاريف اليوم</div>
    </div>
    <div class="stat-card accent">
      <div class="icon">💰</div>
      <div class="value">${formatNum(data.expenses_week)}</div>
      <div class="label">مصاريف الأسبوع</div>
    </div>
  </div>
  <div class="section">
    <div class="section-title">📋 آخر المصاريف</div>
    ${data.recent_expenses.map(e => `
      <div class="list-item expense-item">
        <span class="name">
          <span class="amount">${formatNum(e.amount)}</span> د.ج
          <br><span class="reason">${e.reason || '—'}</span>
        </span>
        <span class="meta">${e.user}<br>${formatDate(e.date)}</span>
      </div>
    `).join('')}
  </div>`;
}

function renderTgButton() {
  if (BOT_USERNAME && BOT_USERNAME !== '__BOT_USERNAME__') {
    return `<a class="tg-btn" href="https://t.me/${BOT_USERNAME}" target="_blank">🤖 فتح البوت في تيليجرام</a>`;
  }
  return '';
}

function switchTab(tab) {
  currentTab = tab;
  if (window._lastData) renderApp(window._lastData);
}

async function loadData() {
  const data = await fetchStats();
  window._lastData = data;
  renderApp(data);
}

// Auto-refresh every 30 seconds
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>"""

# ═══════════════════════════════════════════════════════════
#  PWA Manifest
# ═══════════════════════════════════════════════════════════

PWA_MANIFEST = """{
  "name": "ProduitLeRoi - إدارة المخزون",
  "short_name": "ProduitLeRoi",
  "description": "نظام إدارة المخزون والمصاريف",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0f0f1a",
  "theme_color": "#1a1a2e",
  "orientation": "portrait",
  "icons": [
    {
      "src": "/icon.svg",
      "sizes": "any",
      "type": "image/svg+xml",
      "purpose": "any maskable"
    }
  ]
}"""

# ═══════════════════════════════════════════════════════════
#  SVG Icon
# ═══════════════════════════════════════════════════════════

PWA_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0f3460"/>
      <stop offset="100%" style="stop-color:#1a1a2e"/>
    </linearGradient>
  </defs>
  <rect width="192" height="192" rx="40" fill="url(#bg)"/>
  <text x="96" y="80" text-anchor="middle" font-size="64">🏭</text>
  <text x="96" y="140" text-anchor="middle" font-size="22" fill="#e8e8e8" font-family="Arial" font-weight="bold">PLR</text>
</svg>"""

# ═══════════════════════════════════════════════════════════
#  Service Worker
# ═══════════════════════════════════════════════════════════

PWA_SW = """const CACHE_NAME = 'plr-v1';
const ASSETS = ['/', '/manifest.json', '/icon.svg'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.url.includes('/api/')) return;
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});"""
