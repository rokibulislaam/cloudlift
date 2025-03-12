import pytest
from unittest.mock import MagicMock, call
import dictdiffer

from cloudlift.deployment.editor import edit_config
from cloudlift.config import ParameterStore


@pytest.fixture
def parameter_store_mock():
    """Fixture for mocked ParameterStore."""
    mock = MagicMock()
    mock.get_existing_config_as_string.return_value = "original_content"
    return mock


@pytest.fixture
def mocked_dependencies(monkeypatch, parameter_store_mock):
    """Fixture for common mocked dependencies."""
    mocks = {
        "click_edit": MagicMock(),
        "log_warning": MagicMock(),
        "print_changes": MagicMock(),
        "click_confirm": MagicMock(),
        "read_config": MagicMock(),
    }
    
    monkeypatch.setattr("cloudlift.deployment.editor.ParameterStore", lambda name, env: parameter_store_mock)
    monkeypatch.setattr("cloudlift.deployment.editor.click.edit", mocks["click_edit"])
    monkeypatch.setattr("cloudlift.deployment.editor.log_warning", mocks["log_warning"])
    monkeypatch.setattr("cloudlift.deployment.editor.print_parameter_changes", mocks["print_changes"])
    monkeypatch.setattr("cloudlift.deployment.editor.click.confirm", mocks["click_confirm"])
    monkeypatch.setattr("cloudlift.deployment.editor.read_config", mocks["read_config"])
    
    return mocks


class TestEditor:
    def test_no_changes_when_edit_returns_none(self, parameter_store_mock, mocked_dependencies):
        """Test that when click.edit returns None, no changes are made and the function exits early."""
        # Setup
        mocked_dependencies["click_edit"].return_value = None
        
        # Execute
        edit_config("service-name", "environment")
        
        # Verify
        parameter_store_mock.get_existing_config_as_string.assert_called_once()
        mocked_dependencies["click_edit"].assert_called_once_with(str("original_content"))
        mocked_dependencies["log_warning"].assert_called_once_with("No changes made, exiting.")
        parameter_store_mock.set_config.assert_not_called()

    def test_no_changes_when_content_identical(self, parameter_store_mock, mocked_dependencies):
        """Test that when there are no differences between original and edited content, no config update is performed."""
        # Setup
        mocked_dependencies["click_edit"].return_value = "edited_content"
        mocked_dependencies["read_config"].side_effect = lambda x: {"config": "same"}
        
        # Execute
        edit_config("service-name", "environment")
        
        # Verify
        parameter_store_mock.get_existing_config_as_string.assert_called_once()
        mocked_dependencies["click_edit"].assert_called_once()
        mocked_dependencies["read_config"].assert_has_calls([call("original_content"), call("edited_content")])
        mocked_dependencies["log_warning"].assert_called_once_with("No changes made, exiting.")
        mocked_dependencies["print_changes"].assert_not_called()
        parameter_store_mock.set_config.assert_not_called()

    @pytest.fixture
    def setup_content_diff(self, monkeypatch, mocked_dependencies):
        """Fixture for setting up scenarios with content differences."""
        # Define the differences to be returned by dictdiffer
        differences = [("change", "path", ("old_val", "new_val"))]
        
        # Setup read_config to return different values
        def mock_read_config_with_diff(content):
            if content == "original_content":
                return {"key": "old_val"}
            else:
                return {"key": "new_val"}
        
        mocked_dependencies["read_config"].side_effect = mock_read_config_with_diff
        mocked_dependencies["click_edit"].return_value = "edited_content"
        
        # Patch dictdiffer.diff to return our predefined differences
        monkeypatch.setattr("dictdiffer.diff", lambda old, new: differences)
        
        return differences

    def test_changes_confirmed_by_user(self, parameter_store_mock, mocked_dependencies, setup_content_diff):
        """Test that when changes are detected and user confirms, the config is updated."""
        # Setup
        differences = setup_content_diff
        mocked_dependencies["click_confirm"].return_value = True  # User confirms
        
        # Execute
        edit_config("service-name", "environment")
        
        # Verify
        parameter_store_mock.get_existing_config_as_string.assert_called_once()
        mocked_dependencies["print_changes"].assert_called_once_with(differences)
        parameter_store_mock.set_config.assert_called_once_with(differences)
        mocked_dependencies["log_warning"].assert_not_called()

    def test_changes_aborted_by_user(self, parameter_store_mock, mocked_dependencies, setup_content_diff):
        """Test that when changes are detected but user doesn't confirm, the update is aborted."""
        # Setup
        differences = setup_content_diff
        mocked_dependencies["click_confirm"].return_value = False  # User does not confirm
        
        # Execute
        edit_config("service-name", "environment")
        
        # Verify
        parameter_store_mock.get_existing_config_as_string.assert_called_once()
        mocked_dependencies["print_changes"].assert_called_once_with(differences)
        parameter_store_mock.set_config.assert_not_called()
        mocked_dependencies["log_warning"].assert_called_once_with("Changes aborted.")