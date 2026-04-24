from __future__ import annotations

import difflib
import hashlib
from typing import List, Dict, Any, Optional

from src.services.models.docnode import DocNode
from src.services.diff.paragraph_diff import diff_words


def _norm_text(s: str) -> str:
    return " ".join((s or "").replace("\u00a0", " ").split()).strip()


def _serialize_node(node: Optional[DocNode]) -> Optional[Dict[str, Any]]:
    if node is None:
        return None
    return {
        "uid": getattr(node, "uid", None),
        "type": node.type,
        "display_type": node.type,
        "order": getattr(node, "order", 0), 
        "path": getattr(node, "path", ""),
        "content": node.content or {},
        "children": [_serialize_node(c) for c in (node.children or [])],
    }


def _text_paragraphs(shape_node: DocNode) -> List[DocNode]:
    """Thu thập tất cả paragraph có text (đệ quy vào shape lồng nhau)."""
    result = []
    for c in (shape_node.children or []):
        if c.type == "paragraph":
            if _norm_text(c.content.get("text", "")):
                result.append(c)
        elif c.type == "shape":
            result.extend(_text_paragraphs(c))
    return result


def _para_sig(node: DocNode) -> str:
    """Signature chỉ dựa trên text — bỏ qua style/alignment/format."""
    txt = _norm_text(node.content.get("text", ""))
    h = hashlib.md5(txt.encode("utf-8")).hexdigest()[:16] if txt else "empty"
    return f"P:{h}"


def _build_change(
    cid: int,
    change_type: str,
    change_kind: str,
    original: Optional[Dict[str, Any]],
    modified: Optional[Dict[str, Any]],
    word_diff: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "id": cid,
        "type": change_type,
        "display_type": change_type
            .replace("_modified", "")
            .replace("_inserted", "")
            .replace("_deleted", "")
            .replace("_equal", ""),
        "change_kind": change_kind,
        "original": original,
        "modified": modified,
        "left_context": original,
        "right_context": modified,
        "word_diff": word_diff,
    }


def diff_shape(a_shape: DocNode, b_shape: DocNode) -> List[Dict[str, Any]]:
    """
    So sánh text trong 2 shape:
    - Chỉ xét paragraph có text thực sự.
    - Bỏ qua image, table, paragraph rỗng, thay đổi format/style.
    - Trả về [] nếu không có thay đổi text nào.
    - QUAN TRỌNG: emit cả "equal" paragraphs để frontend có đủ context hiển thị.
    """
    a_paras = _text_paragraphs(a_shape)
    b_paras = _text_paragraphs(b_shape)

    if not a_paras and not b_paras:
        return []

    sm = difflib.SequenceMatcher(
        a=[_para_sig(p) for p in a_paras],
        b=[_para_sig(p) for p in b_paras],
        autojunk=False,
    )

    # Kiểm tra xem có thay đổi thực sự không (bỏ equal)
    has_real_change = any(tag != "equal" for tag, *_ in sm.get_opcodes())
    if not has_real_change:
        return []

    changes: List[Dict[str, Any]] = []
    cid = 1

    for tag, i1, i2, j1, j2 in sm.get_opcodes():

        # ── EQUAL: emit để frontend biết context ──────────────────────
        if tag == "equal":
            for k in range(i2 - i1):
                a_node = a_paras[i1 + k]
                b_node = b_paras[j1 + k]
                changes.append(_build_change(
                    cid=cid,
                    change_type="paragraph_equal",
                    change_kind="equal",
                    original=_serialize_node(a_node),
                    modified=_serialize_node(b_node),
                ))
                cid += 1
            continue

        # ── INSERT ────────────────────────────────────────────────────
        if tag == "insert":
            for node in b_paras[j1:j2]:
                changes.append(_build_change(
                    cid=cid,
                    change_type="paragraph_inserted",
                    change_kind="insert",
                    original=None,
                    modified=_serialize_node(node),
                ))
                cid += 1
            continue

        # ── DELETE ────────────────────────────────────────────────────
        if tag == "delete":
            for node in a_paras[i1:i2]:
                changes.append(_build_change(
                    cid=cid,
                    change_type="paragraph_deleted",
                    change_kind="delete",
                    original=_serialize_node(node),
                    modified=None,
                ))
                cid += 1
            continue

        # ── REPLACE ───────────────────────────────────────────────────
        if tag == "replace":
            pairs = min(i2 - i1, j2 - j1)

            for k in range(pairs):
                a_node = a_paras[i1 + k]
                b_node = b_paras[j1 + k]
                old = _norm_text(a_node.content.get("text", ""))
                new = _norm_text(b_node.content.get("text", ""))

                if old != new:
                    changes.append(_build_change(
                        cid=cid,
                        change_type="paragraph_modified",
                        change_kind="replace",
                        original=_serialize_node(a_node),
                        modified=_serialize_node(b_node),
                        word_diff=diff_words(old, new),
                    ))
                    cid += 1
                else:
                    # text giống nhau (chỉ khác format) → coi là equal
                    changes.append(_build_change(
                        cid=cid,
                        change_type="paragraph_equal",
                        change_kind="equal",
                        original=_serialize_node(a_node),
                        modified=_serialize_node(b_node),
                    ))
                    cid += 1

            for node in a_paras[i1 + pairs:i2]:
                changes.append(_build_change(
                    cid=cid,
                    change_type="paragraph_deleted",
                    change_kind="delete",
                    original=_serialize_node(node),
                    modified=None,
                ))
                cid += 1

            for node in b_paras[j1 + pairs:j2]:
                changes.append(_build_change(
                    cid=cid,
                    change_type="paragraph_inserted",
                    change_kind="insert",
                    original=None,
                    modified=_serialize_node(node),
                ))
                cid += 1

    return changes