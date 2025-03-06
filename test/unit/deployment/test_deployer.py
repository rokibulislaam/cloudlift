import pytest
from unittest.mock import MagicMock
from colorclass import Color
from terminaltables import SingleTable

from cloudlift.deployment.deployer import (
    deploy_new_version,
    deploy_and_wait,
    build_config,
    read_config,
    make_container_defn_env_conf,
    wait_for_finish,
    fetch_events,
    fetch_and_print_new_events,
    print_task_diff
)
from cloudlift.config import ParameterStore
from cloudlift.exceptions import UnrecoverableException


@pytest.fixture
def mock_ecs_client():
    """Mock ECS client for deployment tests"""
    return MagicMock()


@pytest.fixture
def mock_service():
    """Mock ECS service for tests"""
    service = MagicMock()
    service.desired_count = 1
    service.errors = None
    return service


@pytest.fixture
def mock_deployment(mock_ecs_client, mock_service):
    """Mock deployment action for tests"""
    deployment = MagicMock()
    deployment.service = mock_service
    deployment.get_service.return_value = mock_service
    deployment.is_deployed.return_value = False  # By default, not deployed yet
    return deployment


@pytest.fixture
def mock_task_definition():
    """Mock task definition for tests"""
    task_definition = MagicMock()
    task_definition.__getitem__.side_effect = lambda key: {
        'containerDefinitions': [{'name': 'test-container'}]
    }[key]
    task_definition.containers = ['test-container']
    # Create mock diff objects for image and secrets
    image_diff = MagicMock()
    image_diff.field = 'image'
    image_diff.old_value = 'old-image:v1'
    image_diff.value = 'new-image:v2'
    
    env_diff = MagicMock()
    env_diff.field = 'secrets'
    env_diff.old_value = {'KEY1': 'old-val', 'KEY2': 'unchanged'}
    env_diff.value = {'KEY1': 'new-val', 'KEY2': 'unchanged'}
    
    task_definition.diff = [image_diff, env_diff]
    
    return task_definition


def test_read_config_basic():
    """Test basic config file reading with simple key-value pairs"""
    content = """
KEY1=value1
KEY2=value2
"""
    result = read_config(content)
    assert result == {'KEY1': 'value1', 'KEY2': 'value2'}


def test_read_config_with_comments_and_empty_lines():
    """Test reading config file with comments and empty lines"""
    content = """
# This is a comment
KEY1=value1

# Another comment
KEY2=value2

"""
    result = read_config(content)
    assert result == {'KEY1': 'value1', 'KEY2': 'value2'}


def test_make_container_defn_env_conf():
    """Test creating container definition environment configuration"""
    service_config = {'API_KEY': 'key-value', 'DEBUG': 'true'}
    environment_configs_path = {
        'API_KEY': '/path/to/api',
        'DEBUG': '/path/to/debug'
    }
    
    result = make_container_defn_env_conf(service_config, environment_configs_path)
    
    assert len(result) == 2
    assert ('API_KEY', '/path/to/api') in result
    assert ('DEBUG', '/path/to/debug') in result


def test_fetch_events(mock_service):
    """Test fetching and sorting of service events"""
    events = [
        {'createdAt': '2023-01-02', 'message': 'Event 2'},
        {'createdAt': '2023-01-01', 'message': 'Event 1'},
    ]
    # The function expects service.get('events'), not service.get() which returns dict with events key
    mock_service.get = MagicMock(return_value=events)
    
    result = fetch_events(mock_service)
    
    assert len(result) == 2
    assert result[0]['message'] == 'Event 1'  # Should be sorted by createdAt
    assert result[1]['message'] == 'Event 2'


def test_fetch_and_print_new_events(mock_service, monkeypatch):
    """Test fetching and printing new events"""
    existing_events = [{'createdAt': '2023-01-01', 'message': '[DUMMY] Event 1'}]
    new_events = [
        {'createdAt': '2023-01-01', 'message': '[DUMMY] Event 1'}, 
        {'createdAt': '2023-01-02', 'message': '[STATUS] Event 2 (info)'}
    ]
    
    # Need to mock service.get('events') not service.get() to return dict with events key
    mock_service.get = MagicMock(return_value=new_events)
    
    mock_log_with_color = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.deployer.log_with_color", mock_log_with_color)
    
    # Mock fetch_events to avoid the error from previous test
    monkeypatch.setattr(
        "cloudlift.deployment.deployer.fetch_events", 
        lambda service: new_events
    )
    
    result = fetch_and_print_new_events(mock_service, existing_events, 'white')
    
    assert len(result) == 2
    assert mock_log_with_color.call_count == 1  # Should only log the new event
    mock_log_with_color.assert_called_with('Event 2 info', 'white')  # Message is processed by removing () and first 8 chars


