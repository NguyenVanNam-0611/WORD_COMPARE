from __future__ import annotations

import re
from typing import Iterator, Union, Optional, List, Dict, Any

from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

from src.services.models.docnode import DocNode
from src.services.extractor.image import extract_inline_images
from src.services.extractor.shape import extract_shapes_from_paragraph


BlockItem = Union[Paragraph, Table]

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _norm_text(s: str) -> str:
    return " ".join((s or "").replace("\u00a0", " ").split()).strip()


def _heading_level(style_name: str) -> int:
    if not style_name:
        return 0
    m = re.search(r"heading\s*(\d+)", style_name.strip().lower())
    return int(m.group(1)) if m else 0


def _is_heading(par: Paragraph) -> bool:
    name = par.style.name if par.style else ""
    return (name or "").strip().lower().startswith("heading")


def _next_order(order_ref: Dict[str, int]) -> int:
    order_ref["value"] += 1
    return order_ref["value"]


def _safe_pt(value) -> float:
    try:
        return value.pt if value else 0
    except Exception:
        return 0


def _extract_runs(par: Paragraph) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []

    for idx, run in enumerate(par.runs):
        text = run.text or ""
        if not text.strip() and not text:
            continue

        font = run.font

        try:
            color = font.color.rgb
            color = str(color) if color else None
        except Exception:
            color = None

        runs.append(
            {
                "index": idx,
                "text": text,
                "bold": bool(run.bold) if run.bold is not None else False,
                "italic": bool(run.italic) if run.italic is not None else False,
                "underline": bool(run.underline) if run.underline is not None else False,
                "font_name": font.name if font else None,
                "font_size": font.size.pt if font and font.size else None,
                "color": color,
                "highlight": str(font.highlight_color) if font and font.highlight_color else None,
            }
        )

    return runs


