# Deploying the voice agent to AWS ECS + Fargate

One container image runs the Pipecat runner. It is transport-agnostic: the same
image serves a browser (WebRTC/SFU), phone calls (Twilio/Telnyx), or a custom
WebSocket client. What changes is the entrypoint flag (`TRANSPORT`) and the
client — not the infrastructure.

```
                    ┌──────────────┐
   browser / phone  │     ALB      │  HTTPS/WSS :443, sticky cookie
   ────────────────►│  (public)    │
                    └──────┬───────┘
                           │ :7860
              ┌────────────▼────────────┐
              │   ECS Fargate service   │  N tasks, autoscaled
              │  ┌───────────────────┐  │
              │  │ pipecat runner    │  │  one bot() per conversation
              │  │ + Silero VAD      │  │
              │  └─────────┬─────────┘  │
              └────────────┼────────────┘
                           │ outbound
             ┌─────────────┼──────────────┬─────────────┐
             ▼             ▼              ▼             ▼
          Sarvam       OpenRouter    SFU (Daily/     CloudWatch
          STT/TTS        LLM         LiveKit/TURN)   (EMF metric)
```

## The one real constraint: raw WebRTC over UDP on Fargate

`SmallWebRTC` (aiortc) carries media over UDP to the client. On Fargate that
does not work out of the box:

- The task sits behind NAT. The only way in is through a load balancer, and
  Fargate cannot publish a UDP port range (one task = one ENI, fixed ports only).
- aiortc gathers **host** candidates = the task's private ENI IP → unreachable.
- STUN gives a **server-reflexive** candidate = the NAT's public IP, but the NAT
  gateway only forwards replies to outbound flow — unsolicited inbound UDP is
  dropped → still unreachable.

Consequence: with the current `TRANSPORT=webrtc`, HTTP signalling and ICE work
(you will even see `ICE connection state is completed`), but **no audio flows**
unless every session is relayed through a public TURN server.

You have three ways forward:

| Option | Entrypoint | Inbound ports | Notes |
| --- | --- | --- | --- |
| **Managed SFU** (recommended for browser) | `-t daily` / `-t livekit` | none — bot dials **out** over WSS | Fargate scales cleanly, no TURN to run |
| **Telephony** (recommended for phone) | `-t twilio` / `-t telnyx` | TCP 443 only (Media Streams) | Call audio arrives as WSS via the ALB |
| **Keep SmallWebRTC** | `-t webrtc` | needs public relay | Run coturn (EC2, host networking) or a managed TURN (e.g. Twilio Network Traversal) and set `PIPECAT_ICE_SERVERS`; media relays through it, which becomes a second scaling tier |

The infrastructure below is identical for all three; only `TRANSPORT` and the
client change. To switch to Daily, add the extra and re-lock:

```bash
uv add "pipecat-ai[daily]"      # then rebuild the image
```

## Prerequisites

- Docker
- AWS CLI v2, configured (`aws sts get-caller-identity` works)
- Node.js (only to run the CDK CLI via `npx`)
- CDK bootstrapped once per account/region:

```bash
CDK_DEFAULT_REGION=ap-south-1 npx aws-cdk@2 bootstrap
```

## Deploy

```bash
export CDK_DEFAULT_REGION=ap-south-1   # keep this consistent: the VPC lookup is regional
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

cd deploy/infra
npx aws-cdk@2 deploy --require-approval never \
  -c transport=webrtc \
  -c minTasks=2 -c maxTasks=30 -c sessionsPerTask=3
```

CDK builds `deploy/Dockerfile`, pushes it to the CDK asset ECR repo, and creates
the cluster, service, ALB, autoscaling, secrets and log group.

### Load the API keys

The stack creates two Secrets Manager secrets but does not know their values.
Set them once (the tasks pick them up on the next start):

```bash
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id voice-agent/sarvam-api-key     --secret-string 'sk_...'
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id voice-agent/openrouter-api-key --secret-string 'sk-or-...'

aws ecs update-service --region ap-south-1 \
  --cluster $(aws cloudformation describe-stacks --region ap-south-1 \
      --stack-name VoiceAgentStack \
      --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text) \
  --service $(aws cloudformation describe-stacks --region ap-south-1 \
      --stack-name VoiceAgentStack \
      --query "Stacks[0].Outputs[?OutputKey=='ServiceName'].OutputValue" --output text) \
  --force-new-deployment
```

