"""Shared test helpers: images, a recording fake reader."""

import io

from PIL import Image

from backend import fakes, photos


def image_bytes(width=60, height=90, color=(240, 240, 235), fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, fmt)
    return buf.getvalue()


def photo(width=60, height=90) -> photos.Photo:
    return photos.process(image_bytes(width, height))


class FakeReader:
    """Returns the queued outcomes in order (the last one repeats) and records calls."""

    def __init__(self, *outcomes, error: Exception | None = None):
        self.outcomes = list(outcomes) or [fakes.Outcome()]
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, images, *, categories, spent_this_month_usd, start_at=0,
                 qr_total_cents=None):
        self.calls.append({"photos": images, "categories": categories,
                           "spent": spent_this_month_usd, "start_at": start_at,
                           "qr_total_cents": qr_total_cents})
        if self.error:
            raise self.error
        return self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
