import pytest
import boto3
import os
from moto import mock_aws


def pytest_addoption(parser):
    parser.addoption(
        "--keep-resources",
        action="store_true",
        default=False,
        help="my option: type1 or type2",
    )


@pytest.fixture
def keep_resources(request):
    """
    Presence of `keep_resources` retains the AWS resources created by cloudformation
    during the test run. By default, the resources are deleted after the run.
    """
    return request.config.getoption("--keep-resources")


@pytest.fixture(scope="module")
def test_aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "ap-south-1"


@pytest.fixture
def mocked_boto(test_aws_credentials):
    """
    Mock all AWS interactions
    """
    with mock_aws():
        yield


@pytest.fixture(scope="module")
def mocked_boto_module_scopped(test_aws_credentials):
    """
    Mock all AWS interactions
    """
    with mock_aws():
        yield


@pytest.fixture
def mocked_sts_client(mocked_boto):
    yield boto3.client("sts")
