# Careline Clinic — Voice AI Doctor Appointment Assistant

A real-time voice-based doctor appointment booking system built with **Pipecat**, **ElevenLabs STT/TTS**, **Google Gemini**, **Silero VAD**, **FastAPI**, and a live web dashboard.

## LLM Used
- **Google Gemini (`gemini-2.5-flash`)** via Pipecat's `GoogleLLMService` for low-latency conversational reasoning and function calling.

## Features
- **Voice Pipeline (`backend/bot.py`)**:
  - `SmallWebRTCTransport` for browser-to-server peer-to-peer audio streaming.
  - `SileroVADAnalyzer` for voice activity detection and natural turn-taking.
  - `ElevenLabsSTTService` for speech-to-text transcription.
  - `GoogleLLMService` with three registered tools:
    - `check_doctor_availability` — filters open slots by doctor, specialty, or date.
    - `book_appointment` — books a slot using the caller's spoken phone number (read back for confirmation first).
    - `cancel_appointment` — cancels an active appointment using the caller's spoken phone number.
  - `ElevenLabsTTSService` for natural spoken responses.
- **SQLite Persistence (`backend/database.py`)**:
  - Auto-seeded on first startup with 4 doctors, upcoming time slots, and sample appointments.
- **Frontend Dashboard (`frontend/`)**:
  - Real-time doctors/slots grid, appointments table with booking/cancellation row highlights, and live call transcript synced via Server-Sent Events (`/api/events`).
  - **Phone-Style Call Screen**: Click **Try Call** to open a mobile-style call modal with dialing/connected/ended states, synthesized ring and hang-up tones, live audio waveform, mute/end-call controls, in-call transcript, and booking confirmation cards.

## Setup & Run

1. **Create and activate a virtual environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your `GOOGLE_API_KEY` and `ELEVENLABS_API_KEY`.

4. **Start the server**
   ```bash
   python -m backend.server
   ```

5. **Open the dashboard**
   Visit `http://localhost:8000` in Chrome/Edge/Firefox, allow microphone access, and click **Try Call**.
