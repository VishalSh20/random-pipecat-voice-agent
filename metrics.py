# metrics.py
"""Concurrency telemetry.

Emits the number of live sessions as a CloudWatch EMF metric on stdout. The
CloudWatch agent parses the JSON log line and publishes the ``ActiveSessions``
metric, which the ECS service autoscaling policy targets. No AWS SDK calls are
made from the process, so this is free to run and safe when running locally
(disabled unless ``METRICS_NAMESPACE`` is set).
"""

import json
import os
import time
from contextlib import asynccontextmanager

NAMESPACE = os.getenv("METRICS_NAMESPACE")
SERVICE = os.getenv("METRICS_SERVICE", "voice-agent")
CLUSTER = os.getenv("ECS_CLUSTER", "voice-agent")

_active = 0


def _emit(value: int) -> None:
    if not NAMESPACE:
        return
    event = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [["Service", "ClusterName"]],
                    "Metrics": [{"Name": "ActiveSessions", "Unit": "Count"}],
                }
            ],
        },
        "Service": SERVICE,
        "ClusterName": CLUSTER,
        "ActiveSessions": value,
    }
    # EMF requires a bare JSON object on its own line, so no logger formatting.
    print(json.dumps(event), flush=True)


@asynccontextmanager
async def track_session():
    """Increment the live-session gauge for the duration of one conversation."""
    global _active
    _active += 1
    _emit(_active)
    try:
        yield
    finally:
        _active -= 1
        _emit(_active)
