# Android voice agent

A local, push-to-talk assistant that navigates an Android app and changes its settings through tool calls. It runs on your computer and controls a connected Android device or emulator over ADB. The agent reads the UI Automator accessibility hierarchy, chooses a control, taps or scrolls, and inspects the result. LiveKit’s Silero voice activity detector captures speech until a pause, then the audio is transcribed with Gemini, and the reply is spoken using Gemini text-to-speech. A text-only mode is available for debugging.

## Requirements

- Python 3.10 or newer and a [Gemini API key](https://aistudio.google.com/api-keys) with access to the configured models.
- Android SDK Platform Tools (`adb`) on your PATH. Enable Developer options and USB debugging on the device, authorize this computer, and keep the device unlocked. An Android emulator also works.
- For voice mode, a microphone and speaker, and permission for the terminal to access the microphone. `sounddevice` requires PortAudio; on macOS you can install it with `brew install portaudio` if necessary.
- An app that exposes usable text, labels, and controls to Android UI Automator. Games, custom canvases, some WebViews, secure screens, and apps with poor accessibility may not expose enough information.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GEMINI_API_KEY.
adb devices
```

Both scripts load the `.env` file next to `agent.py` at startup. Existing shell environment variables take precedence; `.env` is gitignored. You can also set variables directly in your shell.

Check that `adb devices` lists the device as `device`, then open the target app on the device. If multiple devices are connected, pass `--serial SERIAL` from `adb devices`.

```sh
python agent.py                         # press Enter and speak; pause to finish
python agent.py --silence-seconds 1.2    # wait longer before ending a turn
python agent.py --text          # typed commands, no microphone or audio output
```

Try: “Open settings and turn off notifications” or “Set the theme to dark.” The agent prints each tool call and responds after the task. Follow-up requests share conversation context; type `quit` to exit. Recording ends after silence; the agent processes one turn at a time and is not interruption-aware. The microphone and speaker are on the computer, not on the Android device.

Optional environment variables: `GEMINI_MODEL` (default `gemini-3.8-flash`), `GEMINI_TRANSCRIBE_MODEL` (default `gemini-3.8-flash`), and `GEMINI_TTS_MODEL` (default `gemini-3.8-flash-tts`). Model availability depends on your account. API usage incurs charges.

## How it works

`inspect_screen`, `tap`, `scroll`, `back`, and `type_text` are exposed as function tools. The model uses the current hierarchy to choose actions; the executor rejects stale tap indexes and caps the number of calls per turn. Screen text and spoken commands are sent to Google; this may include information visible in the app. Avoid using sensitive screens or entering credentials. Text entry supports a limited ASCII character set and does not clear existing text automatically. The agent can operate any screen reachable on the connected device; supervise its actions. Confirmation for consequential actions is prompt-based, not a security boundary.

For a lower-latency, continuous voice experience, consider the Gemini Live API with a realtime voice pipeline. For more advanced Android automation, consider Appium with its UiAutomator2 driver; this prototype deliberately uses ADB and the device's built-in UI Automator to minimize setup. Neither can reliably control UI elements that apps do not expose.

References: [LiveKit Silero VAD](https://docs.livekit.io/agents/logic/turns/vad/), [Gemini audio understanding](https://ai.google.dev/gemini-api/docs/audio), [function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling), [text-to-speech](https://ai.google.dev/gemini-api/docs/speech-generation), [Live API](https://ai.google.dev/gemini-api/docs/live), [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools), [Appium UiAutomator2 driver](https://github.com/appium/appium-uiautomator2-driver).

## Logs

By default, the agent writes structured JSON lines to `logs/agent.jsonl` relative to the current working directory. Each event has a timestamp and a turn ID; API requests and responses also share a request ID and include elapsed time. Events cover Gemini requests and responses (including usage metadata when returned), tool arguments and results, and API or turn errors. Audio bytes and thought signatures are omitted. Logs rotate at 10 MB, retaining three backups, and the default log directory is gitignored.

```sh
python agent.py --log-file /path/to/agent.jsonl  # change the destination
python agent.py --log-level WARNING               # errors only
python agent.py --log-level OFF                   # disable file logging
```

You can also set `AGENT_LOG_FILE` and `AGENT_LOG_LEVEL`. INFO logs contain conversation text, screen contents, and tool arguments and may include sensitive app data. Store and share them accordingly. The agent removes configured API key values from logged strings, but does not otherwise redact personal information. Existing console output remains separate from the file log settings.

## Test voice without Android

Use the standalone conversation test to check the microphone, Gemini transcription, the model response, and spoken output without ADB or an Android device. Install the same requirements and set `GEMINI_API_KEY` as described above, then run:

```sh
python voice_test.py                       # press Enter, speak, then pause
python voice_test.py --silence-seconds 1.2  # wait longer before ending a turn
python voice_test.py --text         # test Gemini conversation without audio
```

Type `quit` at the prompt to exit. Both scripts wait up to 15 seconds for speech to start, then up to 60 seconds for the utterance to end. Change these limits with `--start-timeout` and `--max-speech-seconds`. If a limit is reached, that turn is discarded and you can try again. LiveKit’s Silero VAD runs locally; no LiveKit server or account is needed. The test keeps conversation history in memory, but it has no Android tools and cannot change device settings. It uses the same model environment variables and logging options as `agent.py`.

The voice test displays its current stage in the terminal and writes status events to the same JSONL log: recording, transcription (including the recognized text), response generation, speech playback, completion, and errors. Each turn shares a turn ID. Transcripts and responses in the logs can contain sensitive information.

With INFO logging enabled, each WAV sent for transcription is also saved in an `audio/` directory next to the JSONL log. A `transcription_audio` event records its path, size, and turn ID. These recordings can contain sensitive speech: delete them when no longer needed. Audio files are not covered by JSONL log rotation. Use `--log-level WARNING` or `OFF` to disable saving them.

## OpenAI conversation fallback

Optionally set `OPENAI_API_KEY` (or `OPEN_AI_API_KEY`) in `.env` to fall back when a Gemini **conversation model request** times out or returns HTTP 503:

```sh
# Add OPENAI_API_KEY to .env (or export it in your shell).
# Optionally set GEMINI_LLM_TIMEOUT_SECONDS and OPENAI_FALLBACK_MODEL.
python voice_test.py
```

Run `pip install -r requirements.txt` again after updating the project. The timeout defaults to 10 seconds, Gemini’s minimum; accepted values are 10–120 seconds. It configures the Gemini SDK's HTTP timeout in seconds and disables Gemini retries for these requests; it is not a guaranteed end-to-end wall-clock deadline. On a timeout or HTTP 503, the current turn continues with OpenAI, including Android tool access in `agent.py`, and the next turn tries Gemini again. A fallback event and OpenAI request/response are logged. Other Gemini errors do not trigger fallback. Without `OPENAI_API_KEY`, Gemini runs normally without this fallback timeout.

Gemini transcription also falls back to OpenAI on HTTP 503 (default `OPENAI_TRANSCRIBE_MODEL=gpt-transcribe`); the audio is sent to OpenAI when this happens. Transcription timeouts and speech generation errors do not trigger a fallback. Conversation history and, for Android tasks, screen/tool data are sent to OpenAI when fallback activates. OpenAI usage may incur separate charges.

## JSON configuration and camera agent

Run the tool-based configuration agent independently of ADB:

```sh
uv run --with opencv-python-headless configuration_agent.py  # microphone and webcam
uv run --with opencv-python-headless configuration_agent.py --text  # typed requests
uv run --with opencv-python-headless configuration_agent.py --config /path/to/settings.json --camera-index 0
```

It uses `get_configuration`, `update_configuration`, and `get_image` tool calls. Function names use underscores because the model APIs require identifiers without spaces. The included `settings.json` is a local example: ask “What sound is configured for the chair?” or “Set the chair sound to chime.” Updates target existing scalar values and are written to the JSON file; they do not change an Android app unless that app actually reads the same file. The file contains current values, not a schema of all permitted values, so the agent cannot validate app-specific choices beyond retaining each value's JSON type.

`get_image` captures a single frame from the computer webcam (index 0 by default). Grant your terminal camera permission. The frame is sent to the selected model for visual analysis, including OpenAI if fallback occurs. Camera bytes are omitted from logs. No photo is captured until the tool is called. Set `--camera-index` or `CAMERA_INDEX` for another webcam, and `--config` or `CONFIG_FILE` for another settings file. The file must already exist and contain a JSON object.

## Test tool selection on a laptop

To test whether the voice agent selects the appropriate app tools without Android, a webcam, or changing the real configuration, run:

```sh
uv run tool_test.py          # speak a request
uv run tool_test.py --text   # type a request
```

Try “What sound is set for the chair?”, “Set the chair sound to chime”, and “Get the camera view.” The terminal and JSONL logs show each `get_configuration`, `update_configuration`, or `get_image` call and its arguments. The test starts from `settings.json` (or a file supplied with `--config`) and keeps changes only in a temporary copy for the lifetime of the process. `get_image` returns a simulated result without image data; it cannot describe a real view. Gemini and the OpenAI fallback are still used and require their configured credentials. The Android camera integration is not implemented yet.

### Android camera view

The main `agent.py` also exposes `get_image`. For a camera request, it opens the connected Android device's camera app with the standard still-camera intent, waits for the preview, takes an ADB screenshot of the viewfinder, and presses Back. The screenshot is sent to Gemini or the OpenAI fallback for analysis. It does not press the shutter or save a photo. The device must be unlocked, its camera app available, and any first-run camera permissions already granted. Depending on the device and camera app, protected or hardware-rendered previews may appear black in screenshots; a permission prompt or camera controls may also be visible. If that happens, the agent cannot reliably describe the scene. Camera screenshots are not written to the JSON log.
