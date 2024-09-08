import boto3
import boto3.session
import pytest

from cloudlift.config.parameter_store import ParameterStore
from cloudlift.exceptions import UnrecoverableException


@pytest.fixture(autouse=True)
def patch_get_client_for(monkeypatch, mocked_boto):
    def get_client_for(resource, _environment):
        return boto3.session.Session().client(resource)

    monkeypatch.setattr(
        "cloudlift.config.parameter_store.get_client_for", get_client_for
    )


@pytest.fixture
def ssm_client(mocked_boto):
    """Fixture for setting up and tearing down a mocked SSM client."""
    client = boto3.client("ssm")
    yield client


@pytest.fixture
def parameter_store(mocked_boto):
    """Fixture for creating a ParameterStore instance."""
    return ParameterStore(service_name="test-service", environment="test-env")


def test_get_existing_config_as_string(parameter_store, ssm_client):
    """
    Tests that the get_existing_config_as_string method returns the correct string
    representation of the existing parameters in SSM.
    """
    ssm_client.put_parameter(
        Name="/test-env/test-service/param1", Value="value1", Type="String"
    )
    ssm_client.put_parameter(
        Name="/test-env/test-service/param2", Value="value2", Type="String"
    )

    result = parameter_store.get_existing_config_as_string()
    assert result == "param1=value1\nparam2=value2"


def test_get_existing_config(parameter_store, ssm_client):
    """
    Tests that the get_existing_config method returns the correct parameters and their ARNs.
    """
    # Setting up mock parameters in SSM with pagination
    ssm_client.put_parameter(
        Name="/test-env/test-service/param1", Value="value1", Type="String"
    )
    ssm_client.put_parameter(
        Name="/test-env/test-service/param2", Value="value2", Type="String"
    )

    environment_configs, environment_configs_path = (
        parameter_store.get_existing_config()
    )
    assert environment_configs == {"param1": "value1", "param2": "value2"}
    assert len(environment_configs_path) == 2
    assert environment_configs_path["param1"].startswith(
        "arn:aws:ssm:ap-south-1:123456789012:parameter/test-env/test-service/param1"
    )
    assert environment_configs_path["param2"].startswith(
        "arn:aws:ssm:ap-south-1:123456789012:parameter/test-env/test-service/param2"
    )


def test_get_existing_config_with_pagination(parameter_store, ssm_client):
    """
    Tests that the get_existing_config method handles pagination correctly.
    """
    # Setting up mock parameters in SSM with pagination
    for i in range(15):
        ssm_client.put_parameter(
            Name=f"/test-env/test-service/param{i+1}",
            Value=f"value{i+1}",
            Type="String",
        )

    environment_configs, environment_configs_path = (
        parameter_store.get_existing_config()
    )
    assert len(environment_configs) == 15
    assert len(environment_configs_path) == 15
    for i in range(15):
        param_name = f"param{i+1}"
        assert environment_configs[param_name] == f"value{i+1}"
        assert environment_configs_path[param_name].startswith(
            f"arn:aws:ssm:ap-south-1:123456789012:parameter/test-env/test-service/{param_name}"
        )


def test_set_config_change(parameter_store, ssm_client):
    """
    Tests that the set_config method correctly changes an existing parameter value.
    """
    # Change an existing parameter value
    old_value = "value1"
    new_value = "new_value"
    ssm_client.put_parameter(
        Name="/test-env/test-service/param1", Value=old_value, Type="String"
    )

    differences = [("change", "param1", (old_value, new_value))]
    parameter_store.set_config(differences)

    param = ssm_client.get_parameter(Name="/test-env/test-service/param1")
    assert param["Parameter"]["Value"] == f"kms:alias/aws/ssm:{new_value}"


def test_set_config_add(parameter_store, ssm_client):
    # Add new parameters to SSM
    """
    Tests that the set_config method correctly adds new parameters to SSM.
    """

    differences = [("add", "", [("param1", "value1"), ("param2", "value2")])]
    parameter_store.set_config(differences)

    param1 = ssm_client.get_parameter(Name="/test-env/test-service/param1")
    param2 = ssm_client.get_parameter(Name="/test-env/test-service/param2")
    assert param1["Parameter"]["Value"] == f"kms:alias/aws/ssm:value1"
    assert param2["Parameter"]["Value"] == f"kms:alias/aws/ssm:value2"


def test_set_config_remove(parameter_store, ssm_client):
    """
    Tests that the set_config method correctly removes parameters from SSM.
    """
    ssm_client.put_parameter(
        Name="/test-env/test-service/param1", Value="value1", Type="String"
    )
    ssm_client.put_parameter(
        Name="/test-env/test-service/param2", Value="value2", Type="String"
    )

    differences = [("remove", "", [("param1", ""), ("param2", "")])]
    parameter_store.set_config(differences)

    param1 = ssm_client.get_parameters(Names=["/test-env/test-service/param1"])
    param2 = ssm_client.get_parameters(Names=["/test-env/test-service/param2"])
    assert not param1["Parameters"]
    assert not param2["Parameters"]


def test_validate_changes_invalid_key(parameter_store):
    """
    Tests that the _validate_changes method raises an exception when an invalid parameter key is provided.
    """
    # Test validation with an invalid parameter key
    invalid_changes = [("change", "invalid key!", ("value1", "new_value"))]
    with pytest.raises(UnrecoverableException) as excinfo:
        parameter_store._validate_changes(invalid_changes)
    assert "Environment variables validation failed" in str(excinfo.value)


def test_is_a_valid_parameter_key(parameter_store):
    """
    Tests that the _is_a_valid_parameter_key method correctly validates parameter keys.
    """
    assert parameter_store._is_a_valid_parameter_key("valid_key")
    assert parameter_store._is_a_valid_parameter_key("valid-key")
    assert parameter_store._is_a_valid_parameter_key("valid.key")
    assert parameter_store._is_a_valid_parameter_key("valid/key")
    assert not parameter_store._is_a_valid_parameter_key("invalid key!")
    assert not parameter_store._is_a_valid_parameter_key("invalid$key")


def test_set_config_invalid_key(parameter_store):
    """
    Tests that the set_config method raises an exception when an invalid parameter key is provided.
    """
    invalid_changes = [("change", "invalid key!", ("value1", "new_value"))]
    with pytest.raises(UnrecoverableException):
        parameter_store.set_config(invalid_changes)
