from __future__ import annotations

import re
from typing import List, Optional

from src.services.models.docnode import DocNode
from src.services.models.block import Block
from src.services.block.signature import _norm_text, signature


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s

_SKIP_SECTION_PATTERNS = [
    "muc luc", "mục lục", "table of contents", "目次",
    "ly lich sua doi", "lý lịch sửa đổi", "revision history",
    "change history", "document history", "改訂履歴",
]

_SKIP_BLOCK_PATTERNS = [
    "tiep trang sau", "tiếp trang sau",
    "(tiep trang sau)", "(tiếp trang sau)",
    "continued on next page", "continue on next page",
    "次ページに続く",
]


def _is_skip_section_heading(node: DocNode) -> bool:
    text = _norm(node.content.get("text", ""))
    return any(pat in text for pat in _SKIP_SECTION_PATTERNS)


def _is_skip_pattern(text: str) -> bool:
    return any(pat in text for pat in _SKIP_BLOCK_PATTERNS)


def _is_skip_block(node: DocNode) -> bool:
    if node.type in ("paragraph", "heading"):
        text = _norm(node.content.get("text", ""))
        return _is_skip_pattern(text)

    if node.type == "table":
        non_empty_cells = []
        for n in node.walk():
            if n.type == "cell":
                text = _norm(n.content.get("text", ""))
                if text:
                    non_empty_cells.append(text)
        if not non_empty_cells:
            return False
        return all(_is_skip_pattern(t) for t in non_empty_cells)

    return False


# ── Shape helpers ─────────────────────────────────────────────────────────────

def _shape_has_text(node: DocNode) -> bool:
    for child in (node.children or []):
        if child.type == "paragraph":
            txt = _norm_text(child.content.get("text", ""))
            if txt:
                return True
        elif child.type == "shape":
            if _shape_has_text(child):
                return True
    return False


def _shape_has_any_child(node: DocNode) -> bool:
    """
    Kiểm tra shape có bất kỳ child nào không (kể cả paragraph rỗng).
    Dùng để phân biệt shape thực sự (textbox được tạo ra, dù rỗng)
    với shape decorative (chỉ là hình vẽ, không có txbxContent).
    """
    return bool(node.children)


# ── Table merge helpers ───────────────────────────────────────────────────────

def _get_rows(table_node: DocNode) -> List[DocNode]:
    return [c for c in (table_node.children or []) if c.type == "row"]


def _get_cells(row_node: DocNode) -> List[DocNode]:
    return [c for c in (row_node.children or []) if c.type == "cell"]


def _col_count(table_node: DocNode) -> int:
    rows = _get_rows(table_node)
    if not rows:
        return 0
    return len(_get_cells(rows[0]))


def _get_header_key(table_node: DocNode) -> Optional[str]:
    rows = _get_rows(table_node)
    if not rows:
        return None
    cells = _get_cells(rows[0])
    return "|".join(_norm(c.content.get("text", "")) for c in cells)


def _get_cell_texts(row_node: DocNode) -> List[str]:
    texts = []
    for cell in _get_cells(row_node):
        cell_text = _norm(cell.content.get("text", ""))
        if cell_text:
            texts.append(cell_text)
            continue
        for child in (cell.children or []):
            child_text = _norm(child.content.get("text", ""))
            if child_text:
                texts.append(child_text)
    return texts


def _last_row_is_continue(table_node: DocNode) -> bool:
    rows = _get_rows(table_node)
    if not rows:
        return False
    non_empty = _get_cell_texts(rows[-1])
    if not non_empty:
        return False
    return all(_is_skip_pattern(t) for t in non_empty)


def _remove_last_row(table_node: DocNode) -> None:
    rows = _get_rows(table_node)
    if not rows:
        return
    last = rows[-1]
    table_node.children = [c for c in (table_node.children or []) if c is not last]
    table_node.content["rows"] = len(_get_rows(table_node))


def _merge_two_tables(base: DocNode, extra: DocNode) -> None:
    extra_rows = _get_rows(extra)
    data_rows = extra_rows[1:] if len(extra_rows) > 1 else []
    for row in data_rows:
        base.children.append(row)
    base.content["rows"] = len(_get_rows(base))


def _can_merge(prev: Block, curr: Block) -> bool:
    if prev.type != "table" or curr.type != "table":
        return False
    if prev.heading_ctx != curr.heading_ctx:
        return False
    prev_header = _get_header_key(prev.node)
    curr_header = _get_header_key(curr.node)
    if prev_header is None or curr_header is None:
        return False
    if prev_header != curr_header:
        return False
    if _col_count(prev.node) != _col_count(curr.node):
        return False
    if not _last_row_is_continue(prev.node):
        return False
    return True


