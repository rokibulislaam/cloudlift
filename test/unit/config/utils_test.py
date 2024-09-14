import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from click import style
from jsonschema import validate

from cloudlift.config.utils import ConfigUtils
from cloudlift.exceptions import UnrecoverableException
from cloudlift.version import VERSION


@pytest.fixture
def sample_config():
    return {
        "key1": "value1",
        "key2": 42,
        "key3": ["item1", "item2"],
    }


@pytest.fixture
def sample_config_json_schema():
    return {
        "properties": {
            "key1": {"type": "string"},
            "key2": {"type": "integer"},
            "key3": {"items": {"type": "string"}, "type": "array"},
        },
        "type": "object",
        "required": ["key1", "key2", "key3"],
    }


@pytest.fixture()
def changes_validator_mock(sample_config_json_schema):
    # Creating a MagicMock object to replace the 'edit' function
    def validator(config):
        try:
            validate(config, sample_config_json_schema)
        except Exception as e:
            raise UnrecoverableException("Schema validation failed")

    mock = MagicMock(side_effect=validator)
    return mock


@pytest.fixture
def config_utils_instance(sample_config, monkeypatch):
    os.environ["EDITOR"] = "echo"

    # Creating a ConfigUtils instance with the sample configuration
    config_utils = ConfigUtils(current_configuration=sample_config)

    return config_utils


@pytest.fixture(autouse=True)
def click_confirm_mock(monkeypatch):
    """
    Fixture to mock click.confirm to always return True.
    """
    confirm_mock = MagicMock()
    confirm_mock.return_value = True
    monkeypatch.setattr("cloudlift.config.utils.confirm", confirm_mock)
    return confirm_mock


@pytest.fixture()
def click_edit_mock(monkeypatch):
    # Creating a MagicMock object to replace the 'edit' function
    edit_mock = MagicMock()
    monkeypatch.setattr("cloudlift.config.utils.edit", edit_mock)
    return edit_mock


def test_fault_tolerant_edit_config(
    config_utils_instance,
    sample_config,
    sample_config_json_schema,
    click_edit_mock,
    click_confirm_mock,
    capsys,
):
    click_edit_mock.return_value = None
    updated_config = config_utils_instance.fault_tolerant_edit_config()
    assert updated_config is None
    captured = capsys.readouterr()
    assert "No changes made." in captured.out

    # Test when valid changes are made
    new_config = {"key1": "new_value", "key2": 123}
    click_edit_mock.return_value = json.dumps(new_config)
    updated_config = config_utils_instance.fault_tolerant_edit_config(
        current_configuration=new_config
    )
    assert updated_config == new_config
    captured = capsys.readouterr()
    assert "No changes made." not in captured.out

    # Test handling JSONDecodeError
    invalid_json = "invalid_json"
    click_edit_mock.return_value = invalid_json
    click_confirm_mock.return_value = False

    updated_config = config_utils_instance.fault_tolerant_edit_config(
        current_configuration=sample_config
    )
    assert updated_config is None
    captured = capsys.readouterr()
    assert "Error in the JSON configuration" in captured.out


def test_fault_tolerant_edit_config_schema_validation_error(
    config_utils_instance,
    capsys,
    click_edit_mock,
    click_confirm_mock,
    sample_config,
    changes_validator_mock,
):
    # Test handling UnrecoverableException (schema validation error)
    click_edit_mock.return_value = None

    def validate_config(config):
        raise UnrecoverableException("Schema validation failed")

    config_utils_instance.changes_validation_function = validate_config
    updated_config = config_utils_instance.fault_tolerant_edit_config()
    # assert if it exited with UnrecoverableException
    assert updated_config is None

    #  Test schema validation with version injection
    config_utils_instance.inject_version = True
    config_utils_instance.changes_validation_function = changes_validator_mock
    config_utils_instance.current_configuration = sample_config
    config_utils_instance._validate_schema(
        sample_config
    )  # No exception should be raised
    config_utils_instance.changes_validation_function.assert_called_once_with(
        {**sample_config, "cloudlift_version": VERSION}
    )

    config_utils_instance.inject_version = False
    config_utils_instance._validate_schema(
        sample_config
    )  # No exception should be raised
    config_utils_instance.changes_validation_function.assert_called_with(sample_config)

    # Test schema validation with an invalid JSON configuration
    with pytest.raises(UnrecoverableException):
        invalid_config = {"some_key": "some_value"}
        config_utils_instance.changes_validation_function = changes_validator_mock
        config_utils_instance._validate_schema(invalid_config)


def test_edit_temp_config(
    config_utils_instance,
    click_edit_mock,
    monkeypatch,
    click_confirm_mock,
    sample_config,
    sample_config_json_schema,
):
    new_config = {"key1": "new_value", "key2": 123}
    # click_edit_mock.return_value = json.dumps(new_config)
    updated_config = config_utils_instance._edit_temp_config(new_config)
    assert updated_config == new_config
    temp_file = config_utils_instance.temp_file
    assert os.path.exists(temp_file)
    os.remove(temp_file)

    # Test editing with valid changes in the temporary configuration file
    new_config = {"key1": "new_value", "key2": 123}
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    temp_file.write(json.dumps(new_config).encode())
    temp_file.close()
    updated_config = config_utils_instance._edit_config_with_temp_changes(new_config)
    assert updated_config == new_config
    os.remove(temp_file.name)

    # Test editing with changes that fail schema validation in the temporary configuration file
    click_confirm_mock.return_value = False

    def validate_config(config):
        raise UnrecoverableException("Schema validation failed")

    config_utils_instance.changes_validation_function = validate_config
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    temp_file.write(json.dumps({"key1": "value1"}).encode())
    temp_file.close()
    updated_config = config_utils_instance._edit_config_with_temp_changes({})
    assert updated_config is None
    os.remove(temp_file.name)


def test_handle_json_decode_error(config_utils_instance, capsys, click_confirm_mock):
    click_confirm_mock.return_value = False
    # Test handling JSONDecodeError with a string input
    invalid_json = "invalid_json"
    updated_config = config_utils_instance._handle_json_decode_error(invalid_json)
    assert updated_config is None
    captured = capsys.readouterr()
    assert "Error in the JSON configuration" in captured.out


def test_highlight_error_location(config_utils_instance):
    # Test highlighting the error location in JSON content
    json_content = json.dumps({"key1": "value1", "key2": "value2"})
    error_line = 1
    error_column = 2
    highlighted, error_line_content = config_utils_instance._highlight_error_location(
        json_content, error_line, error_column
    )
    expected_highlight = json_content
    expected_highlight = (
        "1: "
        + expected_highlight[: error_column - 1]
        + style(expected_highlight[error_column - 1], fg="red")
        + expected_highlight[error_column:]
    )
    assert highlighted == expected_highlight
    assert error_line_content == json_content

    # Test highlighting the error position in a line
    line = "This is an example line."
    error_column = 9
    highlighted = config_utils_instance._highlight_error_position(line, error_column)
    assert highlighted == "This is " + style("a", fg="red") + "n example line."

    # Test retrieving a line by its li  ne number
    content = "Line 1\nLine 2\nLine 3"
    line_number = 2
    line = config_utils_instance._get_line_by_number(content, line_number)
    assert line == "Line 2"
