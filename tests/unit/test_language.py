"""Telling English from French by the words alone. No database."""

import pytest

from drivetruagent.enums import Language
from drivetruagent.ordering.language import detect_language


@pytest.mark.parametrize(
    "text",
    [
        "Bonjour, je voudrais une grande frite.",
        "Oui, c'est tout.",
        "Merci!",
        "Un petit café avec du lait",
        "C’est bon, merci",  # noqa: RUF001 (a typographic apostrophe)
        "Est-ce que vous avez des frites?",
    ],
)
def test_french_is_recognised(text: str) -> None:
    assert detect_language(text) is Language.FR


@pytest.mark.parametrize(
    "text",
    [
        "Hi, can I get a cheeseburger please?",
        "Yes, that's all.",
        "Thanks!",
        "I'd like a large fries and a small coke",
        "Do you have anything without dairy?",
    ],
)
def test_english_is_recognised(text: str) -> None:
    assert detect_language(text) is Language.EN


@pytest.mark.parametrize(
    "text",
    ["ok", "Coke", "2 large", "Poutine", "cheeseburger", "", "L2", "no"],
)
def test_too_little_to_tell_is_left_alone(text: str) -> None:
    assert detect_language(text) is None


def test_a_french_menu_word_in_english_does_not_switch_to_french() -> None:
    assert detect_language("Can I get the frites and a drink please") is Language.EN
