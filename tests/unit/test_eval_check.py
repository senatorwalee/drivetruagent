"""How a finished conversation is judged. No database and no model."""

import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.enums import Language, SessionState
from drivetruagent.evals.scenario import (
    DEFAULT_BUSINESS_DATE,
    Exchange,
    ExpectedLine,
    Scenario,
    ScenarioResult,
    check,
)
from drivetruagent.evals.scenarios import SCENARIOS
from drivetruagent.ordering.discounts import (
    ComboRule,
    ComboSlot,
    Special,
    apply_combos,
    apply_specials,
)
from drivetruagent.ordering.views import (
    CartSnapshot,
    LineView,
    OrderView,
    SelectedOptionView,
)
from drivetruagent.seed import load_menu

MENU_PATH = Path(__file__).resolve().parents[2] / "data" / "menu.json"
SESSION_ID = uuid.uuid4()


def line(item_code: str, quantity: int = 1, **options: str) -> LineView:
    return LineView(
        line_no=1,
        item_code=item_code,
        item_name=item_code.title(),
        quantity=quantity,
        options=[
            SelectedOptionView(
                group_code=group,
                group_name=group.title(),
                option_code=option,
                option_name=option.title(),
                price_delta_cents=0,
            )
            for group, option in options.items()
        ],
        unit_price_cents=100,
        line_total_cents=100 * quantity,
    )


def result(
    *lines: LineView,
    state: SessionState = SessionState.SUBMITTED,
    language: Language = Language.EN,
    tools: tuple[str, ...] = ("add_item", "request_confirmation", "submit_order"),
    agent_said: str = "Anything else?",
    total_cents: int = 113,
    discount_cents: int = 0,
    with_order: bool = True,
) -> ScenarioResult:
    cart = CartSnapshot(
        session_id=SESSION_ID,
        state=state,
        language=language,
        version=1,
        lines=list(lines),
        item_count=sum(each.quantity for each in lines),
        subtotal_cents=100,
        discounts=[],
        discount_cents=discount_cents,
        tax_cents=13,
        total_cents=total_cents,
    )
    order = (
        OrderView(
            order_id=uuid.uuid4(),
            order_number=1,
            session_id=SESSION_ID,
            language=language,
            lines=list(lines),
            item_count=cart.item_count,
            subtotal_cents=100,
            discounts=[],
            discount_cents=0,
            tax_cents=13,
            total_cents=total_cents,
            submitted_at=datetime.now(UTC),
        )
        if with_order
        else None
    )
    return ScenarioResult((Exchange("Hi", agent_said, tools),), cart, order)


def scenario(**overrides: object) -> Scenario:
    values: dict[str, object] = {
        "name": "example",
        "customer": ("Hi",),
        "final_state": SessionState.SUBMITTED,
        "lines": (ExpectedLine("fries", 1, {"size": "large"}),),
    }
    return Scenario(**values | overrides)  # pyright: ignore[reportArgumentType]


# --- Passing ----------------------------------------------------------------


def test_a_matching_conversation_has_no_failures() -> None:
    assert check(scenario(), result(line("fries", size="large"))) == []


def test_choices_not_listed_in_the_scenario_are_not_checked() -> None:
    """Defaults like style=regular need not be spelled out."""
    actual = result(line("fries", size="large", style="regular", seasoning="plain"))

    assert check(scenario(), actual) == []


def test_lines_may_come_in_any_order() -> None:
    expected = scenario(lines=(ExpectedLine("cola"), ExpectedLine("fries")))

    assert check(expected, result(line("fries"), line("cola"))) == []


def test_lines_are_not_checked_when_the_scenario_gives_none() -> None:
    assert check(scenario(lines=None), result(line("anything", 7))) == []


# --- Failing ----------------------------------------------------------------


def test_wrong_final_state() -> None:
    failures = check(
        scenario(), result(line("fries", size="large"), state=SessionState.ORDERING)
    )

    assert "session ended as ordering, expected submitted" in failures


def test_submitted_without_an_order() -> None:
    failures = check(scenario(), result(line("fries", size="large"), with_order=False))

    assert failures == ["no order was created"]


def test_missing_line() -> None:
    assert check(scenario(), result()) == ["cart is missing 1 x fries (size=large)"]


def test_wrong_option() -> None:
    failures = check(scenario(), result(line("fries", size="small")))

    assert failures == [
        "cart is missing 1 x fries (size=large)",
        "cart has an unexpected line: 1 x fries",
    ]


def test_wrong_quantity() -> None:
    failures = check(scenario(), result(line("fries", 2, size="large")))

    assert "cart is missing 1 x fries (size=large)" in failures


def test_extra_line() -> None:
    failures = check(scenario(), result(line("fries", size="large"), line("cookie")))

    assert failures == ["cart has an unexpected line: 1 x cookie"]


def test_a_correction_added_as_a_second_line_is_caught() -> None:
    """ "Make that two" must edit the line, not add another."""
    expected = scenario(lines=(ExpectedLine("cheeseburger", 2),))

    failures = check(expected, result(line("cheeseburger"), line("cheeseburger")))

    assert "cart is missing 2 x cheeseburger" in failures


def test_wrong_total() -> None:
    failures = check(
        scenario(total_cents=428), result(line("fries", size="large"), total_cents=0)
    )

    assert failures == ["total is 0 cents, expected 428"]


def test_wrong_language() -> None:
    failures = check(
        scenario(language=Language.FR), result(line("fries", size="large"))
    )

    assert failures == ["language is en, expected fr"]


