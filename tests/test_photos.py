"""Photo store (backend/photos.py): orientation, size, WebP, paths."""

import io

import pytest
from PIL import Image

from backend import photos


def jpeg(width, height, orientation=None, color=(200, 30, 30)) -> bytes:
    im = Image.new("RGB", (width, height), color)
    # mark the top-left corner so the rotation can be checked
    for x in range(min(40, width)):
        for y in range(min(20, height)):
            im.putpixel((x, y), (0, 0, 255))
    buf = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    im.save(buf, "JPEG", exif=exif.tobytes(), quality=95)
    return buf.getvalue()


def opened(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def test_large_photo_is_scaled_to_2000_on_the_long_side_as_webp():
    out = photos.process(jpeg(4000, 3000))

    im = opened(out.data)
    assert im.format == "WEBP"
    assert im.size == (2000, 1500)
    assert (out.width, out.height) == (2000, 1500)


def test_small_photo_is_never_upscaled():
    out = photos.process(jpeg(800, 1200))
    assert opened(out.data).size == (800, 1200)


def test_exif_orientation_is_applied():
    # orientation 6: the camera held upright, the pixels stored rotated 90° CCW
    out = photos.process(jpeg(300, 200, orientation=6))

    im = opened(out.data).convert("RGB")
    assert im.size == (200, 300)
    # the stored top-left marker ends up at the top-right after the rotation
    assert im.getpixel((195, 5))[2] > 150
    assert im.getpixel((5, 5))[0] > 150


def test_not_an_image_is_refused():
    with pytest.raises(photos.NotAnImage):
        photos.process(b"definitely not a picture")


def test_png_with_transparency_is_flattened():
    im = Image.new("RGBA", (50, 40), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    assert opened(photos.process(buf.getvalue()).data).mode == "RGB"


def test_store_writes_seq_webp_under_the_receipt_dir_and_remove_deletes_it(tmp_path):
    out = photos.process(jpeg(100, 100))
    rel = photos.store(tmp_path, 7, 2, out)

    assert rel == "7/2.webp"
    assert (tmp_path / "7" / "2.webp").read_bytes() == out.data

    photos.remove(tmp_path, 7)
    assert not (tmp_path / "7").exists()
    photos.remove(tmp_path, 7)  # nothing there: no error
