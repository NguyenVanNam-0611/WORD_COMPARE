from __future__ import annotations

from typing import Dict, Any, List

from src.services.extractor.document import extract_doc_tree
from src.services.block.block_builder import build_blocks
from src.services.align.sequence_align import align_blocks
from src.services.serializer.json_builder import build_ui_json
from src.services.snapshot.pdf_renderer import docx_to_pdf_cached

_DISPLAY_TYPE_MAP = {
    "paragraph_modified": "paragraph",
    "paragraph_inserted": "paragraph",
    "paragraph_deleted": "paragraph",
    "heading_modified": "heading",
    "heading_inserted": "heading",
    "heading_deleted": "heading",
    "table_modified": "table",
    "table_inserted": "table",
    "table_deleted": "table",
    "image_modified": "image",
    "image_inserted": "image",
    "image_deleted": "image",
    "image_added": "image",
    "shape_modified": "shape",
    "shape_inserted": "shape",
    "shape_deleted": "shape",
}


class DiffService:
    def _sort_section_changes(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for section in sections:
            section["changes"].sort(
                key=lambda x: (x.get("order", 0), x.get("id", 0))
            )
        return sections

    def _normalize_section(self, section: Dict[str, Any]) -> Dict[str, Any]:
        heading = section.get("heading") or "(No heading)"
        changes = section.get("changes", [])

        normalized_changes = []
        for ch in changes:
            ch_type = ch.get("type", "")

            order = ch.get("order")
            if not order:
                left = ch.get("left") or {}
                right = ch.get("right") or {}
                candidates = [
                    left.get("order") if isinstance(left, dict) else None,
                    right.get("order") if isinstance(right, dict) else None,
                ]
                order = min((x for x in candidates if x is not None), default=0)

            display_type = (
                _DISPLAY_TYPE_MAP.get(ch_type)
                or ch.get("display_type")
                or ch_type
                    .replace("_modified", "")
                    .replace("_inserted", "")
                    .replace("_deleted", "")
            )

            normalized_changes.append({
                **ch,
                "heading": heading,
                "order": order,
                "display_type": display_type,
            })

        return {
            "heading": heading,
            "changes": normalized_changes,
        }

    def compare(self, original_path: str, modified_path: str) -> Dict[str, Any]:
        original_root = extract_doc_tree(original_path)
        modified_root = extract_doc_tree(modified_path)

        original_blocks = build_blocks(original_root)
        modified_blocks = build_blocks(modified_root)

        # Convert sang PDF để render snapshot (fix: thiếu ở phiên bản cũ)
        original_pdf = docx_to_pdf_cached(original_path)
        modified_pdf = docx_to_pdf_cached(modified_path)

        opcodes = align_blocks(original_blocks, modified_blocks)

        result = build_ui_json(
            original_blocks,
            modified_blocks,
            opcodes,
            original_pdf=original_pdf,
            modified_pdf=modified_pdf,
        )

        sections = result.get("sections", [])
        normalized_sections = [
            self._normalize_section(section)
            for section in sections
        ]
        normalized_sections = self._sort_section_changes(normalized_sections)

        total_changes = sum(
            len(s.get("changes", []))
            for s in normalized_sections
        )

        return {
            "original_file": original_path,
            "modified_file": modified_path,
            "total_sections": len(normalized_sections),
            "total_changes": total_changes,
            "sections": normalized_sections,
        }