def test_required_tool_not_used() -> None:
    failures = check(
        scenario(must_use=frozenset({"request_confirmation"})),
        result(line("fries", size="large"), tools=("add_item", "submit_order")),
    )

    assert failures == ["tool request_confirmation was never used"]


def test_forbidden_tool_used() -> None:
    failures = check(
        scenario(must_not_use=frozenset({"submit_order"})),
        result(line("fries", size="large")),
    )

    assert failures == ["tool submit_order was used but must not be"]


def test_forbidden_phrase_said() -> None:
    failures = check(
        scenario(must_not_say=("Paris",)),
        result(line("fries", size="large"), agent_said="It's paris. Anything else?"),
    )

    assert failures == ['the agent said "Paris"']


def test_every_failure_is_reported_not_just_the_first() -> None:
    failures = check(
        scenario(language=Language.FR, total_cents=428),
        result(state=SessionState.ORDERING, with_order=False, total_cents=0),
    )

    assert failures == [
        "session ended as ordering, expected submitted",
        "no order was created",
        "language is en, expected fr",
        "cart is missing 1 x fries (size=large)",
        "total is 0 cents, expected 428",
    ]


def test_the_transcript_is_readable() -> None:
    assert result().transcript() == (
        "customer: Hi\n"
        "   tools: add_item, request_confirmation, submit_order\n"
        "   agent: Anything else?"
    )


# --- The scenarios themselves -----------------------------------------------


def menu_prices() -> dict[str, tuple[int, dict[tuple[str, str], int]]]:
    """item code -> (base price, {(group, option): price added})."""
    return {
        item.code: (
            item.base_price_cents,
            {
                (group.code, option.code): option.price_delta_cents
                for group in item.option_groups
                for option in group.options
            },
        )
        for category in load_menu(MENU_PATH).categories
        for item in category.items
    }


def test_scenario_names_are_unique() -> None:
    names = [each.name for each in SCENARIOS]

    assert len(names) == len(set(names))


@pytest.mark.parametrize("each", SCENARIOS, ids=lambda each: each.name)
def test_scenario_refers_only_to_real_tools_items_and_options(each: Scenario) -> None:
    tool_names = {tool.name for tool in TOOL_DEFINITIONS}
    prices = menu_prices()

    assert each.must_use | each.must_not_use <= tool_names
    for expected in each.lines or ():
        assert expected.item_code in prices
        for choice in expected.options.items():
            assert choice in prices[expected.item_code][1], choice


@pytest.mark.parametrize(
    "each",
    [each for each in SCENARIOS if each.lines and each.total_cents is not None],
    ids=lambda each: each.name,
)
def test_scenario_total_matches_the_menu_prices(each: Scenario) -> None:
    """Guards the expected totals against a later change to the menu file."""
    prices = menu_prices()
    subtotal = 0
    for expected in each.lines or ():
        base, deltas = prices[expected.item_code]
        unit = base + sum(deltas[choice] for choice in expected.options.items())
        subtotal += unit * expected.quantity
    discount = expected_discount(each)
    taxable = Decimal(subtotal - discount)
    tax = int((taxable * Decimal("0.13")).quantize(Decimal(1), ROUND_HALF_UP))

    assert (each.discount_cents or 0) == discount
    assert each.total_cents == subtotal - discount + tax


def expected_discount(each: Scenario) -> int:
    """What the menu file's combos and specials take off the scenario's
    expected lines, on the day the scenario is played."""
    menu = load_menu(MENU_PATH)
    category_of = {
        item.code: category.code
        for category in menu.categories
        for item in category.items
    }
    prices = menu_prices()
    by_category: Counter[str] = Counter()
    by_item: Counter[str] = Counter()
    for expected in each.lines or ():
        by_category[category_of[expected.item_code]] += expected.quantity
        by_item[expected.item_code] += expected.quantity
    rules = [
        ComboRule(
            code=combo.code,
            name=combo.name.model_dump(),
            discount_cents=combo.discount_cents,
            slots=tuple(ComboSlot(slot, {"en": slot}) for slot in combo.slots),
        )
        for combo in menu.combos
    ]
    specials = [
        Special(
            code=special.code,
            name=special.name.model_dump(),
            description=None,
            item_code=special.item,
            item_name={"en": special.item},
            base_price_cents=prices[special.item][0],
            discount_cents=special.discount_cents,
            weekdays=frozenset(special.weekdays),
        )
        for special in menu.specials
    ]
    running = [special for special in specials if special.runs_on(each.business_date)]
    discounts = (
        *apply_combos(by_category, rules).discounts,
        *apply_specials(by_item, running),
    )
    return sum(discount.amount_cents for discount in discounts)


def test_scenarios_are_played_on_a_monday_unless_they_say_otherwise() -> None:
    """A fixed day, so a total never depends on when the evals are run."""
    assert DEFAULT_BUSINESS_DATE.isoweekday() == 1
    assert all(each.business_date == DEFAULT_BUSINESS_DATE for each in SCENARIOS)


def test_wrong_discount() -> None:
    expected = scenario(discount_cents=200)
    actual = result(line("fries", size="large"), discount_cents=0)

    assert check(expected, actual) == ["discount is 0 cents, expected 200"]


def test_the_discount_is_not_checked_when_the_scenario_gives_none() -> None:
    actual = result(line("fries", size="large"), discount_cents=200)

    assert check(scenario(), actual) == []
    assert check(scenario(discount_cents=200), actual) == []