def test_wait_for_finish_success(mock_deployment, monkeypatch):
    """Test successful deployment completion"""
    # Configure mocks to simulate a successful deployment after one iteration
    mock_deployment.is_deployed.side_effect = [False, True]
    
    # Mock sleep to avoid waiting during test
    sleep_mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.deployer.sleep", sleep_mock)
    
    # Mock fetch_and_print_new_events
    fetch_events_mock = MagicMock(return_value=[])
    monkeypatch.setattr(
        "cloudlift.deployment.deployer.fetch_and_print_new_events",
        fetch_events_mock
    )
    
    existing_events = []
    result = wait_for_finish(mock_deployment, existing_events, 'white')
    
    assert result is True
    assert sleep_mock.call_count == 2  # Called for each iteration
    assert mock_deployment.is_deployed.call_count == 2


def test_wait_for_finish_failure(mock_deployment, monkeypatch):
    """Test failed deployment completion"""
    # Configure mocks to simulate a failed deployment
    mock_deployment.is_deployed.return_value = False
    mock_service = mock_deployment.get_service.return_value
    mock_service.errors = ["Some deployment error"]
    
    # Mock sleep to avoid waiting during test
    sleep_mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.deployer.sleep", sleep_mock)
    
    # Mock fetch_and_print_new_events and log_err
    fetch_events_mock = MagicMock(return_value=[])
    log_err_mock = MagicMock()
    monkeypatch.setattr(
        "cloudlift.deployment.deployer.fetch_and_print_new_events",
        fetch_events_mock
    )
    monkeypatch.setattr("cloudlift.deployment.deployer.log_err", log_err_mock)
    
    existing_events = []
    result = wait_for_finish(mock_deployment, existing_events, 'white')
    
    assert result is False
    assert log_err_mock.called  # Should log the error
    log_err_mock.assert_called_with(str(mock_service.errors))


def test_deploy_and_wait(mock_deployment, monkeypatch):
    """Test deploying and waiting for completion"""
    # Mock the required functions
    mock_wait = MagicMock(return_value=True)
    mock_fetch_events = MagicMock(return_value=[])
    
    monkeypatch.setattr("cloudlift.deployment.deployer.wait_for_finish", mock_wait)
    monkeypatch.setattr("cloudlift.deployment.deployer.fetch_events", mock_fetch_events)
    
    task_definition = MagicMock()
    result = deploy_and_wait(mock_deployment, task_definition, 'white')
    
    assert result is True
    mock_deployment.deploy.assert_called_with(task_definition)
    mock_fetch_events.assert_called_once()
    mock_wait.assert_called_once()


def test_build_config(monkeypatch):
    """Test building configuration with valid parameters"""
    # Sample config content
    sample_config = "KEY1=value1\nKEY2=value2"
    
    # Mock responses
    mock_file = MagicMock()
    mock_file.read.return_value = sample_config
    
    mock_parameter_store = MagicMock()
    mock_parameter_store.get_existing_config.return_value = (
        {'KEY1': 'value1', 'KEY2': 'value2'},
        {'KEY1': '/path/to/key1', 'KEY2': '/path/to/key2'}
    )
    
    monkeypatch.setattr("builtins.open", lambda x: mock_file)
    monkeypatch.setattr(
        "cloudlift.deployment.deployer.ParameterStore",
        lambda service_name, env_name: mock_parameter_store
    )
    
    result = build_config('test-env', 'test-service', 'test-path')
    
    assert len(result) == 2
    assert ('KEY1', '/path/to/key1') in result
    assert ('KEY2', '/path/to/key2') in result


