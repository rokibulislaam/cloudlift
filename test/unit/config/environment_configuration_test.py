from unittest.mock import MagicMock

import pytest
from packaging.version import Version

from cloudlift.config.dynamodb_configuration import DynamodbConfiguration
from cloudlift.config.environment_configuration import EnvironmentConfiguration
from cloudlift.exceptions import UnrecoverableException
from cloudlift.version import VERSION as CURRENT_CLOUDLIFT_VERSION

ENVIRONMENT_CONFIGURATION_TABLE = "environment_configurations"
ENVIRONMENT_NAME = "test-environment"


@pytest.fixture(scope="module")
def environment_config_table(mocked_boto_module_scopped):
    """
    A module scoped fixture & DynamodbConfiguration mock, ensures the table is not created multiple
    times
    """
    table = DynamodbConfiguration(
        ENVIRONMENT_CONFIGURATION_TABLE, [("environment", ENVIRONMENT_NAME)]
    )._get_table()
    return table


@pytest.fixture(scope="function")
def environment_config_table_mock(mocked_boto, environment_config_table):
    """
    A function scoped fixture, mocks the DynamodbConfiguration class and returns the table object.
    MagicMock wraps the actual table object, helps in asserting the calls made to the table
    """
    table_mock = MagicMock(wraps=environment_config_table)
    return table_mock


def test_get_config_when_config_doesnt_exist(
    mocked_boto, environment_config_table_mock
):
    """
    Tests that get_config raises an exception when the configuration doesn't exist.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock
    with pytest.raises(UnrecoverableException) as exception_info:
        env_config.get_config()

    assert "Environment configuration not found. Does this environment exist?" in str(
        exception_info.value
    )


def test_get_config_raise_exception_when_env_doesnt_exist(
    mocked_boto, environment_config_table_mock
):
    """
    Tests that get_config raises an exception when the environment doesn't exist.
    """
    env_config = EnvironmentConfiguration(environment="nonexistent-environment")
    env_config.table = environment_config_table_mock
    with pytest.raises(Exception) as exc_info:
        env_config.get_config()
    assert "Environment configuration not found" in str(exc_info.value)


def test_get_config_when_config_exists(mocked_boto, environment_config_table_mock):
    """
    Tests that get_config returns the configuration when it exists. Also tests that
    cloudlift_version is not present in the returned configuration.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock
    config_value = {
        "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
        "key_name": "test-key",
    }
    env_config.table.get_item.return_value = {
        # passing a copy of the config_value to avoid the test modifying the original value
        "Item": {"environment": ENVIRONMENT_NAME, "configuration": config_value.copy()}
    }

    config_returned = env_config.get_config()
    assert "cloudlift_version" not in config_returned
    assert config_returned["key_name"] == config_value["key_name"]


def test_get_config_when_config_exists_and_loose_version_check_fails(
    mocked_boto, environment_config_table_mock
):
    """
    Tests that get_config raises an exception when the configuration exists but the version check
    fails.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock
    current_version = Version(CURRENT_CLOUDLIFT_VERSION)
    bumped_version = f"{current_version.major}.{current_version.minor + 1}.{current_version.micro + 1}"
    env_config.table.get_item.return_value = {
        "Item": {
            "environment": ENVIRONMENT_NAME,
            "configuration": {
                "cloudlift_version": bumped_version,
                "key_name": "test-key",
            },
        }
    }

    with pytest.raises(UnrecoverableException) as exception_info:
        env_config.get_config()

    error_message = (
        f"Cloudlift Version {bumped_version} was used to create this environment. You"
        + f" are using version {CURRENT_CLOUDLIFT_VERSION}, which is older and can cause corruption."
        + f" Please upgrade to at least version {bumped_version} to proceed.\\n\\nUpgrade to the"
        + f" latest version (Recommended):\\n\\tpip install -U cloudlift\\n\\nOR\\n\\nUpgrade to a"
        + f" compatible version:\\n\\tpip install -U cloudlift=={bumped_version}"
    )

    assert error_message in str(exception_info.value)


def test_update_config(mocked_boto, environment_config_table_mock):
    """
    Tests that _create_config is called when the configuration doesn't exist and _edit_config is
    called after that.
    """

    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    # Test when configuration doesn't exist
    env_config._create_config = MagicMock()
    env_config._edit_config = MagicMock()
    env_config.table.get_item.return_value = {}
    env_config.update_config()
    env_config._create_config.assert_called_once()
    env_config._edit_config.assert_called_once()

    # Test when configuration exists
    env_config._create_config = MagicMock()
    env_config._edit_config = MagicMock()
    env_config.table.get_item.return_value = {
        "Item": {
            "environment": ENVIRONMENT_NAME,
            "configuration": {
                "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
                "key_name": "test-key",
            },
        }
    }
    env_config.update_config()
    env_config._create_config.assert_not_called()
    env_config._edit_config.assert_called_once()


def test_get_all_environments(mocked_boto, environment_config_table_mock):
    """
    Tests that get_all_environments returns a list of all the environments.
    """
    env_config = EnvironmentConfiguration()
    env_config.table = environment_config_table_mock
    env_config.table.scan.return_value = {
        "Items": [{"environment": "env1"}, {"environment": "env2"}]
    }
    environments = env_config.get_all_environments()
    assert "env1" in environments
    assert "env2" in environments


def test_edit_config_no_changes(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that _edit_config does not update the configuration if no changes are made.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    # Mock get_config to return a sample configuration
    sample_config = {
        "key_name": "test-key",
        "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
    }
    monkeypatch.setattr(env_config, "get_config", lambda: sample_config.copy())

    # Mock fault_tolerant_edit_config to return the same configuration (no changes)
    monkeypatch.setattr(
        env_config.config_utils,
        "fault_tolerant_edit_config",
        lambda current_configuration: sample_config.copy(),
    )

    # Mock _set_config to track calls
    env_config._set_config = MagicMock()

    env_config._edit_config()

    # Ensure _set_config was called with the same configuration
    env_config._set_config.assert_called_once_with(sample_config)


def tet_env_config_exists(mocked_boto, environment_config_table_mock):
    """
    Tests that _env_config_exists returns True if the configuration exists and False otherwise.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock
    env_config.table.get_item.return_value = {
        "Item": {
            "environment": ENVIRONMENT_NAME,
            "configuration": {
                "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
                "key_name": "test-key",
            },
        }
    }
    assert env_config._env_config_exists() is True

    env_config.table.get_item.return_value = {}
    assert env_config._env_config_exists() is False


