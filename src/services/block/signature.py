from __future__ import annotations

import hashlib
from src.services.models.docnode import DocNode


def _norm_text(s: str) -> str:
    return " ".join((s or "").replace("\u00a0", " ").split()).strip()


def _short_hash(value: str, length: int = 12) -> str:
    if not value:
        return "empty"
    return hashlib.md5(value.encode("utf-8")).hexdigest()[:length]


def signature(node: DocNode) -> str:
    t = node.type

    if t == "heading":
        txt = _norm_text(node.content.get("text", ""))
        level = int(node.content.get("level", 0) or 0)
        return f"H{level}:{_short_hash(txt, 16)}"

    if t == "paragraph":
        txt = _norm_text(node.content.get("text", ""))
        style = node.content.get("style", "") or ""
        align = node.content.get("alignment", "")
        run_count = len(node.content.get("runs", []) or [])
        return (
            f"P:"
            f"{_short_hash(txt, 16)}:"
            f"{_short_hash(style, 6)}:"
            f"{align}:"
            f"{run_count}"
        )

    if t == "table":
        rows = int(node.content.get("rows", 0) or 0)
        cols = int(node.content.get("cols", 0) or 0)
        txt = _norm_text(node.content.get("text", ""))
        return (
            f"T:"
            f"{rows}x{cols}:"
            f"{_short_hash(txt[:500], 16)}"
        )

    if t == "row":
        row_text = _norm_text(node.content.get("text", ""))
        row_index = int(node.content.get("row_index", -1))
        return (
            f"R:"
            f"{row_index}:"
            f"{_short_hash(row_text[:300], 16)}"
        )

    if t == "cell":
        cell_text = _norm_text(node.content.get("text", ""))
        row_index = int(node.content.get("row_index", -1))
        col_index = int(node.content.get("col_index", -1))
        row_span = int(node.content.get("row_span", 1) or 1)
        col_span = int(node.content.get("col_span", 1) or 1)
        return (
            f"C:"
            f"{row_index}:"
            f"{col_index}:"
            f"{row_span}x{col_span}:"
            f"{_short_hash(cell_text[:200], 12)}"
        )

    if t == "image":
        sha = node.content.get("sha256", "") or ""
        width = int(node.content.get("width_emu", 0) or 0)
        height = int(node.content.get("height_emu", 0) or 0)
        return (
            f"I:"
            f"{sha[:16] if sha else 'nohash'}:"
            f"{width}x{height}"
        )

    if t == "shape":
        # Chỉ hash text của paragraph — bỏ qua image/table/format
        texts = []
        for child in (node.children or []):
            if child.type == "paragraph":
                child_text = _norm_text(child.content.get("text", ""))
                if child_text:
                    texts.append(child_text)

        combined = " ".join(texts)
        shape_type = node.content.get("shape_type", "") or ""

        if not combined:
            # Không có text → dùng uid để signature là unique
            # (tránh tất cả shape rỗng match nhau trong SequenceMatcher)
            uid = getattr(node, "uid", "") or ""
            return f"S:{shape_type}:empty:{_short_hash(uid, 8)}"

        return f"S:{shape_type}:{_short_hash(combined[:300], 16)}"

    text = _norm_text(node.content.get("text", ""))
    return f"U:{t}:{_short_hash(text[:100], 12)}"