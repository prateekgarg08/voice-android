"""Authenticated FastAPI gateway for the Android/configuration voice agent."""
import asyncio
import base64
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import tempfile
import threading
import uuid

import firebase_admin
from firebase_admin import auth
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from agent import Android, TURN_ID, api_call, configure_logging, load_environment, run_turn, transcribe
from configuration_tools import ConfigurationTools, SYSTEM, TOOLS

LOGGER = logging.getLogger("voice_agent_api")
BEARER = HTTPBearer(auto_error=False)
MAX_JSON_BYTES = 64 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024
_firebase_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_environment()
    configure_logging(os.getenv("AGENT_LOG_FILE", "logs/agent.jsonl"), os.getenv("AGENT_LOG_LEVEL", "WARNING"))
    app.state.turn_lock = asyncio.Lock()
    yield


app = FastAPI(title="Authenticated voice agent", lifespan=lifespan)


@app.middleware("http")
async def prevent_agent_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def firebase_app():
    # Do not accept emulator tokens on this authentication boundary.
    if os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        raise RuntimeError("Firebase Auth emulator is not supported by this service")
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    if not project_id:
        raise RuntimeError("FIREBASE_PROJECT_ID is not configured")
    with _firebase_lock:
        try:
            instance = firebase_admin.get_app("voice-agent-api")
        except ValueError:
            instance = firebase_admin.initialize_app(options={"projectId": project_id}, name="voice-agent-api")
        if instance.project_id != project_id:
            raise RuntimeError("Firebase project ID does not match the initialized app")
        return instance


async def require_user(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER)) -> str:
    if not credentials or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Firebase ID token required", headers={"WWW-Authenticate": "Bearer"})
    try:
        instance = firebase_app()
    except Exception:
        LOGGER.exception("Firebase initialization unavailable")
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from None
    try:
        claims = await run_in_threadpool(auth.verify_id_token, credentials.credentials, app=instance, check_revoked=True)
    except (auth.InvalidIdTokenError, auth.UserDisabledError, auth.UserNotFoundError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid, expired, revoked, disabled, or deleted Firebase user token", headers={"WWW-Authenticate": "Bearer"}) from None
    except Exception:
        LOGGER.exception("Firebase token verification unavailable")
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from None
    uid = claims.get("uid")
    if not isinstance(uid, str) or not uid:
        raise HTTPException(status_code=401, detail="Invalid Firebase ID token", headers={"WWW-Authenticate": "Bearer"})
    allowed_value = os.getenv("ALLOWED_FIREBASE_UIDS", "")
    allowed = {part.strip() for part in allowed_value.split(",") if part.strip()}
    if allowed_value and uid not in allowed:
        raise HTTPException(status_code=403, detail="User is not permitted to use this agent")
    return uid


class TurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=4000)


class SpeechInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class AndroidConfigurationTools(ConfigurationTools):
    """Configuration file tools with camera access through the connected Android device."""
    def get_image(self):
        return Android(os.getenv("ANDROID_SERIAL") or None).get_image()


async def read_limited(request: Request, limit: int) -> bytes:
    try:
        declared = request.headers.get("content-length")
        if declared is not None and int(declared) < 0:
            raise ValueError
        if declared is not None and int(declared) > limit:
            raise HTTPException(status_code=413, detail="Request body too large")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length") from None
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="Request body too large")
        data.extend(chunk)
    return bytes(data)


def execute_turn(command: str) -> str:
    from google import genai
    client = genai.Client()
    tools = AndroidConfigurationTools(os.getenv("CONFIG_FILE", "settings.json"))
    # Each request is stateless; never share one user's conversation with another.
    return run_turn(client, tools, [], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), command,
                    tool_definitions=TOOLS, instructions=SYSTEM)


def transcribe_audio(data: bytes) -> str:
    from google import genai
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
        f.write(data)
    try:
        return transcribe(genai.Client(), path, os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe"))
    finally:
        Path(path).unlink(missing_ok=True)


def generate_speech(text: str) -> bytes:
    from google import genai
    request = {"model": os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
               "input": [{"type": "user_input", "content": [{"type": "text", "text": text}]}],
               "response_format": {"type": "audio", "mime_type": "audio/wav"},
               "generation_config": {"speech_config": [{"voice": "Kore"}]}}
    response = api_call("text_to_speech", request, lambda: genai.Client().interactions.create(**request))
    if not response.output_audio or not response.output_audio.data:
        raise RuntimeError("Gemini returned no speech audio")
    return base64.b64decode(response.output_audio.data)


@app.get("/v1/me")
async def me(uid: str = Depends(require_user)):
    return {"uid": uid}


@app.post("/v1/agent/turn")
async def text_turn(request: Request, uid: str = Depends(require_user)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Expected application/json")
    try:
        body = TurnInput.model_validate_json(await read_limited(request, MAX_JSON_BYTES))
    except ValidationError:
        raise HTTPException(status_code=422, detail="Expected a non-empty command, at most 4000 characters") from None
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=422, detail="Command cannot be blank")
    return await run_authenticated_turn(request, command)


@app.post("/v1/agent/audio")
async def audio_turn(request: Request, uid: str = Depends(require_user)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {"audio/wav", "audio/x-wav"}:
        raise HTTPException(status_code=415, detail="Expected audio/wav")
    audio = await read_limited(request, MAX_AUDIO_BYTES)
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise HTTPException(status_code=400, detail="Expected WAV audio")
    async with request.app.state.turn_lock:
        token = TURN_ID.set(uuid.uuid4().hex)
        try:
            command = await run_in_threadpool(transcribe_audio, audio)
            if not command:
                raise HTTPException(status_code=422, detail="No speech recognized")
            reply = await run_in_threadpool(execute_turn, command)
        except HTTPException:
            raise
        except Exception:
            LOGGER.exception("Audio agent request failed")
            raise HTTPException(status_code=502, detail="Agent request failed") from None
        finally:
            TURN_ID.reset(token)
    return {"transcript": command, "reply": reply}


@app.post("/v1/agent/speech")
async def speech(request: Request, uid: str = Depends(require_user)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Expected application/json")
    try:
        body = SpeechInput.model_validate_json(await read_limited(request, MAX_JSON_BYTES))
    except ValidationError:
        raise HTTPException(status_code=422, detail="Expected text of at most 4000 characters") from None
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Text cannot be blank")
    token = TURN_ID.set(uuid.uuid4().hex)
    try:
        audio = await run_in_threadpool(generate_speech, body.text.strip())
    except Exception:
        LOGGER.exception("Speech request failed")
        raise HTTPException(status_code=502, detail="Speech generation failed") from None
    finally:
        TURN_ID.reset(token)
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


async def run_authenticated_turn(request: Request, command: str):
    async with request.app.state.turn_lock:
        token = TURN_ID.set(uuid.uuid4().hex)
        try:
            reply = await run_in_threadpool(execute_turn, command)
        except Exception:
            LOGGER.exception("Text agent request failed")
            raise HTTPException(status_code=502, detail="Agent request failed") from None
        finally:
            TURN_ID.reset(token)
    return {"reply": reply}
