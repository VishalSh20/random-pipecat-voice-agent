#!/usr/bin/env python3
import os

import aws_cdk as cdk

from voice_agent_stack import VoiceAgentStack

app = cdk.App()

VoiceAgentStack(
    app,
    "VoiceAgentStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    ),
)

app.synth()