### HTTPS

Browsers only expose the microphone on a secure origin, so a real deployment
needs a certificate. Point a domain at the ALB and pass the ACM ARN:

```bash
npx aws-cdk@2 deploy -c certificateArn=arn:aws:acm:ap-south-1:...:certificate/... \
  -c publicHost=agent.example.com -c allowedOrigins=https://agent.example.com
```

Without `certificateArn` the stack listens on plain HTTP :80 — fine for a quick
smoke test, useless for an actual browser call.

## Configuration

All optional, passed as `-c key=value`:

| Key | Default | Meaning |
| --- | --- | --- |
| `transport` | `webrtc` | Runner `-t` flag (`webrtc`, `daily`, `livekit`, `twilio`, `telnyx`, `websocket`) |
| `minTasks` | `2` | Floor for the service (2 = one per AZ) |
| `maxTasks` | `30` | Ceiling for autoscaling |
| `sessionsPerTask` | `3` | Concurrent conversations a single 2 vCPU task is expected to hold |
| `certificateArn` | – | ACM cert ARN → enables HTTPS :443 and HTTP→HTTPS redirect |
| `publicHost` | – | Public hostname, used for SDP/webhook URLs |
| `allowedOrigins` | `*` | CORS/WebSocket allow-list (comma-separated) |
| `vpcId` | default VPC | Use an existing VPC instead of the account default |
| `assignPublicIp` | `true` | Needed on the default VPC (public subnets, no NAT) |

## How scaling works

The unit of scale is **one concurrent conversation = one `bot()` invocation**.
Each session runs Silero VAD (ONNX, CPU-bound) plus audio resampling, so a
session is roughly 0.3–0.7 vCPU. Two mechanisms drive the service:

1. **Target tracking on CPU at 60%** — baseline safety net.
2. **Step scaling on the `ActiveSessions` metric** — the real signal. `bot()`
   emits its live-session count as a CloudWatch EMF log line (`metrics.py`), so
   no SDK calls and no `PutMetricData` permission are needed. Scale out one task
   at `sessionsPerTask`, two at `2 × sessionsPerTask`; scale in below one
   session.

Because the scaling signal is concurrency rather than HTTP traffic, bursts of
new callers add tasks immediately instead of waiting for CPU to saturate.

**Session affinity is mandatory.** The WebRTC peer connection and the runner's
session store (`active_sessions`) live in task memory, so the offer and the
follow-up ICE patches must land on the same task. The ALB target group sets an
8-hour stickiness cookie; without it, signalling silently breaks when a session
is spread across tasks.

## Operating it

```bash
# tail logs
aws logs tail /ecs/voice-agent --follow --region ap-south-1

# shell into a task (container has Session Manager enabled)
aws ecs execute-command --region ap-south-1 --cluster <ClusterName> \
  --task <task-id> --container bot --interactive --command "/bin/sh"

# what the service thinks it supports
curl -s http://<LoadBalancerDns>/status
```

### Caveats worth knowing

- Scaling in or replacing a task **drops its in-flight conversations** —
  clients reconnect into a new session. Fine for short interactions; if you run
  long phone calls, raise `deregistration_delay` and avoid aggressive scale-in.
- The runner keeps sessions in local memory. If you ever run more than one
  task *and* need cross-task session continuity, the session store has to move
  to Redis/ElastiCache — the ALB sticky cookie only covers the single-task case.
- `ActiveSessions` is a 1-minute CloudWatch metric, so scale-in has a cooldown
  of 2 minutes to avoid flapping.

## Rough cost (ap-south-1, on-demand)

- 2 × Fargate tasks @ 2 vCPU / 4 GB: ~$0.09/hr → ~$65/mo if always on
- ALB: ~$18/mo + LCU
- Secrets Manager: $0.40/secret/mo
- CloudWatch Logs + EMF metric: usually a few $

Scaling to zero is not configured (min 2 for HA). Drop `minTasks=1` for a demo.

## Teardown

```bash
cd deploy/infra && npx aws-cdk@2 destroy
aws secretsmanager delete-secret --region ap-south-1 \
  --secret-id voice-agent/sarvam-api-key --force-delete-without-recovery
aws secretsmanager delete-secret --region ap-south-1 \
  --secret-id voice-agent/openrouter-api-key --force-delete-without-recovery
```
