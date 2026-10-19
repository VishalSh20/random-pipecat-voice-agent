import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_applicationautoscaling as appscaling,
    aws_certificatemanager as acm,
    aws_cloudwatch as cloudwatch,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

METRIC_NAMESPACE = "VoiceAgent"
METRIC_SERVICE = "voice-agent"


def _flag(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes")


class VoiceAgentStack(Stack):
    """Pipecat voice agent on ECS Fargate behind an ALB, scaled by concurrency."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ctx = self.node.try_get_context

        transport = ctx("transport") or "webrtc"
        min_tasks = int(ctx("minTasks") or 2)
        max_tasks = int(ctx("maxTasks") or 30)
        sessions_per_task = int(ctx("sessionsPerTask") or 3)
        certificate_arn = ctx("certificateArn")
        public_host = ctx("publicHost")
        allowed_origins = ctx("allowedOrigins")
        vpc_id = ctx("vpcId")
        assign_public_ip = _flag(ctx("assignPublicIp"), True)

        if vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
        else:
            vpc = ec2.Vpc.from_lookup(self, "Vpc", is_default=True)

        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        image = ecr_assets.DockerImageAsset(
            self,
            "Image",
            directory=REPO_ROOT,
            file="deploy/Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name="/ecs/voice-agent",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        sarvam_key = secretsmanager.Secret(
            self, "SarvamApiKey", secret_name="voice-agent/sarvam-api-key"
        )
        openrouter_key = secretsmanager.Secret(
            self, "OpenRouterApiKey", secret_name="voice-agent/openrouter-api-key"
        )
        daily_key = secretsmanager.Secret(
            self, "DailyApiKey", secret_name="voice-agent/daily-api-key"
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "Task",
            cpu=2048,
            memory_limit_mib=4096,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        environment = {
            "PORT": "7860",
            "TRANSPORT": transport,
            "METRICS_NAMESPACE": METRIC_NAMESPACE,
            "METRICS_SERVICE": METRIC_SERVICE,
            "ECS_CLUSTER": cluster.cluster_name,
        }
        if public_host:
            environment["PUBLIC_HOST"] = public_host
        if allowed_origins:
            environment["ALLOWED_ORIGINS"] = ",".join(allowed_origins)

        task_definition.add_container(
            "bot",
            image=ecs.ContainerImage.from_docker_image_asset(image),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="bot", log_group=log_group),
            environment=environment,
            secrets={
                "SARVAM_API_KEY": ecs.Secret.from_secrets_manager(sarvam_key),
                "OPENROUTER_API_KEY": ecs.Secret.from_secrets_manager(openrouter_key),
                "DAILY_API_KEY": ecs.Secret.from_secrets_manager(daily_key),
            },
            port_mappings=[
                ecs.PortMapping(container_port=7860, protocol=ecs.Protocol.TCP)
            ],
            stop_timeout=Duration.seconds(120),
        )

        sarvam_key.grant_read(task_definition.execution_role)
        openrouter_key.grant_read(task_definition.execution_role)
        daily_key.grant_read(task_definition.execution_role)

        certificate = (
            acm.Certificate.from_certificate_arn(self, "Cert", certificate_arn)
            if certificate_arn
            else None
        )

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=min_tasks,
            public_load_balancer=True,
            assign_public_ip=assign_public_ip,
            listener_port=443 if certificate else 80,
            certificate=certificate,
            redirect_http=bool(certificate),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            health_check_grace_period=Duration.seconds(60),
            min_healthy_percent=50,
            max_healthy_percent=200,
            enable_execute_command=True,
        )

        # HTTP signalling is stateful: the WebRTC peer connection lives in the
        # task's memory, so offers and ICE patches must stay on the same task.
        target_group = service.target_group
        target_group.configure_health_check(
            path="/status",
            healthy_http_codes="200",
            interval=Duration.seconds(15),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )
        target_group.enable_cookie_stickiness(Duration.hours(8))
        # Drain fast: media sessions should not hold a task for 5 minutes.
        target_group.set_attribute("deregistration_delay.timeout_seconds", "30")

        scalable = service.service.auto_scale_task_count(
            min_capacity=min_tasks, max_capacity=max_tasks
        )
        scalable.scale_on_cpu_utilization("Cpu", target_utilization_percent=60)

        # Scale on live conversations rather than HTTP traffic, which barely
        # moves once a WebRTC session is up.
        sessions_metric = cloudwatch.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="ActiveSessions",
            dimensions_map={
                "Service": METRIC_SERVICE,
                "ClusterName": cluster.cluster_name,
            },
            statistic="Average",
            period=Duration.minutes(1),
        )
        scalable.scale_on_metric(
            "Sessions",
            metric=sessions_metric,
            scaling_steps=[
                appscaling.ScalingInterval(upper=1, change=-1),
                appscaling.ScalingInterval(lower=sessions_per_task, change=1),
                appscaling.ScalingInterval(lower=sessions_per_task * 2, change=2),
            ],
            adjustment_type=appscaling.AdjustmentType.CHANGE_IN_CAPACITY,
            metric_aggregation_type=appscaling.MetricAggregationType.AVERAGE,
            cooldown=Duration.minutes(2),
        )

        CfnOutput(self, "LoadBalancerDns", value=service.load_balancer.load_balancer_dns_name)
        CfnOutput(self, "ImageUri", value=image.image_uri)
        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ServiceName", value=service.service.service_name)
        CfnOutput(self, "LogGroup", value=log_group.log_group_name)
