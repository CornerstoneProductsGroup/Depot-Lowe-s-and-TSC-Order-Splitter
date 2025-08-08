
# Home Depot Order Splitter (Fixed)
- Finds the **Model Number** column by locating the header row (Model Number / Internet Number / Item Description / Qty Shipped).
- Reads only within that column region (prevents address/apartment numbers from being misread as SKUs).
- Robust SKU→Vendor matching: exact normalized match first, then unique prefix/suffix fallback.
- Shows per-vendor PDFs, an error file for unmatched pages, and a pages summary table.
- Use the debug checkbox to see SKUs detected per page and the column window inferred.

## Run
```
pip install -r requirements.txt
streamlit run daily_order_splitter_app.py
```
