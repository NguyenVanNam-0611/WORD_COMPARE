from __future__ import annotations

import base64
from typing import List, Dict, Any, Optional

from src.services.models.block import Block
from src.services.diff.paragraph_diff import diff_words
from src.services.diff.table_diff import diff_table, analyze_table_change
from src.services.diff.shape_diff import diff_shape
from src.services.diff.image_diff import images_equal, build_image_change
from src.services.snapshot.pdf_renderer import render_block_snapshot


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section_key(h: Optional[str]) -> str:
    return h if h else "(No heading)"


def _serialize_node(node) -> Optional[Dict[str, Any]]:
    if not node:
        return None
    return {
        "uid": getattr(node, "uid", None),
        "type": getattr(node, "type", None),
        "content": getattr(node, "content", {}) or {},
        "children": [
            _serialize_node(child)
            for child in (getattr(node, "children", []) or [])
        ],
    }


def _serialize_block_side(block: Optional[Block]) -> Optional[Dict[str, Any]]:
    if not block:
        return None
    return {
        "uid": block.uid,
        "type": block.type,
        "order": block.order,
        "heading": block.heading_ctx,
        "preview_text": block.preview_text,
        "node": _serialize_node(block.node),
    }


def _build_context(
    blocks: List[Block],
    index: int,
) -> Dict[str, Optional[Dict[str, Any]]]:
    prev_block = blocks[index - 1] if index - 1 >= 0 else None
    next_block = blocks[index + 1] if index + 1 < len(blocks) else None
    return {
        "previous": _serialize_block_side(prev_block),
        "next": _serialize_block_side(next_block),
    }


