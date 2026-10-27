"""Turning a caller's number into one form, E.164 (+14165550123).

Pure logic: no database. A number arrives as a SIP From header from the phone
line ("<sip:+14165550123@pstn.twilio.com>;tag=abc"), as a tel: link, or typed
("(416) 555-0123"). The same caller must come out the same whichever it was,
or their orders would be filed under two numbers.
"""

import re

# What carriers put in place of a withheld number.
_WITHHELD = frozenset(
    {"anonymous", "restricted", "unknown", "unavailable", "private", "blocked"}
)
# The user part of a sip:, sips: or tel: address, up to @ or ; or >.
_URI_USER = re.compile(r"(?:sips?|tel):([^@;>]+)", re.IGNORECASE)
# North American numbers: area code and exchange each start with 2 to 9.
_NANP = re.compile(r"[2-9]\d{2}[2-9]\d{6}")


def normalize_phone(raw: str | None) -> str | None:
    """The number in E.164, or None if it was withheld or isn't a number.

    A number without a country code is taken to be North American, where the
    restaurant is.
    """
    if raw is None:
        return None
    text = raw.strip()
    match = _URI_USER.search(text)
    if match:
        text = match.group(1)
    if text.strip().lower() in _WITHHELD:
        return None

    international = text.strip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if international:
        return _international(digits)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10 and _NANP.fullmatch(digits):
        return "+1" + digits
    return None


def _international(digits: str) -> str | None:
    if digits.startswith("1"):
        national = digits[1:]
        return "+" + digits if _NANP.fullmatch(national) else None
    if 8 <= len(digits) <= 15 and not digits.startswith("0"):
        return "+" + digits
    return None
