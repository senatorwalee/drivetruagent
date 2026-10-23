"""Which menu photos are accepted, told from their bytes. No database needed."""

import pytest

from drivetruagent.ordering.errors import PhotoNotAccepted
from drivetruagent.ordering.photos import MAX_PHOTO_BYTES, check_photo, sniff_type

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 8


@pytest.mark.parametrize(
    ("data", "expected"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
)
def test_supported_types_are_told_from_their_first_bytes(
    data: bytes, expected: str
) -> None:
    assert sniff_type(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"GIF89a" + b"\x00" * 16,
        b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        b"%PDF-1.7",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",
    ],
)
def test_other_files_are_not_images_we_take(data: bytes) -> None:
    assert sniff_type(data) is None
    with pytest.raises(PhotoNotAccepted) as raised:
        check_photo(data)
    assert raised.value.details == {"reason": "unsupported_type"}


def test_a_photo_is_hashed_so_its_url_can_change_with_it() -> None:
    first = check_photo(JPEG)
    second = check_photo(JPEG + b"\x01")

    assert first.content_type == "image/jpeg"
    assert len(first.sha256) == 64
    assert first.sha256 != second.sha256


def test_empty_and_oversized_photos_are_refused() -> None:
    with pytest.raises(PhotoNotAccepted) as empty:
        check_photo(b"")
    with pytest.raises(PhotoNotAccepted) as large:
        check_photo(JPEG + b"\x00" * MAX_PHOTO_BYTES)

    assert empty.value.details == {"reason": "empty"}
    assert large.value.details == {"reason": "too_large"}
