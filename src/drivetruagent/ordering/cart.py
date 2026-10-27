"""Changing and reading a cart.

Every function that changes a cart follows the same steps:

  1. check what can be checked without the database;
  2. lock the session, so changes to one cart never interleave;
  3. validate against the menu, before writing anything;
  4. write, bump the cart version, and mark the session as ordering;
  5. return the whole cart, priced.

Nothing here commits. The caller owns the transaction.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from drivetruagent.db.models import Cart, CartLine, CartLineOption, CustomerSession
from drivetruagent.enums import Language, SessionState
from drivetruagent.ordering import offers, pricing
from drivetruagent.ordering.catalog import load_item_by_code, load_items_by_id
from drivetruagent.ordering.discounts import Discount, apply_combos, apply_specials
from drivetruagent.ordering.errors import (
    EmptyCart,
    InsufficientStock,
    InvalidQuantity,
    ItemUnavailable,
    NothingToUpdate,
    UnknownCartLine,
    UnknownItem,
)
from drivetruagent.ordering.inventory import (
    DEFAULT_RESERVATION_TTL,
    StockNeed,
    extend_reservations,
    reserve,
)
from drivetruagent.ordering.menu import alternatives_to
from drivetruagent.ordering.selection import (
    ChosenOption,
    ItemSpec,
    Selections,
    merge_selections,
    resolve_selection,
)
from drivetruagent.ordering.sessions import get_session, lock_active_session
from drivetruagent.ordering.views import (
    DEFAULT_LANGUAGE,
    CartSnapshot,
    DiscountView,
    LineView,
    SelectedOptionView,
    SuggestionView,
    localize,
)


@dataclass(frozen=True)
class PricedLine:
    """A cart line joined with the menu and priced. Internal to this package."""

    line_no: int
    item: ItemSpec
    quantity: int
    chosen: tuple[ChosenOption, ...]

    @property
    def unit_price_cents(self) -> int:
        return pricing.unit_price_cents(
            self.item.base_price_cents,
            (choice.option.price_delta_cents for choice in self.chosen),
        )

    @property
    def line_total_cents(self) -> int:
        return pricing.line_total_cents(self.unit_price_cents, self.quantity)

    def to_view(self, language: Language) -> LineView:
        return LineView(
            line_no=self.line_no,
            item_code=self.item.code,
            item_name=localize(self.item.name, language),
            quantity=self.quantity,
            options=[
                SelectedOptionView(
                    group_code=choice.group.code,
                    group_name=localize(choice.group.name, language),
                    option_code=choice.option.code,
                    option_name=localize(choice.option.name, language),
                    price_delta_cents=choice.option.price_delta_cents,
                )
                for choice in self.chosen
            ],
            unit_price_cents=self.unit_price_cents,
            line_total_cents=self.line_total_cents,
        )


# --- Changes ----------------------------------------------------------------


async def add_item(
    db: AsyncSession,
    session_id: uuid.UUID,
    item_code: str,
    quantity: int = 1,
    selections: Selections | None = None,
    *,
    tax_rate: Decimal,
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> CartSnapshot:
    """Add a new line. Raises MissingRequiredOptions if a choice is still needed."""
    _check_quantity(quantity)
    customer_session = await lock_active_session(db, session_id)
    cart = await load_cart(db, session_id)

    item = await load_item_by_code(db, item_code)
    if item is None:
        raise UnknownItem(item_code)
    if not item.is_active:
        raise ItemUnavailable(item_code)
    chosen = resolve_selection(item, selections or {})
    await _hold_stock(db, customer_session, cart, item, +quantity, reservation_ttl)

    line = CartLine(
        cart_id=cart.id, line_no=cart.next_line_no, item_id=item.id, quantity=quantity
    )
    db.add(line)
    await db.flush()
    await _write_options(db, line.id, chosen)
    cart.next_line_no += 1

    return await _finish_change(db, customer_session, cart, tax_rate, reservation_ttl)


async def update_line(
    db: AsyncSession,
    session_id: uuid.UUID,
    line_no: int,
    quantity: int | None = None,
    selections: Selections | None = None,
    *,
    tax_rate: Decimal,
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> CartSnapshot:
    """Change a line's quantity, its options, or both.

    Options are replaced group by group: a group not named in `selections`
    keeps its current choice, and an empty list clears an optional group.
    """
    if quantity is None and selections is None:
        raise NothingToUpdate()
    if quantity is not None:
        _check_quantity(quantity)
    customer_session = await lock_active_session(db, session_id)
    cart = await load_cart(db, session_id)
    line = await _load_line(db, cart, line_no)

    item = (await load_items_by_id(db, [line.item_id]))[line.item_id]

    # Everything that can refuse the change happens before anything is written.
    chosen: list[ChosenOption] | None = None
    if selections is not None:
        current = _current_selections(
            item, [o.option_id for o in line.selected_options]
        )
        chosen = resolve_selection(item, merge_selections(current, selections))
    if quantity is not None and quantity != line.quantity:
        change = quantity - line.quantity
        await _hold_stock(db, customer_session, cart, item, change, reservation_ttl)

    if chosen is not None:
        await db.execute(
            delete(CartLineOption).where(CartLineOption.cart_line_id == line.id)
        )
        await _write_options(db, line.id, chosen)
    if quantity is not None:
        line.quantity = quantity

    return await _finish_change(db, customer_session, cart, tax_rate, reservation_ttl)


async def remove_line(
    db: AsyncSession,
    session_id: uuid.UUID,
    line_no: int,
    *,
    tax_rate: Decimal,
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> CartSnapshot:
    """Remove a line. Its number is not reused by later lines."""
    customer_session = await lock_active_session(db, session_id)
    cart = await load_cart(db, session_id)
    line = await _load_line(db, cart, line_no)
    item = (await load_items_by_id(db, [line.item_id]))[line.item_id]

    # Give back the stock this line was holding.
    await _hold_stock(db, customer_session, cart, item, -line.quantity, reservation_ttl)
    # The database removes the line's chosen options with it (ON DELETE CASCADE).
    await db.execute(delete(CartLine).where(CartLine.id == line.id))

    return await _finish_change(db, customer_session, cart, tax_rate, reservation_ttl)


async def replace_line(
    db: AsyncSession,
    session_id: uuid.UUID,
    line_no: int,
    item_code: str,
    quantity: int | None = None,
    selections: Selections | None = None,
    *,
    tax_rate: Decimal,
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> CartSnapshot:
    """Swap a line for another item, keeping its line number: "the fish
    burger instead". Everything that can refuse (an unknown item, a choice
    still needed, not enough stock) is checked before the line changes, and
    the caller runs this in a savepoint, so a refusal leaves the line as it was.
    """
    customer_session = await lock_active_session(db, session_id)
    cart = await load_cart(db, session_id)
    line = await _load_line(db, cart, line_no)
    old_item = (await load_items_by_id(db, [line.item_id]))[line.item_id]
    new_quantity = line.quantity if quantity is None else quantity
    _check_quantity(new_quantity)

    item = await load_item_by_code(db, item_code)
    if item is None:
        raise UnknownItem(item_code)
    if not item.is_active:
        raise ItemUnavailable(item_code)
    chosen = resolve_selection(item, selections or {})
    # Each call counts from the cart as it is now, before the line changes.
    if item.id == old_item.id:
        await _hold_stock(
            db,
            customer_session,
            cart,
            item,
            new_quantity - line.quantity,
            reservation_ttl,
        )
    else:
        # The new item's stock first: if there isn't enough, nothing has changed.
        await _hold_stock(
            db, customer_session, cart, item, +new_quantity, reservation_ttl
        )
        await _hold_stock(
            db, customer_session, cart, old_item, -line.quantity, reservation_ttl
        )

    line.item_id = item.id
    line.quantity = new_quantity
    await db.execute(
        delete(CartLineOption).where(CartLineOption.cart_line_id == line.id)
    )
    await db.flush()
    await _write_options(db, line.id, chosen)

    return await _finish_change(db, customer_session, cart, tax_rate, reservation_ttl)


async def request_confirmation(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    tax_rate: Decimal,
    reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL,
) -> CartSnapshot:
    """Mark that the order is being read back to the customer.

    The cart itself does not change, so its version stays the same. Any later
    change to the cart moves the session back to ordering, which means the
    order has to be read back again before it can be submitted.
    """
    customer_session = await lock_active_session(db, session_id)
    cart = await load_cart(db, session_id)
    lines = await load_priced_lines(db, cart)
    if not lines:
        raise EmptyCart()
    customer_session.state = SessionState.CONFIRMING
    await extend_reservations(db, session_id, ttl=reservation_ttl)
    await db.flush()
    discounts = await price_discounts(db, customer_session, lines)
    return snapshot_from_lines(customer_session, cart, lines, discounts, tax_rate)


# --- Reads ------------------------------------------------------------------


async def get_cart(
    db: AsyncSession, session_id: uuid.UUID, *, tax_rate: Decimal
) -> CartSnapshot:
    """The current cart, priced. Takes no lock and works on ended sessions too."""
    customer_session = await get_session(db, session_id)
    cart = await load_cart(db, session_id)
    return await build_snapshot(db, customer_session, cart, tax_rate)


async def suggestion_for(
    db: AsyncSession, session_id: uuid.UUID
) -> SuggestionView | None:
    """Something worth offering the customer given their cart, or None."""
    customer_session = await get_session(db, session_id)
    cart = await load_cart(db, session_id)
    lines = await load_priced_lines(db, cart)
    return await offers.suggest_combo(db, customer_session, units_by_category(lines))


async def load_priced_lines(db: AsyncSession, cart: Cart) -> list[PricedLine]:
    """The cart's lines in line-number order, joined with the menu and priced."""
    statement = (
        select(CartLine)
        .where(CartLine.cart_id == cart.id)
        .order_by(CartLine.line_no)
        .options(selectinload(CartLine.selected_options))
        .execution_options(populate_existing=True)
    )
    lines = (await db.execute(statement)).scalars().all()
    items = await load_items_by_id(db, {line.item_id for line in lines})

    return [
        PricedLine(
            line_no=line.line_no,
            item=items[line.item_id],
            quantity=line.quantity,
            chosen=tuple(
                _chosen_in_menu_order(
                    items[line.item_id], [o.option_id for o in line.selected_options]
                )
            ),
        )
        for line in lines
    ]


