
import streamlit as st
import pandas as pd
import re
from io import BytesIO
from PyPDF2 import PdfReader, PdfWriter
import pdfplumber
import zipfile

st.set_page_config(page_title="Order Splitter — Depot + Lowe's + TSC", layout="wide")
st.title("📦 Multi-Store Order Splitter (Depot + Lowe's + TSC) — Strict Column Match")

# -------------------- Normalization & helpers --------------------
ALNUM = re.compile(r'[^A-Za-z0-9]+')
HAS_ALPHA = re.compile(r'[A-Za-z]')
HAS_DIGIT = re.compile(r'\d')

def norm_sku(s: str) -> str:
    return ALNUM.sub('', str(s)).upper()

def group_lines_by_y(words, tol=3.5):
    buckets = []
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        placed = False
        for b in buckets:
            if abs(b["y"] - w["top"]) <= tol:
                b["words"].append(w); b["y"] = (b["y"] + w["top"]) / 2.0
                placed = True
                break
        if not placed:
            buckets.append({"y": w["top"], "words": [w]})
    return buckets

def find_header_line(words):
    lines = group_lines_by_y(words, tol=4.0)
    best = None
    best_score = -1
    for line in lines:
        texts = [w["text"].strip().lower() for w in line["words"]]
        joined = " ".join(texts)
        score = 0
        hints = [
            ("model number", 3),
            ("model #", 3),
            ("model", 2),
            ("internet number", 1),
            ("item description", 2),
            ("description", 1),
            ("qty shipped", 3),
            ("qty", 1),
            ("sku", 3),
            ("item #", 2),
            ("item", 1),
        ]
        for h, val in hints:
            if h in joined:
                score += val
        if score > best_score or (score == best_score and (best is None or line["y"] < best["y"])):
            best = line; best_score = score
    return best

def cluster_span(line_words, target_terms):
    items = [(w["text"].strip().lower(), w) for w in sorted(line_words, key=lambda x: x["x0"])]
    for i, (t, w) in enumerate(items):
        if t == target_terms[0]:
            j = i; ok = True
            for k in range(1, len(target_terms)):
                found = False
                for jj in range(j+1, min(j+4, len(items))):
                    if items[jj][0] == target_terms[k]:
                        found = True; j = jj; break
                if not found:
                    ok = False; break
            if ok:
                xs = [items[i][1]["x0"], items[j][1]["x1"]]
                return (min(xs), max(xs))
    for t, w in items:
        if t == target_terms[0]:
            return (w["x0"], w["x1"])
    return None

def words_in_region(ppage, x0, x1, y0, y1):
    return [w for w in ppage.extract_words(use_text_flow=True) if (x0 <= w["x0"] <= x1 and y0 <= w["top"] <= y1)]

