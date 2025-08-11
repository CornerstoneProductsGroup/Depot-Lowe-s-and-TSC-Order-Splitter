# Order Splitter App (Home Depot, Lowe's, Tractor Supply)

Streamlit app that splits uploaded packing slip/order PDFs into per-vendor PDFs using a SKU→Vendor mapping spreadsheet.

- Three tabs: Home Depot, Lowe's, Tractor Supply
- Uses PyMuPDF for robust text + word-box extraction
- Anchor-aware extraction reads the SKU under the **Model / Model Number** area
- Per-store ZIPs, summary.csv, unmatched_or_errors.csv
