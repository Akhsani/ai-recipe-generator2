"""
AI Recipe Generator Prototype - Streamlit app.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from data_loader import (
    SAMPLE_SHIPTO_CODE,
    build_price_lookup,
    build_product_unit_info,
    build_shipto_profile,
    compute_ingredient_cost,
    get_catalog_candidates,
    load_products,
    load_transactions,
)

load_dotenv()

# --- Config ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
FAL_KEY = os.getenv("FAL_KEY")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
# Use a capable model; OpenRouter supports google/gemini-2.0-flash, anthropic/claude-3-haiku, etc.
OPENROUTER_MODEL = "google/gemini-3.1-flash-lite-preview"

CATEGORIES = ["Makanan", "Minuman", "Kue & Pastry", "Lainnya"]

DATA_DIR = Path(__file__).resolve().parent


PACK_SIZE_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\s*(KG|GR|G|ML|L|LT)\b", re.IGNORECASE)


def parse_amount(value: object) -> float:
    """Parse numeric values from model output safely."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return 0.0
    return 0.0


def format_idr(value: float) -> str:
    """Format Rupiah display."""
    return f"Rp {value:,.0f}"


def get_display_name(ingredient: dict) -> str:
    """Prefer customer-friendly ingredient naming."""
    return (
        ingredient.get("display_name")
        or ingredient.get("ingredient_name")
        or ingredient.get("name")
        or "Bahan tanpa nama"
    )


def normalize_instruction_steps(instructions: list) -> list[dict]:
    """Support both string and object-based instruction formats."""
    normalized = []
    for index, step in enumerate(instructions or [], start=1):
        if isinstance(step, dict):
            normalized.append(
                {
                    "title": step.get("step_title") or step.get("title") or f"Langkah {index}",
                    "detail": step.get("detail") or step.get("instruction") or "",
                    "time": step.get("time") or "",
                    "tip": step.get("tip") or "",
                }
            )
        else:
            normalized.append(
                {
                    "title": f"Langkah {index}",
                    "detail": str(step),
                    "time": "",
                    "tip": "",
                }
            )
    return normalized


def build_ingredient_warnings(ingredients: list[dict], servings: float) -> list[str]:
    """Detect recipe-output issues that reduce trust."""
    warnings = []
    if servings <= 0:
        warnings.append("Jumlah porsi tidak valid, sehingga COGS per porsi tidak bisa diandalkan.")
    for ing in ingredients:
        name = get_display_name(ing)
        matched_sku_title = (ing.get("matched_sku_title") or "").strip()
        if PACK_SIZE_PATTERN.search(name) and matched_sku_title:
            warnings.append(
                f"`{name}` masih terlihat seperti nama SKU, belum menjadi nama bahan yang ramah pembaca."
            )
    return warnings


def build_recipe_prompt(
    user_text: str,
    category: str,
    profile: dict,
    catalog_with_prices: list[dict],
    menu_note: str = "",
) -> tuple[str, str]:
    """Build the system + user prompt for recipe generation. Aligns with proposal schema."""
    history_lines = []
    for p in profile.get("top_products", [])[:20]:
        history_lines.append(f"- {p['title']} (item: {p['item_code']})")
    history_str = "\n".join(history_lines) if history_lines else "(tidak ada)"

    catalog_lines = []
    for c in catalog_with_prices[:30]:
        line = f"- {c['title']} | item_code: {c['item_code']} | pack: {c.get('pack_unit', '-')}"
        if "price_per_unit" in c:
            line += f" | Rp {c['price_per_unit']:,.0f}/{c.get('uom', 'PAC')}"
        catalog_lines.append(line)
    catalog_str = "\n".join(catalog_lines) if catalog_lines else "(tidak ada)"

    system = """Kamu adalah asisten resep untuk bisnis F&B. Generate resep dalam format JSON STRICT.

SCHEMA (ikuti persis):
{
  "title": "Nama Resep",
  "category": "Kategori",
  "servings": 4,
  "ingredients": [
    {
      "name": "Nama bahan yang mudah dipahami customer, mis. cream cheese",
      "preparation": "kondisi bahan, mis. dilelehkan / suhu ruang / dicincang halus",
      "purpose": "fungsi bahan, mis. isian / topping / saus",
      "amount": 100,
      "unit": "gr",
      "item_code": "xxx atau string kosong",
      "matched_sku_title": "nama SKU jika ada, atau string kosong",
      "source": "catalog|history|external"
    }
  ],
  "instructions": [
    {
      "step_title": "Judul langkah",
      "detail": "Instruksi yang jelas dan operasional",
      "time": "opsional, mis. 10 menit",
      "tip": "opsional"
    }
  ],
  "notes": "Catatan tambahan"
}

RULES:
- amount adalah jumlah untuk SELURUH resep, bukan per porsi
- amount: angka (number), unit: gr|ml|kg|L|sdm|sdt|bt|pc|pac
- Untuk bahan yang cocok dengan SKU, tetap pakai nama bahan yang natural untuk customer, JANGAN copy judul SKU penuh ke field name
- Jika bahan cocok dengan SKU, isi matched_sku_title dan item_code dengan presisi
- Jika bahan memakai SKU tetapi resep lebih natural dalam pcs/bar/lembar, jelaskan kebutuhan sebenarnya di `name`/`preparation`, bukan dengan menyalin ukuran pack
- item_code: WAJIB diisi jika bahan cocok dengan produk di katalog (salin item_code persis)
- source: "catalog" jika ada item_code dari katalog, "history" jika dari riwayat belanja, "external" jika bukan dari katalog
- Prioritaskan bahan dari katalog/history. COGS akan dihitung otomatis dari data transaksi untuk item dengan item_code.
- Bahan external tetap harus detail dan realistis agar customer paham COGS riil menu.
- Instructions harus rinci, urut, dan mudah dieksekusi dapur.
- Jangan tambahkan total_cogs_estimate - akan dihitung dari ingredient costs."""

    user = f"""Generate resep berdasarkan:
- Permintaan user: "{user_text}"
- Kategori: {category}
- Profil bisnis: {profile.get('ship_to_name', '')} ({profile.get('business_type', '')})

Produk yang sering dibeli (prioritaskan, gunakan item_code):
{history_str}

Katalog produk (dengan harga dari transaksi - gunakan item_code untuk mapping):
{catalog_str}
"""
    if menu_note:
        user += f"\n- User mengupload menu: {menu_note}"

    user += "\n\nReturn ONLY valid JSON, no markdown or extra text."

    return system, user


