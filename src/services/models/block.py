from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.services.models.docnode import DocNode


@dataclass
class Block:
    type: str
    node: DocNode
    signature: str

    heading_ctx: Optional[str] = None
    heading_level: int = 0

    uid: Optional[str] = None
    parent_uid: Optional[str] = None
    path: Optional[str] = None

    order: int = 0

    is_heading: bool = False
    is_table: bool = False
    is_paragraph: bool = False
    is_image: bool = False
    is_shape: bool = False

    preview_text: str = ""

    def __post_init__(self) -> None:
        self.uid = self.uid or getattr(self.node, "uid", None)
        self.parent_uid = self.parent_uid or getattr(self.node, "parent_uid", None)
        self.path = self.path or getattr(self.node, "path", None)
        self.order = self.order or getattr(self.node, "order", 0)

        self.is_heading = self.type == "heading"
        self.is_table = self.type == "table"
        self.is_paragraph = self.type == "paragraph"
        self.is_image = self.type == "image"
        self.is_shape = self.type == "shape"

        text = ""

        if self.type in ["heading", "paragraph", "cell", "row"]:
            text = self.node.content.get("text", "") or ""

        elif self.type == "table":
            text = self.node.content.get("text", "") or ""

        elif self.type == "image":
            text = "[IMAGE]"

        elif self.type == "shape":
            text = "[SHAPE]"

        self.preview_text = text[:200].strip()

    def __repr__(self) -> str:
        return (
            f"Block("
            f"type={self.type}, "
            f"uid={self.uid}, "
            f"order={self.order}, "
            f"heading={self.heading_ctx}, "
            f"signature={self.signature}"
            f")"
        )