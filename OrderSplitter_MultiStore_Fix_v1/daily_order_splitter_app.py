
import streamlit as st
import pandas as pd
import re
from io import BytesIO
from PyPDF2 import PdfReader, PdfWriter
import pdfplumber
import zipfile

st.set_page_config(page_title="Order Splitter — Depot + Lowe's + TSC", layout="wide")
st.title("📦 Multi-Store Order Splitter (Depot + Lowe's + TSC)")

# -------------------- Normalization & helpers --------------------
ALNUM = re.compile(r'[^A-Za-z0-9]+')
HAS_ALPHA = re.compile(r'[A-Za-z]')

def norm_sku(s: str) -> str:
    return ALNUM.sub('', str(s)).upper()

def split_model_token(tok: str) -> str:
    t = tok.strip()
    # Trim absurd trailing digit runs (e.g., tracking-like)
    m = re.match(r'^([A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*?)(?=\d{5,}\b)', t)
    if m:
        return m.group(1)
    # Keep first chunk with letters
    for p in re.findall(r'[A-Za-z0-9\-]+', t):
        if HAS_ALPHA.search(p):
            return p
    return t

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
    # Generic heuristic header finder for tables with Model/Qty etc.
    lines = group_lines_by_y(words, tol=4.0)
    best = None
    best_score = -1
    for line in lines:
        texts = [w["text"].strip().lower() for w in line["words"]]
        joined = " ".join(texts)
        score = 0
        hints = [
            ("model number", 2),
            ("model", 1),
            ("internet number", 1),
            ("item description", 1),
            ("qty shipped", 2),
            ("qty", 1),
            ("description", 1),
            ("item", 1),
            ("sku", 1),
            ("item #", 1),
        ]
        for h, val in hints:
            if h in joined:
                score += val
        if score > best_score or (score == best_score and line["y"] < (best["y"] if best else 1e9)):
            best = line; best_score = score
    return best

def cluster_span(line_words, target_terms):
    items = [(w["text"].strip().lower(), w) for w in sorted(line_words, key=lambda x: x["x0"])]
    for i, (t, w) in enumerate(items):
        if t == target_terms[0]:
            # try to match subsequent
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

def pick_sku_from_line(words_on_line):
    if not words_on_line: return None
    for w in sorted(words_on_line, key=lambda w: w["x0"]):
        t = w["text"].strip().rstrip(":,;")
        if len(t) < 2: continue
        if HAS_ALPHA.search(t) and re.fullmatch(r'[A-Za-z0-9\-]{2,}', t):
            return split_model_token(t)
    return None

# -------------------- Mapping loader & matching --------------------
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
        return {}, {}
    df = df[[sku_col, vendor_col]].dropna()
    exact = {}; vendor_keys = {}
    for raw, vend in zip(df[sku_col].astype(str), df[vendor_col].astype(str)):
        for p in re.split(r'[,\s/]+', raw.strip()):
            p = p.strip()
            if not p: continue
            k = norm_sku(p)
            if not k: continue
            exact[k] = vend.strip()
            vendor_keys.setdefault(vend.strip(), set()).add(k)
    all_keys = set(exact.keys())
    return exact, {"all_keys": all_keys, "vendor_keys": vendor_keys}

def match_vendor(token, exact_map, index):
    key = norm_sku(token)
    if key in exact_map: return exact_map[key]
    # unique prefix/suffix overlap
    cand = [k for k in index["all_keys"] if key.startswith(k) or k.startswith(key)]
    cand = list(set(cand))
    if len(cand) == 1:
        return exact_map[cand[0]]
    return None

