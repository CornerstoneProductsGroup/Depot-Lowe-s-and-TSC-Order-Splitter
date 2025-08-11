import os, io, base64, glob
import streamlit as st
import pandas as pd

from splitter_core.run_manager import process_run

st.set_page_config(page_title="Order Splitter (HD/Lowe's/TSC)", layout="wide")

st.title("📄 Order Splitter — Home Depot • Lowe's • Tractor Supply")
st.caption("Upload packing slip/order PDFs + SKU→Vendor Excel. Get per-vendor PDFs, ZIPs, and summaries.")

RUNS_ROOT = "runs"
os.makedirs(RUNS_ROOT, exist_ok=True)

def download_link(path: str, label: str):
    with open(path, "rb") as f:
        b = f.read()
    b64 = base64.b64encode(b).decode("utf-8")
    href = f'<a href="data:application/octet-stream;base64,{b64}" download="{os.path.basename(path)}">{label}</a>'
    st.markdown(href, unsafe_allow_html=True)

def store_tab_ui(store_name: str, key_prefix: str):
    st.subheader(f"{store_name}")
    with st.container(border=True):
        st.markdown("**1) Upload Inputs**")
        pdf_files = st.file_uploader(
            "Upload one or more PDF files",
            type=["pdf"],
            accept_multiple_files=True,
            key=f"{key_prefix}_pdfs",
            help="Drop your packing slips / order PDFs here."
        )
        sku_file = st.file_uploader(
            "Upload SKU→Vendor mapping (Excel .xlsx)",
            type=["xlsx"],
            accept_multiple_files=False,
            key=f"{key_prefix}_sku",
            help="Must contain a vendor column and at least one SKU-like column (SKU, Model, Model #, Model Number, Item #, Internet #, UPC)."
        )
        run_btn = st.button("▶️ Run Splitter", type="primary", use_container_width=True, key=f"{key_prefix}_run", disabled=not (pdf_files and sku_file))

    st.markdown("---")

    if run_btn:
        try:
            pdfs = [(f.name, f.getvalue()) for f in pdf_files]
            sku_bytes = sku_file.getvalue()
            result = process_run(store_name, pdfs, sku_bytes, RUNS_ROOT)

            st.success(f"Done! Assigned {result['assignments_count']} pages. Unmatched pages: {result['unmatched_count']}.")

            st.subheader("Downloads")
            download_link(result["zip_path"], f"💾 Download ZIP ({os.path.basename(result['zip_path'])})")
            download_link(result["summary_csv"], "📊 Download Summary CSV")
            download_link(result["unmatched_csv"], "🚩 Download Unmatched/Errors CSV")

            if result["vendor_files"]:
                with st.expander("Per-Vendor PDFs created"):
                    for fp in result["vendor_files"]:
                        download_link(fp, f"📄 {os.path.basename(fp)}")

            st.subheader("Summary Preview")
            try:
                df = pd.read_csv(result["summary_csv"])
                st.dataframe(df, use_container_width=True, height=300)
            except Exception as e:
                st.info(f"Could not preview summary: {e}")

        except Exception as e:
            st.error(f"Error while processing: {e}")

tab_hd, tab_lowes, tab_tsc = st.tabs(["Home Depot", "Lowe's", "Tractor Supply"])
with tab_hd:
    store_tab_ui("Home Depot", "hd")
with tab_lowes:
    store_tab_ui("Lowe's", "lowes")
with tab_tsc:
    store_tab_ui("Tractor Supply", "tsc")

st.markdown("---")
st.subheader("📥 Download Center")
st.caption("Previously generated outputs are kept here until you redeploy or delete them.")

runs = sorted(glob.glob(os.path.join(RUNS_ROOT, "*")), reverse=True)
if not runs:
    st.write("No past runs yet.")
else:
    for r in runs[:10]:
        store_dirs = [d for d in glob.glob(os.path.join(r, "*")) if os.path.isdir(d)]
        with st.expander(f"Run: {os.path.basename(r)}"):
            for sdir in store_dirs:
                st.write(f"**Store output:** `{os.path.basename(sdir)}`")
                files = sorted(glob.glob(os.path.join(sdir, "*")))
                for fp in files:
                    try:
                        size_kb = os.path.getsize(fp) // 1024
                    except Exception:
                        size_kb = "?"
                    st.write(f"- {os.path.basename(fp)} ({size_kb} KB)")
            zips = glob.glob(os.path.join(r, "*.zip"))
            for z in zips:
                st.write(f"- ZIP: `{os.path.basename(z)}`")
