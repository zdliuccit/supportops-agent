import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.enums import TERMINAL_RUN_STATUSES, RunStatus
from supportops_core.services import cancel_run, get_run_for_user, list_events_after

from supportops_api.dependencies import current_identity, database_session, session_factory_from
from supportops_api.schemas import RunResponse

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("/{run_id}", response_model=RunResponse)
async def read_run(
    run_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> object:
    return await get_run_for_user(
        session,
        run_id=run_id,
        tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id,
    )


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def request_cancel(
    run_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> object:
    async with session.begin():
        run = await get_run_for_user(
            session,
            run_id=run_id,
            tenant_id=identity.principal.tenant_id,
            user_id=identity.user.id,
            lock=True,
        )
        await cancel_run(session, run)
    return run


@router.get("/{run_id}/events")
async def stream_events(
    run_id: UUID,
    request: Request,
    cursor: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> StreamingResponse:
    await get_run_for_user(
        session,
        run_id=run_id,
        tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id,
    )
    start_sequence = cursor
    if last_event_id is not None:
        try:
            start_sequence = max(start_sequence, int(last_event_id))
        except ValueError:
            start_sequence = cursor

    settings = request.app.state.settings
    session_factory = session_factory_from(request)

    async def generate() -> AsyncIterator[str]:
        sequence = start_sequence
        since_heartbeat = 0.0
        while True:
            if await request.is_disconnected():
                return
            async with session_factory() as stream_session:
                run = await get_run_for_user(
                    stream_session,
                    run_id=run_id,
                    tenant_id=identity.principal.tenant_id,
                    user_id=identity.user.id,
                )
                events = await list_events_after(stream_session, run_id=run_id, sequence=sequence)
                for event in events:
                    sequence = event.sequence
                    payload = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
                if RunStatus(run.status) in TERMINAL_RUN_STATUSES and not events:
                    return
            if events:
                since_heartbeat = 0.0
            else:
                await asyncio.sleep(settings.sse_poll_interval_seconds)
                since_heartbeat += settings.sse_poll_interval_seconds
                if since_heartbeat >= settings.sse_heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    since_heartbeat = 0.0

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
