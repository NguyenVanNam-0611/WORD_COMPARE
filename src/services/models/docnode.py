from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DocNode:
    type: str
    content: Dict[str, Any] = field(default_factory=dict)
    children: List["DocNode"] = field(default_factory=list)

    uid: Optional[str] = None
    parent_uid: Optional[str] = None
    order: int = 0
    path: Optional[str] = None

    parent: Optional["DocNode"] = field(default=None, repr=False)

    def add_child(self, child: "DocNode") -> None:
        child.parent = self
        child.parent_uid = self.uid
        self.children.append(child)

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def walk(self) -> List["DocNode"]:
        nodes = [self]
        for c in self.children:
            nodes.extend(c.walk())
        return nodes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "parent_uid": self.parent_uid,
            "order": self.order,
            "path": self.path,
            "type": self.type,
            "content": self.content,
            "children": [c.to_dict() for c in self.children],
        }

    def __repr__(self) -> str:
        return (
            f"DocNode("
            f"type={self.type}, "
            f"uid={self.uid}, "
            f"order={self.order}, "
            f"children={len(self.children)}"
            f")"
        )