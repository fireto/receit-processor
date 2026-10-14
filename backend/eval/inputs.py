"""Sample receipts: discovery, grouping of multi-photo receipts, photo preparation.

Naming convention in the samples directory (git-ignored):
- `name.jpg`                  a one-photo receipt
- `name.1.jpg`, `name.2.jpg`  one receipt in several photos, top to bottom
- `name.expected.json`        the expected reading (optional); a bad photo has
                              `{"quality": ["blurry"]}` (other fields optional)
Extensions: jpg, jpeg, png, webp, heic/heif (needs pillow-heif).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PHOTO_RE = re.compile(r"^(?P<name>.+?)(?:\.(?P<seq>\d+))?\.(?:jpe?g|png|webp|heic|heif)$",
                      re.IGNORECASE)
MAX_SIDE = 2000
WEBP_QUALITY = 80


@dataclass
class Sample:
    name: str
    photos: list[Path] = field(default_factory=list)
    expected: dict | None = None


def discover(directory: Path) -> list[Sample]:
    groups: dict[str, list[tuple[int, Path]]] = {}
    for path in sorted(Path(directory).iterdir()):
        m = PHOTO_RE.match(path.name)
        if not m or not path.is_file():
            continue
        groups.setdefault(m["name"], []).append((int(m["seq"] or 0), path))
    samples = []
    for name in sorted(groups):
        expected_path = Path(directory) / f"{name}.expected.json"
        expected = (json.loads(expected_path.read_text(encoding="utf-8"))
                    if expected_path.exists() else None)
        photos = [p for _, p in sorted(groups[name])]
        samples.append(Sample(name=name, photos=photos, expected=expected))
    return samples


def prepare_photo(path: Path) -> bytes:
    """What the app stores: EXIF orientation applied, long side ≤ 2000 px, WebP q80."""
    from PIL import Image, ImageOps

    if path.suffix.lower() in (".heic", ".heif"):
        from pillow_heif import register_heif_opener  # optional dependency

        register_heif_opener()
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=WEBP_QUALITY)
    return buf.getvalue()
