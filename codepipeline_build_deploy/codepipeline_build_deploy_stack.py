from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elbv2,
    aws_codedeploy as codedeploy,
    aws_codebuild as codebuild,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as codepipeline_actions,
    CfnOutput,
)
from constructs import Construct

class CodepipelineBuildDeployStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Setup Infra (VPC, Repo, Cluster)
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)
        image_repo = ecr.Repository(self, "ImageRepo")
        cluster = ecs.Cluster(self, "EcsCluster", vpc=vpc)
        
        capacity = cluster.add_capacity(
            "ArmCapacity",
            instance_type=ec2.InstanceType("t4g.micro"),
            machine_image=ecs.EcsOptimizedImage.amazon_linux2(ecs.AmiHardwareType.ARM),
            min_capacity=1,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
        )

        # 2. Task Definition (Architecture: ARM64)
        task_def = ecs.Ec2TaskDefinition(self, "Ec2TaskDef")
        container = task_def.add_container(
            "web",
            image=ecs.ContainerImage.from_ecr_repository(image_repo),
            memory_reservation_mib=256,
            cpu=256,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="web-arm")
        )
        container.add_port_mappings(ecs.PortMapping(container_port=80))

        # 3. Load Balancer & Target Groups
        alb = elbv2.ApplicationLoadBalancer(self, "ALB", vpc=vpc, internet_facing=True)
        listener = alb.add_listener("HttpListener", port=80, open=True)

        blue_tg = elbv2.ApplicationTargetGroup(
            self, "BlueTG", vpc=vpc, port=80, 
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", port="80")
        )
        green_tg = elbv2.ApplicationTargetGroup(
            self, "GreenTG", vpc=vpc, port=80, 
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(path="/", port="80")
        )
        listener.add_target_groups("DefaultBlue", target_groups=[blue_tg])

        # 4. ECS Service (CodeDeploy Controlled)
        ec2_service = ecs.Ec2Service(
            self, "Ec2Service",
            cluster=cluster,
            task_definition=task_def,
            deployment_controller=ecs.DeploymentController(type=ecs.DeploymentControllerType.CODE_DEPLOY)
        )
        capacity.connections.allow_from(alb, ec2.Port.tcp(80))

        # 5. CodeDeploy Group
        deploy_group = codedeploy.EcsDeploymentGroup(
            self, "CodeDeployGroup",
            service=ec2_service,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                listener=listener,
                blue_target_group=blue_tg,
                green_target_group=green_tg
            )
        )

        # 6. Build Project (ARM Optimized)
        build_project = codebuild.Project(
            self, "BuildImage",
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"),
            environment=codebuild.BuildEnvironment(
                privileged=True,
                # UPDATE: Use Amazon Linux 2023 for latest ARM tools
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2023_STANDARD_3_0,
                compute_type=codebuild.ComputeType.SMALL
            ),
            environment_variables={
                "REPOSITORY_URI": codebuild.BuildEnvironmentVariable(value=image_repo.repository_uri),
                "TASK_ROLE_ARN": codebuild.BuildEnvironmentVariable(value=task_def.task_role.role_arn),
                "EXECUTION_ROLE_ARN": codebuild.BuildEnvironmentVariable(value=task_def.execution_role.role_arn),
                "TASK_DEFINITION_ARN": codebuild.BuildEnvironmentVariable(value=task_def.task_definition_arn)
            }
        )
        image_repo.grant_pull_push(build_project)

        # 7. Pipeline with GitHub Trigger
        source_output = codepipeline.Artifact()
        build_output = codepipeline.Artifact()
        pipeline = codepipeline.Pipeline(self, "EcsArmPipeline", pipeline_name="EcsArmPipeline")

        pipeline.add_stage(
            stage_name="Source",
            actions=[
                codepipeline_actions.CodeStarConnectionsSourceAction(
                    action_name="GitHub_Source",
                    owner="YOUR_GITHUB_USER",
                    repo="YOUR_REPO_NAME",
                    branch="main",
                    connection_arn="YOUR_CONNECTION_ARN",
                    output=source_output
                )
            ]
        )

        pipeline.add_stage(
            stage_name="Build",
            actions=[
                codepipeline_actions.CodeBuildAction(
                    action_name="Build_ARM_Image",
                    project=build_project,
                    input=source_output,
                    outputs=[build_output]
                )
            ]
        )

        pipeline.add_stage(
            stage_name="Deploy",
            actions=[
                codepipeline_actions.CodeDeployEcsDeployAction(
                    action_name="BlueGreenDeploy",
                    deployment_group=deploy_group,
                    app_spec_template_input=build_output,
                    task_definition_template_input=build_output,
                    container_image_inputs=[
                        codepipeline_actions.CodeDeployEcsContainerImageInput(
                            input=build_output,
                            # Matches <IMAGE1_NAME> in your taskdef.json
                            task_definition_placeholder="IMAGE1_NAME"
                        )
                    ]
                )
            ]
        )

        CfnOutput(self, "ALBUrl", value=f"http://{alb.load_balancer_dns_name}")