"""Matching what a customer said to menu items.

Pure logic, no database and no model. With a menu of this size a
deterministic match is enough, and it can be tested exactly: the same words
always return the same items in the same order.
"""

import unicodedata
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

EXACT = 100
NAME_INSIDE_QUERY = 80
QUERY_INSIDE_NAME = 60
WORDS_IN_COMMON = 40

# Words that carry no information about which item is meant.
_STOP_WORDS = frozenset(
    {
        # English
        "a", "an", "the", "some", "of", "with", "and", "please",
        "i", "want", "would", "like", "can", "get", "have",
        # French
        "un", "une", "des", "le", "la", "les", "du", "de", "d", "au", "aux",
        "et", "avec", "svp", "je", "veux", "voudrais",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Candidate[T]:
    """Something that can be found, and every phrase it is known by."""

    value: T
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class Match[T]:
    value: T
    score: int
    # The customer's words the best phrase did not account for: "large" in
    # "large fries", "oat" in "oat milk shake". Whether a leftover word is a
    # choice the item offers is for the caller to decide.
    leftover: tuple[str, ...] = ()


# Words that say how many or are politeness, never what is wanted. A
# leftover word from this set is never "not offered".
_COUNT_AND_FILLER_TEXT = """
one two three four five six seven eight nine ten eleven twelve dozen half
deux trois quatre cinq six sept huit neuf dix douze demi
order commande piece morceau portion x also aussi just juste then alors
thank merci okay ok yes oui more another encore again for me pour
moi us nous sure great perfect parfait instead plutot actually
"""
_COUNT_AND_FILLER = frozenset(_COUNT_AND_FILLER_TEXT.split())


def normalise(text: str) -> tuple[str, ...]:
    """Reduce text to comparable words.

    Lowercase, punctuation dropped, filler words dropped, accents removed, and
    a trailing plural "s" removed, so "Thé glacé", "the glace" and "Thés
    glacés" all end up comparable.

    Filler words are dropped before accents are removed. Done the other way
    round, "thé" (tea) would turn into the English filler word "the" and
    vanish.
    """
    spaced = "".join(c if c.isalnum() else " " for c in text.lower())
    words = spaced.split()
    # A query made only of filler ("the") is kept, so it matches nothing
    # rather than behaving like an empty query.
    meaningful = [word for word in words if word not in _STOP_WORDS] or words
    return tuple(_singular(_without_accents(word)) for word in meaningful)


def score(query: Sequence[str], phrase: Sequence[str]) -> int:
    """How well one phrase matches the query, from 0 (not at all) to 100 (equal)."""
    if not query or not phrase:
        return 0
    if tuple(query) == tuple(phrase):
        return EXACT
    if _contains(query, phrase):
        # "fries" inside "large fries". A longer phrase is a more specific hit.
        return NAME_INSIDE_QUERY + min(len(phrase), 9)
    if _contains(phrase, query):
        # "burger" inside "veggie burger".
        return QUERY_INSIDE_NAME
    common = len(set(query) & set(phrase))
    return WORDS_IN_COMMON * common // max(len(query), len(phrase))


def search[T](
    query: str, candidates: Iterable[Candidate[T]], limit: int = 5
) -> list[Match[T]]:
    """The best matches, best first. Ties keep the order candidates came in."""
    query_words = normalise(query)
    matches: list[Match[T]] = []
    for candidate in candidates:
        best, best_words = 0, ()
        for phrase in candidate.phrases:
            words = normalise(phrase)
            found = score(query_words, words)
            if found > best:
                best, best_words = found, words
        if best > 0:
            used = set(best_words)
            leftover = tuple(word for word in query_words if word not in used)
            matches.append(Match(candidate.value, best, leftover))
    # sorted() is stable, so equal scores stay in menu order.
    return sorted(matches, key=lambda match: -match.score)[:limit]


def not_offered(leftover: Sequence[str], offered: Collection[str]) -> tuple[str, ...]:
    """The leftover words that name something the item does not have.

    `offered` holds every word that is fine to have left over: the item's own
    choices ("large", "vanilla") and words of other items the customer also
    named ("fries and a cheeseburger"). Counts and filler are always fine.
    """
    return tuple(
        word
        for word in leftover
        if word not in offered and word not in _COUNT_AND_FILLER and not word.isdigit()
    )


def contains_phrase(words: Sequence[str], phrase: Sequence[str]) -> bool:
    """True if `phrase` appears in `words` as a run of whole words."""
    return bool(phrase) and _contains(words, phrase)


def _without_accents(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _contains(words: Sequence[str], phrase: Sequence[str]) -> bool:
    """True if `phrase` appears in `words` as a run of whole words."""
    size = len(phrase)
    return any(
        tuple(words[start : start + size]) == tuple(phrase)
        for start in range(len(words) - size + 1)
    )
