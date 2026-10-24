"""Matching what a customer said to menu items. Pure logic, no database.

The candidates are built from the real menu file, so these tests describe how
search behaves on the menu that actually ships.
"""

from pathlib import Path

import pytest

from drivetruagent.ordering.search import (
    Candidate,
    contains_phrase,
    normalise,
    not_offered,
    score,
    search,
)
from drivetruagent.seed import load_menu

MENU_PATH = Path(__file__).resolve().parents[2] / "data" / "menu.json"


def menu_candidates() -> list[Candidate[str]]:
    """One candidate per item, known by its names, aliases and code."""
    return [
        Candidate(
            value=item.code,
            phrases=(
                item.name.en,
                item.name.fr,
                *(alias for aliases in item.aliases.values() for alias in aliases),
                item.code.replace("_", " "),
            ),
        )
        for category in load_menu(MENU_PATH).categories
        for item in category.items
    ]


CANDIDATES = menu_candidates()


def found(query: str, limit: int = 5) -> list[str]:
    return [match.value for match in search(query, CANDIDATES, limit)]


def scores(query: str) -> dict[str, int]:
    return {match.value: match.score for match in search(query, CANDIDATES, 20)}


# --- Normalising ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fries", ("frie",)),
        ("  LARGE   fries!! ", ("large", "frie")),
        ("Thé glacé", ("the", "glace")),
        ("Thés glacés", ("the", "glace")),
        ("Rondelles d'oignon", ("rondelle", "oignon")),
        ("a coke, please", ("coke",)),
        ("je voudrais des frites svp", ("frite",)),
        ("Lemon-lime soda", ("lemon", "lime", "soda")),
        ("", ()),
    ],
)
def test_normalise(text: str, expected: tuple[str, ...]) -> None:
    assert normalise(text) == expected


def test_normalise_keeps_a_query_made_only_of_filler_words() -> None:
    """Otherwise "the" would become an empty query."""
    assert normalise("the") == ("the",)


# --- Scoring one phrase -----------------------------------------------------


def test_equal_phrases_score_100() -> None:
    assert score(("frie",), ("frie",)) == 100


def test_name_inside_the_query_scores_in_the_80s() -> None:
    assert score(("large", "frie"), ("frie",)) == 81


def test_a_longer_name_inside_the_query_scores_higher() -> None:
    query = ("large", "ice", "cream", "cone")

    assert score(query, ("ice", "cream", "cone")) > score(query, ("cone",))


def test_query_inside_the_name_scores_60() -> None:
    assert score(("burger",), ("veggie", "burger")) == 60


def test_words_must_be_adjacent_to_count_as_inside() -> None:
    # "chocolate cookie" is not a run of words in "chocolate chip cookie".
    assert score(("chocolate", "cookie"), ("chocolate", "chip", "cookie")) < 60


def test_some_words_in_common_score_below_60() -> None:
    assert 0 < score(("apple", "juice"), ("orange", "juice")) < 60


def test_nothing_in_common_scores_0() -> None:
    assert score(("lobster",), ("frie",)) == 0


def test_empty_query_scores_0() -> None:
    assert score((), ("frie",)) == 0


# --- Searching the real menu ------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("fries", "fries"),
        ("Fries", "fries"),
        ("large fries", "fries"),
        ("a large curly fries please", "fries"),
        ("chips", "fries"),
        ("coke", "cola"),
        ("a coke please", "cola"),
        ("sprite", "lemon_lime_soda"),
        ("oj", "orange_juice"),
        ("nuggets", "chicken_nuggets"),
        ("cookies", "cookie"),
        ("water", "bottled_water"),
        ("shake", "milkshake"),
        ("ice cream", "soft_serve_cone"),
        ("double cheeseburger", "double_cheeseburger"),
        ("cheeseburger", "cheeseburger"),
        ("veggie burger", "veggie_burger"),
    ],
)
def test_english_phrases_find_the_right_item_first(query: str, expected: str) -> None:
    assert found(query)[0] == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("frites", "fries"),
        ("des frites", "fries"),
        ("thé glacé", "iced_tea"),
        ("the glace", "iced_tea"),
        ("lait frappé", "milkshake"),
        ("jus d'orange", "orange_juice"),
        ("croquettes de poulet", "chicken_nuggets"),
        ("rondelles d'oignon", "onion_rings"),
        ("un café", "coffee"),
        ("bouteille d'eau", "bottled_water"),
        ("biscuit", "cookie"),
        ("tarte aux pommes", "apple_pie"),
    ],
)
def test_french_phrases_find_the_right_item_first(query: str, expected: str) -> None:
    assert found(query)[0] == expected


