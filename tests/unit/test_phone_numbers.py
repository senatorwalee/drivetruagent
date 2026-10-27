"""Caller numbers in one form. Pure logic, no database."""

import pytest

from drivetruagent.ordering.phone_numbers import normalize_phone


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # From the phone line
        ("<sip:+14165550123@pstn.twilio.com>;tag=abc123", "+14165550123"),
        ("sip:+14165550123@sip.example.com", "+14165550123"),
        ('"Jane" <sips:+14165550123@example.com>', "+14165550123"),
        ("sip:14165550123@example.com;user=phone", "+14165550123"),
        ("tel:+1-416-555-0123", "+14165550123"),
        # Typed
        ("(416) 555-0123", "+14165550123"),
        ("416.555.0123", "+14165550123"),
        ("1 416 555 0123", "+14165550123"),
        ("+1 416 555 0123", "+14165550123"),
        # Outside North America
        ("+44 20 7946 0958", "+442079460958"),
        ("sip:+33612345678@example.com", "+33612345678"),
    ],
)
def test_every_form_of_a_number_comes_out_the_same(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "sip:anonymous@anonymous.invalid",
        "<sip:Restricted@example.com>",
        "unknown",
        "555-0123",  # too short
        "(116) 555-0123",  # no area code starts with 1
        "(416) 155-0123",  # nor an exchange
        "+1 416 555 012",
        "+0 123 4567 8901",
        "+1234",
        "pizza",
    ],
)
def test_a_withheld_or_impossible_number_is_none(raw: str | None) -> None:
    assert normalize_phone(raw) is None
