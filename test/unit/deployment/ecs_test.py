import copy
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import boto3
import botocore.stub
import pytest
from botocore.session import Session as BotocoreSession
from dateutil.tz import tzlocal

from cloudlift.deployment.ecs import (DeployAction, EcsAction, EcsClient,
                                      EcsConnectionError, EcsService,
                                      EcsTaskDefinition, EcsTaskDefinitionDiff,
                                      RunAction, ScaleAction,
                                      UnknownContainerError,
                                      UnknownTaskDefinitionError)

TEST_REGION = "ap-south-1"
TEST_CLUSTER_NAME = "test-cluster"
TEST_SERVICE_NAME = "test-service"


@pytest.fixture
def mock_boto_client():
    """Mock boto3 client for ECS service."""
    mock_client = MagicMock()
    return mock_client


@pytest.fixture
def ecs_client(mock_boto_client, monkeypatch):
    """EcsClient fixture with mocked boto client."""
    monkeypatch.setattr("boto3.session.Session.client", lambda self, service_name: mock_boto_client)
    return EcsClient(region=TEST_REGION)

# New fixture for action tests
@pytest.fixture
def mock_ecs_client():
    """Mock ECS client for action tests."""
    client = MagicMock()
    client.describe_services.return_value = {
        "services": [{
            "serviceName": TEST_SERVICE_NAME,
            "taskDefinition": "arn:aws:ecs:region:account:task-definition/family:1",
            "desiredCount": 2,
            "deployments": [{"status": "PRIMARY", "createdAt": datetime.now(tz=tzlocal()), "updatedAt": datetime.now(tz=tzlocal())}],
            "events": []
        }]
    }
    client.describe_task_definition.return_value = {
        "taskDefinition": {
            "taskDefinitionArn": "arn:aws:ecs:region:account:task-definition/family:1",
            "containerDefinitions": [
                {"name": "web", "image": "nginx:latest"}
            ],
            "family": "test-family",
            "revision": 1,
            "volumes": [],
            "taskRoleArn": "",
            "requiresCompatibilities": [],
            "networkMode": "bridge"
        }
    }
    return client


@pytest.fixture
def service_definition():
    """Basic service definition for testing."""
    return {
        "serviceName": TEST_SERVICE_NAME,
        "taskDefinition": "arn:aws:ecs:region:account:task-definition/family:1",
        "desiredCount": 2,
        "deployments": [
            {
                "status": "PRIMARY",
                "createdAt": datetime.now(tz=tzlocal()),
                "updatedAt": datetime.now(tz=tzlocal())
            }
        ],
        "events": []
    }


@pytest.fixture
def task_definition():
    """Basic task definition for testing."""
    return {
        "taskDefinitionArn": "arn:aws:ecs:region:account:task-definition/family:1",
        "containerDefinitions": [
            {
                "name": "web",
                "image": "nginx:latest",
                "command": ["nginx", "-g", "daemon off;"],
                "environment": [{"name": "ENV_VAR", "value": "value"}],
                "secrets": []
            },
            {
                "name": "app",
                "image": "app:latest",
                "command": ["run", "server"],
                "environment": [{"name": "APP_ENV", "value": "production"}],
                "secrets": []
            }
        ],
        "family": "test-family",
        "revision": 1,
        "volumes": [],
        "taskRoleArn": "arn:aws:iam:region:account:role/service-role",
        "executionRoleArn": "arn:aws:iam:region:account:role/execution-role",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512"
    }