def _build_change(
    cid: int,
    change_type: str,
    heading: Optional[str],
    order: int,
    left: Optional[Dict[str, Any]],
    right: Optional[Dict[str, Any]],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if left is not None and right is None:
        kind = "delete"
    elif left is None and right is not None:
        kind = "insert"
    else:
        kind = "replace"

    return {
        "id": cid,
        "heading": heading,
        "type": change_type,
        "change_kind": kind,
        "order": order,
        "left": left,
        "right": right,
        **(extra or {}),
    }


def _shape_has_text(block: Block) -> bool:
    def _check(node) -> bool:
        if not node:
            return False
        for child in (getattr(node, "children", []) or []):
            if child.type == "paragraph":
                txt = (child.content.get("text") or "").strip()
                if txt:
                    return True
            elif child.type == "shape":
                if _check(child):
                    return True
        return False
    return _check(block.node)


def _shape_has_any_child(block: Block) -> bool:
    node = getattr(block, "node", None)
    children = getattr(node, "children", None) if node is not None else None
    return bool(children)


def _collect_shape_texts(node) -> List[str]:
    texts = []
    for child in (getattr(node, "children", []) or []):
        if getattr(child, "type", None) == "paragraph":
            txt = (getattr(child, "content", {}) or {}).get("text", "") or ""
            txt = txt.strip()
            if txt:
                texts.append(txt)
        elif getattr(child, "type", None) == "shape":
            texts.extend(_collect_shape_texts(child))
    return texts


def _serialize_shape_block_side(block: Optional[Block]) -> Optional[Dict[str, Any]]:
    if not block:
        return None
    base = _serialize_block_side(block)
    if base is None:
        return None
    base["shape_text_lines"] = _collect_shape_texts(block.node)
    return base


# ── Anchor builders ───────────────────────────────────────────────────────────

def _get_table_anchor(block: Block) -> str:
    """
    Dùng header row + dòng data đầu tiên để tạo anchor đủ dài và unique.
    Tránh dùng chỉ header vì nhiều bảng cùng cấu trúc sẽ match nhầm.
    """
    node = block.node
    rows = [c for c in (node.children or []) if c.type == "row"]

    parts: List[str] = []
    for row in rows[:2]:  # header + dòng data đầu
        cells = [
            (c.content.get("text") or "").strip()
            for c in (row.children or [])
            if c.type == "cell"
        ]
        row_text = " ".join(c for c in cells if c)
        if row_text:
            parts.append(row_text)

    anchor = " ".join(parts)[:120]
    if anchor:
        return anchor

    # Fallback: lấy từ content.text đã được build khi extract
    return (node.content.get("text") or "").strip()[:120]


def _get_shape_anchor(block: Block) -> str:
    """
    Dùng tất cả text lines trong shape (ghép lại) để anchor đủ dài.
    Không cắt ngắn quá sớm — pdf_renderer sẽ tự thử từng độ dài.
    """
    texts = _collect_shape_texts(block.node)
    if texts:
        return " ".join(texts)[:120]
    return (block.node.content.get("text") or "").strip()[:120]


# ── Snapshot helpers ──────────────────────────────────────────────────────────

def _estimate_page_hint(block: Block, total_blocks: int, total_pages_hint: int = 20) -> Optional[int]:
    """
    Ước lượng page number từ order của block.
    Không chính xác tuyệt đối nhưng đủ để thu hẹp vùng search trong PDF.
    """
    if total_blocks <= 0:
        return None
    ratio = block.order / max(total_blocks, 1)
    return int(ratio * total_pages_hint)


def _get_row_anchor(row_change: Dict[str, Any], side: str = "left") -> str:
    """
    Lấy anchor text từ một row change dict.
    Ưu tiên ghép text từ tất cả cells để anchor đủ dài và unique.
    side: 'left' → dùng left_cells/left_text, 'right' → dùng right_cells/right_text.
    """
    cells_key = "left_cells" if side == "left" else "right_cells"
    text_key = "left_text" if side == "left" else "right_text"

    cells = row_change.get(cells_key) or []
    cell_texts = [
        (c.get("text") or "").strip()
        for c in cells
        if c and (c.get("text") or "").strip()
    ]
    anchor = " ".join(cell_texts)[:120]
    if anchor:
        return anchor

    # Fallback về row text
    return (row_change.get(text_key) or "")[:120]


def _stack_images_vertically(data_uris: List[str]) -> Optional[str]:
    """
    Ghép danh sách ảnh PNG (data URI) theo chiều dọc thành 1 ảnh duy nhất.
    Dùng fitz.Pixmap để không cần phụ thuộc PIL.
    """
    try:
        import fitz
    except ImportError:
        # Nếu không ghép được thì trả về ảnh đầu tiên
        return data_uris[0] if data_uris else None

    pixmaps = []
    for uri in data_uris:
        try:
            b64 = uri.split(",", 1)[1]
            png_bytes = base64.b64decode(b64)
            pix = fitz.Pixmap(png_bytes)
            # Đảm bảo tất cả đều là RGB (không có alpha channel lạ)
            if pix.alpha:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            pixmaps.append(pix)
        except Exception:
            continue

    if not pixmaps:
        return None
    if len(pixmaps) == 1:
        return data_uris[0]

    total_h = sum(p.height for p in pixmaps)
    max_w = max(p.width for p in pixmaps)

    # Tạo pixmap trắng tổng hợp
    combined = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, max_w, total_h), False)
    combined.clear_with(255)

    y_offset = 0
    for pix in pixmaps:
        # Đặt từng ảnh vào đúng vị trí dọc
        target_rect = fitz.IRect(0, y_offset, pix.width, y_offset + pix.height)
        combined.copy(pix, target_rect)
        y_offset += pix.height

    png_bytes = combined.tobytes("png")
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def _snap_modified_rows(
    table_changes: List[Dict[str, Any]],
    original_pdf: Optional[str],
    modified_pdf: Optional[str],
    page_hint_a: Optional[int],
    page_hint_b: Optional[int],
    padding: int = 8,
    dpi: int = 150,
) -> tuple[Optional[str], Optional[str]]:
    """
    Với mỗi row bị thay đổi nội dung (table_row_modified), render snapshot
    của đúng dòng đó trong PDF.
    Nếu có nhiều dòng thay đổi, ghép tất cả thành 1 ảnh dọc.
    Trả về (left_snapshot, right_snapshot).
    """
    modified_rows = [
        c for c in table_changes
        if c.get("type") == "table_row_modified"
    ]

    if not modified_rows:
        return None, None

    def _render_side(pdf_path: Optional[str], side: str, page_hint: Optional[int]) -> Optional[str]:
        if not pdf_path:
            return None

        snapshots = []
        for row_change in modified_rows:
            anchor = _get_row_anchor(row_change, side=side)
            if not anchor:
                continue
            snap_uri = render_block_snapshot(
                pdf_path=pdf_path,
                anchor_text=anchor,
                dpi=dpi,
                padding=padding,
                page_hint=page_hint,
                fallback_full_page=False,
            )
            if snap_uri:
                snapshots.append(snap_uri)

        if not snapshots:
            return None
        if len(snapshots) == 1:
            return snapshots[0]
        return _stack_images_vertically(snapshots)

    left_snapshot = _render_side(original_pdf, "left", page_hint_a)
    right_snapshot = _render_side(modified_pdf, "right", page_hint_b)
    return left_snapshot, right_snapshot


