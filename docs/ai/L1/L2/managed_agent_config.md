# Managed Agent Config

> **When to Read This:** Load this document when you are changing the agent's prompt, voice, VAD behavior, model selection, session options, or wiring a bring-your-own-key (BYOK) provider on the Python side.

## Where It Lives

All managed agent configuration is in `server/src/agent.py`. The browser sends `{ channelName, rtcUid, userUid }` to `POST /startAgent`, which the FastAPI handler forwards to `agent.start(...)`. That method builds an SDK-driven agent and creates an async session.

## The Agent Builder Chain

The standard `AsyncAgora` client is constructed once. All three provider stages reuse `GOOGLE_API_KEY`; Gemini LLM and MiniMaxTTS are Google-backed rather than Agora-managed.

```python
from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import Agent as AgoraAgent
from agora_agent.agentkit.preview import GeminiSTT
from agora_agent.agentkit.vendors import Gemini, MiniMaxTTS

self.client = AsyncAgora(
    area=Area.US,
    app_id=self.app_id,
    app_certificate=self.app_certificate,
)

agora_agent = AgoraAgent(
    client=self.client,
    instructions=ADA_PROMPT,
    greeting=self.greeting,
    failure_message="Please wait a moment.",
    turn_detection={"language": "en-US", "config": {...}},
    advanced_features={"enable_rtm": True, "enable_tools": True},
    parameters={
        "audio_scenario": "chorus",
        "data_channel": "rtm",
        "enable_error_message": True,
        "enable_metrics": True,
    },
).with_stt(GeminiSTT(
    api_key=self.google_api_key,
    language_codes=["en-US"],
    custom_vocabulary=["Agora", "Gemini"],
    word_timestamp=False,
)).with_llm(Gemini(
    api_key=self.google_api_key,
    model="gemini-3.6-flash",
    greeting_message=self.greeting,
    failure_message="Please wait a moment.",
    max_history=15,
    max_output_tokens=1024,
    temperature=0.7,
    top_p=0.95,
)).with_tts(MiniMaxTTS(
    key=self.google_api_key,
    voice_name="en-US-Chirp3-HD-Charon",
    language_code="en-US",
    sample_rate_hertz=24000,
))
```

## Editing Each Surface

### Change the prompt

Edit the `ADA_PROMPT` string constant at the top of `agent.py`. Keep it concise — long prompts amplify LLM latency.

### Change the greeting

Set `AGENT_GREETING` in `server/.env.local`, or change the inline fallback string in `Agent.__init__`.

### Change VAD

Edit the `turn_detection` dict on `AgoraAgent`. The shape uses a `"config"` wrapper key with nested `start_of_speech` and `end_of_speech` blocks — do **not** use the deprecated flat `"start"`/`"end"` keys. Tuning notes:

- `speech_threshold` — VAD activation sensitivity (0.0–1.0). Lower values trigger on quieter audio.
- `interrupt_duration_ms` — minimum user speech before the agent yields. Lower = more responsive interruptions.
- `prefix_padding_ms` — audio captured before VAD triggers; raise if early phonemes are clipped.
- `silence_duration_ms` — silence after speech before VAD ends the turn. Raise for slow speakers.

### Swap STT / LLM / TTS

The defaults are `GeminiSTT` (`gemini-3.5-transcribe-live`), Gemini `gemini-3.6-flash`, and MiniMaxTTS (`en-US-Chirp3-HD-Charon`, `en-US`, 24000 Hz). They all reuse `GOOGLE_API_KEY`. Replace the corresponding constructor only for an intentional provider change, and document any new credential in `server/.env.example`.

Gemini custom vocabulary and word timestamps are incompatible. Keep `word_timestamp=False` explicit whenever `custom_vocabulary` is configured; the SDK rejects `word_timestamp=True` with a custom vocabulary.

### Session-Level Tuning

- `idle_timeout` (seconds) — drop to 15 for short demos.
- `expires_in` (seconds) — keep aligned with `token_expire` in `get_config`.
- `agent_uid` — the UID the agent occupies in the channel; must match the value the browser expects.
- `enable_string_uid` — `False` keeps UIDs numeric for both RTC and RTM. Flipping to `True` requires matching changes in the browser join path.

`data_channel`, `enable_error_message`, and `enable_metrics` are session-level parameters but live in the `parameters=` dict on `AgoraAgent`, not in `create_async_session`.

## Async Lifetime

`Agent` is constructed once at module import (`agent = Agent()` in `server.py`). Every request reuses the same instance. `Agent.start` returns the new session metadata; `Agent.stop(agent_id)` ends the corresponding session.

Do not hold per-request state on `Agent`. If you need correlation, use the returned `agent_id` and pass it through subsequent calls.

## Response Contract

`startAgent` returns:

```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "agent_id": "string",
    "channel_name": "string",
    "status": "started"
  }
}
```

The client stores `agent_id` in `agoraData` and later passes it to `/api/stopAgent`.

Stop removes the retained session and calls `session.stop()`. An unknown or repeated agent ID is treated as an idempotent no-op; the preview-provider flow never uses standalone `client.stop_agent`.

## Verification

`bun run verify:backend` runs `py_compile` so syntax errors surface, but it does not exercise behavior. `bun run verify:local:fastapi` runs the FastAPI app with `FakeAgent` patched in to ensure routes match expected shapes without touching the real Agora cloud.

After editing `agent.py`, run:

```bash
bun run verify:backend
bun run verify:local:fastapi
bun run verify:web:api
```

## Failure Modes

| Symptom                                                | Cause                                                                  |
| ------------------------------------------------------ | ---------------------------------------------------------------------- |
| Routes return `500 Service not properly configured`    | Missing `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE`, or `GOOGLE_API_KEY`; `Agent()` raises `ValueError` at import and `agent` stays `None`. |
| `400` from `/startAgent` on a valid request            | `Agent.start` raised `ValueError` — usually missing UID fields.         |
| Agent joins but never speaks                           | `GOOGLE_API_KEY` missing/invalid or MiniMaxTTS voice settings changed incorrectly. |
| Agent state stuck in `IDLE`                            | `enable_rtm` missing from `advanced_features` or RTM not subscribed yet. |
| Transcript fragments arrive but no metrics             | `parameters.enable_metrics` not set.                                     |
| Import error on `from agora_agent.agentkit import ...` | SDK version mismatch; `pip install -r server/requirements.txt`.          |

## See Also

- [Back to Architecture](../02_architecture.md)
- [Back to Workflows](../05_workflows.md)
- [Session Lifecycle](session_lifecycle.md)
