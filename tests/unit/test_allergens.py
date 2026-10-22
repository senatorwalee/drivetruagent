"""What an item, as configured, contains. No database."""

import uuid
from collections.abc import Collection

import pytest

from drivetruagent.enums import Allergen, AllergenLevel
from drivetruagent.ordering.allergens import (
    Declaration,
    OptionAdds,
    allergens_avoided,
    allergens_named,
    assess,
    is_about_allergens,
)

CONTAINS = AllergenLevel.CONTAINS
MAY = AllergenLevel.MAY_CONTAIN
CHEESE = uuid.uuid4()
BACON = uuid.uuid4()
RANCH = uuid.uuid4()
GRILL = {"en": "Same grill as cheese.", "fr": "Même gril que le fromage."}

# A classic burger: the bun and sauce, a shared grill, and cheese on request.
BURGER = [
    Declaration(Allergen.WHEAT, CONTAINS),
    Declaration(Allergen.SESAME, CONTAINS),
    Declaration(Allergen.EGG, CONTAINS),
    Declaration(Allergen.MILK, MAY, note=GRILL),
    Declaration(Allergen.MILK, CONTAINS, CHEESE),
]


def summary(
    declarations: list[Declaration],
    chosen: Collection[uuid.UUID] = (),
    asked: Collection[Allergen] | None = None,
) -> list[tuple[Allergen, AllergenLevel, tuple[uuid.UUID | None, ...]]]:
    result = assess(declarations, chosen, asked)
    return [(f.allergen, f.level, f.sources) for f in result.found]


def test_with_nothing_chosen_only_the_item_itself_counts() -> None:
    assert summary(BURGER) == [
        (Allergen.SESAME, CONTAINS, (None,)),
        (Allergen.MILK, MAY, (None,)),
        (Allergen.EGG, CONTAINS, (None,)),
        (Allergen.WHEAT, CONTAINS, (None,)),
    ]


def test_allergens_come_in_a_fixed_order_whatever_the_declarations_order() -> None:
    assert summary(BURGER) == summary(list(reversed(BURGER)))


def test_a_chosen_option_adds_its_allergens() -> None:
    found = assess(BURGER, {CHEESE}, [Allergen.MILK]).found

    assert [(f.allergen, f.level, f.sources) for f in found] == [
        (Allergen.MILK, CONTAINS, (CHEESE,))
    ]


def test_contains_beats_may_contain_and_drops_its_reason() -> None:
    (milk,) = assess(BURGER, {CHEESE}, [Allergen.MILK]).found

    assert milk.level is CONTAINS
    assert milk.notes == ()


def test_may_contain_keeps_its_reason() -> None:
    (milk,) = assess(BURGER, set(), [Allergen.MILK]).may_contain

    assert milk.notes == (GRILL,)


def test_several_sources_at_the_same_level_are_all_named() -> None:
    declarations = [
        Declaration(Allergen.MILK, CONTAINS, CHEESE),
        Declaration(Allergen.MILK, CONTAINS, RANCH),
    ]

    assert summary(declarations, {CHEESE, RANCH}) == [
        (Allergen.MILK, CONTAINS, (CHEESE, RANCH))
    ]


def test_asking_about_allergens_limits_the_answer_to_them() -> None:
    result = assess(BURGER, set(), [Allergen.SESAME, Allergen.PEANUT])

    assert [f.allergen for f in result.found] == [Allergen.SESAME]
    assert result.not_listed == (Allergen.PEANUT,)


def test_with_nothing_asked_nothing_is_called_not_listed() -> None:
    assert assess(BURGER, set()).not_listed == ()


def test_an_item_with_nothing_declared_lists_everything_asked_as_not_listed() -> None:
    result = assess([], set(), [Allergen.MILK, Allergen.EGG])

    assert result.found == ()
    assert result.not_listed == (Allergen.MILK, Allergen.EGG)


