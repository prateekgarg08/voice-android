#!/usr/bin/env python3
"""Test the Gemini voice conversation without an Android device or ADB."""
import argparse
import os
import sys
import time
import uuid

from agent import (TURN_ID, api_call, configure_logging, gemini_http_options, gemini_fallback_reason,
                   load_environment, log_event, openai_fallback, record, speak, transcribe)

SYSTEM = """You are a helpful voice assistant. Respond naturally and concisely.
This is a voice test without Android control; do not claim to have operated a device or changed settings."""


def respond(client, history, command, model):
    """Generate a conversational response, keeping the history in memory."""
    from google.genai import types

    history.append(types.Content(role="user", parts=[types.Part(text=command)]))
    request = {"model": model, "contents": history,
               "config": types.GenerateContentConfig(system_instruction=SYSTEM, http_options=gemini_http_options(types))}
    try:
        response = api_call("voice_test_conversation", request,
                            lambda: client.models.generate_content(**request))
    except Exception as exc:
        reason = gemini_fallback_reason(exc) if os.getenv("OPENAI_API_KEY") else None
        if reason:
            return openai_fallback(history, SYSTEM, reason=reason)
        raise
    if not response.candidates or not response.candidates[0].content or not response.text:
        raise RuntimeError("Gemini returned no text response.")
    history.append(response.candidates[0].content)
    return response.text


def main():
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="Use typed input and output to test the conversation without audio")
    parser.add_argument("--silence-seconds", type=float, default=0.8, help="Silence required to end a voice turn (default: 0.8 seconds)")
    parser.add_argument("--start-timeout", type=float, default=15, help="Seconds to wait for speech to start (default: 15)")
    parser.add_argument("--max-speech-seconds", type=float, default=60, help="Maximum speech duration (default: 60 seconds)")
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    parser.add_argument("--log-file", default=os.getenv("AGENT_LOG_FILE", "logs/agent.jsonl"))
    parser.add_argument("--log-level", choices=["INFO", "WARNING", "OFF"], default=os.getenv("AGENT_LOG_LEVEL", "INFO"))
    args = parser.parse_args()
    if not 0.1 <= args.silence_seconds <= 5 or not 0 < args.start_timeout <= 120 or not 0 < args.max_speech_seconds <= 120:
        parser.error("Use silence 0.1–5 seconds and timeouts greater than 0 and at most 120 seconds")
    if args.log_level not in ("INFO", "WARNING", "OFF"):
        parser.error("Invalid AGENT_LOG_LEVEL")
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        parser.error("Set GEMINI_API_KEY before starting")
    try:
        configure_logging(args.log_file, args.log_level)
    except OSError as exc:
        parser.error(f"Cannot open log file: {exc}")
    from google import genai
    client = genai.Client()
    history = []
    log_event("voice_test_ready", mode="text" if args.text else "voice", model=args.model)
    print("Voice test ready. No Android device or ADB is required. Type 'quit' to exit.")
    print("OpenAI fallback:", "enabled" if os.getenv("OPENAI_API_KEY") else "disabled (set OPENAI_API_KEY in .env)")
    while True:
        try:
            entry = input("Command: " if args.text else "Press Enter to record, or type quit: ").strip()
            if entry.lower() in ("quit", "exit"):
                log_event("voice_test_stopped", reason="user_exit")
                break
            TURN_ID.set(uuid.uuid4().hex)
            log_event("voice_turn_started", mode="text" if args.text else "voice")
            if args.text:
                command = entry
                log_event("voice_input_received", text=command, source="typed")
            else:
                log_event("recording_started", silence_seconds=args.silence_seconds)
                started = time.perf_counter()
                path = record(args.silence_seconds, args.start_timeout, args.max_speech_seconds)
                log_event("recording_completed", duration_ms=round((time.perf_counter() - started) * 1000, 1))
                try:
                    print("Transcribing...", flush=True)
                    log_event("transcription_started", model=os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe"))
                    started = time.perf_counter()
                    command = transcribe(client, path, os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe"))
                    log_event("transcription_completed", text=command, duration_ms=round((time.perf_counter() - started) * 1000, 1))
                finally:
                    os.unlink(path)
                print(f"You: {command}")
            if not command:
                log_event("voice_turn_completed", outcome="empty_input")
                print("No speech or text received.")
                continue
            print("Thinking...", flush=True)
            log_event("response_started", model=args.model)
            started = time.perf_counter()
            reply = respond(client, history, command, args.model)
            log_event("response_completed", text=reply, duration_ms=round((time.perf_counter() - started) * 1000, 1))
            print(f"Agent: {reply}")
            if not args.text:
                print("Generating and playing speech...", flush=True)
                log_event("speech_started", model=os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"))
                started = time.perf_counter()
                speak(client, reply, os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"))
                log_event("speech_completed", duration_ms=round((time.perf_counter() - started) * 1000, 1))
            log_event("voice_turn_completed", outcome="success")
        except (KeyboardInterrupt, EOFError):
            log_event("voice_test_stopped", reason="interrupted")
            print("\nStopped.")
            break
        except Exception as exc:
            log_event("turn_error", error_type=type(exc).__name__, error=str(exc))
            print(f"Error: {exc}", file=sys.stderr)
            history.clear()


if __name__ == "__main__":
    main()