async def build_snapshot(
    db: AsyncSession, customer_session: CustomerSession, cart: Cart, tax_rate: Decimal
) -> CartSnapshot:
    lines = await load_priced_lines(db, cart)
    discounts = await price_discounts(db, customer_session, lines)
    return snapshot_from_lines(customer_session, cart, lines, discounts, tax_rate)


async def price_discounts(
    db: AsyncSession, customer_session: CustomerSession, lines: list[PricedLine]
) -> tuple[Discount, ...]:
    """Everything these lines earn off, read from the rules as they are now.

    Combos first, then the specials running on the date the session opened.
    A special and a combo can both apply to the same item.
    """
    if not lines:
        return ()
    rules = await offers.load_combo_rules(db)
    specials = await offers.load_specials(db, customer_session.business_date)
    return (
        *apply_combos(units_by_category(lines), rules).discounts,
        *apply_specials(units_by_item(lines), specials),
    )


def units_by_category(lines: list[PricedLine]) -> dict[str, int]:
    """How many units the cart holds in each menu category."""
    units: dict[str, int] = defaultdict(int)
    for line in lines:
        units[line.item.category_code] += line.quantity
    return dict(units)


def units_by_item(lines: list[PricedLine]) -> dict[str, int]:
    """How many units the cart holds of each item, across its lines."""
    units: dict[str, int] = defaultdict(int)
    for line in lines:
        units[line.item.code] += line.quantity
    return dict(units)


