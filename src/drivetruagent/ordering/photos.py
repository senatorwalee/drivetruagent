"""What a menu photo may be: its type, told from its bytes, and its size.

The type a browser claims in its Content-Type header is not trusted. The first
few bytes of every supported format are fixed, so they are what is checked.
"""

import hashlib
from dataclasses import dataclass

from drivetruagent.ordering.errors import PhotoNotAccepted

# The browser shrinks photos before sending them, so real uploads are far
# smaller. This is the backstop for one that was not shrunk.
MAX_PHOTO_BYTES = 5 * 1024 * 1024

SUPPORTED_TYPES = ("image/jpeg", "image/png", "image/webp")


@dataclass(frozen=True)
class CheckedPhoto:
    content_type: str
    data: bytes
    sha256: str


def sniff_type(data: bytes) -> str | None:
    """The image type the bytes start with, or None if it is not supported."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def check_photo(data: bytes) -> CheckedPhoto:
    """Accept a photo or raise PhotoNotAccepted, saying why."""
    if not data:
        raise PhotoNotAccepted("empty", "The photo is empty.")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoNotAccepted(
            "too_large",
            f"The photo is larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB.",
        )
    content_type = sniff_type(data)
    if content_type is None:
        raise PhotoNotAccepted(
            "unsupported_type", "Only JPEG, PNG and WebP photos are accepted."
        )
    return CheckedPhoto(
        content_type=content_type, data=data, sha256=hashlib.sha256(data).hexdigest()
    )
