import pandas as pd
from typing import Dict, Any, Set
from .config import normalize_sku

SKU_COLUMNS = [
    "SKU", "Model", "Model #", "Model Number", "Item #", "Internet #", "UPC"
]
VENDOR_COLUMNS = ["Vendor", "Vendor Name"]

class SkuVendorMap:
    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]

        self.vendor_col = None
        for c in VENDOR_COLUMNS:
            if c in df.columns:
                self.vendor_col = c
                break
        if not self.vendor_col:
            raise ValueError(f"SKU sheet must include one of the vendor columns: {VENDOR_COLUMNS}")

        id_cols = [c for c in SKU_COLUMNS if c in df.columns]
        if not id_cols:
            raise ValueError(f"SKU sheet must include at least one SKU-like column: {SKU_COLUMNS}")

        self.lookup: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            vendor = str(row[self.vendor_col]).strip() if pd.notna(row[self.vendor_col]) else ""
            if not vendor:
                continue
            for col in id_cols:
                val = row.get(col, None)
                if pd.isna(val):
                    continue
                key = normalize_sku(val)
                if not key:
                    continue
                self.lookup.setdefault(key, {"Vendor": vendor, "row": row.to_dict()})
        self.df = df

    def find_vendor(self, candidate: str) -> str | None:
        key = normalize_sku(candidate)
        hit = self.lookup.get(key)
        return hit["Vendor"] if hit else None

    def known_keys(self) -> Set[str]:
        return set(self.lookup.keys())
