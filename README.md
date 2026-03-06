# AI Recipe Generator Prototype

Functional prototype for generating recipes from user text, category, optional menu upload, and simulated shopping history.

## Setup

1. **Create virtual environment** (recommended):

   ```bash
   cd documentations/prototypes/ai-recipe-generator
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

2. **Install dependencies**:

   If you see "Import could not be resolved" in the IDE, select the interpreter: `.venv/bin/python` (Cmd+Shift+P → "Python: Select Interpreter").

   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API keys**:

   Copy `.env.example` to `.env` and add your keys:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`:

   - **OPENROUTER_API_KEY**: From [OpenRouter](https://openrouter.ai/keys) — used for recipe text generation
   - **FAL_KEY**: From [Fal Dashboard](https://fal.ai/dashboard/keys) — used for recipe image generation

   Keys are loaded from `.env` (via `python-dotenv`). Do not commit `.env`.

## Run

```bash
streamlit run app.py
```

## Sample Data

- **sample_products.csv**: Product catalog (SKU, title, category, etc.)
- **sample-trx-sample.csv**: Transaction sample (~6K rows, in repo) — EIJI PATISSERIE + top shiptos for demo
- **sample-trx-jan26-feb26.csv**: Full transaction history (Jan–Feb 2026) — not in repo (exceeds GitHub 100MB limit)

The app uses **sample-trx-sample.csv** by default. For full data (production or richer demo):

1. Place `sample-trx-jan26-feb26.csv` in the project root, or
2. Set `TRX_CSV_PATH` in `.env` to the full file path.

To regenerate the sample from the full file:

```bash
python scripts/generate_trx_sample.py
```

The prototype uses a pre-selected sample shipto (**EIJI PATISSERIE**, Bakery Local) with rich transaction history and diverse products for demonstration.

## Form Inputs

1. **Input text**: Free-form prompt (e.g. "Bikin resep untuk ramadhan")
2. **Category**: Makanan / Minuman / Lainnya
3. **Upload menu** (optional): PDF or image file

## Architecture

- **OpenRouter**: Chat completion for structured recipe JSON (supports Gemini, Claude, etc.)
- **Web search grounding**: For ingredients outside our SKU catalog, the app uses OpenRouter's `:online` / web plugin to estimate prices via real-time web search (adds cost)
- **Fal**: Image generation for recipe hero image
- **Data**: Loads shipto profile + shopping history from sample CSVs, enriches prompt with catalog candidates
