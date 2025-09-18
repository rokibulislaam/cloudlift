import pytest
from unittest.mock import MagicMock, call
from botocore.exceptions import ClientError

from cloudlift.deployment.environment_creator import EnvironmentCreator
from cloudlift.exceptions import UnrecoverableException


class TestEnvironmentCreator:
    @pytest.fixture
    def environment_creator(self, monkeypatch):
        """Fixture to create a preconfigured EnvironmentCreator instance"""
        # Mock configuration
        mock_env_config = MagicMock()
        mock_env_config.get_config.return_value = {
            "test-env": {"cluster": {"key_name": "test-key"}}
        }
        mock_env_config.update_config.return_value = None

        # Mock get_client_for
        mock_cloudformation_client = MagicMock()
        mock_autoscaling_client = MagicMock()
        mock_ecs_client = MagicMock()

        def mock_get_client(service, environment):
            if service == "cloudformation":
                return mock_cloudformation_client
            elif service == "autoscaling":
                return mock_autoscaling_client
            elif service == "ecs":
                return mock_ecs_client
            else:
                return MagicMock()

        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.get_client_for", mock_get_client
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.EnvironmentConfiguration",
            lambda env: mock_env_config,
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.get_cluster_name",
            lambda env: f"{env}-cluster",
        )
        monkeypatch.setattr("cloudlift.deployment.environment_creator.log", MagicMock())
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.log_bold", MagicMock()
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.log_err", MagicMock()
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.print_new_events", MagicMock()
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.get_stack_events",
            MagicMock(return_value=[]),
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.create_change_set", MagicMock()
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.ClusterTemplateGenerator",
            MagicMock(),
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.sleep", MagicMock()
        )

        # Create instance
        creator = EnvironmentCreator("test-env")

        # Add mocked clients for testing
        creator.client = mock_cloudformation_client

        # Initialize the existing_events attribute that's used in __print_progress
        creator.existing_events = []

        return (
            creator,
            mock_cloudformation_client,
            mock_autoscaling_client,
            mock_ecs_client,
        )

    def test_init_constructor(self, monkeypatch):
        """
        Test that constructor initializes the instance with correct values
        """
        mock_env_config = MagicMock()
        mock_env_config.get_config.return_value = {
            "test-env": {"cluster": {"key_name": "test-key"}}
        }
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.EnvironmentConfiguration",
            lambda env: mock_env_config,
        )
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.get_cluster_name",
            lambda env: "test-env-cluster",
        )
        mock_client = MagicMock()
        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.get_client_for",
            lambda service, env: mock_client,
        )

        creator = EnvironmentCreator("test-env")

        assert creator.environment == "test-env"
        assert creator.cluster_name == "test-env-cluster"
        assert creator.key_name == "test-key"
        mock_env_config.update_config.assert_called_once()

    def test_run_create_new_stack(self, environment_creator):
        """
        Test creating a new environment stack when it doesn't exist
        """
        creator, mock_cf_client, _, _ = environment_creator

        # Setup mock to raise exception when checking if stack exists (meaning it doesn't)
        mock_cf_client.describe_stacks.side_effect = [
            Exception("Stack does not exist"),  # First call fails - stack doesn't exist
            {
                "Stacks": [
                    {"StackStatus": "CREATE_COMPLETE", "StackId": "test-stack-id"}
                ]
            },  # Second call succeeds during progress check
        ]

        mock_cf_client.create_stack.return_value = {"StackId": "test-stack-id"}

        # Run the method under test
        creator.run()

        # Verify the correct calls were made
        mock_cf_client.describe_stacks.assert_called_with(StackName="test-env-cluster")
        mock_cf_client.create_stack.assert_called_once()
        assert (
            mock_cf_client.create_stack.call_args[1]["StackName"] == "test-env-cluster"
        )
        assert mock_cf_client.create_stack.call_args[1]["OnFailure"] == "DO_NOTHING"
        assert mock_cf_client.create_stack.call_args[1]["Capabilities"] == [
            "CAPABILITY_NAMED_IAM"
        ]

    def test_run_stack_already_exists(self, environment_creator):
        """
        Test that attempting to create an environment with a duplicate name fails
        """
        creator, mock_cf_client, _, _ = environment_creator

        # Setup mock to return a stack meaning it already exists
        mock_cf_client.describe_stacks.return_value = {
            "Stacks": [{"StackId": "existing-stack-id"}]
        }

        # Run the method under test
        creator.run()

        # Verify error was logged and create_stack wasn't called
        from cloudlift.deployment.environment_creator import log_err

        log_err.assert_called_once()
        mock_cf_client.create_stack.assert_not_called()

    def test_run_update_with_changes(self, environment_creator, monkeypatch):
        """
        Test updating an environment stack with changes
        """
        creator, mock_cf_client, _, _ = environment_creator

        # Mock create_change_set to return a valid changeset
        from cloudlift.deployment.environment_creator import create_change_set

        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.create_change_set",
            MagicMock(return_value={"ChangeSetId": "test-changeset-id"}),
        )

        # Mock progress checking
        mock_cf_client.describe_stacks.return_value = {
            "Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]
        }

        # Mock getting desired count
        monkeypatch.setattr(
            creator, "_EnvironmentCreator__get_desired_count", MagicMock(return_value=3)
        )

        # Run the method under test
        creator.run_update(update_ecs_agents=False)

        # Verify the correct calls were made
        mock_cf_client.execute_change_set.assert_called_once_with(
            ChangeSetName="test-changeset-id"
        )

    def test_run_update_no_changes(self, environment_creator, monkeypatch):
        """
        Test updating an environment stack with no changes
        """
        creator, mock_cf_client, _, _ = environment_creator

        # Mock ClientError for no updates case
        def raise_client_error(*args, **kwargs):
            raise ClientError(
                {
                    "Error": {
                        "Code": "ValidationError",
                        "Message": "No updates are to be performed",
                    }
                },
                "ExecuteChangeSet",
            )

        mock_cf_client.execute_change_set.side_effect = raise_client_error

        # Mock create_change_set to return a valid changeset
        from cloudlift.deployment.environment_creator import create_change_set

        monkeypatch.setattr(
            "cloudlift.deployment.environment_creator.create_change_set",
            MagicMock(return_value={"ChangeSetId": "test-changeset-id"}),
        )

        # Mock getting desired count
        monkeypatch.setattr(
            creator, "_EnvironmentCreator__get_desired_count", MagicMock(return_value=3)
        )

        # Run the method under test
        creator.run_update(update_ecs_agents=False)

        # Verify error was logged
        from cloudlift.deployment.environment_creator import log_err

        log_err.assert_called_with("No updates are to be performed")

    def test_get_desired_count(self, environment_creator, monkeypatch):
        """
        Test getting the desired instance count from auto scaling group
        """
        creator, mock_cf_client, mock_as_client, _ = environment_creator

        # Mock cloudformation list_stack_resources to return ASG physical ID
        mock_cf_client.list_stack_resources.return_value = {
            "StackResourceSummaries": [
                {
                    "ResourceType": "AWS::AutoScaling::AutoScalingGroup",
                    "PhysicalResourceId": "test-asg-id",
                }
            ]
        }

        # Mock autoscaling describe_auto_scaling_groups to return desired capacity
        mock_as_client.describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"DesiredCapacity": 5}]
        }

        # Call the method under test (using double underscore to access private method)
        result = creator._EnvironmentCreator__get_desired_count()

        # Verify the result and calls
        assert result == 5
        mock_cf_client.list_stack_resources.assert_called_once_with(
            StackName="test-env-cluster"
        )
        mock_as_client.describe_auto_scaling_groups.assert_called_once_with(
            AutoScalingGroupNames=["test-asg-id"]
        )

    def test_run_ecs_container_agent_update(self, environment_creator):
        """
        Test ECS container agent update process
        """
        creator, _, _, mock_ecs_client = environment_creator

        # Mock list_container_instances
        mock_ecs_client.list_container_instances.return_value = {
            "containerInstanceArns": ["instance1", "instance2"]
        }

        # Mock update_container_agent responses
        mock_ecs_client.update_container_agent.side_effect = [
            {
                "containerInstance": {
                    "containerInstanceArn": "instance1",
                    "agentUpdateStatus": "PENDING",
                }
            },
            ClientError(
                {
                    "Error": {
                        "Code": "NoUpdateAvailableException",
                        "Message": "There is no update available for your container agent.",
                    }
                },
                "UpdateContainerAgent",
            ),
        ]

        # Mock describe_container_instances for status check
        mock_ecs_client.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": "instance1", "agentUpdateStatus": "UPDATED"},
                {
                    "containerInstanceArn": "instance2"
                },  # No update status means it's already updated
            ]
        }

        # Run the method under test
        creator._EnvironmentCreator__run_ecs_container_agent_udpate()

        # Verify the correct calls were made
        mock_ecs_client.list_container_instances.assert_called_once_with(
            cluster="test-env-cluster"
        )
        assert mock_ecs_client.update_container_agent.call_count == 2
        mock_ecs_client.describe_container_instances.assert_called_with(
            cluster="test-env-cluster", containerInstances=["instance1", "instance2"]
        )

    def test_run_ecs_container_agent_update_already_updating(self, environment_creator):
        """
        Test ECS container agent update when update is already in progress
        """
        creator, _, _, mock_ecs_client = environment_creator

        # Mock list_container_instances
        mock_ecs_client.list_container_instances.return_value = {
            "containerInstanceArns": ["instance1"]
        }

        # Mock update_container_agent to indicate update already in progress
        mock_ecs_client.update_container_agent.side_effect = ClientError(
            {
                "Error": {
                    "Code": "UpdateInProgressException",
                    "Message": "Agent update is already in progress.",
                }
            },
            "UpdateContainerAgent",
        )

        # Mock describe_container_instances for status check
        mock_ecs_client.describe_container_instances.return_value = {
            "containerInstances": [
                {"containerInstanceArn": "instance1", "agentUpdateStatus": "UPDATED"}
            ]
        }

        # Run the method under test
        creator._EnvironmentCreator__run_ecs_container_agent_udpate()

        # Verify error was logged but no exception raised
        from cloudlift.deployment.environment_creator import log

        log.assert_any_call("Agent update is already in progress instance1")

    def test_print_progress_success(self, environment_creator):
        """
        Test stack progress monitoring till completion
        """
        creator, mock_cf_client, _, _ = environment_creator

        # Mock describe_stacks responses for progress checking
        mock_cf_client.describe_stacks.side_effect = [
            {"Stacks": [{"StackStatus": "UPDATE_IN_PROGRESS"}]},
            {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},
        ]

        print(creator)
        # Run the method under test
        # with name mangling to access private method
        creator._EnvironmentCreator__print_progress()

        # Verify loop ran correctly
        assert mock_cf_client.describe_stacks.call_count == 2
        from cloudlift.deployment.environment_creator import get_stack_events, sleep

        assert get_stack_events.call_count == 1
        sleep.assert_called_once_with(5)

        # Verify final status was logged
        from cloudlift.deployment.environment_creator import log_bold

        log_bold.assert_called_with("Finished and Status: UPDATE_COMPLETE")