def snapshot_from_lines(
    customer_session: CustomerSession,
    cart: Cart,
    lines: list[PricedLine],
    discounts: tuple[Discount, ...],
    tax_rate: Decimal,
) -> CartSnapshot:
    language = customer_session.language or DEFAULT_LANGUAGE
    totals = pricing.cart_totals(
        (line.line_total_cents for line in lines),
        tax_rate,
        sum(discount.amount_cents for discount in discounts),
    )
    return CartSnapshot(
        session_id=customer_session.id,
        state=customer_session.state,
        language=language,
        version=cart.version,
        lines=[line.to_view(language) for line in lines],
        item_count=sum(line.quantity for line in lines),
        subtotal_cents=totals.subtotal_cents,
        discounts=[
            DiscountView(
                kind=discount.kind,
                code=discount.code,
                name=localize(discount.name, language),
                times=discount.times,
                amount_cents=discount.amount_cents,
            )
            for discount in discounts
        ],
        discount_cents=totals.discount_cents,
        tax_cents=totals.tax_cents,
        total_cents=totals.total_cents,
    )


# --- Helpers ----------------------------------------------------------------


async def _hold_stock(
    db: AsyncSession,
    customer_session: CustomerSession,
    cart: Cart,
    item: ItemSpec,
    change: int,
    ttl: timedelta,
) -> None:
    """Adjust the stock this session holds of an item by `change` units.

    Called before the cart is written, so a refusal leaves the cart untouched.
    If there is not enough, the error is given some in-stock alternatives from
    the same category for the agent to offer.
    """
    in_cart = (
        await db.execute(
            select(func.coalesce(func.sum(CartLine.quantity), 0)).where(
                CartLine.cart_id == cart.id, CartLine.item_id == item.id
            )
        )
    ).scalar_one()
    need = StockNeed(item.id, item.code, int(in_cart) + change)
    try:
        await reserve(db, customer_session.id, need, ttl=ttl)
    except InsufficientStock as error:
        language = customer_session.language or DEFAULT_LANGUAGE
        alternatives = await alternatives_to(db, item.code, language)
        error.details["alternatives"] = [
            alternative.model_dump(mode="json") for alternative in alternatives
        ]
        raise


