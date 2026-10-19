
# agent.py
import os

from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language

load_dotenv()

SYSTEM_PROMPT = """
You are a friendly Hindi voice assistant.
Speak naturally in Hindi or Hinglish, matching the user.
Keep spoken responses short, usually 1 to 3 sentences.
Do not use markdown or bullet points.
"""


def create_services():
    """Create fresh service instances for each voice session."""
    stt = SarvamSTTService(
        api_key=os.environ["SARVAM_API_KEY"],
        model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
        params=SarvamSTTService.InputParams(
            language=Language.HI_IN
        ),
    )

    llm = OpenAILLMService(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        model=os.getenv(
            "OPENROUTER_MODEL", "google/gemma-4-31b-it"
        ),
    )

    tts = SarvamTTSService(
        api_key=os.environ["SARVAM_API_KEY"],
        model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v3"),
        voice_id=os.getenv("SARVAM_VOICE", "shubh"),
        params=SarvamTTSService.InputParams(
            language=Language.HI_IN
        ),
    )

    return stt, llm, tts


async def run_voice_session(transport):
    """
    One invocation = one conversation.
    The caller owns transport creation and client connectivity.
    """
    stt, llm, tts = create_services()

    context = LLMContext([
        {"role": "system", "content": SYSTEM_PROMPT}
    ])

    user_aggregator, assistant_aggregator = (
        LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(stop_secs=0.5)
                )
            ),
        )
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    context.add_message({
        "role": "user",
        "content": "Greet me briefly in Hindi and ask how you can help.",
    })
    await task.queue_frames([LLMRunFrame()])

    # Pipecat handles the pipeline lifecycle.
    await PipelineRunner().run(task)
