"""
Load sample data and build shipto profile + shopping history for recipe generation.
"""
from __future__ import annotations

import csv
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

# Transaction file: prefer env, then full file, then sample (for demo)
TRX_FULL = DATA_DIR / "sample-trx-jan26-feb26.csv"
TRX_SAMPLE = DATA_DIR / "sample-trx-sample.csv"


def _norm(v: str | None) -> str:
    return (v or "").replace(",", "").strip()


def _parse_num(v: str | None) -> float:
    """Parse number with comma thousands separator."""
    if not v:
        return 0.0
    try:
        return float((v or "").replace(",", "").strip() or "0")
    except ValueError:
        return 0.0


def load_products(csv_path: Path | None = None) -> list[dict]:
    """Load product catalog."""
    path = csv_path or DATA_DIR / "sample_products.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_transactions(csv_path: Path | None = None) -> list[dict]:
    """Load transaction data. Prefers full file; falls back to sample for demo."""
    if csv_path is not None:
        path = Path(csv_path)
    else:
        env_path = os.getenv("TRX_CSV_PATH")
        path = Path(env_path) if env_path else None
        if path is None or not path.exists():
            path = TRX_FULL if TRX_FULL.exists() else TRX_SAMPLE
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_shipto_rankings(transactions: list[dict]) -> list[tuple[str, int, int, int, str, str]]:
    """
    Rank shiptos by order count, product diversity, category diversity.
    Returns list of (ship_to_code, n_orders, n_products, n_categories, business_type, ship_to_name).
    """
    received = [r for r in transactions if (r.get("order_status") or "").strip() == "RECEIVED"]
    by_shipto: dict[str, dict] = defaultdict(
        lambda: {"orders": set(), "products": Counter(), "categories": Counter(), "business_type": "", "ship_to_name": ""}
    )
    for r in received:
        st = _norm(r.get("ship_to_code", ""))
        if not st:
            continue
        by_shipto[st]["orders"].add(r.get("order_id", ""))
        by_shipto[st]["products"][_norm(r.get("item_code", ""))] += 1
        by_shipto[st]["categories"][(r.get("hierarchy_1_category") or "").strip()] += 1
        by_shipto[st]["business_type"] = (r.get("cg_3_business_type_desc") or "").strip()
        by_shipto[st]["ship_to_name"] = (r.get("ship_to_name") or "").strip()

    ranked = []
    for st, d in by_shipto.items():
        n_orders = len(d["orders"])
        n_products = len(d["products"])
        n_cats = len(d["categories"])
        ranked.append((st, n_orders, n_products, n_cats, d["business_type"], d["ship_to_name"]))
    ranked.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
    return ranked


# Pre-selected sample: EIJI PATISSERIE - Bakery Local, rich history, diverse products
SAMPLE_SHIPTO_CODE = "10546361"


def build_shipto_profile(
    ship_to_code: str = SAMPLE_SHIPTO_CODE,
    transactions: list[dict] | None = None,
    products: list[dict] | None = None,
) -> dict:
    """
    Build profile for a shipto: name, business type, top products, top categories, brands.
    """
    trx = transactions or load_transactions()
    prod = products or load_products()

    received = [
        r
        for r in trx
        if (r.get("order_status") or "").strip() == "RECEIVED"
        and _norm(r.get("ship_to_code", "")) == ship_to_code
    ]
    if not received:
        return {"ship_to_code": ship_to_code, "ship_to_name": "Unknown", "business_type": "", "top_products": [], "top_categories": [], "top_brands": []}

    product_qty: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    brands: Counter[str] = Counter()
    ship_to_name = ""
    business_type = ""

    for r in received:
        item = _norm(r.get("item_code", ""))
        qty = float((r.get("received_quantity") or r.get("processed_quantity") or "0").replace(",", "") or "0")
        product_qty[item] += qty
        cat = (r.get("hierarchy_1_category") or "").strip()
        if cat:
            categories[cat] += 1
        brand = (r.get("brand_name") or "").strip()
        if brand:
            brands[brand] += 1
        ship_to_name = (r.get("ship_to_name") or "").strip()
        business_type = (r.get("cg_3_business_type_desc") or "").strip()

    # Build product lookup for titles
    prod_by_code: dict[str, dict] = {}
    for p in prod:
        code = _norm(p.get("Item Code", ""))
        if code:
            prod_by_code[code] = p

    top_products = []
    for item_code, qty in product_qty.most_common(50):
        p = prod_by_code.get(item_code, {})
        title = (p.get("Title") or p.get("Display Title") or "").strip() or item_code
        top_products.append({"item_code": item_code, "title": title, "total_qty": qty})

    return {
        "ship_to_code": ship_to_code,
        "ship_to_name": ship_to_name,
        "business_type": business_type,
        "top_products": top_products[:30],
        "top_categories": [c for c, _ in categories.most_common(15)],
        "top_brands": [b for b, _ in brands.most_common(10)],
    }