def call_openrouter(system: str, user: str, use_web_search: bool = False) -> str:
    """Call OpenRouter chat completion. Set use_web_search=True for grounded search."""
    try:
        from openai import OpenAI
    except ImportError:
        st.error("Install openai: pip install openai")
        return ""

    if not OPENROUTER_API_KEY:
        st.error("Set OPENROUTER_API_KEY in .env")
        return ""

    client = OpenAI(base_url=OPENROUTER_BASE, api_key=OPENROUTER_API_KEY)
    model = f"{OPENROUTER_MODEL}:online" if use_web_search else OPENROUTER_MODEL
    kwargs: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 2048,
        "temperature": 0.3 if use_web_search else 0.7,
    }
    if use_web_search:
        kwargs["extra_body"] = {"plugins": [{"id": "web", "max_results": 3}]}
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def fetch_external_ingredient_prices(
    ingredients: list[dict],
) -> dict[str, dict]:
    """
    Use AI with web search to estimate prices for ingredients not in our catalog.
    Returns dict of normalized name -> pricing detail.
    """
    externals = [
        ing
        for ing in ingredients
        if (ing.get("source") == "external" or not ing.get("item_code"))
        and ing.get("_cost_per_portion", 0) == 0
        and (ing.get("name") or "").strip()
    ]
    if not externals:
        return {}

    items_str = "\n".join(
        f"- {get_display_name(ing)}: {ing.get('amount', '')} {ing.get('unit', '')}"
        for ing in externals
    )
    system = """Kamu adalah asisten yang mencari harga bahan makanan di Indonesia.
Gunakan web search untuk menemukan harga eceran/retail terkini.
Return ONLY valid JSON array, no markdown. Format:
[{"name": "Nama bahan persis seperti input", "amount": "100", "unit": "gr", "price_idr": 15000, "price_note": "ringkasan dasar estimasi harga"}]
price_idr = estimasi biaya untuk amount+unit yang diminta, dalam Rupiah.
price_note = 1 kalimat singkat agar user paham asal estimasinya."""
    user = f"""Estimasi harga bahan berikut di Indonesia (harga pasar/eceran). Return JSON array:
{items_str}"""

    try:
        raw = call_openrouter(system, user, use_web_search=True)
    except Exception:
        raw = ""  # Web search may not be supported by all models
    if not raw:
        return {}

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        if not isinstance(data, list):
            return {}
        result: dict[str, dict] = {}
        for item in data:
            name = (item.get("name") or "").strip()
            price = float(item.get("price_idr", 0) or 0)
            if name and price > 0:
                result[name] = {
                    "price_idr": price,
                    "confidence": "grounded",
                    "price_note": (item.get("price_note") or "Estimasi dari web search").strip(),
                }
        return result
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def call_fal_image(prompt: str) -> str | None:
    """Generate recipe image via Fal."""
    if not FAL_KEY:
        st.warning("FAL_KEY not set - skipping image generation")
        return None
    try:
        import fal_client
    except ImportError:
        st.warning("Install fal-client: pip install fal-client")
        return None

    os.environ["FAL_KEY"] = FAL_KEY
    try:
        result = fal_client.subscribe(
            "fal-ai/nano-banana-2",
            arguments={
                "prompt": prompt,
                "image_size": "landscape_4_3",
            },
            with_logs=False,
        )
        images = result.get("images", [])
        if images and isinstance(images[0], dict) and images[0].get("url"):
            return images[0]["url"]
        return None
    except Exception as e:
        st.warning(f"Fal image generation failed: {e}")
        return None