def _snapshot_pair(
    a_block: Optional[Block],
    b_block: Optional[Block],
    original_pdf: Optional[str],
    modified_pdf: Optional[str],
    anchor_fn,
    padding: int = 24,
    total_a_blocks: int = 0,
    total_b_blocks: int = 0,
) -> tuple[Optional[str], Optional[str]]:
    left_snapshot: Optional[str] = None
    right_snapshot: Optional[str] = None

    # ── LEFT ─────────────────────────────────────────────────────────────────
    if a_block is not None and original_pdf:
        anchor = anchor_fn(a_block)
        if anchor:
            page_hint = _estimate_page_hint(a_block, total_a_blocks)
            left_snapshot = render_block_snapshot(
                original_pdf,
                anchor,
                padding=padding,
                page_hint=page_hint,
                fallback_full_page=False,
            )

        # Fallback: dùng data_uri gốc từ extractor (chỉ có ở image/shape)
        if not left_snapshot:
            content = getattr(a_block.node, "content", {}) or {}
            left_snapshot = content.get("data_uri") or None

    # ── RIGHT ─────────────────────────────────────────────────────────────────
    if b_block is not None and modified_pdf:
        anchor = anchor_fn(b_block)
        if anchor:
            page_hint = _estimate_page_hint(b_block, total_b_blocks)
            right_snapshot = render_block_snapshot(
                modified_pdf,
                anchor,
                padding=padding,
                page_hint=page_hint,
                fallback_full_page=False,
            )

        if not right_snapshot:
            content = getattr(b_block.node, "content", {}) or {}
            right_snapshot = content.get("data_uri") or None

    return left_snapshot, right_snapshot


# ── Main builder ──────────────────────────────────────────────────────────────