def test_an_unchosen_option_that_adds_an_allergen_is_pointed_out() -> None:
    declarations = [
        Declaration(Allergen.WHEAT, CONTAINS),
        Declaration(Allergen.MILK, CONTAINS, CHEESE),
    ]

    assert assess(declarations, set()).options_that_add == (
        OptionAdds(CHEESE, (Declaration(Allergen.MILK, CONTAINS, CHEESE),)),
    )


def test_an_option_that_turns_may_contain_into_contains_is_pointed_out() -> None:
    """ "No milk, unless you add cheese": the grill alone is only a "may"."""
    adds = assess(BURGER, set(), [Allergen.MILK]).options_that_add

    assert [a.option_id for a in adds] == [CHEESE]


def test_an_option_adding_nothing_new_is_not_pointed_out() -> None:
    declarations = [
        Declaration(Allergen.MILK, CONTAINS),
        Declaration(Allergen.MILK, CONTAINS, CHEESE),
    ]

    assert assess(declarations, set()).options_that_add == ()


def test_a_chosen_option_is_not_pointed_out_again() -> None:
    assert assess(BURGER, {CHEESE}).options_that_add == ()


def test_options_that_add_follow_what_was_asked() -> None:
    declarations = [
        Declaration(Allergen.MILK, CONTAINS, RANCH),
        Declaration(Allergen.EGG, CONTAINS, RANCH),
        Declaration(Allergen.SULPHITE, CONTAINS, BACON),
    ]

    (ranch,) = assess(declarations, set(), [Allergen.EGG]).options_that_add

    assert ranch == OptionAdds(RANCH, (Declaration(Allergen.EGG, CONTAINS, RANCH),))


# --- Recognising an allergen question ---------------------------------------


@pytest.mark.parametrize(
    ("question", "named"),
    [
        ("Do you fry in peanut oil?", {Allergen.PEANUT}),
        ("Any nuts in the cookie?", {Allergen.TREE_NUT}),
        ("Is it dairy-free?", {Allergen.MILK}),
        ("Gluten or wheat in the bun?", {Allergen.GLUTEN, Allergen.WHEAT}),
        ("Est-ce qu'il y a des arachides?", {Allergen.PEANUT}),
        ("Il y a des œufs dans la sauce?", {Allergen.EGG}),
        ("Contient du blé?", {Allergen.WHEAT}),
        ("Is there fish sauce in it?", {Allergen.FISH}),
        ("What oil do you use?", set()),
        ("Can I get a donut?", set()),
        ("A cheeseburger please", set()),
    ],
)
def test_allergens_are_recognised_by_name(question: str, named: set[Allergen]) -> None:
    assert allergens_named(question) == named


@pytest.mark.parametrize(
    ("question", "about"),
    [
        ("My son has a nut allergy", True),
        ("Je suis allergique", True),
        ("I'm lactose intolerant", True),
        ("Is the burger allergen friendly?", True),
        ("What oil do you fry in?", False),
        ("Is the veggie burger vegan?", False),
    ],
)
def test_an_allergen_question_is_recognised(question: str, about: bool) -> None:
    assert is_about_allergens(question) is about


@pytest.mark.parametrize(
    ("request_text", "avoided"),
    [
        ("an oat milk shake", {Allergen.MILK}),
        ("something dairy-free", {Allergen.MILK}),
        ("I'm lactose intolerant", {Allergen.MILK}),
        ("a vegan burger", {Allergen.MILK, Allergen.EGG}),
        ("gluten-free bun", {Allergen.GLUTEN}),
        ("no nuts please", {Allergen.TREE_NUT}),
        ("I'm allergic to eggs", {Allergen.EGG}),
        ("without sesame", {Allergen.SESAME}),
        ("sans gluten", {Allergen.GLUTEN}),
        ("allergique aux arachides", {Allergen.PEANUT}),
        ("Is there milk in it?", set()),
        ("a large fries", set()),
        ("hold the cheese", set()),
    ],
)
def test_what_a_customer_is_avoiding_is_read_from_how_they_ask(
    request_text: str, avoided: set[Allergen]
) -> None:
    assert allergens_avoided(request_text) == avoided
