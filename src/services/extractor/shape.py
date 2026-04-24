from __future__ import annotations

from typing import List, Optional, Any, Dict
import uuid

from src.services.models.docnode import DocNode
from src.services.utils.hash import image_hash_from_bytes, bytes_to_data_uri, safe_sha256


# Namespace URIs dùng trực tiếp với iter() và tag filter
# BaseOxmlElement.xpath() không hỗ trợ keyword argument namespaces=
_W   = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R   = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A   = "http://schemas.openxmlformats.org/drawingml/2006/main"
_WP  = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _iter_tag(elem, ns: str, local: str):
    return elem.iter(_tag(ns, local))


def _children_tag(elem, ns: str, local: str):
    return [c for c in elem if c.tag == _tag(ns, local)]


def _norm_text(s: str) -> str:
    return " ".join((s or "").replace("\u00a0", " ").split()).strip()


def _emu_to_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _next_order(order_ref: Optional[Dict[str, int]]) -> int:
    if order_ref is None:
        return 0
    order_ref["value"] += 1
    return order_ref["value"]


def _get_all_text(xml_elem) -> str:
    parts = []
    for t in _iter_tag(xml_elem, _W, "t"):
        parts.append(t.text or "")
    return _norm_text("".join(parts))


def _extract_images_from_xml(
    xml_elem,
    part,
    uid_prefix: str,
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> List[DocNode]:
    nodes: List[DocNode] = []

    R_EMBED = _tag(_R, "embed")
    blip_tag = _tag(_A, "blip")
    extent_tag = _tag(_WP, "extent")

    blip_rids = []
    for blip in xml_elem.iter(blip_tag):
        rid = blip.get(R_EMBED)
        if rid:
            blip_rids.append(rid)

    if not blip_rids:
        return nodes

    cx = cy = 0
    for ext in xml_elem.iter(extent_tag):
        cx = _emu_to_int(ext.get("cx"))
        cy = _emu_to_int(ext.get("cy"))
        break

    for i, rid in enumerate(blip_rids, start=1):
        p = part.related_parts.get(rid) if part else None
        if not p:
            continue

        blob = p.blob
        mime = getattr(p, "content_type", None) or "image/png"
        img_uid = f"{uid_prefix}.i{i}"

        nodes.append(
            DocNode(
                type="image",
                uid=img_uid,
                parent_uid=parent_uid,
                order=_next_order(order_ref),
                path=img_uid.replace(".", "/"),
                content={
                    "rid": rid,
                    "hash": image_hash_from_bytes(blob),
                    "sha256": safe_sha256(blob),
                    "mime": mime,
                    "width_emu": cx,
                    "height_emu": cy,
                    "data_uri": bytes_to_data_uri(blob, mime=mime),
                    "image_index": i,
                },
            )
        )

    return nodes


def _parse_p_xml(
    p_xml,
    part,
    uid_prefix: str,
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> List[DocNode]:
    text = _get_all_text(p_xml)

    p_uid = f"{uid_prefix}.p"
    out: List[DocNode] = []

    pnode = DocNode(
        type="paragraph",
        uid=p_uid,
        parent_uid=parent_uid,
        order=_next_order(order_ref),
        path=p_uid.replace(".", "/"),
        content={"text": text},
    )
    out.append(pnode)

    imgs = _extract_images_from_xml(
        p_xml,
        part,
        uid_prefix=f"{uid_prefix}.img",
        parent_uid=p_uid,
        order_ref=order_ref,
    )
    out.extend(imgs)

    return [
        n for n in out
        if not (
            n.type == "paragraph"
            and not n.content.get("text")
            and len(out) == 1
        )
    ]


def _parse_tbl_xml(
    tbl_xml,
    part,
    uid_prefix: str,
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> DocNode:
    rows_xml = _children_tag(tbl_xml, _W, "tr")

    row_count = len(rows_xml)
    col_count = 0
    table_lines: List[str] = []

    for tr in rows_xml:
        tcs = _children_tag(tr, _W, "tc")
        col_count = max(col_count, len(tcs))
        table_lines.append(" | ".join(_get_all_text(tc) for tc in tcs))

    table_uid = f"{uid_prefix}.t"

    tnode = DocNode(
        type="table",
        uid=table_uid,
        parent_uid=parent_uid,
        order=_next_order(order_ref),
        path=table_uid.replace(".", "/"),
        content={
            "rows": row_count,
            "cols": col_count,
            "text": "\n".join(table_lines),
        },
    )

    for r_idx, tr in enumerate(rows_xml):
        row_uid = f"{uid_prefix}.r{r_idx}"
        tcs = _children_tag(tr, _W, "tc")

        rnode = DocNode(
            type="row",
            uid=row_uid,
            parent_uid=table_uid,
            order=_next_order(order_ref),
            path=row_uid.replace(".", "/"),
            content={
                "row_index": r_idx,
                "text": " | ".join(_get_all_text(tc) for tc in tcs),
            },
        )
        tnode.add_child(rnode)

        for c_idx, tc in enumerate(tcs):
            cell_uid = f"{uid_prefix}.r{r_idx}.c{c_idx}"

            cnode = DocNode(
                type="cell",
                uid=cell_uid,
                parent_uid=row_uid,
                order=_next_order(order_ref),
                path=cell_uid.replace(".", "/"),
                content={
                    "row_index": r_idx,
                    "col_index": c_idx,
                    "text": _get_all_text(tc),
                },
            )
            rnode.add_child(cnode)

            children = []
            for child_idx, child in enumerate(tc, start=1):
                if child.tag == _tag(_W, "p"):
                    children.extend(
                        _parse_p_xml(
                            child, part,
                            uid_prefix=f"{cell_uid}.p{child_idx}",
                            parent_uid=cell_uid,
                            order_ref=order_ref,
                        )
                    )
                elif child.tag == _tag(_W, "tbl"):
                    children.append(
                        _parse_tbl_xml(
                            child, part,
                            uid_prefix=f"{cell_uid}.t{child_idx}",
                            parent_uid=cell_uid,
                            order_ref=order_ref,
                        )
                    )

            for ch in children:
                cnode.add_child(ch)

    return tnode


def _parse_txbx_content(
    txbx_xml,
    part,
    uid_prefix: str,
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> DocNode:
    shape = DocNode(
        type="shape",
        uid=uid_prefix,
        parent_uid=parent_uid,
        order=_next_order(order_ref),
        path=uid_prefix.replace(".", "/"),
        content={
            "shape_id": str(uuid.uuid4().hex),
            "shape_type": "textbox",
        },
    )

    children: List[DocNode] = []

    for idx, child in enumerate(txbx_xml, start=1):
        child_uid = f"{uid_prefix}.{idx}"
        if child.tag == _tag(_W, "p"):
            children.extend(
                _parse_p_xml(
                    child, part,
                    uid_prefix=child_uid,
                    parent_uid=uid_prefix,
                    order_ref=order_ref,
                )
            )
        elif child.tag == _tag(_W, "tbl"):
            children.append(
                _parse_tbl_xml(
                    child, part,
                    uid_prefix=child_uid,
                    parent_uid=uid_prefix,
                    order_ref=order_ref,
                )
            )

    shape.content.update({
        "paragraph_count": sum(1 for x in children if x.type == "paragraph"),
        "image_count":     sum(1 for x in children if x.type == "image"),
        "table_count":     sum(1 for x in children if x.type == "table"),
    })

    for ch in children:
        shape.add_child(ch)

    return shape


_WPS_TXBX = _tag(_WPS, "txbx")

def _collect_txbx_elements(p_xml) -> List:
    results = []
    seen = set()

    for txbx in p_xml.iter(_WPS_TXBX):
        txbx_content = None
        for child in txbx.iter(_tag(_W, "txbxContent")):
            txbx_content = child
            break

        if txbx_content is None:
            continue

        eid = id(txbx_content)
        if eid not in seen:
            seen.add(eid)
            results.append(txbx_content)

    return results


def extract_shapes_from_paragraph(
    paragraph,
    uid_prefix: str = "shp",
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> List[DocNode]:
    out: List[DocNode] = []
    part = getattr(paragraph, "part", None)
    p_xml = paragraph._p

    for idx, txbx_xml in enumerate(_collect_txbx_elements(p_xml), start=1):
        shape_uid = f"{uid_prefix}.{idx}"
        out.append(
            _parse_txbx_content(
                txbx_xml, part,
                uid_prefix=shape_uid,
                parent_uid=parent_uid,
                order_ref=order_ref,
            )
        )

    return out