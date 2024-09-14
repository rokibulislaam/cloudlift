from unittest.mock import MagicMock

import boto3
import boto3.exceptions
import pytest
from botocore.exceptions import ClientError

from cloudlift.config.pre_flight import (
    check_sns_topic_exists,
    check_stack_exists,
    check_aws_instance_type,
)
from cloudlift.config.stack import get_service_stack_name
from cloudlift.exceptions import UnrecoverableException


@pytest.fixture
def sns_client(mocked_boto):
    return boto3.client("sns")


def test_check_sns_topic_when_topic_exists(sns_client, mocked_boto):
    """
    Tests that check_sns_topic_exists returns True when the SNS topic exists
    """
    res = sns_client.create_topic(Name="my-topic")
    topic_arn = res["TopicArn"]
    assert check_sns_topic_exists(topic_arn, "test-env") == True


def test_check_sns_topic_exists_not_found(sns_client, mocked_boto):
    """
    Tests that check_sns_topic_exists raises an UnrecoverableException when the SNS topic is not found
    """
    with pytest.raises(
        UnrecoverableException,
        match="Unable to find SNS topic arn:aws:sns:ap-south-1:123456789012:my-topic in test-env environment",
    ):
        check_sns_topic_exists(
            "arn:aws:sns:ap-south-1:123456789012:my-topic", "test-env"
        )


@pytest.fixture
def mock_cloudformation_client(mocked_boto):
    cloudformation_client = boto3.client("cloudformation")
    cf_client_mock = MagicMock(wraps=cloudformation_client)
    return cf_client_mock


@pytest.mark.parametrize(
    "name, environment, cmd, should_stack_exist, describe_stacks_response, expected_exception, expected_message",
    [
        # Stack exists and command is 'create', should raise UnrecoverableException
        (
            "my-stack",
            "test-env",
            "create",
            True,
            {"Stacks": [{}]},  # Simulates a successful stack retrieval
            UnrecoverableException,
            "CloudFormation stack my-stack in test-env environment already exists.",
        ),
        # Stack exists and command is 'update', should return True
        (
            "my-stack",
            "test-env",
            "update",
            True,
            {"Stacks": [{}]},  # Simulates a successful stack retrieval
            None,
            None,
        ),
        # Stack does not exist and command is 'create', should return True
        (
            "my-stack",
            "test-env",
            "create",
            False,
            ClientError(
                {
                    "Error": {
                        "Code": "ValidationError",
                        "Message": "Stack does not exist",
                    }
                },
                "DescribeStacks",
            ),
            None,
            None,
        ),
        # Stack does not exist and command is 'update', should raise UnrecoverableException
        (
            "my-stack",
            "test-env",
            "update",
            False,
            ClientError(
                {
                    "Error": {
                        "Code": "ValidationError",
                        "Message": "Stack does not exist",
                    }
                },
                "DescribeStacks",
            ),
            UnrecoverableException,
            "CloudFormation stack my-stack in test-env environment does not exist.",
        ),
    ],
)
def test_check_stack_exists(
    mock_cloudformation_client,
    name,
    environment,
    cmd,
    should_stack_exist,
    describe_stacks_response,
    expected_exception,
    expected_message,
):
    if should_stack_exist:
        mock_cloudformation_client.create_stack(
            StackName=get_service_stack_name(environment, name),
            TemplateBody="""{
                "Resources": {}
            }""",
            Parameters=[],
            Capabilities=["CAPABILITY_IAM"],
        )
    # Mocking the describe_stacks method behavior based on the test case input
    if isinstance(describe_stacks_response, dict):
        mock_cloudformation_client.describe_stacks.return_value = (
            describe_stacks_response
        )
    else:
        mock_cloudformation_client.describe_stacks.side_effect = (
            describe_stacks_response
        )

    if expected_exception:
        with pytest.raises(expected_exception, match=expected_message):
            check_stack_exists(name, environment, cmd)
    else:
        assert check_stack_exists(name, environment, cmd) is True


@pytest.mark.parametrize(
    "instance_type, expected_result, expected_invalid_type",
    [
        ("t2.micro", True, ""),
        ("m5.large", True, ""),
        ("c5.2xlarge", True, ""),
        ("invalid.type", False, "invalid.type"),
        ("t2.micro,m5.large", True, ""),
        ("t2.micro,invalid.type", False, "invalid.type"),
        ("", False, ""),
    ],
)
def test_check_aws_instance_type(instance_type, expected_result, expected_invalid_type):
    result, invalid_type = check_aws_instance_type(instance_type)
    assert result == expected_result
    assert invalid_type == expected_invalid_type
