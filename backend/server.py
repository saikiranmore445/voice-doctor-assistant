import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from pipecat.transports.smallwebrtc.request_handler import (
    ConnectionMode,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from backend import database
from backend.bot import run_voice_bot

load_dotenv(override=True)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Connected SSE subscribers
event_subscribers: set[asyncio.Queue] = set()
webrtc_handler = SmallWebRTCRequestHandler(connection_mode=ConnectionMode.MULTIPLE)


async def broadcast_event(event_type: str, payload: dict[str, Any]):
    message = {"type": event_type, "data": payload}
    stale: list[asyncio.Queue] = []
    for queue in event_subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            stale.append(queue)
    for q in stale:
        event_subscribers.discard(q)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    logger.info("Clinic database initialized at {}", database.DB_PATH)
    yield
    await webrtc_handler.close()


app = FastAPI(title="Careline Voice AI Doctor Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_dashboard():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gemini_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "elevenlabs_configured": bool(os.getenv("ELEVENLABS_API_KEY")),
    }


@app.get("/api/doctors")
async def list_doctors():
    return {"doctors": database.get_doctors_with_slots()}


@app.get("/api/appointments")
async def list_appointments():
    return {"appointments": database.get_appointments()}


@app.get("/api/events")
async def sse_events():
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    event_subscribers.add(queue)

    async def event_stream():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'data': {}})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_subscribers.discard(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/offer")
async def webrtc_offer(
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
):
    async def on_connection(connection):
        background_tasks.add_task(run_voice_bot, connection, broadcast_event)

    answer = await webrtc_handler.handle_web_request(
        request=request,
        webrtc_connection_callback=on_connection,
    )
    return answer


@app.patch("/api/offer")
async def webrtc_ice_patch(request: SmallWebRTCPatchRequest):
    await webrtc_handler.handle_patch_request(request)
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.server:app", host="0.0.0.0", port=port, reload=True)
