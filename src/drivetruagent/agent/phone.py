"""Answering a phone call: open the session, accept the call, say the notice.

    caller -> phone number -> SIP trunk -> OpenAI -> webhook -> answer_call

Everything after the call is answered is the same as a browser voice call
(agent/voice.py): the same bridge, tools, timers and log. What is particular
to the phone is here: the caller's number, the fixed opening notice, and
where a call goes when a person is needed.
"""

import uuid
from datetime import date

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
)
from drivetruagent.enums import Channel, SessionState
from drivetruagent.ordering.sessions import end_session, open_session

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
) -> uuid.UUID | None:
    """Answer a ringing call and handle it in the background.

    Returns the new session's id, or None if this call is already being
    answered (the webhook was delivered twice). Raises ModelUnavailable if
    OpenAI would not take the call; the session is then closed.
    """
    if not calls.claim(incoming.call_id):
        return None
    customer_session = await open_session(
        db,
        business_date=business_date,
        channel=Channel.PHONE,
        caller_phone=incoming.caller_phone,
    )
    await db.commit()
    session_id = customer_session.id

    instructions = await build_instructions(
        db,
        config.restaurant_name,
        channel=Channel.PHONE,
        pickup_minutes=config.pickup_minutes,
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

    number_known = incoming.caller_phone is not None

    def bridge_for(call: CallControl) -> VoiceBridge:
        return VoiceBridge(
            session_id=session_id,
            scope=scope,
            call=call,
            config=config,
            model_name=voice.model,
            transfer_to=None if staff_phone is None else f"tel:{staff_phone}",
            callback_sentence=CALL_YOU_BACK if number_known else CALL_US_BACK,
        )

    calls.start(
        session_id,
        incoming.call_id,
        run_call(
            client,
            incoming.call_id,
            bridge_for,
            greeting=greeting(config.restaurant_name, number_known=number_known),
        ),
    )
    return session_id