def _check_quantity(quantity: int) -> None:
    if quantity < 1:
        raise InvalidQuantity(quantity)


async def load_cart(db: AsyncSession, session_id: uuid.UUID) -> Cart:
    statement = (
        select(Cart)
        .where(Cart.session_id == session_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(statement)).scalar_one()


async def _load_line(db: AsyncSession, cart: Cart, line_no: int) -> CartLine:
    statement = (
        select(CartLine)
        .where(CartLine.cart_id == cart.id)
        .order_by(CartLine.line_no)
        .options(selectinload(CartLine.selected_options))
        .execution_options(populate_existing=True)
    )
    lines = (await db.execute(statement)).scalars().all()
    for line in lines:
        if line.line_no == line_no:
            return line
    raise UnknownCartLine(line_no, [line.line_no for line in lines])


async def _write_options(
    db: AsyncSession, line_id: uuid.UUID, chosen: list[ChosenOption]
) -> None:
    if chosen:
        await db.execute(
            insert(CartLineOption),
            [
                {"cart_line_id": line_id, "option_id": choice.option.id}
                for choice in chosen
            ],
        )


async def _finish_change(
    db: AsyncSession,
    customer_session: CustomerSession,
    cart: Cart,
    tax_rate: Decimal,
    reservation_ttl: timedelta,
) -> CartSnapshot:
    cart.version += 1
    # A cart edited after the read-back has to be confirmed again.
    customer_session.state = SessionState.ORDERING
    # The session is clearly still here, so its held stock stays held.
    await extend_reservations(db, customer_session.id, ttl=reservation_ttl)
    await db.flush()
    return await build_snapshot(db, customer_session, cart, tax_rate)


def _chosen_in_menu_order(
    item: ItemSpec, option_ids: list[uuid.UUID]
) -> list[ChosenOption]:
    """Match stored option ids back to the menu, in the menu's display order."""
    wanted = set(option_ids)
    return [
        ChosenOption(group, option)
        for group in item.groups
        for option in group.options
        if option.id in wanted
    ]


def _current_selections(
    item: ItemSpec, option_ids: list[uuid.UUID]
) -> dict[str, list[str]]:
    """A line's stored options as {group code: [option codes]}."""
    selections: dict[str, list[str]] = defaultdict(list)
    for choice in _chosen_in_menu_order(item, option_ids):
        selections[choice.group.code].append(choice.option.code)
    return dict(selections)