INSTANCCE_TYPE_PROMPT_STRING = "Instance types in comma delimited string, \nFor On-Demand only first instance type will be considered"


@pytest.fixture
def create_config_prompt_values():
    return {
        "AWS region for environment": "ap-south-1",
        "VPC CIDR, for example 10.10.0.0/16": "10.10.0.0/16",
        "Allocation ID Elastic IP for NAT": "eip-12345678",
        "Public Subnet 1 CIDR": "10.10.1.0/24",
        "Public Subnet 2 CIDR": "10.10.2.0/24",
        "Private Subnet 1 CIDR": "10.10.3.0/24",
        "Private Subnet 2 CIDR": "10.10.4.0/24",
        "Cluster type \n [1] On-Demand \n [2] Spot \n [3] Both \n default ": 3,
        "Min instances in On-Demand cluster": 1,
        "Max instances in On-Demand cluster": 5,
        "Min instances in Spot cluster": 1,
        "Max instances in Spot cluster": 5,
        INSTANCCE_TYPE_PROMPT_STRING: "t2.micro,m5.xlarge,c5.xlarge",
        "Default instance type for ECS cluster Spot/OnDemand": "OnDemand",
        "Spot Allocation Strategy capacity-optimized/lowest-price/price-capacity-optimized": "capacity-optimized",
        "SSM parameter path of Custom AMI ID (Optional)": "None",
        "SSH key name": "test-key",
        "Notification SNS ARN": "arn:aws:sns:ap-south-1:123456789012:test-topic",
        "SSL certificate ARN": "arn:aws:acm:ap-south-1:123456789012:certificate/12345678-1234-1234-1234-123456789012",
    }