def _merge_consecutive_tables(blocks: List[Block]) -> List[Block]:
    if not blocks:
        return blocks

    result: List[Block] = []

    for block in blocks:
        if result and _can_merge(result[-1], block):
            prev = result[-1]
            _remove_last_row(prev.node)
            _merge_two_tables(prev.node, block.node)
            prev.signature = signature(prev.node)
            continue
        result.append(block)

    return result


# ── Shape heading fix ─────────────────────────────────────────────────────────

def _fix_shape_headings(blocks: List[Block]) -> List[Block]:
    """
    Shape floating trong Word XML được extract từ paragraph nào đó trong XML stream.
    Khi sort theo order, một shape có thể bị gán heading_ctx của heading SAU nó
    (vd: shape thuộc flow chart 5.8 nhưng bị gán heading 5.9) vì paragraph chứa
    shape đó nằm sau heading 5.9 trong XML gốc.

    Fix: với mỗi shape block, tìm heading block có order lớn nhất mà vẫn <= order
    của shape → đó mới là heading đúng. Nếu heading hiện tại đang được gán có
    order > order của shape thì sửa lại.
    """
    # Chỉ lấy heading blocks, đã sort theo order (blocks đã sort trước khi gọi hàm này)
    heading_blocks = [b for b in blocks if b.type == "heading"]

    if not heading_blocks:
        return blocks

    for block in blocks:
        if block.type != "shape":
            continue

        block_order = block.order or 0

        # Tìm heading gần nhất có order <= order của shape
        correct_heading_ctx: Optional[str] = None
        for h in heading_blocks:
            if (h.order or 0) <= block_order:
                correct_heading_ctx = h.heading_ctx
            else:
                # heading_blocks đã sort theo order → có thể break sớm
                break

        if correct_heading_ctx is None:
            # Shape nằm trước mọi heading → giữ nguyên
            continue

        if block.heading_ctx == correct_heading_ctx:
            # Đã đúng rồi
            continue

        # Kiểm tra heading hiện tại có order > order của shape không
        # (nếu có → chắc chắn bị gán nhầm)
        current_heading_order: Optional[int] = None
        for h in heading_blocks:
            if h.heading_ctx == block.heading_ctx:
                current_heading_order = h.order or 0
                break

        if current_heading_order is not None and current_heading_order > block_order:
            block.heading_ctx = correct_heading_ctx

    return blocks


# ── Main builder ──────────────────────────────────────────────────────────────

def build_blocks(doc_root: DocNode) -> List[Block]:
    blocks: List[Block] = []

    current_heading: Optional[str] = None
    current_heading_level: int = 0
    heading_stack: List[dict] = []
    skip_until_level: Optional[int] = None

    for node in doc_root.children:

        if node.type == "heading":
            heading_level = int(node.content.get("level", 1) or 1)

            if skip_until_level is not None:
                if heading_level <= skip_until_level:
                    skip_until_level = None
                else:
                    continue

            if _is_skip_section_heading(node):
                skip_until_level = heading_level
                continue

            if _is_skip_block(node):
                continue

            heading_text = (node.content.get("text") or "").strip()

            while heading_stack and heading_stack[-1]["level"] >= heading_level:
                heading_stack.pop()

            heading_stack.append({"text": heading_text, "level": heading_level})

            current_heading = " > ".join(
                x["text"] for x in heading_stack if x["text"]
            )
            current_heading_level = heading_level

            blocks.append(
                Block(
                    type="heading",
                    node=node,
                    signature=signature(node),
                    heading_ctx=current_heading,
                    heading_level=current_heading_level,
                    order=node.order,
                    uid=node.uid,
                )
            )
            continue

        if skip_until_level is not None:
            continue

        if _is_skip_block(node):
            continue

        # Shape: chỉ bỏ qua nếu KHÔNG có child nào cả
        # (shape decorative thuần túy — không phải textbox).
        # Shape có child (kể cả paragraph rỗng) vẫn giữ lại để
        # json_builder có thể detect trường hợp text bị xóa hết.
        if node.type == "shape" and not _shape_has_any_child(node):
            continue

        blocks.append(
            Block(
                type=node.type,
                node=node,
                signature=signature(node),
                heading_ctx=current_heading,
                heading_level=current_heading_level,
                order=node.order,
                uid=node.uid,
            )
        )

    sorted_blocks = sorted(
        blocks,
        key=lambda x: (
            x.order if x.order is not None else 0,
            x.uid or "",
        ),
    )
    sorted_blocks = _fix_shape_headings(sorted_blocks)

    return _merge_consecutive_tables(sorted_blocks)