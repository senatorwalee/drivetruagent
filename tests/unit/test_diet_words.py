"""Recognising a halal or kosher question. Pure logic, no database."""

import pytest

from drivetruagent.enums import Diet
from drivetruagent.ordering.diet_words import diets_named


@pytest.mark.parametrize(
    ("question", "named"),
    [
        ("Is your chicken halal?", {Diet.HALAL}),
        ("Is the beef zabiha?", {Diet.HALAL}),
        ("HALAL options?", {Diet.HALAL}),
        ("Do you have anything kosher?", {Diet.KOSHER}),
        ("C'est cacher ?", {Diet.KOSHER}),
        ("Est-ce que c'est casher?", {Diet.KOSHER}),
        ("La viande est halal ?", {Diet.HALAL}),
        ("Halal or kosher, either is fine", {Diet.HALAL, Diet.KOSHER}),
        ("A cheeseburger please", set()),
        ("Is the fish fried separately?", set()),
        ("Kosherish", set()),
    ],
)
def test_diets_are_recognised_by_name(question: str, named: set[Diet]) -> None:
    assert diets_named(question) == named