def test_build_config_missing_env_config(monkeypatch):
    """Test build_config raises exception when environment config is missing keys"""
    # Sample config content with keys that aren't in the environment
    sample_config = "KEY1=value1\nKEY2=value2"
    
    # Mock responses
    mock_file = MagicMock()
    mock_file.read.return_value = sample_config
    
    mock_parameter_store = MagicMock()
    mock_parameter_store.get_existing_config.return_value = (
        {'KEY1': 'value1'},  # KEY2 is missing from env config
        {'KEY1': '/path/to/key1'}
    )
    
    monkeypatch.setattr("builtins.open", lambda x: mock_file)
    monkeypatch.setattr("cloudlift.deployment.deployer.ParameterStore", 
                        lambda service_name, env_name: mock_parameter_store)
    monkeypatch.setattr("cloudlift.deployment.deployer.log_intent", MagicMock())
    
    with pytest.raises(UnrecoverableException) as exc_info:
        build_config('test-env', 'test-service', 'test-path')
    assert "There is no config value for the keys" in str(exc_info.value)


def test_build_config_missing_keys(monkeypatch):
    """Test build_config raises exception when sample config is missing keys"""
    # Sample config content with fewer keys than the environment has
    sample_config = "KEY1=value1"
    
    # Mock responses
    mock_file = MagicMock()
    mock_file.read.return_value = sample_config
    
    mock_parameter_store = MagicMock()
    mock_parameter_store.get_existing_config.return_value = (
        {'KEY1': 'value1', 'KEY2': 'value2'},  # Extra key in env config
        {'KEY1': '/path/to/key1', 'KEY2': '/path/to/key2'}
    )
    
    monkeypatch.setattr("builtins.open", lambda x: mock_file)
    monkeypatch.setattr("cloudlift.deployment.deployer.ParameterStore", 
                        lambda service_name, env_name: mock_parameter_store)
    
    with pytest.raises(UnrecoverableException) as exc_info:
        build_config('test-env', 'test-service', 'test-path')
    assert "There is no config value for the keys in env.sample file" in str(exc_info.value)


def test_print_task_diff_with_changes(monkeypatch, capsys):
    """Test printing task diff when there are changes"""
    # Mock diff objects
    image_diff = MagicMock()
    image_diff.field = 'image'
    image_diff.old_value = 'old-image:v1'
    image_diff.value = 'new-image:v2'
    image_diff.__str__ = lambda self: 'old-image:v1 -> new-image:v2'
    
    env_diff = MagicMock()
    env_diff.field = 'secrets'
    env_diff.old_value = {'ENV_VAR1': 'old-value', 'ENV_VAR2': 'unchanged'}
    env_diff.value = {'ENV_VAR1': 'new-value', 'ENV_VAR2': 'unchanged'}
    
    diffs = [image_diff, env_diff]
    
    # Mock SingleTable and log_with_color
    mock_log_with_color = MagicMock()
    mock_table = MagicMock()
    mock_table.table = "Mocked Table Output"
    
    monkeypatch.setattr("cloudlift.deployment.deployer.log_with_color", mock_log_with_color)
    monkeypatch.setattr("cloudlift.deployment.deployer.SingleTable", lambda x: mock_table)
    monkeypatch.setattr("cloudlift.deployment.deployer.Color", lambda x: x)
    monkeypatch.setattr("builtins.print", MagicMock())
    
    print_task_diff("test-service", diffs, "green")
    
    # Verify log calls - should be 3 since image is changed:
    # 1. Image status message
    # 2. Image diff details
    # 3. Environment changes message
    assert mock_log_with_color.call_count == 3
    mock_log_with_color.assert_any_call("test-service New image getting deployed", "green")
    mock_log_with_color.assert_any_call("test-service old-image:v1 -> new-image:v2", "green")
    mock_log_with_color.assert_any_call("test-service Environment changes", "green")


