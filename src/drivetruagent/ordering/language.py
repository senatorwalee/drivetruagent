"""Which language a customer is speaking, from their words alone.

Pure logic. English and French only, and only when it is clear: a message
like "ok", "Coke" or "2 large" says nothing, and the session keeps whatever
language it had. Getting this in code saves the model a round trip on the
first turn and whenever the customer switches.
"""

import re
import unicodedata

from drivetruagent.enums import Language

# Words that mark one language and are rare in the other, after accents are
# removed. Strong words settle it alone (2); the others support (1).
_FRENCH_STRONG = """
bonjour bonsoir merci oui voudrais aimerais plait svp c'est j'aimerais est-ce je
"""
_FRENCH_WEAK = """
avec sans aussi petit petite grand grande moyen moyenne une des du pour tout non
frites boisson trio
"""
_ENGLISH_STRONG = """
hello hi please thanks thank yes would i'd i'll that's want
"""
_ENGLISH_WEAK = """
like can could with without and the get have all small medium large also no
drink combo
"""


def _weights(strong: str, weak: str) -> dict[str, int]:
    return {**dict.fromkeys(weak.split(), 1), **dict.fromkeys(strong.split(), 2)}


_FRENCH = _weights(_FRENCH_STRONG, _FRENCH_WEAK)
_ENGLISH = _weights(_ENGLISH_STRONG, _ENGLISH_WEAK)
_ACCENTED = re.compile("[àâçèéêîôùû]")
_WORD = re.compile(r"[a-z]+(?:['-][a-z]+)*")
# A typographic apostrophe, as phones type it.
_CURLY_APOSTROPHE = chr(0x2019)
# How far ahead one language must be.
_MARGIN = 2


def detect_language(text: str) -> Language | None:
    """English or French when the words make it clear, otherwise None."""
    lowered = text.lower()
    french = 2 if _ACCENTED.search(lowered) else 0
    english = 0
    plain = _strip_accents(lowered).replace(_CURLY_APOSTROPHE, "'")
    for word in _WORD.findall(plain):
        french += _FRENCH.get(word, 0)
        english += _ENGLISH.get(word, 0)
    if french - english >= _MARGIN:
        return Language.FR
    if english - french >= _MARGIN:
        return Language.EN
    return None


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
