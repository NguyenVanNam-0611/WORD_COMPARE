from __future__ import annotations

import difflib
import hashlib
from typing import List, Tuple

from src.services.models.block import Block

Opcode = Tuple[str, int, int, int, int]


def _norm_text(s: str) -> str:
    return " ".join((s or "").replace("\u00a0", " ").split()).strip()


def _shape_text(block: Block) -> str:
    """Thu thập toàn bộ text paragraph trong shape, đệ quy vào shape lồng nhau."""
    def _collect(node) -> list:
        parts = []
        for child in (node.children or []):
            if child.type == "paragraph":
                txt = _norm_text(child.content.get("text", ""))
                if txt:
                    parts.append(txt)
            elif child.type == "shape":
                parts.extend(_collect(child))
        return parts

    return " ".join(_collect(block.node))


def _count_text_paragraphs(node) -> int:
    """Đếm số paragraph có text trong shape, đệ quy.
    Dùng để phân biệt 2 shape có cùng text nhưng khác số dòng
    (vd: 1 dòng bị xóa khỏi flow chart).
    """
    count = 0
    for child in (getattr(node, "children", []) or []):
        t = getattr(child, "type", None)
        if t == "paragraph":
            txt = _norm_text((getattr(child, "content", {}) or {}).get("text", ""))
            if txt:
                count += 1
        elif t == "shape":
            count += _count_text_paragraphs(child)
    return count


def _order_bonus(a: Block, b: Block) -> float:
    """Bonus nếu 2 block gần nhau theo order (giảm match nhầm khi nhiều shape rỗng)."""
    ao = a.order or 0
    bo = b.order or 0
    d = abs(ao - bo)
    if d <= 1:
        return 0.25
    if d <= 3:
        return 0.15
    if d <= 8:
        return 0.05
    return 0.0


def _block_similarity(a: Block, b: Block) -> float:
    if a.type != b.type:
        return 0.0

    same_heading = a.heading_ctx == b.heading_ctx

    if a.type == "shape":
        a_text = _shape_text(a)
        b_text = _shape_text(b)

        # cả 2 đều rỗng: chỉ match mạnh khi cùng heading + gần order
        if not a_text and not b_text:
            base = (0.45 if same_heading else 0.05) + _order_bonus(a, b)
            return min(1.0, base)

        # một bên rỗng, một bên có text: coi là "cleared/filled"
        # ưu tiên cùng heading + gần order để emit replace (shape_modified)
        if not a_text or not b_text:
            base = (0.45 if same_heading else 0.0) + _order_bonus(a, b)
            return min(1.0, base)

        # cả 2 có text: so text + bonus heading
        text_ratio = difflib.SequenceMatcher(a=a_text, b=b_text, autojunk=False).ratio()
        heading_bonus = 0.2 if same_heading else 0.0
        return min(1.0, text_ratio + heading_bonus)

    if a.type == "table":
        a_text = (a.signature or "").strip()
        b_text = (b.signature or "").strip()
        text_ratio = difflib.SequenceMatcher(a=a_text, b=b_text, autojunk=False).ratio()
        if same_heading:
            return max(0.5, text_ratio)
        return min(1.0, text_ratio + 0.15)

    heading_bonus = 0.35 if same_heading else 0.15
    a_text = (a.signature or "").strip()
    b_text = (b.signature or "").strip()
    text_ratio = difflib.SequenceMatcher(a=a_text, b=b_text, autojunk=False).ratio()
    return min(1.0, text_ratio + heading_bonus)


def _make_sig(block: Block) -> str:
    heading = block.heading_ctx or ""

    if block.type == "shape":
        txt = _shape_text(block)
        # Shape rỗng: thêm order để SequenceMatcher phân biệt 2 shape rỗng khác vị trí
        if not txt:
            uid = getattr(block.node, "uid", "") or ""
            order = block.order or 0
            return f"shape|{heading}|empty:o{order}:{uid}"
        # FIX: thêm para_count vào sig để 2 shape có cùng text nhưng khác số
        # paragraph (vd: flow chart bị xóa 1 dòng) không bị coi là equal.
        # Trước đây chỉ hash txt → nếu txt join giống nhau (text leaf không đổi
        # nhưng số node thay đổi) thì sig giống → SequenceMatcher emit "equal"
        # → json_builder bỏ qua hoàn toàn, không hiển thị change.
        para_count = _count_text_paragraphs(block.node)
        h = hashlib.md5(txt.encode("utf-8")).hexdigest()[:16]
        return f"shape|{heading}|{h}|p{para_count}"

    return f"{block.type}|{heading}|{block.signature}"


