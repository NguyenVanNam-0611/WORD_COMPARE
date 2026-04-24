from __future__ import annotations

import os
import base64
import logging
from typing import Optional, Tuple, List
import win32com.client
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

WD_FORMAT_PDF = 17  # wdFormatPDF

_pdf_cache: dict[str, str] = {}  # docx_path → pdf_path


# ─────────────────────────────────────────────────────────
# DOCX → PDF
# ─────────────────────────────────────────────────────────
def docx_to_pdf_cached(docx_path: str) -> Optional[str]:
    abs_path = os.path.abspath(docx_path)
    if abs_path in _pdf_cache:
        cached = _pdf_cache[abs_path]
        if os.path.exists(cached):
            return cached

    pdf_path = os.path.splitext(abs_path)[0] + "_snapshot.pdf"
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(abs_path, ReadOnly=True)
        doc.SaveAs(pdf_path, FileFormat=WD_FORMAT_PDF)
        doc.Close(False)
        doc = None
        _pdf_cache[abs_path] = pdf_path
        logger.info(f"[snapshot] PDF created: {pdf_path}")
        return pdf_path
    except Exception as e:
        logger.error(f"[snapshot] PDF convert failed: {e}")
        return None
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
# BUILD ANCHOR QUERIES — nhiều candidate, dài hơn
# ─────────────────────────────────────────────────────────
def _build_queries(anchor_text: str) -> List[str]:
    """
    Tạo danh sách query theo thứ tự ưu tiên:
    1. 80 ký tự đầu (chính xác nhất)
    2. 60 ký tự đầu
    3. 40 ký tự đầu
    4. 25 ký tự đầu (fallback cuối)
    Mỗi query đều được normalize whitespace.
    """
    text = " ".join(anchor_text.strip().split())
    lengths = [80, 60, 40, 25]
    seen = set()
    queries = []
    for length in lengths:
        q = text[:length].strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
    return queries


# ─────────────────────────────────────────────────────────
# FIND BBOX — multi-query, page_hint, không fallback nhầm
# ─────────────────────────────────────────────────────────
def _find_bbox_for_text(
    pdf_path: str,
    anchor_text: str,
    padding: int = 16,
    page_hint: Optional[int] = None,
) -> Optional[Tuple[int, fitz.Rect]]:
    """
    Tìm bbox của anchor_text trong PDF.

    - Thử nhiều độ dài query (80 → 25 ký tự), dừng khi tìm thấy.
    - Nếu có page_hint, ưu tiên search trang đó và các trang lân cận trước.
    - Trả về (page_idx, expanded_rect) hoặc None nếu không tìm thấy.
    - KHÔNG fallback trang 1 — caller tự quyết định xử lý None.
    """
    if not anchor_text or not anchor_text.strip():
        return None

    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    # Thứ tự trang cần search: ưu tiên page_hint nếu có
    if page_hint is not None:
        hint = max(0, min(page_hint, total_pages - 1))
        # Sắp xếp tất cả trang theo khoảng cách đến page_hint
        page_order = sorted(range(total_pages), key=lambda p: abs(p - hint))
    else:
        page_order = list(range(total_pages))

    queries = _build_queries(anchor_text)

    for query in queries:
        for page_idx in page_order:
            page = doc[page_idx]
            _ignore_case = getattr(fitz, "TEXT_IGNORECASE", 0)
            results = page.search_for(query, flags=_ignore_case)

            if not results:
                continue

            # Gộp tất cả rect tìm được thành 1 bbox bao phủ
            combined = results[0]
            for r in results[1:]:
                combined |= r

            expanded = fitz.Rect(
                max(0, combined.x0 - padding),
                max(0, combined.y0 - padding),
                min(page.rect.width, combined.x1 + padding),
                min(page.rect.height, combined.y1 + padding),
            )

            doc.close()
            logger.debug(
                f"[snapshot] matched query[:{len(query)}] "
                f"on page {page_idx}: {query[:40]!r}"
            )
            return page_idx, expanded

    doc.close()
    logger.warning(f"[snapshot] no match found for: {anchor_text[:60]!r}")
    return None


# ─────────────────────────────────────────────────────────
# RENDER FULL PAGE — dùng khi cần fallback có kiểm soát
# ─────────────────────────────────────────────────────────
def _render_full_page(
    pdf_path: str,
    page_idx: int = 0,
    dpi: int = 150,
) -> Optional[str]:
    """Render toàn bộ 1 trang PDF thành data URI."""
    try:
        doc = fitz.open(pdf_path)
        page_idx = max(0, min(page_idx, len(doc) - 1))
        page = doc[page_idx]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        png_bytes = pix.tobytes("png")
        doc.close()
        return "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    except Exception as e:
        logger.error(f"[snapshot] render_full_page failed page={page_idx}: {e}")
        return None


# ─────────────────────────────────────────────────────────
# SNAPSHOT — entry point chính
# ─────────────────────────────────────────────────────────
def render_block_snapshot(
    pdf_path: str,
    anchor_text: str,
    dpi: int = 150,
    padding: int = 20,
    page_hint: Optional[int] = None,
    fallback_full_page: bool = False,
) -> Optional[str]:
    """
    Render snapshot vùng chứa anchor_text trong PDF.

    Args:
        pdf_path:          Đường dẫn file PDF.
        anchor_text:       Text dùng để locate block trong PDF.
        dpi:               Độ phân giải render (mặc định 150).
        padding:           Số pixel mở rộng xung quanh bbox (mặc định 20).
        page_hint:         Gợi ý trang (0-based) để ưu tiên search trước.
        fallback_full_page: Nếu True và không tìm thấy anchor, render toàn
                            trang page_hint (hoặc trang 0). Mặc định False
                            để tránh trả về snapshot sai vị trí.

    Returns:
        data URI (PNG) hoặc None nếu không tìm thấy và fallback tắt.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        logger.error(f"[snapshot] PDF not found: {pdf_path!r}")
        return None

    result = _find_bbox_for_text(
        pdf_path, anchor_text, padding=padding, page_hint=page_hint
    )

    if result is not None:
        page_idx, bbox = result
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_idx]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, clip=bbox, colorspace=fitz.csRGB)
            png_bytes = pix.tobytes("png")
            doc.close()
            return "data:image/png;base64," + base64.b64encode(png_bytes).decode()
        except Exception as e:
            logger.error(f"[snapshot] render clip failed: {e}")
            return None

    # Không tìm thấy anchor
    if fallback_full_page:
        fallback_page = page_hint if page_hint is not None else 0
        logger.warning(
            f"[snapshot] fallback full page={fallback_page} "
            f"for anchor: {anchor_text[:50]!r}"
        )
        return _render_full_page(pdf_path, page_idx=fallback_page, dpi=dpi)

    # Không fallback → trả None, để caller dùng data_uri gốc từ extractor
    return None

def render_row_snapshot(
    pdf_path: str,
    row_anchor_text: str,
    dpi: int = 150,
    padding: int = 8,
    page_hint: Optional[int] = None,
) -> Optional[str]:
    """
    Render snapshot của 1 dòng bảng cụ thể dựa trên text anchor của dòng đó.
    Dùng cho trường hợp table_row_modified (chỉ thay đổi nội dung cell).
    """
    return render_block_snapshot(
        pdf_path=pdf_path,
        anchor_text=row_anchor_text,
        dpi=dpi,
        padding=padding,
        page_hint=page_hint,
        fallback_full_page=False,
    )
# ─────────────────────────────────────────────────────────
def clear_pdf_cache():
    _pdf_cache.clear()