def test_mixed_languages_still_match() -> None:
    assert found("un cheeseburger")[0] == "cheeseburger"


def test_a_vague_word_returns_every_candidate_with_the_same_score() -> None:
    """ "Burger" could be any of six; the agent has to ask which."""
    result = scores("burger")

    assert set(result) == {
        "classic_burger",
        "cheeseburger",
        "double_cheeseburger",
        "chicken_burger",
        "veggie_burger",
        "fish_burger",
    }
    assert set(result.values()) == {100}


def test_ties_keep_menu_order() -> None:
    assert found("burger") == [
        "classic_burger",
        "cheeseburger",
        "double_cheeseburger",
        "chicken_burger",
        "veggie_burger",
    ]


def test_an_exact_match_outranks_a_partial_one() -> None:
    result = scores("cheeseburger")

    assert result["cheeseburger"] == 100
    assert result["double_cheeseburger"] < 100


def test_the_more_specific_item_wins() -> None:
    result = scores("double cheeseburger")

    assert result["double_cheeseburger"] > result["cheeseburger"]


def test_ice_cream_prefers_the_cone_but_also_offers_the_sundae() -> None:
    result = found("ice cream")

    assert result[0] == "soft_serve_cone"
    assert "sundae" in result


@pytest.mark.parametrize("query", ["lobster", "pizza", "???", ""])
def test_nothing_on_the_menu_returns_nothing(query: str) -> None:
    assert found(query) == []


def test_limit_is_respected() -> None:
    assert len(found("burger", limit=2)) == 2


def test_results_are_sorted_best_first() -> None:
    values = [match.score for match in search("cheeseburger", CANDIDATES, 20)]

    assert values == sorted(values, reverse=True)


def test_search_is_deterministic() -> None:
    assert found("chocolate") == found("chocolate")


# --- What the search couldn't account for -----------------------------------


def test_a_match_reports_the_words_its_phrase_did_not_cover() -> None:
    (found,) = search("oat milk shake", [Candidate("milkshake", ("milk shake",))])

    assert found.leftover == ("oat",)


def test_an_exact_match_has_nothing_left_over() -> None:
    (found,) = search("milk shake", [Candidate("milkshake", ("milk shake",))])

    assert found.leftover == ()


def test_choices_counts_and_filler_are_never_not_offered() -> None:
    leftover = ("two", "large", "oat", "2", "actually")

    assert not_offered(leftover, offered={"large", "small"}) == ("oat",)


@pytest.mark.parametrize(
    ("said", "choice"),
    [
        ("grande", "grand"),
        ("grandes", "grand"),
        ("petite", "petit"),
        ("moyenne", "moyen"),
        ("épicée", "épicé"),
        ("epicee", "épicé"),
    ],
)
def test_a_french_choice_is_offered_in_either_gender(said: str, choice: str) -> None:
    assert not_offered((said,), offered={choice}) == ()


def test_gender_folding_does_not_let_other_words_through() -> None:
    assert not_offered(("grande", "glacee"), offered={"petit", "grand"}) == ("glacee",)


def test_a_phrase_counts_only_as_whole_words_in_order() -> None:
    assert contains_phrase(("medium", "fry", "cheeseburger"), ("cheeseburger",))
    assert not contains_phrase(("iced", "coffee"), ("iced", "tea"))
    assert not contains_phrase(("anything",), ())
