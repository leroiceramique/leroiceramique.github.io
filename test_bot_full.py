#!/usr/bin/env python3
"""
فحص شامل لبوت تلغرام - AST-based analysis
يفحص كل الدوال والأنماط والعلاقات بينها
Run: python test_bot_full.py
"""
import re
import ast
import sys

BOT_FILE = "/home/z/my-project/download/telegram-bot-neon/bot.py"

with open(BOT_FILE, "r", encoding="utf-8") as f:
    source = f.read()

tree = ast.parse(source)

# ═══════════════════════════════════════════════════════════
#  جمع كل أسماء الدوال
# ═══════════════════════════════════════════════════════════

async_funcs = set()
sync_funcs = set()
all_func_names = set()

for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        sync_funcs.add(node.name)
        all_func_names.add(node.name)
    elif isinstance(node, ast.AsyncFunctionDef):
        async_funcs.add(node.name)
        all_func_names.add(node.name)

print("=" * 60)
print("🔍 فحص شامل لبوت تلغرام")
print("=" * 60)

# ═══════════════════════════════════════════════════════════
#  استخراج handlers من build_application باستخدام AST
# ═══════════════════════════════════════════════════════════

def extract_handlers_from_source(src):
    """Extract all handlers registered in build_application."""
    handlers = []
    
    lines = src.split('\n')
    in_build_app = False
    for line in lines:
        if 'def build_application(' in line:
            in_build_app = True
            continue
        if in_build_app:
            if line.startswith('def ') and 'build_application' not in line:
                break
        
        # CommandHandler("cmd", func)
        m = re.search(r'CommandHandler\("(\w+)",\s*(\w+)\)', line)
        if m:
            handlers.append(("command", m.group(1), m.group(2)))
            continue
        
        # MessageHandler(filters.XXX, func)
        m = re.search(r'MessageHandler\((filters[\w&~.]+),\s*(\w+)\)', line)
        if m:
            handlers.append(("message", m.group(1), m.group(2)))
            continue
        
        # CallbackQueryHandler(func, pattern="xxx")
        m = re.search(r'CallbackQueryHandler\((\w+),\s*pattern="([^"]+)"\)', line)
        if m:
            handlers.append(("callback", m.group(2), m.group(1)))
            continue
        
        # add_error_handler(func)
        m = re.search(r'add_error_handler\((\w+)\)', line)
        if m:
            handlers.append(("error", "global", m.group(1)))
            continue
    
    return handlers

registered_handlers = extract_handlers_from_source(source)

# Find the main block separately
main_block = ""
main_match = re.search(r'if __name__ == "__main__":(.*)', source, re.DOTALL)
if main_match:
    main_block = main_match.group(1)

# ═══════════════════════════════════════════════════════════
# 1) فحص Handlers - هل كل الدوال موجودة؟
# ═══════════════════════════════════════════════════════════

print(f"\n📊 إحصائيات:")
print(f"   دوال sync:  {len(sync_funcs)}")
print(f"   دوال async: {len(async_funcs)}")
print(f"   handlers:   {len(registered_handlers)}")

print(f"\n{'='*60}")
print("1️⃣ فحص Handlers - هل الدوال موجودة ومعرّفة؟")
print("=" * 60)

errors_1 = []
for htype, pattern, func_name in registered_handlers:
    if func_name not in all_func_names:
        errors_1.append((htype, pattern, func_name))
        print(f"  ❌ [{htype}] pattern='{pattern}' → {func_name}() غير موجودة!")
    elif htype in ("callback", "command") and func_name in sync_funcs:
        print(f"  ⚠️ [{htype}] '{pattern}' → {func_name}() متزامنة (يفضّل async)")
    else:
        tag = "cmd" if htype == "command" else "msg" if htype == "message" else "cb"
        print(f"  ✅ [{tag:3s}] '{pattern}' → {func_name}()")

if not errors_1:
    print("\n  ✅ كل الـ handlers تشير لدوال موجودة")
else:
    print(f"\n  ❌ أخطاء: {len(errors_1)}")

# ═══════════════════════════════════════════════════════════
# 2) فحص States (FSM)
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("2️⃣ فحص State Machine (FSM)")
print("=" * 60)

states_set = re.findall(r'(?:_set_state\(context,\s*|context\.user_data\["state"\]\s*=\s*)"([^"]+)"', source)
states_checked = re.findall(r'(?:state\s*==\s*|state\s*!=\s*)"([^"]+)"', source)

states_set = list(set(states_set))
states_checked = list(set(states_checked))

print(f"\n  States ({len(states_set)}):")
for s in sorted(states_set):
    checked = "✅ يُفحص" if s in states_checked else "⚠️ لا يُفحص"
    print(f"     {s:35s} {checked}")

orphan_states = [s for s in states_set if s not in states_checked]
phantom_states = [s for s in states_checked if s not in states_set]

if orphan_states:
    print(f"\n  ⚠️ States تُعطى ولا تُفحص: {orphan_states}")
if phantom_states:
    print(f"\n  ⚠️ States تُفحص ولا تُعطى: {phantom_states}")
if not orphan_states and not phantom_states:
    print("\n  ✅ كل الـ states متسقة")

# ═══════════════════════════════════════════════════════════
# 3) فحص Main Block
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("3️⃣ فحص Main Block")
print("=" * 60)

