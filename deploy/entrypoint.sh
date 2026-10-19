#!/bin/sh
# Translate environment variables into pipecat runner arguments.
set -eu

set -- -t "${TRANSPORT:-webrtc}" --host 0.0.0.0 --port "${PORT:-7860}"

if [ -n "${PUBLIC_HOST:-}" ]; then
    set -- "$@" -x "$PUBLIC_HOST"
fi

if [ -n "${ALLOWED_ORIGINS:-}" ]; then
    # shellcheck disable=SC2086
    set -- "$@" --allowed-origins $ALLOWED_ORIGINS
fi

exec python bot.py "$@"
