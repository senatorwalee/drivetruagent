"""What a scenario is, and how a finished conversation is checked against it."""

from dataclasses import dataclass, field

from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering.views import CartSnapshot, LineView, OrderView


@dataclass(frozen=True)
class ExpectedLine:
    item_code: str
    quantity: int = 1
    # Choices that must be on the line: group code -> option code. Choices not
    # listed here (defaults, for example) are not checked.
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    name: str
    # What the customer says, one entry per turn.
    customer: tuple[str, ...]
    # The state the session must end in.
    final_state: SessionState
    # The lines the cart must hold at the end, in any order. None: not checked.
    lines: tuple[ExpectedLine, ...] | None = None
    # The total that must be charged. None: not checked.
    total_cents: int | None = None
    language: Language | None = None
    # Tools that must, and must not, have been used at some point.
    must_use: frozenset[str] = frozenset()
    must_not_use: frozenset[str] = frozenset()
    # Words that must not appear in anything the agent said (case ignored).
    must_not_say: tuple[str, ...] = ()


@dataclass(frozen=True)
class Exchange:
    customer: str
    agent: str
    tools_used: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioResult:
    exchanges: tuple[Exchange, ...]
    cart: CartSnapshot
    order: OrderView | None

    @property
    def tools_used(self) -> frozenset[str]:
        return frozenset(
            tool for exchange in self.exchanges for tool in exchange.tools_used
        )

    def transcript(self) -> str:
        """The conversation as text, for reading when a scenario fails."""
        rows: list[str] = []
        for exchange in self.exchanges:
            rows.append(f"customer: {exchange.customer}")
            if exchange.tools_used:
                rows.append(f"   tools: {', '.join(exchange.tools_used)}")
            rows.append(f"   agent: {exchange.agent}")
        return "\n".join(rows)


def check(scenario: Scenario, result: ScenarioResult) -> list[str]:
    """Everything that is wrong, as readable sentences. Empty means it passed."""
    failures: list[str] = []
    cart = result.cart

    if cart.state is not scenario.final_state:
        failures.append(
            f"session ended as {cart.state.value}, "
            f"expected {scenario.final_state.value}"
        )
    if scenario.final_state is SessionState.SUBMITTED and result.order is None:
        failures.append("no order was created")
    if scenario.language is not None and cart.language is not scenario.language:
        failures.append(
            f"language is {cart.language.value}, expected {scenario.language.value}"
        )
    if scenario.lines is not None:
        failures.extend(_check_lines(scenario.lines, cart.lines))
    if scenario.total_cents is not None and cart.total_cents != scenario.total_cents:
        failures.append(
            f"total is {cart.total_cents} cents, expected {scenario.total_cents}"
        )

    used = result.tools_used
    failures.extend(
        f"tool {tool} was never used" for tool in sorted(scenario.must_use - used)
    )
    failures.extend(
        f"tool {tool} was used but must not be"
        for tool in sorted(scenario.must_not_use & used)
    )

    said = " ".join(exchange.agent for exchange in result.exchanges).lower()
    failures.extend(
        f'the agent said "{phrase}"'
        for phrase in scenario.must_not_say
        if phrase.lower() in said
    )
    return failures


def _check_lines(
    expected: tuple[ExpectedLine, ...], actual: list[LineView]
) -> list[str]:
    failures: list[str] = []
    unmatched = list(actual)
    for wanted in expected:
        match = next((line for line in unmatched if _matches(wanted, line)), None)
        if match is None:
            failures.append(f"cart is missing {_describe_expected(wanted)}")
        else:
            unmatched.remove(match)
    failures.extend(
        f"cart has an unexpected line: {line.quantity} x {line.item_code}"
        for line in unmatched
    )
    return failures


def _matches(wanted: ExpectedLine, line: LineView) -> bool:
    if (line.item_code, line.quantity) != (wanted.item_code, wanted.quantity):
        return False
    chosen = {(option.group_code, option.option_code) for option in line.options}
    return all(choice in chosen for choice in wanted.options.items())


def _describe_expected(wanted: ExpectedLine) -> str:
    options = ", ".join(f"{group}={option}" for group, option in wanted.options.items())
    detail = f" ({options})" if options else ""
    return f"{wanted.quantity} x {wanted.item_code}{detail}"
