import fitz  # PyMuPDF
from typing import Iterator, Dict, Any, List

def iter_pdf_pages(pdf_bytes: bytes) -> Iterator[tuple[int, str, list[dict]]]:
    """
    Yield (page_index, page_text, words) for each page in the PDF bytes.
    words: list of dicts {x0,y0,x1,y1,text}
    """
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            words_raw = page.get_text("words")
            words: List[Dict[str, Any]] = []
            for w in words_raw:
                words.append({
                    "x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4],
                    "block": w[5], "line": w[6], "word_no": w[7]
                })
            yield i, text, words

def build_vendor_pdf(source_pdf_bytes_list: list[bytes], page_refs: list[tuple[int, int]]) -> bytes:
    out = fitz.open()
    src_docs = [fitz.open(stream=b, filetype="pdf") for b in source_pdf_bytes_list]
    for src_idx, page_idx in page_refs:
        src = src_docs[src_idx]
        out.insert_pdf(src, from_page=page_idx, to_page=page_idx)
    result = out.tobytes()
    out.close()
    for d in src_docs:
        d.close()
    return result
