"""Receipt photos: `<photos dir>/<receipt id>/<seq>.webp`, served only by the app.

A photo is stored with its EXIF orientation applied, at most MAX_SIDE px on the long side
(never upscaled), WebP quality QUALITY (~300 KB), and kept for good so a receipt can be
read again with a better prompt or model.
"""

import io
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 2000
QUALITY = 80
# a phone photo is ~12-50 MP; refuse anything absurd before decoding it
Image.MAX_IMAGE_PIXELS = 120_000_000


class NotAnImage(ValueError):
    pass


@dataclass(frozen=True)
class Photo:
    data: bytes
    width: int
    height: int


def process(data: bytes) -> Photo:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            im.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise NotAnImage(str(exc)) from exc
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, (255, 255, 255))
        flat.paste(im, mask=im.getchannel("A"))
        im = flat
    elif im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)  # shrinks only
    out = io.BytesIO()
    im.save(out, "WEBP", quality=QUALITY, method=4)
    return Photo(out.getvalue(), im.width, im.height)


def store(root: Path, receipt_id: int, seq: int, photo: Photo) -> str:
    """Write the photo; returns its path relative to `root`."""
    rel = f"{receipt_id}/{seq}.webp"
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(photo.data)
    tmp.replace(path)
    return rel


def remove(root: Path, receipt_id: int) -> None:
    """Delete all photos of a receipt."""
    shutil.rmtree(Path(root) / str(receipt_id), ignore_errors=True)