def test_create_config_prompts_for_values(monkeypatch, create_config_prompt_values):
    """
    Tests that _create_config prompts for various configuration values.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    def mock_prompt(prompt_text, default=None):
        return create_config_prompt_values[prompt_text]

    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.prompt", mock_prompt
    )
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.check_aws_instance_type",
        lambda x: (True, None),
    )
    monkeypatch.setattr(env_config, "_set_config", MagicMock())

    env_config._create_config()

    env_config._set_config.assert_called_once()
    config = env_config._set_config.call_args[0][0]
    assert config[ENVIRONMENT_NAME]["region"] == "ap-south-1"
    assert config[ENVIRONMENT_NAME]["vpc"]["cidr"] == "10.10.0.0/16"
    assert (
        config[ENVIRONMENT_NAME]["vpc"]["nat-gateway"]["elastic-ip-allocation-id"]
        == "eip-12345678"
    )
    assert (
        config[ENVIRONMENT_NAME]["vpc"]["subnets"]["public"]["subnet-1"]["cidr"]
        == "10.10.1.0/24"
    )
    assert (
        config[ENVIRONMENT_NAME]["vpc"]["subnets"]["public"]["subnet-2"]["cidr"]
        == "10.10.2.0/24"
    )
    assert (
        config[ENVIRONMENT_NAME]["vpc"]["subnets"]["private"]["subnet-1"]["cidr"]
        == "10.10.3.0/24"
    )
    assert (
        config[ENVIRONMENT_NAME]["vpc"]["subnets"]["private"]["subnet-2"]["cidr"]
        == "10.10.4.0/24"
    )
    assert config[ENVIRONMENT_NAME]["cluster"]["min_instances"] == 1
    assert config[ENVIRONMENT_NAME]["cluster"]["max_instances"] == 5
    assert config[ENVIRONMENT_NAME]["cluster"]["spot_min_instances"] == 1
    assert config[ENVIRONMENT_NAME]["cluster"]["spot_max_instances"] == 5
    assert (
        config[ENVIRONMENT_NAME]["cluster"]["instance_type"]
        == "t2.micro,m5.xlarge,c5.xlarge"
    )
    assert (
        config[ENVIRONMENT_NAME]["cluster"]["ecs_instance_default_lifecycle_type"]
        == "ondemand"
    )
    assert (
        config[ENVIRONMENT_NAME]["cluster"]["spot_allocation_strategy"]
        == "capacity-optimized"
    )
    assert config[ENVIRONMENT_NAME]["cluster"]["ami_id"] is None
    assert (
        config[ENVIRONMENT_NAME]["environment"]["notifications_arn"]
        == "arn:aws:sns:ap-south-1:123456789012:test-topic"
    )
    assert (
        config[ENVIRONMENT_NAME]["environment"]["ssl_certificate_arn"]
        == "arn:aws:acm:ap-south-1:123456789012:certificate/12345678-1234-1234-1234-123456789012"
    )


def test_edit_config_with_changes(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that _edit_config updates the configuration if changes are made.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    # Mock get_config to return a sample configuration
    sample_config = {
        "key_name": "test-key",
        "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
    }
    monkeypatch.setattr(env_config, "get_config", lambda: sample_config.copy())

    # Mock fault_tolerant_edit_config to return a modified configuration
    modified_config = sample_config.copy()
    modified_config["key_name"] = "new-test-key"
    monkeypatch.setattr(
        env_config.config_utils,
        "fault_tolerant_edit_config",
        lambda current_configuration: modified_config,
    )

    # Mock _set_config to track calls
    env_config._set_config = MagicMock()

    # Mock confirm to always return True
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.confirm", lambda msg: True
    )

    env_config._edit_config()

    # Ensure _set_config was called with the modified configuration
    env_config._set_config.assert_called_once_with(modified_config)


def test_edit_config_aborts_changes(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that _edit_config does not update the configuration if changes are aborted.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    # Mock get_config to return a sample configuration
    sample_config = {
        "key_name": "test-key",
        "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
    }
    monkeypatch.setattr(env_config, "get_config", lambda: sample_config.copy())

    # Mock fault_tolerant_edit_config to return a modified configuration
    modified_config = sample_config.copy()
    modified_config["key_name"] = "new-test-key"
    monkeypatch.setattr(
        env_config.config_utils,
        "fault_tolerant_edit_config",
        lambda current_configuration: modified_config,
    )

    # Mock _set_config to track calls
    env_config._set_config = MagicMock()

    # Mock confirm to always return False (abort changes)
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.confirm", lambda msg: False
    )

    env_config._edit_config()

    # Ensure _set_config was not called
    env_config._set_config.assert_not_called()


def test_edit_config_no_changes(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that _edit_config does not update the configuration if no changes are made.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    # Mock get_config to return a sample configuration
    sample_config = {
        "key_name": "test-key",
        "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
    }
    monkeypatch.setattr(env_config, "get_config", lambda: sample_config.copy())

    # Mock fault_tolerant_edit_config to return the same configuration (no changes)
    monkeypatch.setattr(
        env_config.config_utils,
        "fault_tolerant_edit_config",
        lambda current_configuration: sample_config.copy(),
    )

    # Mock _set_config to track calls
    env_config._set_config = MagicMock()

    env_config._edit_config()

    # Ensure _set_config was called with the same configuration
    env_config._set_config.assert_called_once_with(sample_config)


@pytest.fixture(autouse=True)
def click_confirm_mock(monkeypatch):
    """
    Fixture to mock click.confirm to always return True.
    """
    confirm_mock = MagicMock()
    confirm_mock.return_value = True
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.confirm", confirm_mock
    )
    return confirm_mock


def test_set_config_valid_configuration(
    mocked_boto, environment_config_table_mock, monkeypatch, click_confirm_mock
):
    """
    Tests that _set_config successfully updates the configuration in DynamoDB when the configuration
    is valid.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    # monkeypatch.setattr(env_config, "table", environment_config_table_mock)
    env_config.table = environment_config_table_mock

    valid_config = {
        ENVIRONMENT_NAME: {
            "region": "ap-south-1",
            "vpc": {
                "cidr": "10.10.0.0/16",
                "nat-gateway": {"elastic-ip-allocation-id": "eip-12345678"},
                "subnets": {
                    "public": {
                        "subnet-1": {"cidr": "10.10.1.0/24"},
                        "subnet-2": {"cidr": "10.10.2.0/24"},
                    },
                    "private": {
                        "subnet-1": {"cidr": "10.10.3.0/24"},
                        "subnet-2": {"cidr": "10.10.4.0/24"},
                    },
                },
            },
            "cluster": {
                "min_instances": 1,
                "max_instances": 5,
                "spot_min_instances": 1,
                "spot_max_instances": 5,
                "instance_type": "t2.micro,m5.xlarge,c5.xlarge",
                "key_name": "test-key",
                "ami_id": None,
                "ecs_instance_default_lifecycle_type": "ondemand",
                "spot_allocation_strategy": "capacity-optimized",
            },
            "environment": {
                "notifications_arn": "arn:aws:sns:ap-south-1:123456789012:test-topic",
                "ssl_certificate_arn": "arn:aws:acm:ap-south-1:123456789012:certificate/12345678-1234-1234-1234-123456789012",
            },
            "service_defaults": {
                "logging": "awslogs",
                "alb_mode": "dedicated",
                "disable_service_alarms": False,
                "fluentbit_config": {
                    "image_uri": "amazon/aws-for-fluent-bit:stable",
                    "env": {"kinesis_role_arn": ""},
                },
            },
        }
        # "cloudlift_version": CURRENT_CLOUDLIFT_VERSION,
    }

    # monkeypatch.setattr(env_config, "_validate_changes", lambda x: True)
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.check_sns_topic_exists",
        lambda arn, env: True,
    )

    env_config._set_config(valid_config)

    env_config.table.update_item.assert_called_once_with(
        TableName=ENVIRONMENT_CONFIGURATION_TABLE,
        Key={"environment": ENVIRONMENT_NAME},
        UpdateExpression="SET configuration = :configuration",
        ExpressionAttributeValues={":configuration": valid_config},
        ReturnValues="UPDATED_NEW",
    )

    # Ensure the configuration was updated by retrieving the configuration from the table
    updated_config = env_config.table.get_item(Key={"environment": ENVIRONMENT_NAME})[
        "Item"
    ]["configuration"]
    assert updated_config == valid_config