def align_blocks(a_blocks: List[Block], b_blocks: List[Block]) -> List[Opcode]:
    a_signatures = [_make_sig(b) for b in a_blocks]
    b_signatures = [_make_sig(b) for b in b_blocks]

    sm = difflib.SequenceMatcher(a=a_signatures, b=b_signatures, autojunk=False)
    raw_opcodes = sm.get_opcodes()
    refined: List[Opcode] = []

    for tag, i1, i2, j1, j2 in raw_opcodes:
        if tag != "replace":
            refined.append((tag, i1, i2, j1, j2))
            continue

        a_slice = a_blocks[i1:i2]
        b_slice = b_blocks[j1:j2]

        # 1-1 replace
        if len(a_slice) == 1 and len(b_slice) == 1:
            similarity = _block_similarity(a_slice[0], b_slice[0])
            if similarity >= 0.45:
                refined.append(("replace", i1, i2, j1, j2))
            else:
                refined.append(("delete", i1, i2, j1, j1))
                refined.append(("insert", i2, i2, j1, j2))
            continue

        # N-N replace toàn bảng cùng số lượng → pair theo thứ tự
        if (
            len(a_slice) == len(b_slice)
            and all(b.type == "table" for b in a_slice)
            and all(b.type == "table" for b in b_slice)
        ):
            for k in range(len(a_slice)):
                sim = _block_similarity(a_slice[k], b_slice[k])
                if sim >= 0.45:
                    refined.append(("replace", i1 + k, i1 + k + 1, j1 + k, j1 + k + 1))
                else:
                    refined.append(("delete", i1 + k, i1 + k + 1, j1 + k, j1 + k))
                    refined.append(("insert", i1 + k + 1, i1 + k + 1, j1 + k, j1 + k + 1))
            continue

        if len(a_slice) > 0 and len(b_slice) > 0:
            _match_mixed_slice(refined, a_blocks, b_blocks, i1, i2, j1, j2)
            continue

        refined.append((tag, i1, i2, j1, j2))

    return refined


def _match_mixed_slice(
    refined: List[Opcode],
    a_blocks: List[Block],
    b_blocks: List[Block],
    i1: int, i2: int,
    j1: int, j2: int,
) -> None:
    a_slice = a_blocks[i1:i2]
    b_slice = b_blocks[j1:j2]

    THRESHOLD = 0.3

    # Tạo tất cả candidate pairs, sau đó greedy theo sim cao nhất
    candidates: List[Tuple[float, int, int]] = []
    for ki, a in enumerate(a_slice):
        for kj, b in enumerate(b_slice):
            sim = _block_similarity(a, b)
            if sim >= THRESHOLD:
                candidates.append((sim, ki, kj))

    # sort sim giảm dần, tie-breaker theo gần order để ổn định hơn
    def _tie_key(item):
        sim, ki, kj = item
        ao = a_slice[ki].order or 0
        bo = b_slice[kj].order or 0
        return (-sim, abs(ao - bo), ki, kj)

    candidates.sort(key=_tie_key)

    matched_a = set()
    matched_b = set()
    pairs: List[Tuple[int, int]] = []

    for sim, ki, kj in candidates:
        if ki in matched_a or kj in matched_b:
            continue
        matched_a.add(ki)
        matched_b.add(kj)
        pairs.append((ki, kj))

    # emit replace theo thứ tự b để giữ dòng chảy
    pairs.sort(key=lambda x: x[1])
    for ki, kj in pairs:
        refined.append(("replace", i1 + ki, i1 + ki + 1, j1 + kj, j1 + kj + 1))

    # a không match → delete
    for ki in range(len(a_slice)):
        if ki not in matched_a:
            refined.append(("delete", i1 + ki, i1 + ki + 1, j1, j1))

    # b không match → insert
    for kj in range(len(b_slice)):
        if kj not in matched_b:
            refined.append(("insert", i1, i1, j1 + kj, j1 + kj + 1))