def build_price_lookup(
    transactions: list[dict] | None = None,
    ship_to_code: str | None = None,
) -> dict[str, dict]:
    """
    Build price lookup from transaction data: item_code -> {price_per_unit, uom, source}.
    Uses subtotal / quantity for effective price per UOM. Prefers customer-specific data when ship_to_code given.
    """
    trx = transactions or load_transactions()
    received = [
        r
        for r in trx
        if (r.get("order_status") or "").strip() == "RECEIVED"
        and (ship_to_code is None or _norm(r.get("ship_to_code", "")) == ship_to_code)
    ]

    # Collect (item_code, uom) -> [price_per_unit]
    by_item_uom: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in received:
        item = _norm(r.get("item_code", ""))
        if not item:
            continue
        qty = _parse_num(r.get("received_quantity") or r.get("processed_quantity"))
        if qty <= 0:
            continue
        subtotal = _parse_num(r.get("subtotal"))
        if subtotal <= 0:
            continue
        uom = (r.get("uom") or "").strip() or "PAC"
        price_per = subtotal / qty
        by_item_uom[(item, uom)].append(price_per)

    # Use median for robustness; return one entry per item_code (prefer most common UOM)
    result: dict[str, dict] = {}
    for (item, uom), prices in by_item_uom.items():
        if not prices:
            continue
        prices_sorted = sorted(prices)
        median_price = prices_sorted[len(prices_sorted) // 2]
        if item not in result or result[item].get("n_observations", 0) < len(prices):
            result[item] = {
                "price_per_unit": median_price,
                "uom": uom,
                "source": "transaction",
                "n_observations": len(prices),
            }
    return result


def _parse_pack_unit(pack_unit: str | None) -> tuple[float, str] | None:
    """
    Parse Pack Unit string to (qty_per_pack, base_unit).
    E.g. '750 GR' -> (750, 'GR'), '6X500 ML' -> (3000, 'ML'), '12X180 GR' -> (2160, 'GR').

    For "N X M L/LT" (e.g. 6 X 1 L, 6 X 2 LT): transaction UOM PAC is typically per bottle,
    not per case. Use per-bottle volume (M*1000 ml) for cost.

    Handles: comma decimal ("4X2,8 KG"), surrounding quotes.
    Unhandled (fallback to Net Weight/Volume): ranges ("7-8 KG/LOAF"), nested ("6(100X2,5)").
    """
    if not pack_unit or not (s := pack_unit.strip().strip('"\'"')):
        return None
    # Normalize comma decimal, then match
    s_clean = s.replace(",", ".")
    m = re.match(r"^(?:(\d+(?:\.\d+)?)\s*[xX]\s*)?(\d+(?:\.\d+)?)\s*(GR|ML|KG|G|L|LT|BT|PC|PAC|CAR|CTN)?", s_clean, re.I)
    if not m:
        return None
    mult_str, qty_str, unit = m.group(1), m.group(2), (m.group(3) or "PAC").upper()
    mult = float(mult_str) if mult_str else 1.0
    qty = float(qty_str)
    total = mult * qty
    # Normalize to GR or ML for recipe conversion
    if unit in ("GR", "G", "KG"):
        base = "GR"
        if unit == "KG":
            total *= 1000
    elif unit in ("ML", "L", "LT"):
        base = "ML"
        if unit in ("L", "LT"):
            total *= 1000
            # "N X M L/LT": transaction PAC = 1 bottle (M L), not 1 case
            if mult > 1:
                total = qty * 1000.0  # per-bottle volume in ml
    else:
        base = unit
    return (total, base)


def build_product_unit_info(products: list[dict] | None = None) -> dict[str, dict]:
    """
    Build product unit info from catalog: item_code -> {qty_per_pack, base_unit, pack_unit}.
    Used to convert recipe amount (e.g. 100 gr) to cost from price_per_pack.
    """
    prod = products or load_products()
    result: dict[str, dict] = {}
    for p in prod:
        code = _norm(p.get("Item Code", ""))
        if not code:
            continue
        pack_unit = (p.get("Pack Unit") or "").strip()
        parsed = _parse_pack_unit(pack_unit)
        if parsed:
            qty, base = parsed
            result[code] = {"qty_per_pack": qty, "base_unit": base, "pack_unit": pack_unit}
        else:
            # Fallback: BT/JAR (bottles/jars) use Volume when available; else Net Weight
            vol = _parse_num(p.get("Volume"))
            vol_unit = (p.get("Volume Unit") or "").strip().upper()
            nw = _parse_num(p.get("Net Weight"))
            wu = (p.get("Weight Unit") or "").strip().upper()
            pu_upper = pack_unit.upper()
            if vol > 0 and vol_unit in ("CCM", "ML") and pu_upper in ("BT", "JAR"):
                result[code] = {"qty_per_pack": vol, "base_unit": "ML", "pack_unit": pack_unit or "1"}
            elif nw > 0:
                if wu == "KG":
                    nw *= 1000
                result[code] = {"qty_per_pack": nw, "base_unit": "GR", "pack_unit": pack_unit or "1"}
    return result


def _normalize_to_base(amount: float, unit: str) -> tuple[float, str]:
    """Convert amount to base unit (GR or ML). Returns (amount_in_base, base)."""
    u = (unit or "").strip().upper()
    if u in ("KG",):
        return amount * 1000, "GR"
    if u in ("G", "GR", "GRAM", "GRAMS"):
        return amount, "GR"
    if u in ("L", "LITER", "LITRE"):
        return amount * 1000, "ML"
    if u in ("ML", "CC", "CCM"):
        return amount, "ML"
    return amount, "GR"  # Default assume weight


def compute_ingredient_cost(
    amount: float,
    unit: str,
    item_code: str | None,
    price_lookup: dict[str, dict],
    product_units: dict[str, dict],
) -> tuple[float, str]:
    """
    Compute cost for one portion from price data.
    cost = (recipe_amount_in_base / qty_per_pack) * price_per_pack
    Returns (cost_idr, confidence).
    """
    if not item_code or amount <= 0:
        return 0.0, "unknown"
    price_info = price_lookup.get(item_code)
    unit_info = product_units.get(item_code)
    if not price_info or not unit_info:
        return 0.0, "unknown"
    price_per_pack = price_info["price_per_unit"]
    qty_per_pack = unit_info["qty_per_pack"]
    base_unit = unit_info["base_unit"]
    if qty_per_pack <= 0:
        return 0.0, "unknown"
    amount_base, recipe_base = _normalize_to_base(amount, unit)
    if recipe_base != base_unit:
        return 0.0, "unknown"  # Can't convert GR<->ML
    cost = (amount_base / qty_per_pack) * price_per_pack
    return cost, price_info.get("source", "transaction")


def get_catalog_candidates(
    profile: dict,
    products: list[dict] | None = None,
    price_lookup: dict[str, dict] | None = None,
    product_units: dict[str, dict] | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Get catalog products relevant to the shipto's history (for retrieval layer).
    Includes price_per_unit and unit info when available from transaction data.
    """
    prod = products or load_products()
    prices = price_lookup or {}
    units = product_units or build_product_unit_info(prod)
    history_codes = {p["item_code"] for p in profile.get("top_products", [])}
    top_cats = set(profile.get("top_categories", []))
    top_brands = set(profile.get("top_brands", []))

    candidates = []
    for p in prod:
        code = _norm(p.get("Item Code", ""))
        if not code:
            continue
        cat = (p.get("Finance Category 5") or "").strip()
        brand = (p.get("Brand Name") or "").strip()
        title = (p.get("Title") or p.get("Display Title") or "").strip()
        pack_unit = (p.get("Pack Unit") or "").strip()
        score = 0
        if code in history_codes:
            score += 100
        if cat in top_cats:
            score += 10
        if brand in top_brands:
            score += 5
        entry: dict = {
            "item_code": code,
            "title": title,
            "brand": brand,
            "category": cat,
            "score": score,
        }
        if code in prices:
            entry["price_per_unit"] = prices[code]["price_per_unit"]
            entry["uom"] = prices[code]["uom"]
            entry["price_source"] = prices[code].get("source", "transaction")
        if code in units:
            entry["qty_per_pack"] = units[code]["qty_per_pack"]
            entry["base_unit"] = units[code]["base_unit"]
            entry["pack_unit"] = units[code].get("pack_unit", "")
        candidates.append(entry)

    candidates.sort(key=lambda x: (-x["score"], x["title"]))
    return candidates[:limit]