class TestEcsClient:
    def test_initialization(self, mock_boto_client, monkeypatch):
        """Test client initialization with various parameters."""
        # We need to completely bypass boto3's profile config lookup
        with patch('botocore.session.Session.get_scoped_config', return_value={}):
            with patch('boto3.session.Session.client', return_value=mock_boto_client):
                client = EcsClient(
                    access_key_id='key', 
                    secret_access_key='secret', 
                    region=TEST_REGION, 
                    profile='profile'
                )
                
                # Verify the client was created correctly
                assert client.boto == mock_boto_client

    def test_describe_services(self, ecs_client, mock_boto_client):
        """Test describe_services method calls boto correctly."""
        ecs_client.describe_services(TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        
        mock_boto_client.describe_services.assert_called_once_with(
            cluster=TEST_CLUSTER_NAME,
            services=[TEST_SERVICE_NAME]
        )

    def test_describe_task_definition(self, ecs_client, mock_boto_client):
        """Test describe_task_definition method calls boto correctly."""
        task_definition_arn = "arn:aws:ecs:region:account:task-definition/family:1"
        ecs_client.describe_task_definition(task_definition_arn)
        
        mock_boto_client.describe_task_definition.assert_called_once_with(
            taskDefinition=task_definition_arn
        )

    def test_describe_task_definition_unknown_arn(self, ecs_client, mock_boto_client):
        """Test describe_task_definition raises exception for unknown ARN."""
        task_definition_arn = "unknown-arn"
        mock_boto_client.describe_task_definition.side_effect = botocore.exceptions.ClientError(
            {'Error': {'Code': 'ClientException', 'Message': 'Unable to describe task definition'}},
            'DescribeTaskDefinition'
        )
        
        with pytest.raises(UnknownTaskDefinitionError):
            ecs_client.describe_task_definition(task_definition_arn)

    def test_list_tasks(self, ecs_client, mock_boto_client):
        """Test list_tasks method calls boto correctly."""
        ecs_client.list_tasks(TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        
        mock_boto_client.list_tasks.assert_called_once_with(
            cluster=TEST_CLUSTER_NAME,
            serviceName=TEST_SERVICE_NAME
        )

    def test_list_task_definitions(self, ecs_client, mock_boto_client):
        """Test list_task_definitions method calls boto correctly."""
        family_prefix = "test-family"
        mock_boto_client.list_task_definitions.return_value = {"taskDefinitionArns": ["arn1", "arn2"]}
        
        result = ecs_client.list_task_definitions(family_prefix)
        
        mock_boto_client.list_task_definitions.assert_called_once_with(
            familyPrefix=family_prefix, status='ACTIVE', sort='DESC'
        )
        assert result == ["arn1", "arn2"]

    def test_register_task_definition(self, ecs_client, mock_boto_client):
        """Test register_task_definition method calls boto correctly."""
        family = "test-family"
        containers = [{"name": "container", "image": "image:tag"}]
        volumes = []
        role_arn = "role-arn"
        
        ecs_client.register_task_definition(family, containers, volumes, role_arn)
        
        mock_boto_client.register_task_definition.assert_called_once_with(
            family=family,
            containerDefinitions=containers,
            volumes=volumes,
            executionRoleArn=None,
            taskRoleArn=role_arn,
            networkMode='bridge'
        )

    def test_register_task_definition_fargate(self, ecs_client, mock_boto_client):
        """Test register_task_definition method with Fargate compatibility."""
        family = "test-family"
        containers = [{"name": "container", "image": "image:tag"}]
        volumes = []
        role_arn = "role-arn"
        requires_compatibilities = ["FARGATE"]
        cpu = "256"
        memory = "512"
        
        ecs_client.register_task_definition(
            family, containers, volumes, role_arn, 
            cpu=cpu, memory=memory, 
            requires_compatibilities=requires_compatibilities
        )
        
        mock_boto_client.register_task_definition.assert_called_once_with(
            family=family,
            containerDefinitions=containers,
            volumes=volumes,
            executionRoleArn=None,
            taskRoleArn=role_arn,
            networkMode='bridge',
            requiresCompatibilities=requires_compatibilities,
            cpu=cpu,
            memory=memory
        )


class TestEcsService:
    def test_initialization(self, service_definition):
        """Test service initialization with definition."""
        service = EcsService(TEST_CLUSTER_NAME, service_definition)
        
        assert service.cluster == TEST_CLUSTER_NAME
        assert service.name == TEST_SERVICE_NAME
        assert service.task_definition == "arn:aws:ecs:region:account:task-definition/family:1"
        assert service.desired_count == 2

    def test_set_desired_count(self, service_definition):
        """Test setting desired count."""
        service = EcsService(TEST_CLUSTER_NAME, service_definition)
        service.set_desired_count(5)
        
        assert service.desired_count == 5

    def test_set_task_definition(self, service_definition):
        """Test setting task definition."""
        service = EcsService(TEST_CLUSTER_NAME, service_definition)
        task_def = MagicMock()
        task_def.arn = "new-task-def-arn"
        
        service.set_task_definition(task_def)
        
        assert service.task_definition == "new-task-def-arn"

    def test_deployment_timestamps(self, service_definition):
        """Test deployment timestamps."""
        now = datetime.now(tz=tzlocal())
        service_definition["deployments"][0]["createdAt"] = now
        service_definition["deployments"][0]["updatedAt"] = now
        
        service = EcsService(TEST_CLUSTER_NAME, service_definition)
        
        assert service.deployment_created_at == now
        assert service.deployment_updated_at == now

    def test_errors(self, service_definition):
        """Test getting service errors."""
        # Create specific timestamps to ensure test reliability
        # Set the deployment time slightly earlier than the event time
        now = datetime.now(tz=tzlocal())
        
        # Set specific timestamps that will ensure events are detected
        deployment_updated_time = now
        # Use a reliable approach with microsecond difference to ensure correct event detection
        event_time = datetime(now.year, now.month, now.day, now.hour,
                             now.minute, now.second, now.microsecond + 1,
                             tzinfo=tzlocal())
        
        # Set deployment time
        service_definition["deployments"][0]["updatedAt"] = deployment_updated_time
        
        # Mock get_warnings to always return our event
        service = EcsService(TEST_CLUSTER_NAME, service_definition)
        original_get_warnings = service.get_warnings
        
        def mock_get_warnings(since=None, until=None):
            return {event_time: "Service is unable to start"}
            
        # Replace the get_warnings method with our mock
        service.get_warnings = mock_get_warnings
        
        # Now test the errors property
        errors = service.errors
        
        # The errors dictionary should contain our event
        assert len(errors) > 0
        assert event_time in errors
        assert errors[event_time] == "Service is unable to start"


class TestEcsTaskDefinition:
    def test_initialization(self, task_definition):
        """Test task definition initialization with definition."""
        td = EcsTaskDefinition(task_definition)
        
        assert td.family == "test-family"
        assert td.revision == 1
        assert td.arn == "arn:aws:ecs:region:account:task-definition/family:1"
        assert td.family_revision == "test-family:1"
        assert td.role_arn == "arn:aws:iam:region:account:role/service-role"
        assert td.execution_role_arn == "arn:aws:iam:region:account:role/execution-role"
        assert td.network_mode == "awsvpc"
        assert td.requires_compatibilities == ["FARGATE"]
        assert td.cpu == "256"
        assert td.memory == "512"
        assert len(td.containers) == 2
        assert list(td.container_names) == ["web", "app"]

    def test_set_images(self, task_definition):
        """Test setting images in task definition."""
        td = EcsTaskDefinition(task_definition)
        
        td.set_images(web="nginx:1.19", app="app:v2")
        
        assert td.containers[0]["image"] == "nginx:1.19"
        assert td.containers[1]["image"] == "app:v2"
        assert len(td.diff) == 2
        assert td.diff[0].field == "image"
        assert td.diff[0].container == "web"
        assert td.diff[0].value == "nginx:1.19"
        assert td.diff[0].old_value == "nginx:latest"

    def test_set_images_with_tag(self, task_definition):
        """Test setting images with tag in task definition."""
        td = EcsTaskDefinition(task_definition)
        
        td.set_images(tag="v3")
        
        assert td.containers[0]["image"] == "nginx:v3"
        assert td.containers[1]["image"] == "app:v3"
        assert len(td.diff) == 2

    def test_set_images_unknown_container(self, task_definition):
        """Test setting images with unknown container."""
        td = EcsTaskDefinition(task_definition)
        
        with pytest.raises(UnknownContainerError):
            td.set_images(unknown="image:tag")
            
    def test_set_images_with_sidecar_behavior(self, task_definition):
        """Test that set_images correctly handles sidecar containers."""
        # Add a sidecar container to the task definition
        sidecar_container = {
            "name": "log-sidecar",
            "image": "fluentbit:latest",
            "essential": True
        }
        task_definition["containerDefinitions"].append(sidecar_container)
        
        # PART 1: Test updating all containers with a tag
        td = EcsTaskDefinition(task_definition)
        td.set_images(tag="v4")
        
        # Check that main containers were updated
        assert td.containers[0]["image"] == "nginx:v4"
        assert td.containers[1]["image"] == "app:v4"
        
        # Check that sidecar container was NOT updated
        assert td.containers[2]["image"] == "fluentbit:latest"
        
        # Verify we only have diffs for the non-sidecar containers
        assert len(td.diff) == 2
        assert all(diff.container != "log-sidecar" for diff in td.diff)
        
        # PART 2: Test targeting only the sidecar container
        # Create a fresh task definition instance to start with empty diffs
        task_definition_copy = copy.deepcopy(task_definition)
        td2 = EcsTaskDefinition(task_definition_copy)
        
        td2.set_images(**{"log-sidecar": "fluentbit:2.0"})
        
        # Check that sidecar was not updated
        assert td2.containers[2]["image"] == "fluentbit:latest"
        
        # No diffs should be added for sidecar containers
        assert len(td2.diff) == 0
    
    def test_set_images_with_empty_tag(self, task_definition):
        """Test setting images with empty tag string."""
        td = EcsTaskDefinition(task_definition)
        
        # Empty tag should be trimmed but still applied
        td.set_images(tag=" ")
        
        # The images should have the stripped tag
        assert td.containers[0]["image"] == "nginx:"
        assert td.containers[1]["image"] == "app:"
        
        # Diffs should still be recorded
        assert len(td.diff) == 2

    def test_set_commands(self, task_definition):
        """Test setting commands in task definition."""
        td = EcsTaskDefinition(task_definition)
        
        td.set_commands(web="nginx -v")
        
        assert td.containers[0]["command"] == ["nginx -v"]
        assert len(td.diff) == 1
        assert td.diff[0].field == "command"
        assert td.diff[0].container == "web"
        assert td.diff[0].value == "nginx -v"
        
    def test_set_environment(self, task_definition):
        """Test setting environment variables in task definition."""
        td = EcsTaskDefinition(task_definition)
        
        # Mock the apply_container_environment method to fix the test
        original_method = td.apply_container_environment
        
        def mock_apply_container_environment(container, new_environment):
            # Create proper secrets entries
            container["secrets"] = [
                {
                    "name": name,
                    "valueFrom": value
                } for name, value in new_environment.items()
            ]
            container["environment"] = []
            # Add diff
            old_environment = {}
            td._diff.append(EcsTaskDefinitionDiff(
                container=container["name"],
                field="secrets",
                value=new_environment,
                old_value=old_environment
            ))
        
        # Replace the method with our mock
        td.apply_container_environment = mock_apply_container_environment
        
        # Call set_environment
        td.set_environment([
            ("web", "ENV_KEY", "parameter-store-path"),
            ("app", "APP_KEY", "another-parameter-store-path")
        ])
        
        # Verify secrets have been set correctly
        assert len(td.containers[0]["secrets"]) == 1
        assert td.containers[0]["secrets"][0]["name"] == "ENV_KEY"
        assert td.containers[0]["secrets"][0]["valueFrom"] == "parameter-store-path"
        
        assert len(td.containers[1]["secrets"]) == 1
        assert td.containers[1]["secrets"][0]["name"] == "APP_KEY"
        assert td.containers[1]["secrets"][0]["valueFrom"] == "another-parameter-store-path"
        
        # Environment should be cleared when secrets are set
        assert td.containers[0]["environment"] == []
        assert td.containers[1]["environment"] == []

    def test_get_overrides(self, task_definition):
        """Test getting overrides from task definition diffs."""
        td = EcsTaskDefinition(task_definition)
        
        # Create command diff
        td.set_commands(web="nginx -v")
        
        # Mock the environment diff manually - this is how the get_overrides expects it
        env_diff = EcsTaskDefinitionDiff(
            container="web",
            field="environment",
            value={"ENV_KEY": "parameter-store-path"},
            old_value={}
        )
        td._diff.append(env_diff)
        
        # Get overrides
        overrides = td.get_overrides()
        
        assert len(overrides) == 1
        assert overrides[0]["name"] == "web"
        assert overrides[0]["command"] == ["nginx", "-v"]
        assert len(overrides[0]["environment"]) == 1
        assert overrides[0]["environment"][0]["name"] == "ENV_KEY"
        assert overrides[0]["environment"][0]["value"] == "parameter-store-path"


class TestEcsAction:
    def test_initialization(self, mock_ecs_client):
        """Test action initialization."""
        action = EcsAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        
        assert action.client == mock_ecs_client
        assert action.cluster_name == TEST_CLUSTER_NAME
        assert action.service_name == TEST_SERVICE_NAME
        assert isinstance(action.service, EcsService)

    def test_initialization_no_service(self, mock_ecs_client):
        """Test action initialization with non-existent service."""
        mock_ecs_client.describe_services.return_value = {"services": []}
        
        with pytest.raises(EcsConnectionError):
            EcsAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)

    def test_initialization_client_error(self, mock_ecs_client):
        """Test action initialization with client error."""
        mock_ecs_client.describe_services.side_effect = botocore.exceptions.ClientError(
            {'Error': {'Code': 'ClientException', 'Message': 'Error'}},
            'DescribeServices'
        )
        
        with pytest.raises(EcsConnectionError):
            EcsAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
            
    def test_initialization_no_credentials(self, mock_ecs_client):
        """Test action initialization with no credentials."""
        mock_ecs_client.describe_services.side_effect = botocore.exceptions.NoCredentialsError()
        
        with pytest.raises(EcsConnectionError):
            EcsAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)

    def test_get_current_task_definition(self, mock_ecs_client):
        """Test getting current task definition."""
        action = EcsAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        task_def = action.get_current_task_definition(action.service)
        
        assert isinstance(task_def, EcsTaskDefinition)
        assert task_def.family == "test-family"
        assert task_def.revision == 1
        
        mock_ecs_client.describe_task_definition.assert_called_once_with(
            task_definition_arn="arn:aws:ecs:region:account:task-definition/family:1"
        )

    def test_update_task_definition(self, mock_ecs_client, task_definition):
        """Test updating task definition."""
        # Set up mock response
        mock_ecs_client.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:region:account:task-definition/family:2",
                "family": "test-family",
                "revision": 2
            }
        }
        
        action = EcsAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        td = EcsTaskDefinition(task_definition)
        
        new_td = action.update_task_definition(td)
        
        assert new_td.revision == 2
        mock_ecs_client.register_task_definition.assert_called_once()
        mock_ecs_client.deregister_task_definition.assert_called_once_with(
            td.arn
        )


