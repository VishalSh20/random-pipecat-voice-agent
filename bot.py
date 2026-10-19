# bot.py
from pipecat.runner.run import main
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

from agent import run_voice_session
from metrics import track_session


def _transport_params():
    params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
        "websocket": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=ProtobufFrameSerializer(),
        ),
    }
    try:
        from pipecat.transports.daily.transport import DailyParams

        params["daily"] = lambda: DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        )
    except ImportError:
        pass
    return params


async def bot(runner_args: RunnerArguments):
    """Called by Pipecat for each incoming session."""
    async with track_session():
        transport = await create_transport(runner_args, _transport_params())
        await run_voice_session(transport)


if __name__ == "__main__":
    main()
