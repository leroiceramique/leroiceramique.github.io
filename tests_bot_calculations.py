#!/usr/bin/env python3
"""
Unit tests for Produit Le Roi bot - Critical Calculations
Tests: m2 subtotal, plante pieces, levenshtein, product type detection, unit codes
Run: python tests_bot_calculations.py
"""
import math
import sys

# ═══════════════════════════════════════════════════════════════
#  Copied pure functions from bot.py (no DB/Telegram dependencies)
# ═══════════════════════════════════════════════════════════════

def _is_pcs_product(name):
    """Check if product should use pcs unit."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip().lower()
    return (n.startswith("motif") or n.startswith("plante")
            or n.startswith("120/60") or n.startswith("120x60")
            or n.startswith("120×60"))


def _invoice_product_type(name):
    """Detect invoice product type from name."""
    if not name or not isinstance(name, str):
        return "m2"
    n = name.strip().lower()
    if n.startswith("motif") or n.startswith("120/60") or n.startswith("120x60") or n.startswith("120×60"):
        return "pcs"
    if n.startswith("plante"):
        return "plant"
    return "m2"


def _get_unit_code(product_name):
    return "pcs" if _is_pcs_product(product_name) else "crt"


def _get_unit_ar(product_name):
    return "قطعة" if _is_pcs_product(product_name) else "كرتون"


def _normalize_text(text):
    import re
    import unicodedata
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("/", "x")
    text = text.replace("×", "x")
    return text


def _levenshtein_distance(s1, s2):
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


# ── Invoice calculation functions (from bot.py logic) ──

def calc_m2_subtotal(qty_m2, unit_price):
    """Calculate m² subtotal: area × price per m²."""
    return round(qty_m2 * unit_price, 2)


def calc_m2_boxes(qty_m2, box_area):
    """Calculate number of boxes needed for given m² area. Always rounds UP."""
    if box_area <= 0:
        return 0
    return math.ceil(qty_m2 / box_area)


def calc_plante_pieces(length_m, piece_length=0.45):
    """Calculate number of plante pieces for given length. Always rounds UP."""
    if piece_length <= 0:
        return 0
    return math.ceil(length_m / piece_length)


def calc_plante_subtotal(length_m, unit_price):
    """Calculate plante subtotal: length × price per meter."""
    return round(length_m * unit_price, 2)


def calc_pcs_subtotal(qty, unit_price):
    """Calculate pieces subtotal: qty × unit price."""
    return round(qty * unit_price, 2)


# ═══════════════════════════════════════════════════════════════
#  TESTS
# ═══════════════════════════════════════════════════════════════

passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        errors.append(name)


# ── Product Type Detection Tests ──
print("\n📦 Product Type Detection:")
test("motif → pcs", _invoice_product_type("motif beige 30x30") == "pcs")
test("Motif → pcs (case insensitive)", _invoice_product_type("Motif Bleu") == "pcs")
test("120/60 → pcs", _invoice_product_type("120/60 blanc") == "pcs")
test("120x60 → pcs", _invoice_product_type("120x60 gris") == "pcs")
test("120×60 → pcs (unicode ×)", _invoice_product_type("120×60 noir") == "pcs")
test("plante → plant", _invoice_product_type("plante gris") == "plant")
test("Plante → plant (case insensitive)", _invoice_product_type("Plante Blanc") == "plant")
test("ceram → m2", _invoice_product_type("ceram 60x60") == "m2")
test("empty string → m2", _invoice_product_type("") == "m2")
test("None → m2", _invoice_product_type(None) == "m2")

# ── Unit Code Tests ──
print("\n📏 Unit Codes:")
test("motif → pcs", _get_unit_code("motif beige") == "pcs")
test("plante → pcs", _get_unit_code("plante gris") == "pcs")
test("120/60 → pcs", _get_unit_code("120/60 blanc") == "pcs")
test("ceram → crt", _get_unit_code("ceram 60x60") == "crt")
test("random product → crt", _get_unit_code("savon 5L") == "crt")

# ── Unit Arabic Tests ──
print("\n📝 Unit Arabic Labels:")
test("motif → قطعة", _get_unit_ar("motif beige") == "قطعة")
test("ceram → كرتون", _get_unit_ar("ceram 60x60") == "كرتون")

# ── m² Subtotal Tests ──
print("\n🧮 m² Subtotal Calculations:")
test("10m² × 1500 = 15000", calc_m2_subtotal(10, 1500) == 15000)
test("5.5m² × 2000 = 11000", calc_m2_subtotal(5.5, 2000) == 11000)
test("0m² × 1000 = 0", calc_m2_subtotal(0, 1000) == 0)
test("3.33m² × 1200 = 3996", calc_m2_subtotal(3.33, 1200) == 3996)
test("1m² × 0 = 0", calc_m2_subtotal(1, 0) == 0)

# ── m² Box Count Tests (round UP) ──
print("\n📦 m² Box Count (ceil rounding):")
test("10m² / 1.44m² = 7 boxes", calc_m2_boxes(10, 1.44) == 7)
test("5m² / 1.44m² = 4 boxes", calc_m2_boxes(5, 1.44) == 4)
test("1.44m² / 1.44m² = 1 box (exact)", calc_m2_boxes(1.44, 1.44) == 1)
test("1.45m² / 1.44m² = 2 boxes (rounds up)", calc_m2_boxes(1.45, 1.44) == 2)
test("0.5m² / 1.44m² = 1 box (rounds up from 0.34)", calc_m2_boxes(0.5, 1.44) == 1)
test("0m² / 1.44m² = 0 boxes", calc_m2_boxes(0, 1.44) == 0)

# ── Plante Pieces Tests (round UP) ──
print("\n🌿 Plante Pieces (ceil rounding):")
test("1m / 0.45m = 3 pieces", calc_plante_pieces(1, 0.45) == 3)
test("0.45m / 0.45m = 1 piece (exact)", calc_plante_pieces(0.45, 0.45) == 1)
test("0.5m / 0.45m = 2 pieces (rounds up)", calc_plante_pieces(0.5, 0.45) == 2)
test("2m / 0.45m = 5 pieces", calc_plante_pieces(2, 0.45) == 5)
test("0m / 0.45m = 0 pieces", calc_plante_pieces(0, 0.45) == 0)

# ── Plante Subtotal Tests ──
print("\n💰 Plante Subtotal:")
test("1m × 500 = 500", calc_plante_subtotal(1, 500) == 500)
test("2.5m × 400 = 1000", calc_plante_subtotal(2.5, 400) == 1000)
test("0.45m × 600 = 270", calc_plante_subtotal(0.45, 600) == 270)

# ── PCS Subtotal Tests ──
print("\n🔢 PCS Subtotal:")
test("10 pcs × 500 = 5000", calc_pcs_subtotal(10, 500) == 5000)
test("1 pcs × 100 = 100", calc_pcs_subtotal(1, 100) == 100)
test("0 pcs × 999 = 0", calc_pcs_subtotal(0, 999) == 0)

# ── Levenshtein Distance Tests ──
print("\n📝 Levenshtein Distance:")
test("identical = 0", _levenshtein_distance("hello", "hello") == 0)
test("empty = length", _levenshtein_distance("abc", "") == 3)
test("one insertion = 1", _levenshtein_distance("abc", "abcd") == 1)
test("one deletion = 1", _levenshtein_distance("abcd", "abc") == 1)
test("one substitution = 1", _levenshtein_distance("abc", "axc") == 1)
test("ceram vs cerame = 1", _levenshtein_distance("ceram", "cerame") == 1)
test("60x60 vs 60/60 = 1", _levenshtein_distance("60x60", "60/60") == 1)

# ── Text Normalization Tests ──
print("\n🔤 Text Normalization:")
test("lowercase", _normalize_text("CERAM 60X60") == "ceram 60x60")
test("slash to x", _normalize_text("60/60") == "60x60")
test("multiply to x", _normalize_text("60×60") == "60x60")
test("extra spaces", _normalize_text("  ceram   60x60  ") == "ceram 60x60")
test("empty = empty", _normalize_text("") == "")

# ── Edge Cases ──
print("\n⚠️ Edge Cases:")
test("plante 0.9m → 2 pieces", calc_plante_pieces(0.9, 0.45) == 2)
test("100m² / 1.44 = 70 boxes", calc_m2_boxes(100, 1.44) == 70)
test("m2 subtotal precision: 3.33 × 1200 = 3996.0", calc_m2_subtotal(3.33, 1200) == 3996.0)

# ═══════════════════════════════════════════════════════════════
#  RESULTS
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
if errors:
    print(f"\nFailed tests: {', '.join(errors)}")
print(f"{'='*50}")

sys.exit(0 if failed == 0 else 1)
