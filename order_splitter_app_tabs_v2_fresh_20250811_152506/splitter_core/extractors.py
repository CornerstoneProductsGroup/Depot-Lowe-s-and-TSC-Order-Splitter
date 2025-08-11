import re
from typing import Set, List, Dict, Any, Tuple
from .config import STORE_CONFIGS, normalize_sku

ANCHOR_KEYWORDS = [
    "MODEL", "MODEL#", "MODELNO", "MODELNUMBER", "MODEL#.", "MODELNO.", "MODELNUMBER."
]

def _words_to_upper_seq(words: List[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    seq = []
    for w in words:
        txt = str(w.get("text","")).strip()
        if not txt:
            continue
        seq.append((txt.upper(), w))
    return seq

def _merge_bbox(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    return {"x0": min(a["x0"], b["x0"]), "y0": min(a["y0"], b["y0"]), "x1": max(a["x1"], b["x1"]), "y1": max(a["y1"], b["y1"])}

def _find_model_anchors(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seq = _words_to_upper_seq(words)
    anchors = []
    i = 0
    while i < len(seq):
        token, w = seq[i]
        if token == "MODEL":
            bbox = {"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"]}
            j = i + 1
            combined = bbox.copy()
            took = False
            if j < len(seq):
                t1, w1 = seq[j]
                if t1 in {"#", "NO", "NO.", "NUMBER", "NUMBER."} or t1.startswith("NO") or t1.startswith("NUMBER"):
                    combined = _merge_bbox(combined, {"x0": w1["x0"], "y0": w1["y0"], "x1": w1["x1"], "y1": w1["y1"]})
                    took = True
                    j2 = j + 1
                    if j2 < len(seq):
                        t2, w2 = seq[j2]
                        if t2 in {"#", "NO.", "NO", "NUMBER", "NUMBER."}:
                            combined = _merge_bbox(combined, {"x0": w2["x0"], "y0": w2["y0"], "x1": w2["x1"], "y1": w2["y1"]})
            anchors.append(combined if took else bbox)
            i += 1
            continue
        if token in ANCHOR_KEYWORDS:
            anchors.append({"x0": w["x0"], "y0": w["y0"], "x1": w["x1"], "y1": w["y1"]})
            i += 1
            continue
        i += 1
    return anchors

def _tokens_below_anchor(words: List[Dict[str, Any]], anchor: Dict[str, Any], max_dy: float = 180.0, x_slack: float = 220.0) -> List[str]:
    x0 = anchor["x0"] - 20
    x1 = anchor["x1"] + x_slack
    y_top = anchor["y1"]
    y_bot = anchor["y1"] + max_dy
    candidates = []
    for w in words:
        wx0, wy0, wx1, wy1 = w["x0"], w["y0"], w["x1"], w["y1"]
        if wy0 >= y_top and wy0 <= y_bot and wx0 >= x0 and wx1 <= x1:
            token = normalize_sku(w.get("text",""))
            if token and len(token) >= 4:
                candidates.append(token)
    return candidates

def _look_for_sku_below_model(words: List[Dict[str, Any]]) -> Set[str]:
    anchors = _find_model_anchors(words)
    results: Set[str] = set()
    for anchor in anchors:
        toks = _tokens_below_anchor(words, anchor, max_dy=180.0, x_slack=220.0)
        for t in toks:
            results.add(t)
    return results

def extract_candidates(page_text: str, store: str, words: List[Dict[str, Any]] | None = None) -> Set[str]:
    cfg = STORE_CONFIGS[store]
    candidates = set()

    for pat in cfg.candidate_patterns:
        for m in re.finditer(pat, page_text, flags=re.IGNORECASE):
            token = m.group(1)
            if token:
                candidates.add(normalize_sku(token))

    if words:
        for t in _look_for_sku_below_model(words):
            candidates.add(normalize_sku(t))

    for pat in cfg.loose_token_patterns:
        for m in re.finditer(pat, page_text, flags=re.IGNORECASE):
            token = m.group(1)
            if token:
                candidates.add(normalize_sku(token))

    candidates = {c for c in candidates if c}
    return candidates