def test_print_task_diff_no_changes(monkeypatch):
    """Test printing task diff when there are no changes"""
    # Mock diff objects with no changes
    image_diff = MagicMock()
    image_diff.field = 'image'
    image_diff.old_value = 'same-image:v1'  # Same values
    image_diff.value = 'same-image:v1'
    
    env_diff = MagicMock()
    env_diff.field = 'secrets'
    env_diff.old_value = {'ENV_VAR1': 'same', 'ENV_VAR2': 'same'}  # No changes
    env_diff.value = {'ENV_VAR1': 'same', 'ENV_VAR2': 'same'}
    
    diffs = [image_diff, env_diff]
    
    # Mock log_with_color
    mock_log_with_color = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.deployer.log_with_color", mock_log_with_color)
    # Fix: Use builtins.print instead of module-level print
    monkeypatch.setattr("builtins.print", MagicMock())
    
    print_task_diff("test-service", diffs, "blue")
    
    # Verify log calls - should report no changes
    assert mock_log_with_color.call_count == 2
    mock_log_with_color.assert_any_call("test-service No change in image version", "blue")
    mock_log_with_color.assert_any_call("test-service No change in environment variables", "blue")


def test_deploy_new_version_success(monkeypatch, mock_task_definition):
    """Test successful deployment of a new version"""
    # Mock the required objects and functions
    mock_ecs_client = MagicMock()
    mock_deployment = MagicMock()
    mock_deployment.service = MagicMock()
    mock_deployment.service.desired_count = 1
    mock_deployment.get_current_task_definition.return_value = mock_task_definition
    mock_deployment.update_task_definition.return_value = MagicMock()
    
    # Mock build_config, log_bold, deploy_and_wait
    mock_build_config = MagicMock(return_value=[('KEY1', '/path/to/key1')])
    mock_deploy_and_wait = MagicMock(return_value=True)
    mock_log_bold = MagicMock()
    mock_print_task_diff = MagicMock()
    
    monkeypatch.setattr("cloudlift.deployment.deployer.build_config", mock_build_config)
    monkeypatch.setattr("cloudlift.deployment.deployer.deploy_and_wait", mock_deploy_and_wait)
    monkeypatch.setattr("cloudlift.deployment.deployer.log_bold", mock_log_bold)
    monkeypatch.setattr("cloudlift.deployment.deployer.print_task_diff", mock_print_task_diff)
    monkeypatch.setattr("cloudlift.deployment.deployer.EcsClient", lambda *args: mock_ecs_client)
    monkeypatch.setattr("cloudlift.deployment.deployer.DeployAction", lambda *args: mock_deployment)
    
    # Execute the function under test
    result = deploy_new_version(
        region='ap-south-1',
        cluster_name='test-cluster',
        ecs_service_name='test-service',
        deploy_version_tag='v1.0.0',
        service_name='test-service',
        sample_env_file_path='env.sample',
        env_name='test-env'
    )
    
    # Verify the results
    assert result is True
    mock_log_bold.assert_called_with("test-service Deployed successfully.")
    mock_deployment.get_current_task_definition.assert_called_once()
    mock_task_definition.set_images.assert_called_once_with('v1.0.0')
    mock_deployment.update_task_definition.assert_called_once()
    mock_deploy_and_wait.assert_called_once()


def test_deploy_new_version_failure(monkeypatch, mock_task_definition):
    """Test failed deployment of a new version"""
    # Mock the required objects and functions
    mock_ecs_client = MagicMock()
    mock_deployment = MagicMock()
    mock_deployment.service = MagicMock()
    mock_deployment.service.desired_count = 1
    mock_deployment.get_current_task_definition.return_value = mock_task_definition
    mock_deployment.update_task_definition.return_value = MagicMock()
    
    # Mock build_config, log_err, deploy_and_wait
    mock_build_config = MagicMock(return_value=[('KEY1', '/path/to/key1')])
    mock_deploy_and_wait = MagicMock(return_value=False)  # Deployment fails
    mock_log_err = MagicMock()
    mock_print_task_diff = MagicMock()
    
    monkeypatch.setattr("cloudlift.deployment.deployer.build_config", mock_build_config)
    monkeypatch.setattr("cloudlift.deployment.deployer.deploy_and_wait", mock_deploy_and_wait)
    monkeypatch.setattr("cloudlift.deployment.deployer.log_err", mock_log_err)
    monkeypatch.setattr("cloudlift.deployment.deployer.print_task_diff", mock_print_task_diff)
    monkeypatch.setattr("cloudlift.deployment.deployer.EcsClient", lambda *args: mock_ecs_client)
    monkeypatch.setattr("cloudlift.deployment.deployer.DeployAction", lambda *args: mock_deployment)
    
    # Execute the function under test
    result = deploy_new_version(
        region='ap-south-1',
        cluster_name='test-cluster',
        ecs_service_name='test-service',
        deploy_version_tag='v1.0.0',
        service_name='test-service',
        sample_env_file_path='env.sample',
        env_name='test-env'
    )
    
    # Verify the results
    assert result is False
    mock_log_err.assert_called_with("test-service Deployment failed.")


