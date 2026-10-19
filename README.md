# voice-agent-101

Hindi/Hinglish voice agent built with [Pipecat](https://pipecat.ai): Sarvam STT → OpenRouter LLM → Sarvam TTS, packaged for [Pipecat Cloud](https://docs.pipecat.ai/pipecat-cloud).

## Layout

| File | Purpose |
| --- | --- |
| `bot.py` | Pipecat Cloud entrypoint. Exposes `async def bot(runner_args)` and builds the transport. |
| `agent.py` | The pipeline: transport → Sarvam STT → LLM context → OpenRouter LLM → Sarvam TTS → transport. |
| `metrics.py` | Optional CloudWatch EMF concurrency metric (no-op unless `METRICS_NAMESPACE` is set). |
| `Dockerfile` | Pipecat Cloud image based on `dailyco/pipecat-base`, built for `linux/arm64`. |
| `pcc-deploy.toml` | Deployment config; no `image` key, so `pipecat cloud deploy` uses a cloud build. |

`bot.py` is the file the base image runs. Pipecat Cloud calls into `bot()` over its `POST /bot` route; no `CMD` is required in the Dockerfile.

## Deploy to Pipecat Cloud

Prerequisites: an active Pipecat Cloud account and the CLI (`pip install pipecatcloud` or `uv tool install pipecatcloud`).

```bash
# 1. Authenticate
pipecat cloud auth login

# 2. Create the secret set referenced by pcc-deploy.toml
pipecat cloud secrets set voice-agent-secrets \
  SARVAM_API_KEY=... \
  OPENROUTER_API_KEY=...

# 3. Build in the cloud from ./Dockerfile and deploy
pipecat cloud deploy
```

Useful overrides:

```bash
pipecat cloud deploy --region us-west --min-agents 1
pipecat cloud agent status voice-agent-101
```

`pcc-deploy.toml` already sets `agent_name`, `secret_set`, `region`, `agent_profile` and scaling.

## Docker notes

- Base image: `dailyco/pipecat-base:latest-py3.13` (matches `requires-python >=3.13`).
- Daily-hosted Pipecat Cloud regions run ARM, so build with `--platform linux/arm64`.
- `uv sync` uses `--inexact`: without it, uv prunes packages the base image ships (notably `pipecatcloud`), and the container exits at startup with `ModuleNotFoundError: No module named 'pipecatcloud'`.
- Dependencies come from `uv.lock`; project code is copied separately so the dependency layer caches.

Build locally:

```bash
docker build --platform linux/arm64 -t voice-agent-101 .
```

## Run locally

```bash
uv run python bot.py -t webrtc   # browser (SmallWebRTC)
uv run python bot.py -t daily    # Daily
uv run python bot.py -t websocket --host 127.0.0.1 --port 7860
```

The WebSocket transport streams PCM16 mono: **16 kHz in, 24 kHz out**, in ~40 ms frames.
