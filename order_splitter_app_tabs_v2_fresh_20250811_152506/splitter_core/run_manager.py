import os, io, zipfile, datetime
import pandas as pd
from typing import List, Dict, Any, Tuple
from .sku_map import SkuVendorMap
from .pdf_utils import iter_pdf_pages, build_vendor_pdf
from .extractors import extract_candidates

def process_run(store: str, pdf_files: List[Tuple[str, bytes]], sku_map_bytes: bytes, run_root: str) -> Dict[str, Any]:
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = os.path.join(run_root, ts)
    os.makedirs(run_dir, exist_ok=True)
    out_store_dir = os.path.join(run_dir, store.replace(" ", "_"))
    os.makedirs(out_store_dir, exist_ok=True)

    sku_df = pd.read_excel(io.BytesIO(sku_map_bytes), engine="openpyxl")
    mapping = SkuVendorMap(sku_df)
    known_keys = mapping.known_keys()

    page_records = []
    for src_idx, (name, data) in enumerate(pdf_files):
        for page_idx, text, words in iter_pdf_pages(data):
            cands_all = list(extract_candidates(text, store, words=words))
            filtered = [c for c in cands_all if c in known_keys] or cands_all
            matched_vendors = []
            matched_skus = []
            for tok in filtered:
                v = mapping.find_vendor(tok)
                if v:
                    matched_vendors.append(v)
                    matched_skus.append(tok)
            page_records.append({
                "source_idx": src_idx,
                "source_name": name,
                "page_idx": page_idx,
                "candidates": filtered,
                "matched_skus": matched_skus,
                "matched_vendors": matched_vendors,
            })

    assignments = []
    unmatched = []
    for rec in page_records:
        if rec["matched_vendors"]:
            counts = {}
            for v in rec["matched_vendors"]:
                counts[v] = counts.get(v, 0) + 1
            vendor = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]
            decided_by = "majority-sku"
            notes = ""
        else:
            vendor = "Unassigned"
            decided_by = "no-match"
            notes = f"No vendor match; candidates seen: {', '.join(rec['candidates'][:10])}"
            unmatched.append({
                "store": store,
                "source_name": rec["source_name"],
                "page_idx": rec["page_idx"],
                "candidates": rec["candidates"],
                "reason": "No candidates matched SKU map",
                "notes": notes,
            })
        assignments.append({
            "store": store,
            "vendor": vendor,
            "source_idx": rec["source_idx"],
            "source_name": rec["source_name"],
            "page_idx": rec["page_idx"],
            "matched_skus": rec["matched_skus"],
            "decided_by": decided_by,
            "notes": notes,
        })

    refs: Dict[str, list[tuple[int, int]]] = {}
    for a in assignments:
        refs.setdefault(a["vendor"], []).append((a["source_idx"], a["page_idx"]))

    pdf_bytes_list = [data for _, data in pdf_files]
    vendor_files = []
    for vendor, page_refs in refs.items():
        if vendor == "Unassigned":
            continue
        pdf_bytes = build_vendor_pdf(pdf_bytes_list, page_refs)
        try:
            mdy = datetime.datetime.now().strftime("%-m-%-d-%Y")
        except Exception:
            mdy = datetime.datetime.now().strftime("%m-%d-%Y").lstrip("0").replace("-0", "-")
        base_name = f"{store} {mdy} order page bundle {vendor}.pdf"
        out_path = os.path.join(out_store_dir, base_name)
        with open(out_path, "wb") as f:
            f.write(pdf_bytes)
        vendor_files.append(out_path)

    from .reporting import build_summary_df, build_unmatched_df
    summary_df = build_summary_df(assignments)
    unmatched_df = build_unmatched_df(unmatched)
    summary_csv = os.path.join(out_store_dir, "summary.csv")
    unmatched_csv = os.path.join(out_store_dir, "unmatched_or_errors.csv")
    summary_df.to_csv(summary_csv, index=False)
    unmatched_df.to_csv(unmatched_csv, index=False)

    zip_name = f"{store}_{datetime.datetime.now().strftime('%Y-%m-%d')}.zip"
    zip_path = os.path.join(run_dir, zip_name)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(out_store_dir):
            for fn in files:
                fp = os.path.join(root, fn)
                arc = os.path.relpath(fp, run_dir)
                z.write(fp, arcname=arc)

    return {
        "run_dir": run_dir,
        "store_dir": out_store_dir,
        "zip_path": zip_path,
        "summary_csv": summary_csv,
        "unmatched_csv": unmatched_csv,
        "vendor_files": vendor_files,
        "assignments_count": len(assignments),
        "unmatched_count": len(unmatched),
        "timestamp": ts,
    }