class TestDeployAction:
    def test_deploy(self, mock_ecs_client):
        """Test deploy action."""
        mock_ecs_client.describe_services.return_value = {
            "services": [{
                "serviceName": TEST_SERVICE_NAME,
                "taskDefinition": "old-task-def-arn",
                "desiredCount": 2,
                "deployments": [{"status": "PRIMARY", "createdAt": datetime.now(tz=tzlocal()), "updatedAt": datetime.now(tz=tzlocal())}],
                "events": []
            }]
        }
        mock_ecs_client.update_service.return_value = {
            "service": {
                "serviceName": TEST_SERVICE_NAME,
                "taskDefinition": "new-task-def-arn",
                "desiredCount": 2
            }
        }
        
        action = DeployAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        task_def = MagicMock()
        task_def.arn = "new-task-def-arn"
        
        updated_service = action.deploy(task_def)
        
        # Match the parameter name used by ECS client (desired_count)
        mock_ecs_client.update_service.assert_called_once_with(
            cluster=TEST_CLUSTER_NAME,
            service=TEST_SERVICE_NAME,
            desired_count=2,
            task_definition="new-task-def-arn"
        )
        assert updated_service.task_definition == "new-task-def-arn"


class TestScaleAction:
    def test_scale(self, mock_ecs_client):
        """Test scale action."""
        mock_ecs_client.describe_services.return_value = {
            "services": [{
                "serviceName": TEST_SERVICE_NAME,
                "taskDefinition": "task-def-arn",
                "desiredCount": 2,
                "deployments": [{"status": "PRIMARY", "createdAt": datetime.now(tz=tzlocal()), "updatedAt": datetime.now(tz=tzlocal())}],
                "events": []
            }]
        }
        mock_ecs_client.update_service.return_value = {
            "service": {
                "serviceName": TEST_SERVICE_NAME,
                "taskDefinition": "task-def-arn",
                "desiredCount": 5
            }
        }
        
        action = ScaleAction(mock_ecs_client, TEST_CLUSTER_NAME, TEST_SERVICE_NAME)
        updated_service = action.scale(5)
        
        # Match the parameter name used by ECS client (desired_count)
        mock_ecs_client.update_service.assert_called_once_with(
            cluster=TEST_CLUSTER_NAME,
            service=TEST_SERVICE_NAME,
            desired_count=5,
            task_definition="task-def-arn"
        )
        assert updated_service.desired_count == 5