def main() -> None:
    st.set_page_config(page_title="AI Recipe Generator", page_icon="🍳", layout="wide")
    st.title("🍳 AI Recipe Generator")
    st.markdown(
        "Generate resep dari teks, kategori, dan riwayat belanja. "
        "Biaya bahan dihitung dari **data transaksi** (prioritas) atau **web search** untuk bahan di luar katalog."
    )
    st.caption("Tujuan prototype ini adalah membantu customer memahami komposisi menu, cara buat, dan COGS yang lebih nyata.")

    # Load data once
    @st.cache_data
    def load_data():
        trx = load_transactions()
        prod = load_products()
        profile = build_shipto_profile(SAMPLE_SHIPTO_CODE, trx, prod)
        price_lookup = build_price_lookup(trx, SAMPLE_SHIPTO_CODE)
        product_units = build_product_unit_info(prod)
        candidates = get_catalog_candidates(
            profile, prod, price_lookup, product_units, limit=40
        )
        return profile, candidates, price_lookup, product_units

    profile, catalog_candidates, price_lookup, product_units = load_data()

    with st.expander("📋 Profil Shipto Simulasi", expanded=False):
        st.json(
            {
                "ship_to_name": profile["ship_to_name"],
                "business_type": profile["business_type"],
                "top_categories": profile["top_categories"],
                "top_brands": profile["top_brands"],
                "sample_products": profile["top_products"][:5],
            }
        )

    st.divider()
    st.subheader("Permintaan Resep")

    # Form
    with st.form("recipe_form"):
        col_a, col_b = st.columns(2)
        with col_a:
            user_text = st.text_input(
                "Apa resep yang diinginkan?",
                value="Bikin resep untuk ramadhan",
                placeholder="Contoh: Resep kolak pisang, Matcha latte, ...",
                help="Deskripsi bebas tentang resep yang ingin dibuat",
            )
            category = st.selectbox("Kategori", CATEGORIES, index=0)
        with col_b:
            menu_file = st.file_uploader(
                "Upload menu (opsional)",
                type=["pdf", "png", "jpg", "jpeg"],
                help="PDF atau gambar menu restoran untuk referensi",
            )
            gen_image = st.checkbox("Generate gambar resep", value=False)
        submitted = st.form_submit_button("Generate Resep")

    if not submitted:
        st.stop()

    if not user_text.strip():
        st.warning("Masukkan teks permintaan")
        st.stop()

    menu_note = ""
    if menu_file:
        menu_note = f"File: {menu_file.name} ({menu_file.size} bytes)"
        # TODO: extract text from PDF or pass image to vision model for menu parsing

    with st.status("Mempersiapkan resep...", expanded=True) as status:
        st.write("Memanggil model AI...")
        system, user = build_recipe_prompt(
            user_text, category, profile, catalog_candidates, menu_note
        )
        raw = call_openrouter(system, user)

        if not raw:
            st.error("Failed to generate recipe")
            status.update(label="Gagal", state="error")
            st.stop()

        st.write("Mem-parsing resep...")
        try:
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            recipe = json.loads(raw.strip())
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
            st.code(raw, language="text")
            status.update(label="Gagal", state="error")
            st.stop()

        st.write("Menghitung biaya dari data transaksi...")
        servings = max(parse_amount(recipe.get("servings") or 1), 1)
        total_recipe_cogs = 0.0
        for ing in recipe.get("ingredients", []):
            amt = parse_amount(ing.get("amount"))
            unit = (ing.get("unit") or "").strip()
            item_code = (ing.get("item_code") or "").strip() or None
            cost, conf = compute_ingredient_cost(
                amt, unit, item_code, price_lookup, product_units
            )
            ing["_cost_total_recipe"] = cost
            ing["_cost_per_portion"] = cost / servings if cost else 0.0
            ing["_cost_confidence"] = conf
            ing["_cost_note"] = "Biaya dihitung dari transaksi historis SKU." if cost else ""
            total_recipe_cogs += cost

        externals_missing = [
            ing for ing in recipe.get("ingredients", [])
            if ing.get("_cost_total_recipe", 0) == 0 and ing.get("_cost_confidence") == "unknown"
        ]
        if externals_missing:
            st.write("Mencari harga bahan eksternal (web search)...")
            grounded = fetch_external_ingredient_prices(recipe.get("ingredients", []))
            for ing in externals_missing:
                name = get_display_name(ing).strip()
                if name in grounded:
                    ing["_cost_total_recipe"] = grounded[name]["price_idr"]
                    ing["_cost_per_portion"] = grounded[name]["price_idr"] / servings
                    ing["_cost_confidence"] = grounded[name]["confidence"]
                    ing["_cost_note"] = grounded[name]["price_note"]

        total_recipe_cogs = sum(ing.get("_cost_total_recipe", 0) for ing in recipe.get("ingredients", []))
        status.update(label="Selesai!", state="complete")
    cogs_per_portion = total_recipe_cogs / servings if servings else 0.0
    ingredients_list = recipe.get("ingredients", [])
    warnings = build_ingredient_warnings(ingredients_list, servings)
    instructions_list = normalize_instruction_steps(recipe.get("instructions", []))

    # Display recipe
    st.success("Resep berhasil digenerate!")
    st.divider()

    cols = st.columns([1.5, 1]) if (gen_image and recipe.get("title")) else st.columns([1])
    recipe_col = cols[0]
    img_col = cols[1] if len(cols) > 1 else None

    with recipe_col:
        st.subheader(recipe.get("title", "Resep"))
        if recipe.get("notes"):
            st.info(recipe["notes"])

        meta_col1, meta_col2, meta_col3 = st.columns(3)
        with meta_col1:
            st.metric("Porsi", int(servings) if servings.is_integer() else servings)
        with meta_col2:
            st.metric("COGS resep", format_idr(total_recipe_cogs))
        with meta_col3:
            st.metric("COGS/porsi", format_idr(cogs_per_portion))

        n_catalog = sum(1 for i in ingredients_list if i.get("source") in ("catalog", "history"))
        n_total = len(ingredients_list)
        sku_pct = (n_catalog / n_total * 100) if n_total else 0
        st.caption(
            f"📦 Bahan dari katalog: {n_catalog}/{n_total} ({sku_pct:.0f}%) — "
            "🟢 Data transaksi | 🟡 Web search | ⚪ Belum ada harga"
        )
        if warnings:
            for warning in warnings:
                st.warning(warning)

        ingredients_tab, method_tab, debug_tab = st.tabs(
            ["Bahan & COGS", "Cara Buat", "Debug"]
        )

        with ingredients_tab:
            st.markdown("### Detail Bahan")
            for ing in ingredients_list:
                conf = ing.get("_cost_confidence", "")
                badge = "🟢" if conf == "transaction" else "🟡" if conf == "grounded" else "⚪"
                with st.container(border=True):
                    top_col, cost_col = st.columns([2.3, 1])
                    with top_col:
                        st.markdown(f"{badge} **{get_display_name(ing)}**")
                        meta_bits = [
                            f"{ing.get('amount', '')} {(ing.get('unit') or '').strip()} untuk total resep",
                        ]
                        if ing.get("preparation"):
                            meta_bits.append(f"Prep: {ing['preparation']}")
                        if ing.get("purpose"):
                            meta_bits.append(f"Fungsi: {ing['purpose']}")
                        st.caption(" | ".join(meta_bits))
                        if ing.get("matched_sku_title"):
                            st.caption(
                                f"SKU SOL: `{ing.get('item_code', '')}` - {ing['matched_sku_title']}"
                            )
                        elif ing.get("item_code"):
                            st.caption(f"SKU SOL: `{ing.get('item_code', '')}`")
                        if ing.get("_cost_note"):
                            st.caption(ing["_cost_note"])
                    with cost_col:
                        st.metric("Biaya resep", format_idr(ing.get("_cost_total_recipe", 0)))
                        st.metric("Biaya/porsi", format_idr(ing.get("_cost_per_portion", 0)))

        with method_tab:
            st.markdown("### Cara Membuat")
            for index, step in enumerate(instructions_list, start=1):
                with st.container(border=True):
                    st.markdown(f"**{index}. {step['title']}**")
                    if step["time"]:
                        st.caption(f"Estimasi waktu: {step['time']}")
                    st.write(step["detail"])
                    if step["tip"]:
                        st.caption(f"Tip: {step['tip']}")

        with debug_tab:
            st.markdown("### Raw JSON")
            st.json(recipe)

    if img_col is not None:
        with img_col:
            with st.spinner("Membuat gambar resep..."):
                img_prompt = f"Professional food photography, {recipe['title']}, appetizing, high quality, restaurant style"
                img_url = call_fal_image(img_prompt)
            if img_url:
                st.image(img_url, caption=recipe["title"], use_container_width=True)
            else:
                st.caption("Gagal membuat gambar")


if __name__ == "__main__":
    main()
