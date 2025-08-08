
import streamlit as st
import pandas as pd
import re
from io import BytesIO
from PyPDF2 import PdfReader, PdfWriter
import pdfplumber
import zipfile

st.set_page_config(page_title="Home Depot Order Splitter (Fixed)", layout="wide")
st.title("🏠 Home Depot Order Splitter — Column-aware Model # extractor")

# -------------------- Normalization & helpers --------------------
ALNUM = re.compile(r'[^A-Za-z0-9]+')
HAS_ALPHA = re.compile(r'[A-Za-z]')
TOKEN_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9\-]{1,}')

def norm_sku(s: str) -> str:
    return ALNUM.sub('', str(s)).upper()

def split_model_token(tok: str) -> str:
    t = tok.strip()
    # If there's a very long trailing digit run (shipping ids etc.), trim it.
    m = re.match(r'^([A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*?)(?=\d{5,}\b)', t)
    if m:
        return m.group(1)
    # Otherwise keep alnum/hyphen chunk that has letters
    parts = re.findall(r'[A-Za-z0-9\-]+', t)
    for p in parts:
        if HAS_ALPHA.search(p):
            return p
    return t

def group_lines_by_y(words, tol=3.5):
    buckets = []
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        placed = False
        for b in buckets:
            if abs(b["y"] - w["top"]) <= tol:
                b["words"].append(w)
                b["y"] = (b["y"] + w["top"]) / 2.0
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
        if "model" in texts: score += 1
        if "number" in texts: score += 1
        if "internet" in texts: score += 1
        if "item" in texts and "description" in texts: score += 1
        if "qty" in texts: score += 1
        if "shipped" in texts: score += 1
        if "model number" in joined: score += 1
        if "qty shipped" in joined: score += 1
        if score > best_score or (score == best_score and line["y"] < (best["y"] if best else 1e9)):
            best = line
            best_score = score
    return best

def cluster_span(line_words, target_terms):
    items = []
    for w in sorted(line_words, key=lambda x: x["x0"]):
        t = w["text"].strip().lower()
        items.append((t, w))
    for i, (t, w) in enumerate(items):
        if t == target_terms[0]:
            ok = True
            j = i
            for k in range(1, len(target_terms)):
                found = False
                for jj in range(j+1, min(j+4, len(items))):
                    if items[jj][0] == target_terms[k]:
                        found = True
                        j = jj
                        break
                if not found:
                    ok = False
                    break
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
    if not words_on_line:
        return None
    words_on_line = sorted(words_on_line, key=lambda w: w["x0"])
    for w in words_on_line:
        t = w["text"].strip().rstrip(":,;")
        if len(t) < 2:
            continue
        if HAS_ALPHA.search(t) and re.fullmatch(r'[A-Za-z0-9\-]{2,}', t):
            return split_model_token(t)
    return None

# -------------------- Mapping loader & matching --------------------
def load_mapping(xlsx_file):
    df = pd.read_excel(xlsx_file)
    df.columns = [str(c).strip() for c in df.columns]
    sku_col = None
    vendor_col = None
    for c in df.columns:
        cl = c.lower()
        if sku_col is None and ('sku' in cl or 'model' in cl or 'model number' in cl):
            sku_col = c
        if vendor_col is None and 'vendor' in cl:
            vendor_col = c
    if sku_col is None or vendor_col is None:
        st.error("❌ Mapping sheet must include a 'SKU/Model' column and a 'Vendor' column.")
        return {}, {}
    df = df[[sku_col, vendor_col]].dropna()
    exact = {}
    keys_for_vendor = {}
    for raw, vend in zip(df[sku_col].astype(str), df[vendor_col].astype(str)):
        parts = re.split(r'[,\s/]+', raw.strip())
        for p in parts:
            p = p.strip()
            if not p:
                continue
            k = norm_sku(p)
            if not k: 
                continue
            exact[k] = vend.strip()
            keys_for_vendor.setdefault(vend.strip(), set()).add(k)
    all_keys = set(exact.keys())
    return exact, {"all_keys": all_keys, "vendor_keys": keys_for_vendor}

def match_vendor(token, exact_map, index):
    key = norm_sku(token)
    if key in exact_map:
        return exact_map[key]
    pref = [k for k in index["all_keys"] if key.startswith(k) or k.startswith(key)]
    pref = list(set(pref))
    if len(pref) == 1:
        return exact_map[pref[0]]
    return None

