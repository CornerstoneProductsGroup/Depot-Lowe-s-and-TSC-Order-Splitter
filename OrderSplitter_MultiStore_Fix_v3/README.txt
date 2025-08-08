
# Multi-Store Order Splitter (Depot + Lowe's + TSC) — v3

Strict column-anchored SKU detection:
- Anchors on the Model/Item/SKU column header, falls back to x-density.
- Exact match by default; optional unique-prefix fuzzy match.
- Per-store tabs, per-vendor PDFs, error PDF, ZIP, and persistent batch list.

## Run
```
pip install -r requirements.txt
streamlit run daily_order_splitter_app.py
```
