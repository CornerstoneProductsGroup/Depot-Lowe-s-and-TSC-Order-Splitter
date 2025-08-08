
# Multi-Store Order Splitter (Depot + Lowe's + TSC)
This Streamlit app splits packing slip PDFs by **vendor** using a SKU→Vendor mapping Excel.

## Why this works better
- It *only* reads SKUs from the **Model Number / Model # / Item # / SKU** column region found via the header row.
- Ignores addresses, phone numbers, apartment numbers, etc.
- Exact normalized matching first, then a *unique* prefix/suffix fallback.

## How to run
```
pip install -r requirements.txt
streamlit run daily_order_splitter_app.py
```

## Usage
- Go to the tab for the store (Home Depot, Lowe's, Tractor Supply).
- Upload that store's SKU→Vendor Excel (needs a SKU/Model column and a Vendor column).
- Upload the PDF, click **Split**.
- Download per-vendor PDFs, a batch ZIP, and review the pages summary.
- Toggle the *debug* checkbox to see detected SKUs and column windows per page.