def test_deploy_new_version_with_complete_image(monkeypatch, mock_task_definition):
    """Test deployment with complete image URI specified"""
    # Mock the required objects and functions
    mock_ecs_client = MagicMock()
    mock_deployment = MagicMock()
    mock_deployment.service = MagicMock()
    mock_deployment.service.desired_count = 1
    mock_deployment.get_current_task_definition.return_value = mock_task_definition
    mock_deployment.update_task_definition.return_value = MagicMock()
    
    # Mock build_config, log_bold, deploy_and_wait
    mock_build_config = MagicMock(return_value=[('KEY1', '/path/to/key1')])
    mock_deploy_and_wait = MagicMock(return_value=True)
    mock_log_bold = MagicMock()
    mock_print_task_diff = MagicMock()
    
    monkeypatch.setattr("cloudlift.deployment.deployer.build_config", mock_build_config)
    monkeypatch.setattr("cloudlift.deployment.deployer.deploy_and_wait", mock_deploy_and_wait)
    monkeypatch.setattr("cloudlift.deployment.deployer.log_bold", mock_log_bold)
    monkeypatch.setattr("cloudlift.deployment.deployer.print_task_diff", mock_print_task_diff)
    monkeypatch.setattr("cloudlift.deployment.deployer.EcsClient", lambda *args: mock_ecs_client)
    monkeypatch.setattr("cloudlift.deployment.deployer.DeployAction", lambda *args: mock_deployment)
    
    # Execute the function under test with a complete image URI
    complete_image = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/test-repo:v1.0.0"
    result = deploy_new_version(
        region='ap-south-1',
        cluster_name='test-cluster',
        ecs_service_name='test-service',
        deploy_version_tag='v1.0.0',
        service_name='test-service',
        sample_env_file_path='env.sample',
        env_name='test-env',
        complete_image_uri=complete_image
    )
    
    # Verify the results
    assert result is True
    mock_task_definition.set_images.assert_called_once_with(
        'v1.0.0', 
        **{'test-container': complete_image}
    )


def test_deploy_new_version_with_zero_desired_count(monkeypatch, mock_task_definition):
    """Test deployment with zero desired count in service"""
    # Mock the required objects and functions
    mock_ecs_client = MagicMock()
    mock_deployment = MagicMock()
    mock_deployment.service = MagicMock()
    mock_deployment.service.desired_count = 0  # Zero desired count
    mock_deployment.get_current_task_definition.return_value = mock_task_definition
    mock_deployment.update_task_definition.return_value = MagicMock()
    
    # Mock build_config, log_bold, deploy_and_wait
    mock_build_config = MagicMock(return_value=[('KEY1', '/path/to/key1')])
    mock_deploy_and_wait = MagicMock(return_value=True)
    mock_log_bold = MagicMock()
    mock_print_task_diff = MagicMock()
    
    monkeypatch.setattr("cloudlift.deployment.deployer.build_config", mock_build_config)
    monkeypatch.setattr("cloudlift.deployment.deployer.deploy_and_wait", mock_deploy_and_wait)
    monkeypatch.setattr("cloudlift.deployment.deployer.log_bold", mock_log_bold)
    monkeypatch.setattr("cloudlift.deployment.deployer.print_task_diff", mock_print_task_diff)
    monkeypatch.setattr("cloudlift.deployment.deployer.EcsClient", lambda *args: mock_ecs_client)
    monkeypatch.setattr("cloudlift.deployment.deployer.DeployAction", lambda *args: mock_deployment)
    
    # Execute the function under test
    result = deploy_new_version(
        region='ap-south-1',
        cluster_name='test-cluster',
        ecs_service_name='test-service',
        deploy_version_tag='v1.0.0',
        service_name='test-service',
        sample_env_file_path='env.sample',
        env_name='test-env'
    )
    
    # Verify the results
    assert result is True
    # Should set desired count to 1 since it was 0
    mock_deployment.service.set_desired_count.assert_called_with(1)