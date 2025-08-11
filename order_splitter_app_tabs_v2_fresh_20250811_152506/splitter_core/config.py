from dataclasses import dataclass, field
import re

def normalize_sku(raw: str) -> str:
    if not raw:
        return ""
    keep = []
    for ch in str(raw).strip():
        if ch.isalnum() or ch in "-":
            keep.append(ch.upper())
    return "".join(keep)

def label_capture(label: str) -> str:
    return rf"{re.escape(label)}\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-\_]+)"

@dataclass
class StoreConfig:
    name: str
    candidate_patterns: list[str] = field(default_factory=list)
    loose_token_patterns: list[str] = field(default_factory=lambda: [r"\b([A-Za-z0-9][A-Za-z0-9\-]{3,})\b"])

HOME_DEPOT = StoreConfig(
    name="Home Depot",
    candidate_patterns=[
        label_capture("Model"),
        label_capture("Model #"),
        label_capture("Model No"),
        label_capture("Model Number"),
        label_capture("MODEL"),
        label_capture("Internet #"),
        label_capture("SKU"),
        label_capture("SKU #"),
        label_capture("SKU#"),
        label_capture("Item #"),
    ]
)

LOWES = StoreConfig(
    name="Lowe's",
    candidate_patterns=[
        label_capture("Model"),
        label_capture("Model #"),
        label_capture("Model No"),
        label_capture("Model Number"),
        label_capture("SKU"),
        label_capture("SKU #"),
        label_capture("SKU#"),
        label_capture("Item #"),
        label_capture("MFR #"),
        label_capture("MFR No"),
    ]
)

TRACTOR_SUPPLY = StoreConfig(
    name="Tractor Supply",
    candidate_patterns=[
        label_capture("Model"),
        label_capture("Model #"),
        label_capture("Model No"),
        label_capture("Model Number"),
        label_capture("SKU"),
        label_capture("SKU #"),
        label_capture("SKU#"),
        label_capture("Item #"),
        label_capture("Vendor SKU"),
    ]
)

STORE_CONFIGS = {
    "Home Depot": HOME_DEPOT,
    "Lowe's": LOWES,
    "Tractor Supply": TRACTOR_SUPPLY
}
