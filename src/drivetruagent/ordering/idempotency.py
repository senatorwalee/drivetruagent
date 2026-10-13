"""Run something once per key, however many times it is asked for.

A caller that is unsure whether a request got through (a timeout, a dropped
connection) sends it again with the same key. The first request runs and its
response is stored; a repeat gets the stored response and nothing runs again.

Two things make this safe:

  - The session is locked first, so two copies of a request arriving at the
    same moment run one after the other, and the second finds the record.
  - The record is written in the same transaction as the work. Either both
    are saved or neither is.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drivetruagent.db.models import IdempotencyRecord
from drivetruagent.ordering.errors import IdempotencyKeyReused
from drivetruagent.ordering.sessions import lock_session


async def run_once[ResponseT: BaseModel](
    db: AsyncSession,
    session_id: uuid.UUID,
    key: str | None,
    *,
    operation: str,
    request: Any,
    response_model: type[ResponseT],
    run: Callable[[], Awaitable[ResponseT]],
) -> ResponseT:
    """Run `run` unless this key has already been answered for this session.

    With no key the function simply runs. A key that was used before for a
    different operation or request raises IdempotencyKeyReused: a key names
    one request.
    """
    if key is None:
        return await run()

    await lock_session(db, session_id)
    fingerprint = _fingerprint(operation, request)
    record = (
        await db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.session_id == session_id,
                IdempotencyRecord.key == key,
            )
        )
    ).scalar_one_or_none()

    if record is not None:
        if record.request_fingerprint != fingerprint:
            raise IdempotencyKeyReused(key)
        return response_model.model_validate(record.response)

    response = await run()
    db.add(
        IdempotencyRecord(
            session_id=session_id,
            key=key,
            operation=operation,
            request_fingerprint=fingerprint,
            response=response.model_dump(mode="json"),
        )
    )
    await db.flush()
    return response


def _fingerprint(operation: str, request: Any) -> str:
    canonical = json.dumps(
        {"operation": operation, "request": request}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
