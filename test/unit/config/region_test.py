from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

from cloudlift.config.region import (
    get_client_for,
    get_notifications_arn_for_environment,
    get_region_for_environment,
    get_resource_for,
    get_ssl_certification_for_environment,
)
from cloudlift.exceptions import UnrecoverableException


@pytest.fixture(autouse=True)
def patch_get_region_for_environment(monkeypatch):
    def mock_get_region_for_environment(environment):
        return "ap-south-1"

    monkeypatch.setattr(
        "cloudlift.config.region.get_region_for_environment",
        mock_get_region_for_environment,
    )


@pytest.fixture
def environment_configuration(mocked_boto):
    """Fixture to mock EnvironmentConfiguration."""
    config = {
        "test-env": {
            "region": "ap-south-1",
            "environment": {
                "notifications_arn": "arn:aws:sns:ap-south-1:123456789012:test-topic",
                "ssl_certificate_arn": "arn:aws:acm:ap-south-1:123456789012:certificate/test-cert",
            },
        }
    }

    # environment_configuration = EnvironmentConfiguration("test-env")
    # environment_config_mock = MagicMock(wraps=environment_configuration)
    environment_config_mock = MagicMock()
    environment_config_mock.get_config.return_value = config
    return environment_config_mock


@pytest.fixture(autouse=True)
def patch_environment_configuration(monkeypatch, environment_configuration):
    monkeypatch.setattr(
        "cloudlift.config.EnvironmentConfiguration", lambda x: environment_configuration
    )
    monkeypatch.setattr(
        "cloudlift.config.region.EnvironmentConfiguration",
        lambda x: environment_configuration,
    )


def test_get_notifications_arn_for_environment(monkeypatch):
    """
    Tests that get_notifications_arn_for_environment returns the correct notifications ARN.
    """

    arn = get_notifications_arn_for_environment("test-env")
    assert arn == "arn:aws:sns:ap-south-1:123456789012:test-topic"


def test_get_ssl_certification_for_environment(monkeypatch, environment_configuration):
    """
    Tests that get_ssl_certification_for_environment returns the correct SSL certificate ARN.
    """
    monkeypatch.setattr(
        "cloudlift.config.EnvironmentConfiguration", lambda x: environment_configuration
    )

    arn = get_ssl_certification_for_environment("test-env")
    assert arn == "arn:aws:acm:ap-south-1:123456789012:certificate/test-cert"


def test_get_notifications_arn_for_environment_key_error(
    monkeypatch, environment_configuration
):
    """
    Tests that get_notifications_arn_for_environment raises an UnrecoverableException for missing key.
    """
    environment_configuration.get_config.return_value["test-env"]["environment"].pop(
        "notifications_arn"
    )
    monkeypatch.setattr(
        "cloudlift.config.EnvironmentConfiguration", lambda x: environment_configuration
    )

    with pytest.raises(
        UnrecoverableException, match="Unable to find notifications arn for test-env"
    ):
        get_notifications_arn_for_environment("test-env")


def test_get_ssl_certification_for_environment_key_error(
    monkeypatch, environment_configuration
):
    """
    Tests that get_ssl_certification_for_environment raises an UnrecoverableException for missing key.
    """
    environment_configuration.get_config.return_value["test-env"]["environment"].pop(
        "ssl_certificate_arn"
    )
    monkeypatch.setattr(
        "cloudlift.config.EnvironmentConfiguration", lambda x: environment_configuration
    )

    with pytest.raises(
        UnrecoverableException, match="Unable to find ssl certificate for test-env"
    ):
        get_ssl_certification_for_environment("test-env")


@pytest.mark.parametrize("service_name", ["ec2", "s3"])
def test_get_client_for_valid_environment(service_name):
    """
    Tests that the get_client_for method returns the correct boto3 client for various services.
    """
    client = get_client_for(service_name, "test-env")
    assert client.meta.service_model.service_name == service_name


@pytest.mark.parametrize("service_name", ["ec2", "s3"])
def test_get_resource_for_valid_environment(service_name):
    """
    Tests that the get_resource_for method returns the correct boto3 resource for various services.
    """
    resource = get_resource_for(service_name, "test-env")
    assert resource.meta.service_name == service_name


@pytest.mark.parametrize(
    "error_code, expected_message",
    [
        (
            "ExpiredTokenException",
            "AWS session associated with this profile has expired or is otherwise invalid",
        ),
        (
            "InvalidIdentityTokenException",
            "AWS token that was passed could not be validated by Amazon Web Services",
        ),
        (
            "RegionDisabledException",
            "STS is not activated in the requested region for the account that is being asked to generate credentials",
        ),
        (
            "AccessDeniedException",
            "User is not authorized to perform get boto3 resource session on ssm",
        ),
        ("SomeOtherException", "Unable to find valid AWS credentials"),
    ],
)
def test_get_resource_for_exceptions(monkeypatch, error_code, expected_message):
    """
    Tests that the get_resource_for method raises an UnrecoverableException for various ClientError codes.
    """

    def mock_boto3_session(*args, **kwargs):
        raise ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": expected_message,
                }
            },
            "AssumeRole",
        )

    monkeypatch.setattr(boto3.session.Session, "resource", mock_boto3_session)

    with pytest.raises(UnrecoverableException, match=expected_message):
        get_resource_for("ssm", "test-env")


@pytest.mark.parametrize(
    "environment, expected_region",
    [
        ("test-env", "ap-south-1"),
        ("dev-env", "us-west-2"),
        ("prod-env", "eu-central-1"),
    ],
)
def test_get_region_for_environment(
    monkeypatch, environment, expected_region, environment_configuration
):
    """
    Tests that the get_region_for_environment method returns the correct region for different environments.
    """
    environment_configuration.get_config.return_value = {
        "test-env": {"region": "ap-south-1"},
        "dev-env": {"region": "us-west-2"},
        "prod-env": {"region": "eu-central-1"},
    }

    def mock_get_region_for_environment(env):
        regions = {
            "test-env": "ap-south-1",
            "dev-env": "us-west-2",
            "prod-env": "eu-central-1",
        }
        return regions[env]

    monkeypatch.setattr(
        "cloudlift.config.region.get_region_for_environment",
        mock_get_region_for_environment,
    )

    region = get_region_for_environment(environment)
    assert region == expected_region
