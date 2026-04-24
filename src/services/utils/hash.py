from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from typing import Tuple, Optional

from PIL import Image


def bytes_to_data_uri(img_bytes: bytes, mime: str = "image/png") -> str:
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def image_hash_from_bytes(img_bytes: bytes, size: Tuple[int, int] = (64, 64)) -> Optional[str]:
    try:
        im = Image.open(BytesIO(img_bytes))
        im = im.convert("RGB")
        im = im.resize(size)
        raw = im.tobytes()
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return None


def safe_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest() 