def build_ui_json(
    a_blocks: List[Block],
    b_blocks: List[Block],
    opcodes,
    original_pdf: Optional[str] = None,
    modified_pdf: Optional[str] = None,
) -> Dict[str, Any]:
    sections_map: Dict[str, Dict[str, Any]] = {}
    flat_changes: List[Dict[str, Any]] = []

    total_a = len(a_blocks)
    total_b = len(b_blocks)

    cid = 1

    def add_change(change: Dict[str, Any]):
        nonlocal cid
        heading = _section_key(change.get("heading"))
        if heading not in sections_map:
            sections_map[heading] = {"heading": heading, "changes": []}
        sections_map[heading]["changes"].append(change)
        flat_changes.append(change)

    # ── Helper nội bộ để gọi _snapshot_pair với total counts ─────────────────
    def snap(
        a_block: Optional[Block],
        b_block: Optional[Block],
        o_pdf: Optional[str],
        m_pdf: Optional[str],
        anchor_fn,
        padding: int = 24,
    ) -> tuple[Optional[str], Optional[str]]:
        return _snapshot_pair(
            a_block, b_block, o_pdf, m_pdf, anchor_fn,
            padding=padding,
            total_a_blocks=total_a,
            total_b_blocks=total_b,
        )

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue

        # ── INSERT ────────────────────────────────────────────────────────────
        if tag == "insert":
            for j in range(j1, j2):
                b = b_blocks[j]

                if b.type == "shape" and not _shape_has_text(b) and not _shape_has_any_child(b):
                    continue

                extra: Dict[str, Any] = {
                    "left_context": None,
                    "right_context": _build_context(b_blocks, j),
                    "left_snapshot": None,
                    "right_snapshot": None,
                }

                if b.type == "table":
                    _, rs = snap(None, b, None, modified_pdf, _get_table_anchor)
                    extra["right_snapshot"] = rs

                elif b.type == "shape":
                    _, rs = snap(None, b, None, modified_pdf, _get_shape_anchor, padding=30)
                    extra["right_snapshot"] = rs

                elif b.type == "image":
                    extra["right_snapshot"] = (b.node.content or {}).get("data_uri")

                add_change(_build_change(
                    cid=cid,
                    change_type=f"{b.type}_inserted",
                    heading=b.heading_ctx,
                    order=b.order,
                    left=None,
                    right=_serialize_shape_block_side(b) if b.type == "shape" else _serialize_block_side(b),
                    extra=extra,
                ))
                cid += 1

        # ── DELETE ────────────────────────────────────────────────────────────
        elif tag == "delete":
            for i in range(i1, i2):
                a = a_blocks[i]

                if a.type == "shape" and not _shape_has_text(a) and not _shape_has_any_child(a):
                    continue

                extra: Dict[str, Any] = {
                    "left_context": _build_context(a_blocks, i),
                    "right_context": None,
                    "left_snapshot": None,
                    "right_snapshot": None,
                }

                if a.type == "table":
                    ls, _ = snap(a, None, original_pdf, None, _get_table_anchor)
                    extra["left_snapshot"] = ls
                    extra["table_analysis"] = analyze_table_change(
                        a_tbl=a.node, b_tbl=None, table_changes=[]
                    )

                elif a.type == "shape":
                    ls, _ = snap(a, None, original_pdf, None, _get_shape_anchor, padding=30)
                    extra["left_snapshot"] = ls

                elif a.type == "image":
                    extra["left_snapshot"] = (a.node.content or {}).get("data_uri")

                add_change(_build_change(
                    cid=cid,
                    change_type=f"{a.type}_deleted",
                    heading=a.heading_ctx,
                    order=a.order,
                    left=_serialize_shape_block_side(a) if a.type == "shape" else _serialize_block_side(a),
                    right=None,
                    extra=extra,
                ))
                cid += 1

        # ── REPLACE ───────────────────────────────────────────────────────────
        elif tag == "replace":
            pairs = min(i2 - i1, j2 - j1)

            for k in range(pairs):
                ai = i1 + k
                bj = j1 + k
                a = a_blocks[ai]
                b = b_blocks[bj]

                heading = a.heading_ctx or b.heading_ctx
                order = min(a.order, b.order)

                # ── Paragraph ────────────────────────────────────────────────
                if a.type == "paragraph" and b.type == "paragraph":
                    old_text = a.node.content.get("text", "") or ""
                    new_text = b.node.content.get("text", "") or ""
                    if old_text != new_text:
                        add_change(_build_change(
                            cid=cid,
                            change_type="paragraph_modified",
                            heading=heading,
                            order=order,
                            left=_serialize_block_side(a),
                            right=_serialize_block_side(b),
                            extra={
                                "word_diff": diff_words(old_text, new_text),
                                "left_context": _build_context(a_blocks, ai),
                                "right_context": _build_context(b_blocks, bj),
                            },
                        ))
                        cid += 1
                    continue

                # ── Table ─────────────────────────────────────────────────────
                if a.type == "table" and b.type == "table":
                    table_changes = diff_table(a.node, b.node)
                    if table_changes:
                        table_analysis = analyze_table_change(
                            a_tbl=a.node, b_tbl=b.node, table_changes=table_changes
                        )

                        render_mode = table_analysis.get("render_mode", "")

                        if render_mode == "row_modified":
                            # Chỉ thay đổi nội dung cell → snapshot từng dòng thay đổi
                            page_hint_a = _estimate_page_hint(a, total_a)
                            page_hint_b = _estimate_page_hint(b, total_b)
                            ls, rs = _snap_modified_rows(
                                table_changes=table_changes,
                                original_pdf=original_pdf,
                                modified_pdf=modified_pdf,
                                page_hint_a=page_hint_a,
                                page_hint_b=page_hint_b,
                            )
                            # Fallback: nếu không snap được row nào thì dùng toàn bảng
                            if not ls and not rs:
                                ls, rs = snap(a, b, original_pdf, modified_pdf, _get_table_anchor)
                        else:
                            # full_table / row_added / row_deleted / structure_changed
                            # → snapshot toàn bộ bảng
                            ls, rs = snap(a, b, original_pdf, modified_pdf, _get_table_anchor)

                        add_change(_build_change(
                            cid=cid,
                            change_type="table_modified",
                            heading=heading,
                            order=order,
                            left=_serialize_block_side(a),
                            right=_serialize_block_side(b),
                            extra={
                                "table_changes": table_changes,
                                "table_analysis": table_analysis,
                                "left_snapshot": ls,
                                "right_snapshot": rs,
                                "left_context": _build_context(a_blocks, ai),
                                "right_context": _build_context(b_blocks, bj),
                            },
                        ))
                        cid += 1
                    continue

                # ── Shape ─────────────────────────────────────────────────────
                if a.type == "shape" and b.type == "shape":
                    a_has_text = _shape_has_text(a)
                    b_has_text = _shape_has_text(b)

                    if not a_has_text and not b_has_text:
                        continue

                    shape_changes = diff_shape(a.node, b.node) if (a_has_text and b_has_text) else []
                    a_cleared = a_has_text and not b_has_text

                    # Chỉ emit nếu có thay đổi thực sự
                    if not a_cleared and not (not a_has_text and b_has_text) and not shape_changes:
                        continue

                    ls, rs = snap(a, b, original_pdf, modified_pdf, _get_shape_anchor, padding=30)
                    extra: Dict[str, Any] = {
                        "shape_changes": shape_changes,
                        "left_snapshot": ls,
                        "right_snapshot": rs,
                        "left_context": _build_context(a_blocks, ai),
                        "right_context": _build_context(b_blocks, bj),
                    }
                    if a_cleared:
                        extra["shape_cleared"] = True

                    add_change(_build_change(
                        cid=cid,
                        change_type="shape_modified",
                        heading=heading,
                        order=order,
                        left=_serialize_shape_block_side(a),
                        right=_serialize_shape_block_side(b),
                        extra=extra,
                    ))
                    cid += 1
                    continue

                # ── Image ─────────────────────────────────────────────────────
                if a.type == "image" and b.type == "image":
                    if not images_equal(a.node, b.node):
                        a_content = a.node.content or {}
                        b_content = b.node.content or {}
                        add_change(_build_change(
                            cid=cid,
                            change_type="image_modified",
                            heading=heading,
                            order=order,
                            left=_serialize_block_side(a),
                            right=_serialize_block_side(b),
                            extra={
                                "left_snapshot": a_content.get("data_uri"),
                                "right_snapshot": b_content.get("data_uri"),
                                "image_change": build_image_change(a.node, b.node),
                                "left_context": _build_context(a_blocks, ai),
                                "right_context": _build_context(b_blocks, bj),
                            },
                        ))
                        cid += 1
                    continue

                # ── Type mismatch ─────────────────────────────────────────────
                add_change(_build_change(
                    cid=cid,
                    change_type=f"{a.type}_to_{b.type}_modified",
                    heading=heading,
                    order=order,
                    left=_serialize_block_side(a),
                    right=_serialize_block_side(b),
                    extra={
                        "left_context": _build_context(a_blocks, ai),
                        "right_context": _build_context(b_blocks, bj),
                    },
                ))
                cid += 1

            # ── Phần dư bên a → deleted ───────────────────────────────────────
            for i in range(i1 + pairs, i2):
                a = a_blocks[i]
                if a.type == "shape" and not _shape_has_text(a):
                    continue

                extra: Dict[str, Any] = {
                    "left_context": _build_context(a_blocks, i),
                    "right_context": None,
                    "left_snapshot": None,
                    "right_snapshot": None,
                }

                if a.type == "table":
                    ls, _ = snap(a, None, original_pdf, None, _get_table_anchor)
                    extra["left_snapshot"] = ls
                    extra["table_analysis"] = analyze_table_change(
                        a_tbl=a.node, b_tbl=None, table_changes=[]
                    )
                elif a.type == "shape":
                    ls, _ = snap(a, None, original_pdf, None, _get_shape_anchor, padding=30)
                    extra["left_snapshot"] = ls
                elif a.type == "image":
                    extra["left_snapshot"] = (a.node.content or {}).get("data_uri")

                add_change(_build_change(
                    cid=cid,
                    change_type=f"{a.type}_deleted",
                    heading=a.heading_ctx,
                    order=a.order,
                    left=_serialize_shape_block_side(a) if a.type == "shape" else _serialize_block_side(a),
                    right=None,
                    extra=extra,
                ))
                cid += 1

            # ── Phần dư bên b → inserted ──────────────────────────────────────
            for j in range(j1 + pairs, j2):
                b = b_blocks[j]
                if b.type == "shape" and not _shape_has_text(b):
                    continue

                extra: Dict[str, Any] = {
                    "left_context": None,
                    "right_context": _build_context(b_blocks, j),
                    "left_snapshot": None,
                    "right_snapshot": None,
                }

                if b.type == "table":
                    _, rs = snap(None, b, None, modified_pdf, _get_table_anchor)
                    extra["right_snapshot"] = rs
                elif b.type == "shape":
                    _, rs = snap(None, b, None, modified_pdf, _get_shape_anchor, padding=30)
                    extra["right_snapshot"] = rs
                elif b.type == "image":
                    extra["right_snapshot"] = (b.node.content or {}).get("data_uri")

                add_change(_build_change(
                    cid=cid,
                    change_type=f"{b.type}_inserted",
                    heading=b.heading_ctx,
                    order=b.order,
                    left=None,
                    right=_serialize_shape_block_side(b) if b.type == "shape" else _serialize_block_side(b),
                    extra=extra,
                ))
                cid += 1

    ordered_sections = sorted(
        sections_map.values(),
        key=lambda x: min((c.get("order", 0) for c in x["changes"]), default=0),
    )
    for section in ordered_sections:
        section["changes"] = sorted(
            section["changes"],
            key=lambda x: (x.get("order", 0), x.get("id", 0)),
        )

    return {
        "sections": ordered_sections,
        "changes": sorted(
            flat_changes,
            key=lambda x: (x.get("order", 0), x.get("id", 0)),
        ),
    }