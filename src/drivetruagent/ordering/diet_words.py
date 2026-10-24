"""Recognising a question about halal or kosher food, from the words alone.

Pure logic: no database, no model.
"""

import re
import unicodedata

from drivetruagent.enums import Diet

_PATTERNS = {
    Diet.HALAL: re.compile(r"\b(halal|hallal|halaal|zabiha|zabihah|dhabiha)\b"),
    Diet.KOSHER: re.compile(r"\b(kosher|cacher|casher|cachere|cashere)\b"),
}


def diets_named(text: str) -> frozenset[Diet]:
    """The diets a question names: "is it halal?", "c'est cacher ?"."""
    plain = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return frozenset(
        diet for diet, pattern in _PATTERNS.items() if pattern.search(plain)
    )
