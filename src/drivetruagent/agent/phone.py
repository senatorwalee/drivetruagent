"""Answering a phone call: open the session, accept the call, say the notice.

    caller -> phone number -> SIP trunk -> OpenAI -> webhook -> answer_call

Everything after the call is answered is the same as a browser voice call
(agent/voice.py): the same bridge, tools, timers and log. What is particular
to the phone is here: the caller's number, the fixed opening notice, and
where a call goes when a person is needed.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.agent.loop import AgentConfig
from drivetruagent.agent.model import ModelUnavailable
from drivetruagent.agent.prompt import build_instructions
from drivetruagent.agent.tools import TOOL_DEFINITIONS
from drivetruagent.agent.voice import (
    CallControl,
    IncomingCall,
    LiveCalls,
    RealtimeClient,
    SessionScope,
    VoiceBridge,
    VoiceSettings,
    accept_call,
    run_call,
    session_config,
    turn_away,
)
from drivetruagent.enums import Channel, SessionState
from drivetruagent.ordering.callers import record_call
from drivetruagent.ordering.sessions import end_session, open_session
from drivetruagent.policy import caller_rules
from drivetruagent.policy.caller_rules import TURN_AWAY_SENTENCES, CallerLimits
from drivetruagent.policy.callers import counts_for

# Said by code, word for word, before the model says anything. It tells the
# caller they are talking to an automated assistant, that the call is
# recorded, why their number is kept, and how to stop it. Have the wording
# reviewed before a real launch.
_GREETING = (
    "Hi, thanks for calling {restaurant}. You're speaking with our automated "
    "assistant. This call is recorded, and we keep your number with your "
    "order to help with pickup and to remember your recent orders. Say "
    "“don't save my number” at any time. Pour le service en "
    "français, parlez-moi en français. What can I get you?"
)
# With a withheld number there is no number to keep.
_GREETING_WITHHELD = (
    "Hi, thanks for calling {restaurant}. You're speaking with our automated "
    "assistant, and this call is recorded. Pour le service en français, "
    "parlez-moi en français. What can I get you?"
)
# When a person is needed and there is no number to put the caller through to.
CALL_YOU_BACK = "A team member will call you back on this number shortly. Goodbye."
CALL_US_BACK = "Please call back in a few minutes and ask for a team member. Goodbye."


def greeting(restaurant: str, *, number_known: bool) -> str:
    template = _GREETING if number_known else _GREETING_WITHHELD
    return template.format(restaurant=restaurant)


@dataclass(frozen=True)
class CallOutcome:
    # The session the call is being handled in, if it was taken.
    session_id: uuid.UUID | None = None
    # Why it was not, if it was turned away.
    turned_away: caller_rules.TurnAway | None = None


async def answer_call(
    db: AsyncSession,
    incoming: IncomingCall,
    *,
    client: RealtimeClient,
    calls: LiveCalls,
    scope: SessionScope,
    config: AgentConfig,
    voice: VoiceSettings,
    business_date: date,
    staff_phone: str | None,
    caller_limits: CallerLimits,
) -> CallOutcome:
    """Answer a ringing call and handle it in the background.

    A blocked number, or one that keeps calling without ordering, is told
    so in a fixed sentence and never reaches the agent. Nothing is returned
    if this call is already being answered (the webhook was delivered
    twice). Raises ModelUnavailable if OpenAI would not take the call; the
    session is then closed.
    """
    if not calls.claim(incoming.call_id):
        return CallOutcome()
    refusal = await screen_caller(db, incoming.caller_phone, caller_limits)
    if refusal is not None:
        calls.start(
            # Not a session; the key only has to be unique.
            uuid.uuid4(),
            incoming.call_id,
            turn_away(client, incoming.call_id, TURN_AWAY_SENTENCES[refusal], voice),
        )
        return CallOutcome(turned_away=refusal)

    session_id, instructions = await open_phone_session(
        db, incoming.caller_phone, business_date=business_date, config=config
    )
    try:
        await accept_call(
            client,
            incoming.call_id,
            session_config(instructions, TOOL_DEFINITIONS, voice),
        )
    except ModelUnavailable:
        await end_session(db, session_id, SessionState.ABANDONED, "call_not_answered")
        await db.commit()
        raise

    handle_phone_call(
        session_id,
        incoming.call_id,
        incoming.caller_phone,
        client=client,
        calls=calls,
        scope=scope,
        config=config,
        voice=voice,
        transfer_to=None if staff_phone is None else f"tel:{staff_phone}",
    )
    return CallOutcome(session_id=session_id)


async def screen_caller(
    db: AsyncSession, caller_phone: str | None, limits: CallerLimits
) -> caller_rules.TurnAway | None:
    """Why a call from this number is turned away, if it is.

    A withheld number is never turned away here. A refused call is still a
    call from the number: it counts, and keeps the number from being
    forgotten while it goes on calling.
    """
    if caller_phone is None:
        return None
    counts = await counts_for(
        db, limits, now=datetime.now(UTC), phone_e164=caller_phone
    )
    refusal = caller_rules.turn_away(counts, limits)
    if refusal is not None:
        await record_call(db, caller_phone)
        await db.commit()
    return refusal


async def open_phone_session(
    db: AsyncSession,
    caller_phone: str | None,
    *,
    business_date: date,
    config: AgentConfig,
) -> tuple[uuid.UUID, str]:
    """Open the call's session, committed. Returns it with the instructions
    the call is set up with."""
    customer_session = await open_session(
        db,
        business_date=business_date,
        channel=Channel.PHONE,
        caller_phone=caller_phone,
    )
    await db.commit()
    instructions = await build_instructions(
        db,
        config.restaurant_name,
        channel=Channel.PHONE,
        pickup_minutes=config.pickup_minutes,
    )
    return customer_session.id, instructions


def handle_phone_call(
    session_id: uuid.UUID,
    call_id: str,
    caller_phone: str | None,
    *,
    client: RealtimeClient,
    calls: LiveCalls,
    scope: SessionScope,
    config: AgentConfig,
    voice: VoiceSettings,
    transfer_to: str | None,
) -> None:
    """Handle an answered call in the background, from the opening notice
    on. `transfer_to`: where the call goes when a person is needed; None
    and the caller is told someone will call back."""
    number_known = caller_phone is not None

    def bridge_for(call: CallControl) -> VoiceBridge:
        return VoiceBridge(
            session_id=session_id,
            scope=scope,
            call=call,
            config=config,
            model_name=voice.model,
            transfer_to=transfer_to,
            callback_sentence=CALL_YOU_BACK if number_known else CALL_US_BACK,
        )

    calls.start(
        session_id,
        call_id,
        run_call(
            client,
            call_id,
            bridge_for,
            greeting=greeting(config.restaurant_name, number_known=number_known),
        ),
    )
