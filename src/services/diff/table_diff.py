from __future__ import annotations

import difflib
from typing import List, Dict, Any, Optional

from src.services.models.docnode import DocNode
from src.services.diff.paragraph_diff import diff_words
from src.services.diff.image_diff import images_equal, build_image_change


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize_node(node: Optional[DocNode]) -> Any:
    if node is None:
        return None
    return {
        "uid": getattr(node, "uid", None),
        "type": node.type,
        "content": node.content,
        "children": [_serialize_node(c) for c in (node.children or [])],
    }


def _serialize_cell(cell: Optional[DocNode]) -> Optional[Dict[str, Any]]:
    if cell is None:
        return None
    content = cell.content or {}
    return {
        "uid": getattr(cell, "uid", None),
        "type": "cell",
        "text": (content.get("text") or "").strip(),
        "row_index": content.get("row_index"),
        "col_index": content.get("col_index"),
        "row_span": content.get("row_span", 1),
        "col_span": content.get("col_span", 1),
        "is_merged": content.get("is_merged", False),
        "children": [_serialize_node(c) for c in (cell.children or [])],
    }


def _serialize_row(row: Optional[DocNode]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    cells = [c for c in (row.children or []) if c.type == "cell"]
    return {
        "uid": getattr(row, "uid", None),
        "type": "row",
        "row_index": (row.content or {}).get("row_index"),
        "text": (row.content or {}).get("text", ""),
        "cells": [_serialize_cell(c) for c in cells],
    }


def _serialize_full_table(tbl: DocNode) -> Dict[str, Any]:
    """Serialize toàn bộ bảng với tất cả rows và cells."""
    rows = _get_rows(tbl)
    return {
        "uid": getattr(tbl, "uid", None),
        "type": "table",
        "content": tbl.content or {},
        "rows": [_serialize_row(r) for r in rows],
        "all_cells": [
            [_serialize_cell(c) for c in _get_cells(r)]
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _cell_text(cell: Optional[DocNode]) -> str:
    if not cell:
        return ""
    return (cell.content.get("text") or "").strip()


def _row_text(row: Optional[DocNode]) -> str:
    if not row:
        return ""
    return (row.content.get("text") or "").strip()


def _get_rows(tbl: DocNode) -> List[DocNode]:
    return [c for c in (tbl.children or []) if c.type == "row"]


def _get_cells(row: DocNode) -> List[DocNode]:
    return [c for c in (row.children or []) if c.type == "cell"]


def _real_col_index(cell: Optional[DocNode], fallback: int) -> int:
    if cell is None:
        return fallback
    return int((cell.content or {}).get("col_index") or fallback)


# ---------------------------------------------------------------------------
# Structure change detection
# ---------------------------------------------------------------------------

def detect_structure_change(a_tbl: DocNode, b_tbl: DocNode) -> bool:
    a_cols = int((a_tbl.content or {}).get("cols", 0) or 0)
    b_cols = int((b_tbl.content or {}).get("cols", 0) or 0)
    if a_cols > 0 and b_cols > 0 and a_cols != b_cols:
        return True

    a_rows = _get_rows(a_tbl)
    b_rows = _get_rows(b_tbl)
    if a_rows and b_rows:
        if len(_get_cells(a_rows[0])) != len(_get_cells(b_rows[0])):
            return True

    return False


def get_header_row(tbl: DocNode) -> Optional[Dict[str, Any]]:
    rows = _get_rows(tbl)
    if not rows:
        return None
    return _serialize_row(rows[0])


# ---------------------------------------------------------------------------
# Paragraph diff inside cell
# ---------------------------------------------------------------------------

def _diff_paragraph(a: DocNode, b: DocNode) -> Optional[Dict[str, Any]]:
    old_text = (a.content.get("text") or "").strip()
    new_text = (b.content.get("text") or "").strip()

    text_changed = old_text != new_text
    a_imgs = [c for c in (a.children or []) if c.type == "image"]
    b_imgs = [c for c in (b.children or []) if c.type == "image"]

    if not text_changed and not a_imgs and not b_imgs:
        return None

    changes: List[Dict[str, Any]] = []

    if text_changed:
        word_diff = diff_words(old_text, new_text)
        diff_result = word_diff if word_diff else {}
        changes.append({
            "type": "paragraph_modified",
            "old_full_text": diff_result.get("old_full_text", old_text),
            "new_full_text": diff_result.get("new_full_text", new_text),
            "spans": diff_result.get("spans", []),
            "original_content": a.content,
            "modified_content": b.content,
        })

    for i in range(max(len(a_imgs), len(b_imgs))):
        ai = a_imgs[i] if i < len(a_imgs) else None
        bi = b_imgs[i] if i < len(b_imgs) else None
        if images_equal(ai, bi):
            continue
        changes.append(build_image_change(ai, bi))

    if not changes:
        return None

    return {
        "type": "paragraph_container_modified",
        "original": _serialize_node(a),
        "modified": _serialize_node(b),
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# Cell diff
# ---------------------------------------------------------------------------

def _diff_cell(a: DocNode, b: DocNode) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []

    a_children = a.children or []
    b_children = b.children or []
    max_len = max(len(a_children), len(b_children))

    for i in range(max_len):
        ac = a_children[i] if i < len(a_children) else None
        bc = b_children[i] if i < len(b_children) else None

        if ac is None and bc is not None:
            changes.append({
                "type": f"{bc.type}_inserted",
                "change_kind": "insert",
                "original": None,
                "modified": _serialize_node(bc),
            })
            continue

        if bc is None and ac is not None:
            changes.append({
                "type": f"{ac.type}_deleted",
                "change_kind": "delete",
                "original": _serialize_node(ac),
                "modified": None,
            })
            continue

        if ac is None or bc is None:
            continue

        if ac.type == "paragraph" and bc.type == "paragraph":
            paragraph_change = _diff_paragraph(ac, bc)
            if paragraph_change:
                changes.append(paragraph_change)
            continue

        if ac.type == "image" and bc.type == "image":
            if not images_equal(ac, bc):
                changes.append(build_image_change(ac, bc))
            continue

        if ac.type == "table" and bc.type == "table":
            nested_changes = diff_table(ac, bc)
            if nested_changes:
                changes.append({
                    "type": "nested_table_modified",
                    "change_kind": "replace",
                    "original": _serialize_node(ac),
                    "modified": _serialize_node(bc),
                    "changes": nested_changes,
                })
            continue

        if ac.type != bc.type:
            changes.append({
                "type": "node_type_changed",
                "change_kind": "replace",
                "original": _serialize_node(ac),
                "modified": _serialize_node(bc),
            })

    return changes


# ---------------------------------------------------------------------------
# Row diff helper — dùng sau khi đã align đúng cặp (ar, br)
# ---------------------------------------------------------------------------

def _diff_row_pair(
    ar: DocNode,
    br: DocNode,
    row_index_b: int,
) -> Optional[Dict[str, Any]]:
    """
    So sánh 2 row đã được align đúng cặp.
    Trả về change dict nếu có thay đổi, None nếu bằng nhau.
    row_index_b: index của row trong bảng B (dùng làm row_index chuẩn).
    """
    a_cells = _get_cells(ar)
    b_cells = _get_cells(br)
    max_cells = max(len(a_cells), len(b_cells)) if (a_cells or b_cells) else 0

    row_changed = False
    row_cell_changes: List[Dict[str, Any]] = []

    for c in range(max_cells):
        ac = a_cells[c] if c < len(a_cells) else None
        bc = b_cells[c] if c < len(b_cells) else None

        real_col = _real_col_index(ac if ac is not None else bc, c)

        if ac is None and bc is not None:
            row_changed = True
            row_cell_changes.append({
                "type": "table_cell_added",
                "change_kind": "insert",
                "col_index": real_col,
                "left_cell": None,
                "right_cell": _serialize_cell(bc),
                "left_text": "",
                "right_text": _cell_text(bc),
                "changes": [],
            })
            continue

        if bc is None and ac is not None:
            row_changed = True
            row_cell_changes.append({
                "type": "table_cell_deleted",
                "change_kind": "delete",
                "col_index": real_col,
                "left_cell": _serialize_cell(ac),
                "right_cell": None,
                "left_text": _cell_text(ac),
                "right_text": "",
                "changes": [],
            })
            continue

        if ac is None or bc is None:
            continue

        cell_changes = _diff_cell(ac, bc)
        if cell_changes:
            row_changed = True
            row_cell_changes.append({
                "type": "table_cell_modified",
                "change_kind": "replace",
                "col_index": real_col,
                "left_cell": _serialize_cell(ac),
                "right_cell": _serialize_cell(bc),
                "left_text": _cell_text(ac),
                "right_text": _cell_text(bc),
                "changes": cell_changes,
            })

    if not row_changed and _row_text(ar) == _row_text(br):
        return None

    return {
        "type": "table_row_modified",
        "change_kind": "replace",
        "row_index": row_index_b,
        "col_count": max(len(a_cells), len(b_cells)),
        "left_row": _serialize_row(ar),
        "right_row": _serialize_row(br),
        "left_cells": [_serialize_cell(c) for c in a_cells],
        "right_cells": [_serialize_cell(c) for c in b_cells],
        "left_text": _row_text(ar),
        "right_text": _row_text(br),
        "cell_changes": row_cell_changes,
    }


# ---------------------------------------------------------------------------
# Main diff_table — dùng SequenceMatcher để align rows đúng vị trí
# ---------------------------------------------------------------------------

def diff_table(a_tbl: DocNode, b_tbl: DocNode) -> List[Dict[str, Any]]:
    """
    So sánh 2 bảng theo row.

    Dùng SequenceMatcher để align rows theo text signature TRƯỚC khi diff,
    tránh trường hợp row được chèn vào giữa khiến tất cả row phía sau
    bị so sánh nhầm cặp → sinh hàng loạt false positive.

    row_index trong mỗi change luôn là index trong bảng B (modified)
    để frontend có thể highlight đúng vị trí.
    """
    changes: List[Dict[str, Any]] = []

    a_rows = _get_rows(a_tbl)
    b_rows = _get_rows(b_tbl)

    if not a_rows and not b_rows:
        return changes

    # Signature để SequenceMatcher so sánh: dùng row text
    # Bỏ qua whitespace để tránh false diff do format
    a_sigs = [_row_text(r) for r in a_rows]
    b_sigs = [_row_text(r) for r in b_rows]

    sm = difflib.SequenceMatcher(a=a_sigs, b=b_sigs, autojunk=False)

    for tag, i1, i2, j1, j2 in sm.get_opcodes():

        # ── EQUAL: không thay đổi, bỏ qua ────────────────────────────────
        if tag == "equal":
            continue

        # ── INSERT: các row chỉ có ở bảng B ──────────────────────────────
        if tag == "insert":
            for j in range(j1, j2):
                br = b_rows[j]
                b_cells = _get_cells(br)
                changes.append({
                    "type": "table_row_added",
                    "change_kind": "insert",
                    "row_index": j,
                    "col_count": len(b_cells),
                    "left_row": None,
                    "right_row": _serialize_row(br),
                    "left_cells": [],
                    "right_cells": [_serialize_cell(c) for c in b_cells],
                    "left_text": "",
                    "right_text": _row_text(br),
                    "cell_changes": [],
                })
            continue

        # ── DELETE: các row chỉ có ở bảng A ──────────────────────────────
        if tag == "delete":
            for i in range(i1, i2):
                ar = a_rows[i]
                a_cells = _get_cells(ar)
                # row_index dùng i vì row không có trong B
                changes.append({
                    "type": "table_row_deleted",
                    "change_kind": "delete",
                    "row_index": i,
                    "col_count": len(a_cells),
                    "left_row": _serialize_row(ar),
                    "right_row": None,
                    "left_cells": [_serialize_cell(c) for c in a_cells],
                    "right_cells": [],
                    "left_text": _row_text(ar),
                    "right_text": "",
                    "cell_changes": [],
                })
            continue

        # ── REPLACE: pair các row theo thứ tự, phần dư là delete/insert ──
        if tag == "replace":
            a_slice = a_rows[i1:i2]
            b_slice = b_rows[j1:j2]
            pairs = min(len(a_slice), len(b_slice))

            # Pair lần lượt theo thứ tự
            for k in range(pairs):
                ar = a_slice[k]
                br = b_slice[k]
                row_change = _diff_row_pair(ar, br, row_index_b=j1 + k)
                if row_change:
                    changes.append(row_change)

            # Phần dư bên A → deleted
            for i in range(pairs, len(a_slice)):
                ar = a_slice[i]
                a_cells = _get_cells(ar)
                changes.append({
                    "type": "table_row_deleted",
                    "change_kind": "delete",
                    "row_index": i1 + i,
                    "col_count": len(a_cells),
                    "left_row": _serialize_row(ar),
                    "right_row": None,
                    "left_cells": [_serialize_cell(c) for c in a_cells],
                    "right_cells": [],
                    "left_text": _row_text(ar),
                    "right_text": "",
                    "cell_changes": [],
                })

            # Phần dư bên B → inserted
            for j in range(pairs, len(b_slice)):
                br = b_slice[j]
                b_cells = _get_cells(br)
                changes.append({
                    "type": "table_row_added",
                    "change_kind": "insert",
                    "row_index": j1 + j,
                    "col_count": len(b_cells),
                    "left_row": None,
                    "right_row": _serialize_row(br),
                    "left_cells": [],
                    "right_cells": [_serialize_cell(c) for c in b_cells],
                    "left_text": "",
                    "right_text": _row_text(br),
                    "cell_changes": [],
                })

    return changes


# ---------------------------------------------------------------------------
# High-level table change analysis — dùng bởi json_builder
# ---------------------------------------------------------------------------

TABLE_RENDER_FULL         = "full_table"
TABLE_RENDER_ROW_ADDED    = "row_added"
TABLE_RENDER_ROW_DELETED  = "row_deleted"
TABLE_RENDER_DELETED      = "table_deleted"
TABLE_RENDER_ROW_MODIFIED = "row_modified"


def analyze_table_change(
    a_tbl: Optional[DocNode],
    b_tbl: Optional[DocNode],
    table_changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    # ── Xóa cả bảng ──────────────────────────────────────────────────────────
    if a_tbl is not None and b_tbl is None:
        return {
            "render_mode": TABLE_RENDER_DELETED,
            "structure_changed": False,
            "full_table_original": _serialize_full_table(a_tbl),
            "full_table_modified": None,
            "header_row": get_header_row(a_tbl),
            "added_rows": [],
            "deleted_rows": [],
            "table_changes": [],
        }

    # ── Thêm cả bảng mới ─────────────────────────────────────────────────────
    if b_tbl is not None and a_tbl is None:
        return {
            "render_mode": TABLE_RENDER_FULL,
            "structure_changed": False,
            "full_table_original": None,
            "full_table_modified": _serialize_full_table(b_tbl),
            "header_row": get_header_row(b_tbl),
            "added_rows": [],
            "deleted_rows": [],
            "table_changes": [],
        }

    assert a_tbl is not None and b_tbl is not None

    structure_changed = detect_structure_change(a_tbl, b_tbl)

    # ── Thay đổi cấu trúc → hiển thị toàn bộ 2 bảng ─────────────────────────
    if structure_changed:
        return {
            "render_mode": TABLE_RENDER_FULL,
            "structure_changed": True,
            "full_table_original": _serialize_full_table(a_tbl),
            "full_table_modified": _serialize_full_table(b_tbl),
            "header_row": None,
            "added_rows": [],
            "deleted_rows": [],
            "table_changes": table_changes,
        }

    added_rows   = [c for c in table_changes if c.get("type") == "table_row_added"]
    deleted_rows = [c for c in table_changes if c.get("type") == "table_row_deleted"]
    modified_rows = [c for c in table_changes if c.get("type") == "table_row_modified"]

    has_added    = bool(added_rows)
    has_deleted  = bool(deleted_rows)
    has_modified = bool(modified_rows)

    # ── Chỉ thêm dòng ────────────────────────────────────────────────────────
    if has_added and not has_deleted and not has_modified:
        return {
            "render_mode": TABLE_RENDER_ROW_ADDED,
            "structure_changed": False,
            "full_table_original": None,
            "full_table_modified": None,
            "header_row": get_header_row(b_tbl),
            "added_rows": added_rows,
            "deleted_rows": [],
            "table_changes": table_changes,
        }

    # ── Chỉ xóa dòng ─────────────────────────────────────────────────────────
    if has_deleted and not has_added and not has_modified:
        return {
            "render_mode": TABLE_RENDER_ROW_DELETED,
            "structure_changed": False,
            "full_table_original": _serialize_full_table(a_tbl),
            "full_table_modified": _serialize_full_table(b_tbl),
            "header_row": None,
            "added_rows": [],
            "deleted_rows": deleted_rows,
            "table_changes": table_changes,
        }

    # ── Chỉ sửa cell ─────────────────────────────────────────────────────────
    if has_modified and not has_added and not has_deleted:
        return {
            "render_mode": TABLE_RENDER_ROW_MODIFIED,
            "structure_changed": False,
            "full_table_original": None,
            "full_table_modified": None,
            "header_row": None,
            "added_rows": [],
            "deleted_rows": [],
            "table_changes": table_changes,
        }

    # ── Hỗn hợp nhiều loại → full table ──────────────────────────────────────
    return {
        "render_mode": TABLE_RENDER_FULL,
        "structure_changed": False,
        "full_table_original": _serialize_full_table(a_tbl),
        "full_table_modified": _serialize_full_table(b_tbl),
        "header_row": None,
        "added_rows": added_rows,
        "deleted_rows": deleted_rows,
        "table_changes": table_changes,
    }