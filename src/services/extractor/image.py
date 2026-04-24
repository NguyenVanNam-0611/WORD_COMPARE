from __future__ import annotations

from typing import List, Optional, Any, Dict
from docx.text.paragraph import Paragraph

from src.services.models.docnode import DocNode
from src.services.utils.hash import image_hash_from_bytes, bytes_to_data_uri, safe_sha256


_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


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


def extract_inline_images(
    par: Paragraph,
    uid_prefix: str = "img",
    parent_uid: Optional[str] = None,
    order_ref: Optional[Dict[str, int]] = None,
) -> List[DocNode]:
    nodes: List[DocNode] = []
    run_idx = 0

    for run in par.runs:
        run_idx += 1
        r = run._r
        if r is None:
            continue

        blips = r.xpath(".//a:blip/@r:embed")
        if not blips:
            continue

        ext = r.xpath(".//wp:extent")
        cx = cy = 0
        if ext:
            cx = _emu_to_int(ext[0].get("cx"))
            cy = _emu_to_int(ext[0].get("cy"))

        for j, rid in enumerate(blips):
            part = par.part.related_parts.get(rid)
            if not part:
                continue

            blob = part.blob
            h = image_hash_from_bytes(blob)
            sha = safe_sha256(blob)

            mime = getattr(part, "content_type", None) or "image/png"
            data_uri = bytes_to_data_uri(blob, mime=mime)

            img_uid = f"{uid_prefix}.r{run_idx}.{j+1}"

            nodes.append(
                DocNode(
                    type="image",
                    uid=img_uid,
                    parent_uid=parent_uid,
                    order=_next_order(order_ref),
                    path=img_uid.replace(".", "/"),
                    content={
                        "rid": rid,
                        "hash": h,
                        "sha256": sha,
                        "mime": mime,
                        "width_emu": cx,
                        "height_emu": cy,
                        "data_uri": data_uri,
                        "run_index": run_idx,
                        "image_index": j + 1,
                    },
                )
            )

    return nodes