def best_model_column_region(ppage, header):
    words = ppage.extract_words(use_text_flow=True)
    if header:
        h = header["words"]
        sp_model = (cluster_span(h, ["model","number"]) or
                    cluster_span(h, ["model", "#"]) or
                    cluster_span(h, ["model"]) or
                    cluster_span(h, ["sku"]) or
                    cluster_span(h, ["item","#"]) or
                    cluster_span(h, ["item","number"]))
        candidates = [cluster_span(h, ["internet","number"]),
                      cluster_span(h, ["item","description"]),
                      cluster_span(h, ["description"]),
                      cluster_span(h, ["qty","shipped"]),
                      cluster_span(h, ["qty"])]
    else:
        h = []
        sp_model = None
        candidates = []

    words = ppage.extract_words(use_text_flow=True)
    if header and sp_model:
        x0 = sp_model[0] - 2
        right_stops = [c[0] for c in candidates if c]
        x1_cap = min(right_stops) if right_stops else ppage.width * 0.6
        x1 = x1_cap - 2
        y0 = min(w["bottom"] for w in h) + 2 if h else 100
        y1 = ppage.height - 36
        return (max(0, x0), max(x0+10, x1), y0, y1)

    # Fallback by x-density of code-like tokens
    words = [w for w in words if 40 <= w["x0"] <= ppage.width - 40 and 60 <= w["top"] <= ppage.height - 40]
    HAS_ALPHA = re.compile(r'[A-Za-z]'); HAS_DIGIT = re.compile(r'\d')
    buckets = {}
    for w in words:
        t = w["text"].strip()
        if len(t) >= 3 and HAS_ALPHA.search(t) and HAS_DIGIT.search(t):
            b = int(w["x0"] // 30)
            buckets[b] = buckets.get(b, 0) + 1
    if not buckets:
        return (40, ppage.width * 0.6, 100, ppage.height - 40)
    best_b = max(buckets, key=buckets.get)
    x0 = best_b * 30 - 10
    x1 = x0 + 220
    return (max(0, x0), min(ppage.width-20, x1), 100, ppage.height - 40)

def load_mapping(xlsx_file):
    df = pd.read_excel(xlsx_file)
    df.columns = [str(c).strip() for c in df.columns]
    sku_col = None; vendor_col = None
    for c in df.columns:
        cl = c.lower()
        if sku_col is None and ('sku' in cl or 'model' in cl):
            sku_col = c
        if vendor_col is None and 'vendor' in cl:
            vendor_col = c
    if sku_col is None or vendor_col is None:
        st.error("❌ Mapping must include a SKU/Model column and a Vendor column.")
        return {}, set()
    df = df[[sku_col, vendor_col]].dropna()
    exact = {}
    for raw, vend in zip(df[sku_col].astype(str), df[vendor_col].astype(str)):
        for p in re.split(r'[,\s/]+', raw.strip()):
            p = p.strip()
            if not p: continue
            k = re.sub(r'[^A-Za-z0-9]+','',p).upper()
            if not k: continue
            exact[k] = vend.strip()
    return exact, set(exact.keys())

def extract_candidates_from_line(words_on_line, keyset):
    HAS_ALPHA = re.compile(r'[A-Za-z]'); HAS_DIGIT = re.compile(r'\d')
    toks = []
    for w in sorted(words_on_line, key=lambda w: w["x0"]):
        t = w["text"].strip().rstrip(":,;")
        if len(t) < 2: continue
        if not HAS_ALPHA.search(t) or not HAS_DIGIT.search(t):
            continue
        if not re.fullmatch(r'[A-Za-z0-9\-]{2,}', t):
            continue
        norm = re.sub(r'[^A-Za-z0-9]+','',t).upper()
        if len(norm) >= 3:
            toks.append(t)
    return toks

def extract_skus_generic(ppage, debug=False):
    words = ppage.extract_words(use_text_flow=True)
    header = find_header_line(words)
    x0,x1,y0,y1 = best_model_column_region(ppage, header)
    region = words_in_region(ppage, x0, x1, y0, y1)
    lines = group_lines_by_y(region, tol=3.5)
    if debug:
        st.write(f"Region x0-x1=({x0:.1f}-{x1:.1f}) y0-y1=({y0:.1f}-{y1:.1f}); lines={len(lines)}")
    return lines

def words_in_region(ppage, x0, x1, y0, y1):
    return [w for w in ppage.extract_words(use_text_flow=True) if (x0 <= w["x0"] <= x1 and y0 <= w["top"] <= y1)]

def match_vendor_strict(token, exact_map):
    key = re.sub(r'[^A-Za-z0-9]+','',token).upper()
    return exact_map.get(key)

def match_vendor_fuzzy(token, exact_map, keyset):
    key = re.sub(r'[^A-Za-z0-9]+','',token).upper()
    if key in exact_map: return exact_map[key]
    cands = [k for k in keyset if key.startswith(k) or k.startswith(key)]
    cands = list(set(cands))
    if len(cands) == 1:
        return exact_map[cands[0]]
    return None

def split_pdf_to_vendors_in_memory(pdf_uploaded, exact_map, keyset, base_name, allow_fuzzy=False, debug=False):
    if hasattr(pdf_uploaded, "getvalue"):
        pdf_bytes = pdf_uploaded.getvalue()
    elif isinstance(pdf_uploaded, (bytes, bytearray)):
        pdf_bytes = bytes(pdf_uploaded)
    else:
        raise ValueError("Unsupported PDF input type.")

    reader = PdfReader(BytesIO(pdf_bytes))
    pl = pdfplumber.open(BytesIO(pdf_bytes))

    writers = {}
    error_writer = PdfWriter()

    try:
        for idx, (ppage, pypage) in enumerate(zip(pl.pages, reader.pages), start=1):
            lines = extract_skus_generic(ppage, debug=debug)
            page_skus = []
            for line in lines:
                page_skus.extend(extract_candidates_from_line(line["words"], keyset))
            if debug:
                st.write(f"Page {idx} raw candidates: {page_skus}")

            matched_vendors = set()
            for sku in page_skus:
                vend = match_vendor_strict(sku, exact_map)
                if not vend and allow_fuzzy:
                    vend = match_vendor_fuzzy(sku, exact_map, keyset)
                if vend:
                    matched_vendors.add(vend)

            if matched_vendors:
                for v in matched_vendors:
                    writers.setdefault(v, PdfWriter()).add_page(pypage)
            else:
                error_writer.add_page(pypage)
    finally:
        pl.close()

    vendor_files = {}
    for vendor, writer in writers.items():
        fname = f"{base_name} {vendor}.pdf"
        bio = BytesIO()
        writer.write(bio)
        vendor_files[vendor] = (fname, bio.getvalue())

    error_file = None
    if len(error_writer.pages) > 0:
        ef_name = f"{base_name} error.pdf"
        bio = BytesIO()
        error_writer.write(bio)
        error_file = (ef_name, bio.getvalue())

    zip_name = f"{base_name}.zip"
    zip_bio = BytesIO()
    with zipfile.ZipFile(zip_bio, "w", zipfile.ZIP_DEFLATED) as zf:
        for v, (fname, data) in vendor_files.items():
            zf.writestr(fname, data)
        if error_file:
            zf.writestr(error_file[0], error_file[1])
    zip_bytes = zip_bio.getvalue()

    return vendor_files, error_file, zip_name, zip_bytes

def stats_from_files(files_dict):
    rows = []
    for vendor, (fname, data) in sorted(files_dict.items()):
        try:
            r = PdfReader(BytesIO(data))
            pages = len(r.pages)
        except Exception:
            pages = 0
        rows.append({"Vendor": vendor, "File": fname, "Pages": pages})
    df = pd.DataFrame(rows)
    total = int(df["Pages"].sum()) if not df.empty else 0
    return df, total

if "batches" not in st.session_state:
    st.session_state["batches"] = {"Depot": [], "Lowe's": [], "TSC": []}

tabs = st.tabs(["Home Depot", "Lowe's", "Tractor Supply"])

def store_tab(label, key_store):
    st.subheader(label)
    sku = st.file_uploader(f"Upload {label} SKU→Vendor Excel", type=["xlsx"], key=f"sku_{key_store}")
    pdf = st.file_uploader(f"Upload {label} PDF", type=["pdf"], key=f"pdf_{key_store}")
    dbg = st.checkbox("Show per-page SKUs (debug)", key=f"dbg_{key_store}")
    fuzzy = st.checkbox("Allow fuzzy (unique prefix) matches", value=False, key=f"fuzzy_{key_store}")
    c1, c2 = st.columns(2)
    if c1.button(f"🚀 Split {label}", disabled=not (sku and pdf), key=f"btn_{key_store}"):
        exact_map, keyset = load_mapping(sku)
        if not exact_map:
            st.error("No SKUs in mapping; check your file.")
        else:
            base = (pdf.name or label.replace(' ', '')).rsplit(".", 1)[0]
            try:
                files, err, zip_name, zip_bytes = split_pdf_to_vendors_in_memory(
                    pdf, exact_map, keyset, base, allow_fuzzy=fuzzy, debug=dbg
                )
            except Exception as e:
                st.error(f"{label} split failed: {e}")
            else:
                st.session_state["batches"][key_store].append({"batch": base, "zip_name": zip_name, "zip_bytes": zip_bytes, "files": files, "err": err})
                st.success(f"{label} split complete.")
    if c2.button(f"🆕 New {label} Session", key=f"new_{key_store}"):
        st.session_state["batches"][key_store].clear()
        st.success(f"{label} session cleared.")

    if st.session_state["batches"][key_store]:
        st.write(f"### 📥 {label} Downloads & Stats")
        sel = st.selectbox("Select batch", [b["batch"] for b in st.session_state["batches"][key_store]], key=f"sel_{key_store}")
        b = next(x for x in st.session_state["batches"][key_store] if x["batch"] == sel)
        st.sidebar.header(f"📦 {label} Batch ZIP")
        st.sidebar.download_button(f"⬇️ {b['zip_name']}", b["zip_bytes"], file_name=b["zip_name"], key=f"zip_{key_store}_{sel}")
        st.write("#### Vendor Files")
        for v, (fname, data) in sorted(b["files"].items()):
            st.download_button(fname, data, file_name=fname, key=f"{key_store}_{sel}_{v}")
        if b["err"]:
            st.download_button(b["err"][0], b["err"][1], file_name=b["err"][0], key=f"{key_store}_{sel}_err")
        st.write("#### Pages Summary")
        df, total = stats_from_files(b["files"])
        st.table(df)
        st.write(f"**Total pages:** {total}")

with tabs[0]:
    store_tab("Home Depot", "Depot")
with tabs[1]:
    store_tab("Lowe's", "Lowe's")
with tabs[2]:
    store_tab("Tractor Supply", "TSC")
