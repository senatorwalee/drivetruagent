"""Talk to the agent in a terminal.

    uv run python -m drivetruagent.agent.chat

Opens a session on its own lane, then reads what you type and prints the
agent's reply and the cart. Type "quit" to stop. Needs OPENAI_API_KEY and a
migrated, seeded database (make up, make migrate, make seed).
"""

import asyncio
import uuid

from drivetruagent.agent.loop import run_turn
from drivetruagent.agent.model import ModelUnavailable
from drivetruagent.api.conversation import agent_config, get_model_client
from drivetruagent.config import get_settings
from drivetruagent.db.session import create_engine, create_session_factory
from drivetruagent.enums import FINAL_SESSION_STATES
from drivetruagent.ordering.sessions import open_session
from drivetruagent.ordering.views import CartSnapshot


def describe(cart: CartSnapshot) -> str:
    if not cart.lines:
        return "  (cart is empty)"
    rows: list[str] = []
    for line in cart.lines:
        options = ", ".join(option.option_name for option in line.options)
        detail = f" ({options})" if options else ""
        rows.append(
            f"  L{line.line_no}  {line.quantity} x {line.item_name}{detail}"
            f"  {line.line_total_cents / 100:.2f}"
        )
    rows.append(
        f"  subtotal {cart.subtotal_cents / 100:.2f}"
        f"  tax {cart.tax_cents / 100:.2f}"
        f"  total {cart.total_cents / 100:.2f}"
    )
    return "\n".join(rows)


async def chat() -> None:
    settings = get_settings()
    model = get_model_client(settings)
    config = agent_config(settings)
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as db:
            # A lane of its own, so it never collides with another session.
            customer_session = await open_session(db, f"chat-{uuid.uuid4().hex[:8]}")
            await db.commit()
            session_id = customer_session.id
            print(f"Session {session_id}. Type what the customer says; quit to stop.\n")

            while True:
                text = (await asyncio.to_thread(input, "you> ")).strip()
                if text.lower() in {"quit", "exit"}:
                    break
                if not text:
                    continue
                reply = await run_turn(db, session_id, text, model=model, config=config)
                print(f"agent> {reply.text}")
                if reply.tools_used:
                    print(f"  [tools: {', '.join(reply.tools_used)}]")
                print(describe(reply.cart))
                if reply.cart.state in FINAL_SESSION_STATES:
                    print(f"\nSession ended: {reply.cart.state.value}.")
                    break
    finally:
        await engine.dispose()


def main() -> None:
    try:
        asyncio.run(chat())
    except ModelUnavailable as error:
        raise SystemExit(str(error)) from error
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