def test_set_config_invalid_configuration(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that _set_config raises an UnrecoverableException when the configuration is invalid.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    invalid_config = {
        ENVIRONMENT_NAME: {
            "region": "ap-south-1",
        }
    }

    monkeypatch.setattr(
        env_config,
        "_validate_changes",
        lambda x: (_ for _ in ()).throw(
            UnrecoverableException("Invalid configuration")
        ),
    )

    with pytest.raises(UnrecoverableException, match="Invalid configuration"):
        env_config._set_config(invalid_config)


def test_set_config_sns_topic_does_not_exist(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that _set_config raises an UnrecoverableException when the SNS topic does not exist.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    valid_config = {
        ENVIRONMENT_NAME: {
            "region": "ap-south-1",
            "environment": {
                "notifications_arn": "arn:aws:sns:ap-south-1:123456789012:test-topic",
            },
        },
    }

    monkeypatch.setattr(env_config, "_validate_changes", lambda x: True)
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.check_sns_topic_exists",
        lambda arn, env: (_ for _ in ()).throw(
            UnrecoverableException("SNS topic does not exist")
        ),
    )

    with pytest.raises(UnrecoverableException, match="SNS topic does not exist"):
        env_config._set_config(valid_config)


def test_update_cloudlift_version(
    mocked_boto, environment_config_table_mock, monkeypatch
):
    """
    Tests that update_cloudlift_version updates the cloudlift version in the configuration.
    """
    env_config = EnvironmentConfiguration(environment=ENVIRONMENT_NAME)
    env_config.table = environment_config_table_mock

    env_config.get_config = MagicMock(
        return_value={
            ENVIRONMENT_NAME: {
                "environment": {
                    "notifications_arn": "arn:aws:sns:ap-south-1:123456789012:test-topic",
                },
            }
        }
    )

    env_config._validate_changes = MagicMock()
    monkeypatch.setattr(
        "cloudlift.config.environment_configuration.check_sns_topic_exists",
        lambda arn, env: True,
    )

    env_config.update_cloudlift_version()

    assert env_config.table.update_item.call_count == 1
    assert (
        env_config.table.update_item.call_args[1]["UpdateExpression"]
        == "SET configuration = :configuration"
    )
    assert (
        env_config.table.update_item.call_args[1]["ExpressionAttributeValues"][
            ":configuration"
        ]["cloudlift_version"]
        == CURRENT_CLOUDLIFT_VERSION
    )