# -------------------- Store-specific extractors --------------------
def extract_skus_depot(ppage, debug=False):
    words = ppage.extract_words(use_text_flow=True)
    header = find_header_line(words)
    skus = []
    if header:
        h = header["words"]
        sp_model = cluster_span(h, ["model","number"]) or cluster_span(h, ["model", "#"]) or cluster_span(h, ["model"])
        sp_internet = cluster_span(h, ["internet","number"]) or cluster_span(h, ["internet"])
        sp_item = cluster_span(h, ["item","description"]) or cluster_span(h, ["description"])
        sp_qty = cluster_span(h, ["qty","shipped"]) or cluster_span(h, ["qty"])
        x0 = (sp_model[0] if sp_model else 40) - 2
        nxt = [sp for sp in [sp_internet, sp_item, sp_qty] if sp]
        x1 = min(sp[0] for sp in nxt) - 2 if nxt else ppage.width * 0.55
        y0 = min(w["bottom"] for w in h) + 2
        y1 = ppage.height - 36
        region = words_in_region(ppage, x0, x1, y0, y1)
        lines = group_lines_by_y(region, tol=3.5)
        for line in lines:
            sku = pick_sku_from_line(line["words"])
            if sku: skus.append(sku)
        if debug: st.write(f"[Depot] x0-x1=({x0:.1f}-{x1:.1f}) lines={len(lines)}")
    if not skus:
        txt = ppage.extract_text() or ""
        for m in re.finditer(r'Model\s*#?:?\s*([A-Za-z0-9\-]{2,})', txt, flags=re.I):
            s = split_model_token(m.group(1))
            if HAS_ALPHA.search(s): skus.append(s)
    return list(dict.fromkeys(skus))

def extract_skus_lowes(ppage, debug=False):
    # Lowe's: "Model #" column appears; use similar header find
    words = ppage.extract_words(use_text_flow=True)
    header = find_header_line(words)
    skus = []
    if header:
        h = header["words"]
        sp_model = cluster_span(h, ["model", "#"]) or cluster_span(h, ["model","number"]) or cluster_span(h, ["model"])
        sp_item = cluster_span(h, ["item","description"]) or cluster_span(h, ["description"])
        sp_qty = cluster_span(h, ["qty","shipped"]) or cluster_span(h, ["qty"])
        x0 = (sp_model[0] if sp_model else 40) - 2
        nxt = [sp for sp in [sp_item, sp_qty] if sp]
        x1 = min(sp[0] for sp in nxt) - 2 if nxt else ppage.width * 0.60
        y0 = min(w["bottom"] for w in h) + 2
        y1 = ppage.height - 36
        region = words_in_region(ppage, x0, x1, y0, y1)
        lines = group_lines_by_y(region, tol=3.5)
        for line in lines:
            sku = pick_sku_from_line(line["words"])
            if sku: skus.append(sku)
        if debug: st.write(f"[Lowe's] x0-x1=({x0:.1f}-{x1:.1f}) lines={len(lines)}")
    if not skus:
        txt = ppage.extract_text() or ""
        # Sometimes appears as "Model #: ABC123"
        for m in re.finditer(r'Model\s*#?:?\s*([A-Za-z0-9\-]{2,})', txt, flags=re.I):
            s = split_model_token(m.group(1))
            if HAS_ALPHA.search(s): skus.append(s)
    return list(dict.fromkeys(skus))

def extract_skus_tsc(ppage, debug=False):
    words = ppage.extract_words(use_text_flow=True)
    header = find_header_line(words)
    skus = []
    if header:
        h = header["words"]
        # TSC often uses "Item #" or "Model" or "SKU"
        sp_model = cluster_span(h, ["sku"]) or cluster_span(h, ["item","#"]) or cluster_span(h, ["model"]) or cluster_span(h, ["item","number"])
        sp_desc = cluster_span(h, ["description"]) or cluster_span(h, ["item","description"])
        sp_qty = cluster_span(h, ["qty","shipped"]) or cluster_span(h, ["qty"])
        x0 = (sp_model[0] if sp_model else 40) - 2
        nxt = [sp for sp in [sp_desc, sp_qty] if sp]
        x1 = min(sp[0] for sp in nxt) - 2 if nxt else ppage.width * 0.60
        y0 = min(w["bottom"] for w in h) + 2
        y1 = ppage.height - 36
        region = words_in_region(ppage, x0, x1, y0, y1)
        lines = group_lines_by_y(region, tol=3.5)
        for line in lines:
            sku = pick_sku_from_line(line["words"])
            if sku: skus.append(sku)
        if debug: st.write(f"[TSC] x0-x1=({x0:.1f}-{x1:.1f}) lines={len(lines)}")
    if not skus:
        txt = ppage.extract_text() or ""
        for m in re.finditer(r'(?:Model|Item|SKU)\s*#?:?\s*([A-Za-z0-9\-]{2,})', txt, flags=re.I):
            s = split_model_token(m.group(1))
            if HAS_ALPHA.search(s): skus.append(s)
    return list(dict.fromkeys(skus))