if main_block:
    checks = [
        ("init_db()", "init_db()" in main_block),
        ("seed_sample_products()", "seed_sample_products()" in main_block),
        ("init_pool()", "init_pool()" in main_block),
        ("build_application()", "build_application()" in main_block),
        ("WEBHOOK_URL أولاً", re.search(r'if\s+WEBHOOK_URL:', main_block) is not None),
        ("render_port ثانياً", "elif render_port" in main_block),
        ("_keep_alive", "_keep_alive" in main_block),
        ("_daily_backup_loop", "_daily_backup_loop" in main_block),
        ("_weekly_report_loop", "_weekly_report_loop" in main_block),
        ("/health endpoint", '"/health"' in main_block),
        ("drop_pending_updates", "drop_pending_updates=True" in main_block),
        ("_set_state موجودة", "_set_state" in source),
        ("_is_state_expired موجودة", "_is_state_expired" in source),
        ("_STATE_TIMEOUT_SECONDS", "_STATE_TIMEOUT_SECONDS" in source),
    ]
    
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")

# ═══════════════════════════════════════════════════════════
# 4) فحص دوال DB المستخدمة في async بدون to_thread
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("4️⃣ فحص - DB calls في async بدون to_thread")
print("=" * 60)

db_funcs_to_check = [
    "get_connection", "get_user", "register_user", "is_admin",
    "is_approved", "search_products", "get_all_products",
    "db_add_product", "db_update_quantity", "log_transaction",
    "can_extract", "sync_user_info", "get_customer_keyboard",
    "get_admin_keyboard", "get_pending_extraction_requests",
    "create_extraction_request", "process_extraction_request",
    "set_extract_permission",
]

blocking_issues = []
for func_name in sorted(async_funcs):
    # Get the function source lines
    func_match = re.search(
        rf'async def {func_name}\([^)]*\):',
        source
    )
    if not func_match:
        continue
    
    start_pos = func_match.start()
    # Find the function body (simplified - get next 5000 chars)
    body_chunk = source[start_pos:start_pos+8000]
    
    for db_func in db_funcs_to_check:
        # Check if db_func( appears in body
        if db_func + "(" not in body_chunk:
            continue
        # Check if it's wrapped in asyncio.to_thread
        if f"to_thread({db_func}" in body_chunk:
            continue
        # Check if it's wrapped in asyncio.to_thread with dotted name
        if f"to_thread(get_" in body_chunk and db_func.startswith("get_"):
            continue
        # It's a direct call inside async - flag it
        blocking_issues.append((func_name, db_func))

if blocking_issues:
    print("  ⚠️ دوال DB تُستدعى مباشرة في async (بدون to_thread):")
    seen = set()
    for func, db_call in blocking_issues:
        key = f"{func} → {db_call}()"
        if key not in seen:
            seen.add(key)
            print(f"     ⚠️ {func}() يستدعي {db_call}() مباشرة")
    print(f"\n  ملاحظة: بعض هذه قد تكون مقصودة (مثل auto_sync_user مع block=False)")
else:
    print("  ✅ كل استدعاءات DB مغلّفة بـ asyncio.to_thread")

# ═══════════════════════════════════════════════════════════
# 5) فحص error_handler
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("5️⃣ فحص error_handler")
print("=" * 60)

has_error_handler = "add_error_handler" in source
has_error_func = "error_handler" in all_func_names
print(f"  {'✅' if has_error_handler else '❌'} add_error_handler مسجّل")
print(f"  {'✅' if has_error_func else '❌'} error_handler() معرّفة")

# ═══════════════════════════════════════════════════════════
# 6) فحص Callback patterns vs callback_data في الأزرار
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("6️⃣ فحص Callback Patterns")
print("=" * 60)

# Registered patterns
reg_patterns = [(p, f) for t, p, f in registered_handlers if t == "callback"]
print(f"\n  Patterns مسجلة ({len(reg_patterns)}):")
for pat, func in reg_patterns:
    print(f"     • {pat:40s} → {func}")

# ═══════════════════════════════════════════════════════════
# 7) فحص schema SQL
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("7️⃣ فحص Schema SQL")
print("=" * 60)

tables = re.findall(r'CREATE TABLE.*?IF NOT EXISTS\s+(\w+)', source)
print(f"  الجداول ({len(tables)}):")
for t in tables:
    print(f"     • {t}")

migrations = re.findall(r'ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+IF NOT EXISTS\s+(\w+)', source)
if migrations:
    print(f"\n  Migrations:")
    for table, col in migrations:
        print(f"     • ALTER TABLE {table} ADD COLUMN {col}")

# ═══════════════════════════════════════════════════════════
# النتيجة النهائية
# ═══════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("📋 ملخص النتيجة النهائي")
print("=" * 60)

total_critical = len(errors_1)
total_warnings = len(orphan_states) + len(phantom_states) + len(blocking_issues)

print(f"  🔴 أخطاء حرجة (دوال غير موجودة):  {total_critical}")
print(f"  🟡 تحذيرات:                         {total_warnings}")
print(f"  🟢 handlers صحيحة:                  {len(registered_handlers) - len(errors_1)}")
print(f"  🟢 states متسقة:                     {len(states_set) - len(orphan_states)}")

if total_critical == 0:
    print(f"\n  🎉 لا أخطاء حرجة! البوت جاهز للنشر.")
    if total_warnings > 0:
        print(f"  ⚠️ يوجد {total_warnings} تحذيرات (غير حرجة)")
else:
    print(f"\n  ❌ يوجد {total_critical} أخطاء حرجة - يجب إصلاحها قبل النشر!")

print("=" * 60)
sys.exit(0 if total_critical == 0 else 1)
