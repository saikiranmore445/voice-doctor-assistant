import os
from datetime import datetime
from typing import Any, Callable, Awaitable

import aiohttp
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from backend import database

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]

SYSTEM_PROMPT = f"""You are a warm, concise, and professional medical clinic receptionist for Careline Medical Clinic.
Today's date is {datetime.now().strftime('%A, %B %d, %Y')}.

Your job is to help callers over the phone with three tasks:
1. Check doctor availability (by doctor name, specialty, or date).
2. Book an appointment for an available slot.
3. Cancel an existing appointment.

Strict rules you MUST follow:
- Identify the caller ONLY by the phone number they say out loud. Never ask for login, OTP, or email.
- ALWAYS read the phone number digits back to the caller clearly (e.g., "Just to confirm, that's 9-8-7-6-5-4-3-2-1-0, correct?") and get their confirmation BEFORE calling `book_appointment` or `cancel_appointment`.
- Keep responses short, natural, and conversational — suitable for spoken audio. Never use markdown, bullet points, asterisks, or emojis.
- When listing slots, read out 2 to 3 clear options with doctor name, date, and time.
- Once a booking or cancellation succeeds, confirm the appointment ID, doctor, date, and time clearly.
"""


class UserTranscriptBridge(FrameProcessor):
    def __init__(self, emit_event: EventCallback):
        super().__init__()
        self._emit_event = emit_event

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self._emit_event("transcript", {"speaker": "user", "text": frame.text.strip()})
        await self.push_frame(frame, direction)


class AssistantTranscriptBridge(FrameProcessor):
    def __init__(self, emit_event: EventCallback):
        super().__init__()
        self._emit_event = emit_event
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame) and frame.text:
            self._buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            full_text = "".join(self._buffer).strip()
            self._buffer.clear()
            if full_text:
                await self._emit_event("transcript", {"speaker": "agent", "text": full_text})
        await self.push_frame(frame, direction)


def build_tools(emit_event: EventCallback):
    async def check_doctor_availability(
        params: FunctionCallParams,
        doctor_name: str = "",
        specialty: str = "",
        date: str = "",
    ):
        """Check available appointment slots for doctors at the clinic.

        Args:
            doctor_name: Optional full or partial doctor name.
            specialty: Optional specialty (Cardiology, Dermatology, Pediatrics, General Medicine).
            date: Optional date in YYYY-MM-DD format.
        """
        result = database.check_availability(doctor_name=doctor_name, specialty=specialty, date=date)
        await params.result_callback(result)

    async def book_appointment(
        params: FunctionCallParams,
        phone_number: str,
        doctor_id: str,
        slot_id: str,
        patient_name: str = "",
    ):
        """Book an available doctor appointment slot after confirming the caller's phone number.

        Args:
            phone_number: The caller's 10-digit phone number, confirmed out loud.
            doctor_id: The doctor's ID from check_doctor_availability.
            slot_id: The slot ID from check_doctor_availability.
            patient_name: Optional patient name if shared.
        """
        result = database.book_slot(
            phone_number=phone_number,
            doctor_id=doctor_id,
            slot_id=slot_id,
            patient_name=patient_name,
        )
        if result.get("success"):
            await emit_event("appointment_booked", result["appointment"])
        await params.result_callback(result)

    async def cancel_appointment(
        params: FunctionCallParams,
        phone_number: str,
        appointment_id: str = "",
    ):
        """Cancel an existing confirmed appointment using the caller's confirmed phone number.

        Args:
            phone_number: The caller's 10-digit phone number, confirmed out loud.
            appointment_id: Optional appointment ID if specified.
        """
        result = database.cancel_by_phone(phone_number=phone_number, appointment_id=appointment_id)
        if result.get("success"):
            await emit_event("appointment_cancelled", result["cancelled_appointment"])
        await params.result_callback(result)

    return [check_doctor_availability, book_appointment, cancel_appointment]


async def run_voice_bot(webrtc_connection: SmallWebRTCConnection, emit_event: EventCallback):
    google_api_key = os.getenv("GOOGLE_API_KEY", "")
    elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

    if not google_api_key or not elevenlabs_api_key:
        logger.error("Missing GOOGLE_API_KEY or ELEVENLABS_API_KEY in environment.")
        await emit_event("call_error", {"message": "Missing API keys in .env."})
        return

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    async with aiohttp.ClientSession() as session:
        stt = ElevenLabsSTTService(api_key=elevenlabs_api_key, aiohttp_session=session)
        tts = ElevenLabsHttpTTSService(
            api_key=elevenlabs_api_key,
            aiohttp_session=session,
            settings=ElevenLabsHttpTTSService.Settings(
                voice=elevenlabs_voice_id,
                model="eleven_flash_v2_5",
            ),
        )
        llm = GoogleLLMService(
            api_key=google_api_key,
            stream_idle_timeout_secs=30.0,
            settings=GoogleLLMService.Settings(
                model=gemini_model,
                system_instruction=SYSTEM_PROMPT,
                temperature=0.4,
            ),
        )

        tools = build_tools(emit_event)
        greeting = "Hello! Welcome to Careline Medical Clinic. How can I help you today?"
        context = LLMContext(messages=[], tools=tools)
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
        )

        pipeline = Pipeline([
            transport.input(),
            stt,
            UserTranscriptBridge(emit_event),
            user_aggregator,
            llm,
            AssistantTranscriptBridge(emit_event),
            tts,
            transport.output(),
            assistant_aggregator,
        ])

        task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("WebRTC client connected")
            await emit_event("call_state", {"state": "connected"})
            await emit_event("transcript", {"speaker": "agent", "text": greeting})
            await task.queue_frames([TTSSpeakFrame(greeting)])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("WebRTC client disconnected")
            await emit_event("call_state", {"state": "ended"})
            await task.cancel()

        runner = PipelineRunner(handle_sigint=False)
        await runner.run(task)