# -------------------- Generic splitter --------------------
def split_pdf_to_vendors_in_memory(pdf_uploaded, exact_map, index, base_name, extractor, debug=False):
    if hasattr(pdf_uploaded, "getvalue"):
        pdf_bytes = pdf_uploaded.getvalue()
    elif isinstance(pdf_uploaded, (bytes, bytearray)):
        pdf_bytes = bytes(pdf_uploaded)
    else:
        raise ValueError("Unsupported PDF input type.")
    if not exact_map:
        raise ValueError("Empty SKU→Vendor mapping. Upload the correct sheet.")

    reader = PdfReader(BytesIO(pdf_bytes))
    pl = pdfplumber.open(BytesIO(pdf_bytes))

    writers = {}
    error_writer = PdfWriter()

    try:
        for idx, (ppage, pypage) in enumerate(zip(pl.pages, reader.pages), start=1):
            page_skus = extractor(ppage, debug=debug)
            if debug:
                st.write(f"Page {idx} SKUs: {page_skus}")
            matched_vendors = set()
            for sku in page_skus:
                vend = match_vendor(sku, exact_map, index)
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

# -------------------- UI --------------------
if "batches" not in st.session_state:
    st.session_state["batches"] = {"Depot": [], "Lowe's": [], "TSC": []}

tabs = st.tabs(["Home Depot", "Lowe's", "Tractor Supply"])

# Depot tab
with tabs[0]:
    st.subheader("Home Depot")
    sku = st.file_uploader("Upload Home Depot SKU→Vendor Excel", type=["xlsx"], key="sku_depot")
    pdf = st.file_uploader("Upload Home Depot PDF", type=["pdf"], key="pdf_depot")
    dbg = st.checkbox("Show per-page SKUs (debug)", key="dbg_depot")
    c1, c2 = st.columns(2)
    if c1.button("🚀 Split Home Depot", disabled=not (sku and pdf), key="btn_depot"):
        exact_map, index = load_mapping(sku)
        base = (pdf.name or "Depot").rsplit(".", 1)[0]
        try:
            files, err, zip_name, zip_bytes = split_pdf_to_vendors_in_memory(pdf, exact_map, index, base, extractor=extract_skus_depot, debug=dbg)
        except Exception as e:
            st.error(f"Depot split failed: {e}")
        else:
            st.session_state["batches"]["Depot"].append({"batch": base, "zip_name": zip_name, "zip_bytes": zip_bytes, "files": files, "err": err})
            st.success("Depot split complete.")
    if c2.button("🆕 New Depot Session", key="new_depot"):
        st.session_state["batches"]["Depot"].clear()
        st.success("Depot session cleared.")

    if st.session_state["batches"]["Depot"]:
        st.write("### 📥 Depot Downloads & Stats")
        sel = st.selectbox("Select batch", [b["batch"] for b in st.session_state["batches"]["Depot"]], key="sel_depot")
        b = next(x for x in st.session_state["batches"]["Depot"] if x["batch"] == sel)
        st.sidebar.header("📦 Depot Batch ZIP")
        st.sidebar.download_button(f"⬇️ {b['zip_name']}", b["zip_bytes"], file_name=b["zip_name"], key=f"zip_depot_{sel}")
        st.write("#### Vendor Files")
        for v, (fname, data) in sorted(b["files"].items()):
            st.download_button(fname, data, file_name=fname, key=f"depot_{sel}_{v}")
        if b["err"]:
            st.download_button(b["err"][0], b["err"][1], file_name=b["err"][0], key=f"depot_{sel}_err")
        st.write("#### Pages Summary")
        df, total = stats_from_files(b["files"])
        st.table(df)
        st.write(f"**Total pages:** {total}")