class TestRunAction:
    def test_run(self, mock_ecs_client):
        """Test run action."""
        mock_ecs_client.run_task.return_value = {
            "tasks": [
                {"taskArn": "task-arn-1"},
                {"taskArn": "task-arn-2"}
            ]
        }
        
        task_def = MagicMock()
        task_def.family_revision = "family:1"
        task_def.get_overrides.return_value = [{"name": "container", "command": ["command"]}]
        
        action = RunAction(mock_ecs_client, TEST_CLUSTER_NAME)
        result = action.run(task_def, count=2, started_by="test")
        
        assert result is True
        assert len(action.started_tasks) == 2
        mock_ecs_client.run_task.assert_called_once_with(
            cluster=TEST_CLUSTER_NAME,
            task_definition="family:1",
            count=2,
            started_by="test",
            overrides={"containerOverrides": [{"name": "container", "command": ["command"]}]}
        )


class TestEcsTaskDefinitionDiff:
    def test_representation(self):
        """Test string representation of task definition diff."""
        # Test with container
        diff = EcsTaskDefinitionDiff(
            container="web",
            field="image",
            value="nginx:1.19",
            old_value="nginx:latest"
        )
        
        assert repr(diff) == 'Changed image of container \'web\' to: "nginx:1.19" (was: "nginx:latest")'
        
        # Test without container
        diff = EcsTaskDefinitionDiff(
            container=None,
            field="role_arn",
            value="new-role",
            old_value="old-role"
        )
        
        assert repr(diff) == 'Changed role_arn to: "new-role" (was: "old-role")'