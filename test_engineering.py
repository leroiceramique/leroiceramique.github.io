#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_engineering.py — باقة الاختبارات الهندسية (v202) لمخزون السيراميك.
تُشغَّل بلا قاعدة بيانات وبلا خادم: تختبر البدائيات الأمنية الحقيقية
الموجودة في bot.py + سلامة بنية الحزمة والأختام.
التشغيل محليًا:   python -m pytest -q test_engineering.py
التشغيل في CI تلقائيًا عند كل رفع (.github/workflows).
ملاحظة: قيم البيئة وهمية للاستيراد فقط — لا يتصل الملف بأي خدمة.
"""
import os
import re
import sys
import types
from pathlib import Path

# ── بيئة وهمية قبل استيراد bot (الاستيراد يطلبها ولا يتصل بشيء فعلياً) ──
os.environ.setdefault("BOT_TOKEN", "123456:AAEdummy_token_for_tests_only_1234567890")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/tests_dummy")
os.environ.pop("RENDER_EXTERNAL_URL", None)  # اختبارات الكوكي تتحكم بها بنفسها

_HERE = Path(__file__).resolve().parent
_CANDIDATE_DIRS = [_HERE, _HERE / "1-الخادم-Render"]
_BOT = next((d / "bot.py" for d in _CANDIDATE_DIRS if (d / "bot.py").exists()), None)
assert _BOT is not None, "لم أجد bot.py بجوار ملف الاختبار"
sys.path.insert(0, str(_BOT.parent))
import bot  # noqa: E402  (الاستيراد المتأخر بعد حقن البيئة)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    """حالة نظيفة قبل وبعد كل اختبار — العزل الكامل بين الاختبارات."""
    bot._PWA_IPB.clear()
    bot._PWA_LOGIN_FAILS.clear()
    yield
    bot._PWA_IPB.clear()
    bot._PWA_LOGIN_FAILS.clear()


# ═══════════════════ 1) سقف لكل IP — v201 (_pwa_ip_budget) ═══════════════════

def test_ip_budget_allows_within_limit():
    for _ in range(10):                       # سقف OTP: 10 طلبات في النافذة
        assert bot._pwa_ip_budget("ipb-otp|1.2.3.4", 10, 900) is True


def test_ip_budget_blocks_over_limit():
    for _ in range(10):
        bot._pwa_ip_budget("ipb-otp|1.2.3.4", 10, 900)
    assert bot._pwa_ip_budget("ipb-otp|1.2.3.4", 10, 900) is False


def test_ip_budget_window_slides():
    import time
    old = time.time() - 901                   # طوابع أقدم من النافذة لا تُحسب
    bot._PWA_IPB["ipb-otp|5.6.7.8"] = [old] * 10
    assert bot._pwa_ip_budget("ipb-otp|5.6.7.8", 10, 900) is True


def test_ip_budget_keys_are_independent():
    for _ in range(10):
        bot._pwa_ip_budget("ipb-otp|1.1.1.1", 10, 900)
    # عنوان آخر يبدأ بميزانية كاملة — ومفتاح خدمة أخرى لنفس العنوان كذلك
    assert bot._pwa_ip_budget("ipb-otp|2.2.2.2", 10, 900) is True
    assert bot._pwa_ip_budget("ipb-resend|1.1.1.1", 10, 900) is True


def test_ip_budget_prunes_stale_keys():
    import time
    old = time.time() - 7200
    for i in range(600):                      # فوق عتبة التنظيف 512
        bot._PWA_IPB["stale|%d" % i] = [old]
    assert bot._pwa_ip_budget("fresh|1", 5, 900) is True
    assert len([k for k in bot._PWA_IPB if k.startswith("stale|")]) < 512


# ═══════════════════ 2) حارس التخمين — v29/v121/v122 ═══════════════════

def test_login_gate_allows_clean_key():
    assert bot._pwa_login_gate("1.2.3.4|user") is True


def test_login_fail_locks_after_5():
    for _ in range(5):
        bot._pwa_login_fail("1.2.3.4|user")
    with pytest.raises(bot.PwaLocked) as ei:
        bot._pwa_login_gate("1.2.3.4|user")
    assert 890 <= ei.value.retry_after <= 901  # قفل 15 دقيقة (بعد قابل للفحص)


def test_lock_alert_fires_exactly_once():
    flags = [bot._pwa_login_fail("k|u") for _ in range(5)]
    assert flags == [False, False, False, False, True]   # لحظة التفعيل فقط
    assert bot._pwa_login_fail("k|u") is False           # بعدها لا تكرار للتنبيه


def test_login_clear_unblocks():
    for _ in range(5):
        bot._pwa_login_fail("k|u")
    with pytest.raises(bot.PwaLocked):
        bot._pwa_login_gate("k|u")
    bot._pwa_login_clear("k|u")
    assert bot._pwa_login_gate("k|u") is True


def test_corrupted_state_purged_not_crash():
    # v121/v122: أي قيمة تالفة (نص/قائمة بدل dict) تُنظّف فورًا بلا AttributeError
    bot._PWA_LOGIN_FAILS["bad1"] = "garbage-string"
    bot._PWA_LOGIN_FAILS["bad2"] = ["list", "instead", "of", "dict"]
    assert bot._pwa_login_gate("bad1") is True
    assert bot._pwa_login_fail("bad2") is False   # سُجّلت محاولة على حالة مُصلحة
    assert isinstance(bot._PWA_LOGIN_FAILS["bad2"], dict)


def test_login_window_slides():
    import time
    old = time.time() - 700                   # فوق نافذة الـ10 دقائق
    bot._PWA_LOGIN_FAILS["k|u"] = {"marks": [old] * 4, "until": 0, "alerted": False}
    assert bot._pwa_login_fail("k|u") is False  # المحاولة القديمة انقضت — لم تُفعّل القفل


# ═══════════════════ 3) كلمات المرور scrypt ═══════════════════

def test_password_roundtrip():
    stored = bot._pwa_hash_password("S3cret!pass")
    assert bot._pwa_verify_password("S3cret!pass", stored) is True


def test_password_wrong_rejected():
    stored = bot._pwa_hash_password("S3cret!pass")
    assert bot._pwa_verify_password("wrong-pass", stored) is False


def test_password_salted_uniquely():
    a = bot._pwa_hash_password("same-password")
    b = bot._pwa_hash_password("same-password")
    assert a != b                              # ملح عشوائي لكل تخزين
    assert bot._pwa_verify_password("same-password", a)
    assert bot._pwa_verify_password("same-password", b)


def test_password_tampered_stored_safe():
    stored = bot._pwa_hash_password("pw123456")
    assert bot._pwa_verify_password("pw123456", stored[:-4] + "0000") is False
    assert bot._pwa_verify_password("pw123456", "not-a-valid-format") is False


# ═══════════════════ 4) تحقق المدخلات والتطبيع ═══════════════════

def test_norm_username_strips_space_at_and_emoji():
    assert bot._pwa_norm_username("  Youssef @D1 ") == "youssefd1"
    assert bot._pwa_norm_username("يوسف 🇩🇿") == "يوسف"
    assert bot._pwa_norm_username(None) == ""


def test_gemail_regex():
    assert bot._PWA_GEMAIL_RE.match("user.name@domain.co")
    assert not bot._PWA_GEMAIL_RE.match("bad@@domain.co")
    assert not bot._PWA_GEMAIL_RE.match("no-at-sign")


def test_mask_identifier():
    assert bot._pwa_mask_identifier("ab@example.com") == "a•••@example.com"
    assert bot._pwa_mask_identifier("0551234567") == "05••••67"
    assert bot._pwa_mask_identifier("abc") == "•••"
    assert bot._pwa_mask_identifier(None) == "•••"


# ═══════════════════ 5) أعلام الكوكي حسب السياق — v125 ═══════════════════

class _FakeRequest:
    def __init__(self, origin=None, secure=False):
        self.headers = {"Origin": origin} if origin else {}
        self.secure = secure
        self.url = types.SimpleNamespace(
            origin=lambda: types.SimpleNamespace(__str__=lambda s: origin or "https://x.onrender.com"))

    def __repr__(self):  # يضمن أن str() على الأصل يعمل
        return "fake"


def test_cookie_cross_origin_gets_none_secure():
    kind, secure = bot._pwa_cookie_flags(
        _FakeRequest(origin="https://leroiceramique.github.io"))
    assert kind == "None" and secure is True


def test_cookie_same_origin_gets_lax():
    os.environ.pop("RENDER_EXTERNAL_URL", None)
    kind, secure = bot._pwa_cookie_flags(_FakeRequest(origin=None, secure=False))
    assert kind == "Lax" and secure is False


# ═══════════════════ 6) بنية الحزمة والأختام — حارس الرجوع للخلف ═══════════════════

_DEAD_13 = [
    "_can_caption", "_dimension_variants", "_load_user_perm_states",
    "_org_theme_set", "_push_subs_fetch", "_pwa_api_registration_delete",
    "_sizes_name_by_id", "_theme_logo_stream", "confirm_add_product",
    "db_add_size", "db_delete_product", "db_list_sizes", "get_cached_products",
]

_PAGES_DIRS = [_HERE, _HERE / "2-واجهة-Pages"]


def _read(name):
    p = next((d / name for d in _PAGES_DIRS if (d / name).exists()), None)
    assert p is not None, "لم أجد الملف: " + name
    return p.read_text(encoding="utf-8")


def test_stamps_consistent_across_three_files():
    bot_src = _BOT.read_text(encoding="utf-8")
    idx = _read("index.html")
    sw = _read("sw.js")
    m_bot = re.search(r'PWA_VERSION = "(\d+)"', bot_src)
    m_idx = re.search(r'APP_VER = "(\d+)"', idx)
    m_sw = re.search(r"ceram-pwa-gp-v(\d+)", sw)
    assert m_bot and m_idx and m_sw, "ختم ناقص في أحد الملفات الثلاثة"
    assert m_bot.group(1) == m_idx.group(1) == m_sw.group(1), "الأختام غير متطابقة"


def test_changelog_version_matches_stamp():
    stamp = int(re.search(r'PWA_VERSION = "(\d+)"', _BOT.read_text(encoding="utf-8")).group(1))
    chg = next((_HERE / d / "CHANGELOG.md" for d in ["", "1-الخادم-Render"]
                if (_HERE / d / "CHANGELOG.md").exists()), None)
    head = chg.read_text(encoding="utf-8").lstrip().splitlines()[0]
    m = re.match(r"## v(\d+)", head)
    assert m, "أول إدخال في السجل ليس إصداراً: " + head[:40]
    assert int(m.group(1)) == stamp + 123, "خريطة الإصدار↔الختم انكسرت"


def test_dead_13_stay_dead():
    src = _BOT.read_text(encoding="utf-8")
    for name in _DEAD_13:
        assert not re.search(r"\bdef %s\b" % re.escape(name), src), \
            "دالة ميتة عادت للكود: " + name


def test_route_count_sane():
    src = _BOT.read_text(encoding="utf-8")
    n = len(re.findall(r"app\.router\.add_(?:get|post|delete|put|options)", src))
    assert n >= 140, "عدد المسارات انخفض فجأة: %d" % n


def test_ip_budget_wired_to_three_endpoints():
    src = _BOT.read_text(encoding="utf-8")
    assert len(re.findall(r'_pwa_ip_budget, "ipb-(?:otp|resend|reg)\|', src)) == 3


def test_audit_labels_include_rate_limited():
    assert bot.AUDIT_LABELS.get("rate_limited") == "⏳ تجاوز سقف الإرسال (IP)"
    for k in ("login", "login_fail", "lockout", "brute_force_lock"):
        assert k in bot.AUDIT_LABELS


def test_owner_gate_still_first_class():
    # حماية صاحب البوت (v193) لم تُمس: الاسم موجود وضمان المالك قبل الفحص
    src = _BOT.read_text(encoding="utf-8")
    assert "_pwa_ensure_owner" in src and "_OWNER_LOGIN" in src
