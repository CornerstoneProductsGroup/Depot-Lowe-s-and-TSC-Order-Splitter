import pandas as pd
from typing import List, Dict, Any

def build_summary_df(assignments: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for a in assignments:
        rows.append({
            "Store": a.get("store"),
            "Vendor": a.get("vendor"),
            "Source PDF": a.get("source_name"),
            "Page Index (0-based)": a.get("page_idx"),
            "Matched SKUs": ", ".join(a.get("matched_skus", [])),
            "Decision": a.get("decided_by"),
            "Notes": a.get("notes", ""),
        })
    return pd.DataFrame(rows)

def build_unmatched_df(unmatched: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for u in unmatched:
        rows.append({
            "Store": u.get("store"),
            "Source PDF": u.get("source_name"),
            "Page Index (0-based)": u.get("page_idx"),
            "Found Candidates": ", ".join(u.get("candidates", [])),
            "Reason": u.get("reason", ""),
            "Notes": u.get("notes", ""),
        })
    return pd.DataFrame(rows)