def _iter_block_items(parent: Union[_Document, _Cell]) -> Iterator[BlockItem]:
    if isinstance(parent, _Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError("unsupported parent")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


# ── Merged cell helpers ───────────────────────────────────────────────────────

def _get_cell_spans(cell: _Cell):
    """
    Trả về (col_span, is_vmerge_restart, is_vmerge_continue).
    - col_span           : số cột cell chiếm (gridSpan), mặc định 1
    - is_vmerge_restart  : True nếu là ô đầu của nhóm row-merge (vMerge val="restart")
    - is_vmerge_continue : True nếu là ô tiếp tục trong nhóm row-merge (vMerge không có val)
    """
    tc = cell._tc
    tcPr = tc.find(f"{{{W}}}tcPr")
    if tcPr is None:
        return 1, False, False

    # colspan
    gridSpan = tcPr.find(f"{{{W}}}gridSpan")
    col_span = int(gridSpan.get(f"{{{W}}}val", 1)) if gridSpan is not None else 1

    # rowspan
    vMerge = tcPr.find(f"{{{W}}}vMerge")
    is_vmerge_restart = False
    is_vmerge_continue = False
    if vMerge is not None:
        val = vMerge.get(f"{{{W}}}val", "")
        if val == "restart":
            is_vmerge_restart = True
        else:
            is_vmerge_continue = True

    return col_span, is_vmerge_restart, is_vmerge_continue


def _get_xml_tcs(tbl: Table) -> list:
    """
    Trả về list các list <w:tc> element theo từng hàng, đọc thẳng từ XML.
    Đây là physical cells thực sự trong XML, không bị python-docx deduplicate.
    """
    TR = f"{{{W}}}tr"
    TC = f"{{{W}}}tc"
    result = []
    for tr in tbl._tbl.iterchildren(TR):
        result.append(list(tr.iterchildren(TC)))
    return result


def _tc_grid_col_span(tc) -> int:
    """Lấy gridSpan của một <w:tc> element."""
    tcPr = tc.find(f"{{{W}}}tcPr")
    if tcPr is None:
        return 1
    gs = tcPr.find(f"{{{W}}}gridSpan")
    return int(gs.get(f"{{{W}}}val", 1)) if gs is not None else 1


def _count_row_span(tbl: Table, row_idx: int, grid_col: int) -> int:
    """
    Đếm số hàng mà ô tại grid_col chiếm (bao gồm chính nó).
    grid_col: vị trí cột logic trong grid (tính theo gridSpan tích lũy).
    """
    xml_rows = _get_xml_tcs(tbl)
    span = 1
    for r in range(row_idx + 1, len(xml_rows)):
        # Tìm tc trong hàng r có grid position == grid_col
        pos = 0
        found_tc = None
        for tc in xml_rows[r]:
            if pos == grid_col:
                found_tc = tc
                break
            pos += _tc_grid_col_span(tc)
            if pos > grid_col:
                break  # vượt qua rồi, không tìm thấy
        if found_tc is None:
            break
        tcPr = found_tc.find(f"{{{W}}}tcPr")
        if tcPr is None:
            break
        vMerge = tcPr.find(f"{{{W}}}vMerge")
        if vMerge is None:
            break
        val = vMerge.get(f"{{{W}}}val", "")
        if val == "restart":
            break
        span += 1
    return span


# ── Paragraph / cell / table extractors ──────────────────────────────────────

def _extract_paragraph_nodes(
    par: Paragraph,
    uid: str,
    parent_uid: Optional[str],
    order_ref: Dict[str, int],
) -> List[DocNode]:
    text = _norm_text(par.text)
    imgs = extract_inline_images(
        par,
        uid_prefix=f"{uid}.img",
        parent_uid=uid,
        order_ref=order_ref,
    )
    shapes = extract_shapes_from_paragraph(
        par,
        uid_prefix=f"{uid}.shp",
        parent_uid=uid,
        order_ref=order_ref,
    )

    style_name = par.style.name if par.style else ""
    alignment = par.alignment if par.alignment is not None else None
    pf = par.paragraph_format

    try:
        line_spacing = pf.line_spacing
        if hasattr(line_spacing, "pt"):
            line_spacing = line_spacing.pt
    except Exception:
        line_spacing = None

    try:
        first_line_indent = pf.first_line_indent.pt if pf.first_line_indent else 0
    except Exception:
        first_line_indent = 0

    runs = _extract_runs(par)

    base_content = {
        "text": text,
        "style": style_name,
        "alignment": alignment,
        "is_heading": _is_heading(par),
        "heading_level": _heading_level(style_name),
        "space_before": _safe_pt(pf.space_before),
        "space_after": _safe_pt(pf.space_after),
        "left_indent": _safe_pt(pf.left_indent),
        "right_indent": _safe_pt(pf.right_indent),
        "first_line_indent": first_line_indent,
        "line_spacing": line_spacing,
        "keep_together": bool(pf.keep_together) if pf.keep_together is not None else False,
        "keep_with_next": bool(pf.keep_with_next) if pf.keep_with_next is not None else False,
        "page_break_before": bool(pf.page_break_before) if pf.page_break_before is not None else False,
        "widow_control": bool(pf.widow_control) if pf.widow_control is not None else False,
        "run_count": len(runs),
        "runs": runs,
        "image_count": len(imgs),
        "shape_count": len(shapes),
    }

    if _is_heading(par):
        return [
            DocNode(
                type="heading",
                uid=uid,
                parent_uid=parent_uid,
                order=_next_order(order_ref),
                path=uid.replace(".", "/"),
                content={
                    **base_content,
                    "level": _heading_level(style_name),
                },
            )
        ]

    nodes: List[DocNode] = []

    if text or imgs or shapes:
        pnode = DocNode(
            type="paragraph",
            uid=uid,
            parent_uid=parent_uid,
            order=_next_order(order_ref),
            path=uid.replace(".", "/"),
            content=base_content,
        )

        for im in imgs:
            pnode.add_child(im)

        nodes.append(pnode)

    for sh in shapes:
        nodes.append(sh)

    return nodes


def _extract_cell(
    cell: _Cell,
    uid_prefix: str,
    parent_uid: Optional[str],
    order_ref: Dict[str, int],
    row_index: int,
    col_index: int,
    col_span: int = 1,
    row_span: int = 1,
    is_merged: bool = False,
) -> DocNode:
    cell_node = DocNode(
        type="cell",
        uid=uid_prefix,
        parent_uid=parent_uid,
        order=_next_order(order_ref),
        path=uid_prefix.replace(".", "/"),
        content={
            "text": _norm_text(cell.text),
            "row_index": row_index,
            "col_index": col_index,
            "row_span": row_span,
            "col_span": col_span,
            "is_merged": is_merged,
        },
    )

    n = 0
    for item in _iter_block_items(cell):
        n += 1

        if isinstance(item, Paragraph):
            nodes = _extract_paragraph_nodes(
                item,
                uid=f"{uid_prefix}.p{n}",
                parent_uid=uid_prefix,
                order_ref=order_ref,
            )
            for x in nodes:
                cell_node.add_child(x)
        else:
            cell_node.add_child(
                _extract_table(
                    item,
                    uid=f"{uid_prefix}.t{n}",
                    parent_uid=uid_prefix,
                    order_ref=order_ref,
                )
            )

    return cell_node


def _extract_table(
    tbl: Table,
    uid: Optional[str] = None,
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> DocNode:
    if order_ref is None:
        order_ref = {"value": 0}

    n_rows = len(tbl.rows)
    cols = len(tbl.columns) if n_rows > 0 else 0

    table_text_lines = []
    for row in tbl.rows:
        row_text = " | ".join(_norm_text(cell.text) for cell in row.cells)
        table_text_lines.append(row_text)

    table_node = DocNode(
        type="table",
        uid=uid,
        parent_uid=parent_uid,
        order=_next_order(order_ref),
        path=(uid or "").replace(".", "/"),
        content={
            "rows": n_rows,
            "cols": cols,
            "text": "\n".join(table_text_lines),
            "row_count": n_rows,
            "col_count": cols,
        },
    )

    # skip_map[(r, logical_col)] = True → ô này là phần continue của rowspan, bỏ qua
    skip_map: Dict[tuple, bool] = {}

    # Đọc XML tc elements trực tiếp để tránh python-docx deduplicate merged cells
    xml_rows = _get_xml_tcs(tbl)

    for r_idx, row in enumerate(tbl.rows):
        row_uid = f"{uid}.r{r_idx}" if uid else f"row.{r_idx}"
        # row_cells từ python-docx chỉ dùng để lấy text và tạo _Cell object
        # xml_tcs dùng để đọc span info chính xác
        xml_tcs = xml_rows[r_idx] if r_idx < len(xml_rows) else []

        row_text = " | ".join(_norm_text(c.text) for c in row.cells)

        row_node = DocNode(
            type="row",
            uid=row_uid,
            parent_uid=uid,
            order=_next_order(order_ref),
            path=row_uid.replace(".", "/"),
            content={
                "row_index": r_idx,
                "text": row_text,
                "cell_count": len(xml_tcs),
            },
        )
        table_node.add_child(row_node)

        logical_col = 0
        for c_idx, tc in enumerate(xml_tcs):
            # Tạo _Cell object từ tc element để dùng với _iter_block_items
            cell = _Cell(tc, tbl)
            col_span, is_restart, is_continue = _get_cell_spans(cell)

            # Ô này là phần "continue" của rowspan từ hàng trên — bỏ qua
            if skip_map.get((r_idx, logical_col)):
                logical_col += col_span
                continue

            # Ô vMerge continue không phải restart → bỏ qua luôn
            if is_continue:
                logical_col += col_span
                continue

            row_span = 1
            is_merged = False

            if is_restart:
                # Đếm rowspan thực từ XML dùng xml_col_idx chính xác
                row_span = _count_row_span(tbl, r_idx, logical_col)
                is_merged = True
                # Đánh dấu các slot bên dưới là skip
                for dr in range(1, row_span):
                    for dc in range(col_span):
                        skip_map[(r_idx + dr, logical_col + dc)] = True

            if col_span > 1:
                is_merged = True

            cell_uid = (
                f"{uid}.r{r_idx}.c{logical_col}"
                if uid
                else f"cell.r{r_idx}.c{logical_col}"
            )

            cell_node = _extract_cell(
                cell=cell,
                uid_prefix=cell_uid,
                parent_uid=row_uid,
                order_ref=order_ref,
                row_index=r_idx,
                col_index=logical_col,
                col_span=col_span,
                row_span=row_span,
                is_merged=is_merged,
            )
            row_node.add_child(cell_node)

            logical_col += col_span

    return table_node


def extract_doc_tree(docx_path: str) -> DocNode:
    doc = Document(docx_path)

    order_ref = {"value": 0}

    root = DocNode(
        type="document",
        uid="document",
        parent_uid=None,
        order=0,
        path="document",
        content={
            "source": docx_path,
        },
    )

    i = 0
    for item in _iter_block_items(doc):
        i += 1
        uid = f"n{i}"

        if isinstance(item, Paragraph):
            nodes = _extract_paragraph_nodes(
                item,
                uid=uid,
                parent_uid="document",
                order_ref=order_ref,
            )
            for x in nodes:
                root.add_child(x)
        else:
            root.add_child(
                _extract_table(
                    item,
                    uid=uid,
                    parent_uid="document",
                    order_ref=order_ref,
                )
            )

    return root