# Lowe's tab
with tabs[1]:
    st.subheader("Lowe's")
    sku = st.file_uploader("Upload Lowe's SKU→Vendor Excel", type=["xlsx"], key="sku_lowes")
    pdf = st.file_uploader("Upload Lowe's PDF", type=["pdf"], key="pdf_lowes")
    dbg = st.checkbox("Show per-page SKUs (debug)", key="dbg_lowes")
    c1, c2 = st.columns(2)
    if c1.button("🚀 Split Lowe's", disabled=not (sku and pdf), key="btn_lowes"):
        exact_map, index = load_mapping(sku)
        base = (pdf.name or "Lowes").rsplit(".", 1)[0]
        try:
            files, err, zip_name, zip_bytes = split_pdf_to_vendors_in_memory(pdf, exact_map, index, base, extractor=extract_skus_lowes, debug=dbg)
        except Exception as e:
            st.error(f"Lowe's split failed: {e}")
        else:
            st.session_state["batches"]["Lowe's"].append({"batch": base, "zip_name": zip_name, "zip_bytes": zip_bytes, "files": files, "err": err})
            st.success("Lowe's split complete.")
    if c2.button("🆕 New Lowe's Session", key="new_lowes"):
        st.session_state["batches"]["Lowe's"].clear()
        st.success("Lowe's session cleared.")

    if st.session_state["batches"]["Lowe's"]:
        st.write("### 📥 Lowe's Downloads & Stats")
        sel = st.selectbox("Select batch", [b["batch"] for b in st.session_state["batches"]["Lowe's"]], key="sel_lowes")
        b = next(x for x in st.session_state["batches"]["Lowe's"] if x["batch"] == sel)
        st.sidebar.header("📦 Lowe's Batch ZIP")
        st.sidebar.download_button(f"⬇️ {b['zip_name']}", b["zip_bytes"], file_name=b["zip_name"], key=f"zip_lowes_{sel}")
        st.write("#### Vendor Files")
        for v, (fname, data) in sorted(b["files"].items()):
            st.download_button(fname, data, file_name=fname, key=f"lowes_{sel}_{v}")
        if b["err"]:
            st.download_button(b["err"][0], b["err"][1], file_name=b["err"][0], key=f"lowes_{sel}_err")
        st.write("#### Pages Summary")
        df, total = stats_from_files(b["files"])
        st.table(df)
        st.write(f"**Total pages:** {total}")

# TSC tab
with tabs[2]:
    st.subheader("Tractor Supply")
    sku = st.file_uploader("Upload TSC SKU→Vendor Excel", type=["xlsx"], key="sku_tsc")
    pdf = st.file_uploader("Upload TSC PDF", type=["pdf"], key="pdf_tsc")
    dbg = st.checkbox("Show per-page SKUs (debug)", key="dbg_tsc")
    c1, c2 = st.columns(2)
    if c1.button("🚀 Split TSC", disabled=not (sku and pdf), key="btn_tsc"):
        exact_map, index = load_mapping(sku)
        base = (pdf.name or "TSC").rsplit(".", 1)[0]
        try:
            files, err, zip_name, zip_bytes = split_pdf_to_vendors_in_memory(pdf, exact_map, index, base, extractor=extract_skus_tsc, debug=dbg)
        except Exception as e:
            st.error(f"TSC split failed: {e}")
        else:
            st.session_state["batches"]["TSC"].append({"batch": base, "zip_name": zip_name, "zip_bytes": zip_bytes, "files": files, "err": err})
            st.success("TSC split complete.")
    if c2.button("🆕 New TSC Session", key="new_tsc"):
        st.session_state["batches"]["TSC"].clear()
        st.success("TSC session cleared.")

    if st.session_state["batches"]["TSC"]:
        st.write("### 📥 TSC Downloads & Stats")
        sel = st.selectbox("Select batch", [b["batch"] for b in st.session_state["batches"]["TSC"]], key="sel_tsc")
        b = next(x for x in st.session_state["batches"]["TSC"] if x["batch"] == sel)
        st.sidebar.header("📦 TSC Batch ZIP")
        st.sidebar.download_button(f"⬇️ {b['zip_name']}", b["zip_bytes"], file_name=b["zip_name"], key=f"zip_tsc_{sel}")
        st.write("#### Vendor Files")
        for v, (fname, data) in sorted(b["files"].items()):
            st.download_button(fname, data, file_name=fname, key=f"tsc_{sel}_{v}")
        if b["err"]:
            st.download_button(b["err"][0], b["err"][1], file_name=b["err"][0], key=f"tsc_{sel}_err")
        st.write("#### Pages Summary")
        df, total = stats_from_files(b["files"])
        st.table(df)
        st.write(f"**Total pages:** {total}")