# -------------------- Depot extractor --------------------
def extract_skus_depot(ppage, debug=False):
    words = ppage.extract_words(use_text_flow=True)
    header = find_header_line(words)
    skus = []
    if header:
        hwords = header["words"]
        sp_model = cluster_span(hwords, ["model","number"]) or cluster_span(hwords, ["model"])
        sp_internet = cluster_span(hwords, ["internet","number"]) or cluster_span(hwords, ["internet"])
        sp_item = cluster_span(hwords, ["item","description"]) or cluster_span(hwords, ["item"])
        sp_qty = cluster_span(hwords, ["qty","shipped"]) or cluster_span(hwords, ["qty"])
        x0 = (sp_model[0] if sp_model else 40) - 2
        candidates = [sp for sp in [sp_internet, sp_item, sp_qty] if sp]
        if candidates:
            x1 = min(sp[0] for sp in candidates) - 2
        else:
            x1 = ppage.width * 0.55
        y0 = min(w["bottom"] for w in hwords) + 2
        y1 = ppage.height - 36
        region = words_in_region(ppage, x0, x1, y0, y1)
        lines = group_lines_by_y(region, tol=3.5)
        for line in lines:
            sku = pick_sku_from_line(line["words"])
            if sku:
                skus.append(sku)
        if debug:
            st.write(f"Header y≈{header['y']:.1f} | x0-x1=({x0:.1f}-{x1:.1f}) | lines={len(lines)}")
    if not skus:
        txt = ppage.extract_text() or ""
        for m in re.finditer(r'Model\s*#?:?\s*([A-Za-z0-9\-]{2,})', txt, flags=re.I):
            s = split_model_token(m.group(1))
            if HAS_ALPHA.search(s):
                skus.append(s)
    seen = set(); ordered = []
    for s in skus:
        if s not in seen:
            ordered.append(s); seen.add(s)
    return ordered

# -------------------- Splitter --------------------
def split_pdf_to_vendors_in_memory(pdf_uploaded, exact_map, index, base_name, debug=False):
    if hasattr(pdf_uploaded, "getvalue"):
        pdf_bytes = pdf_uploaded.getvalue()
    elif isinstance(pdf_uploaded, (bytes, bytearray)):
        pdf_bytes = bytes(pdf_uploaded)
    else:
        raise ValueError("Unsupported PDF input type.")
    if not exact_map:
        raise ValueError("Empty SKU→Vendor mapping. Please upload the correct Home Depot sheet.")

    reader = PdfReader(BytesIO(pdf_bytes))
    pl = pdfplumber.open(BytesIO(pdf_bytes))

    writers = {}
    error_writer = PdfWriter()

    try:
        for idx, (ppage, pypage) in enumerate(zip(pl.pages, reader.pages), start=1):
            page_skus = extract_skus_depot(ppage, debug=debug)
            if debug:
                st.write(f"Page {idx} SKUs detected: {page_skus}")
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
if "depot_batches" not in st.session_state:
    st.session_state["depot_batches"] = []

st.subheader("Home Depot")
sku = st.file_uploader("Upload Home Depot SKU→Vendor Excel", type=["xlsx"], key="sku_depot")
pdf = st.file_uploader("Upload Home Depot PDF", type=["pdf"], key="pdf_depot")
dbg = st.checkbox("Show per-page SKUs (debug)", key="dbg_depot")

col1, col2 = st.columns(2)
if col1.button("🚀 Split Home Depot", disabled=not (sku and pdf)):
    exact_map, index = load_mapping(sku)
    base = (pdf.name or "Depot").rsplit(".", 1)[0]
    try:
        files, err, zip_name, zip_bytes = split_pdf_to_vendors_in_memory(pdf, exact_map, index, base, debug=dbg)
    except Exception as e:
        st.error(f"Depot split failed: {e}")
    else:
        st.session_state["depot_batches"].append({"batch": base, "zip_name": zip_name, "zip_bytes": zip_bytes, "files": files, "err": err})
        st.success("Depot split complete.")
if col2.button("🆕 New Depot Session"):
    st.session_state["depot_batches"].clear()
    st.success("Depot session cleared.")

if st.session_state["depot_batches"]:
    st.write("### 📥 Downloads & Stats")
    sel = st.selectbox("Select batch", [b["batch"] for b in st.session_state["depot_batches"]], key="sel_depot")
    b = next(x for x in st.session_state["depot_batches"] if x["batch"] == sel)
    st.sidebar.header("📦 Batch ZIP")
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
