from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from cloudlift.deployment.changesets import create_change_set
from cloudlift.exceptions import UnrecoverableException


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def mock_sleep(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.changesets.sleep", mock)
    return mock


@pytest.fixture
def mock_log(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.changesets.log", mock)
    return mock


@pytest.fixture
def mock_log_bold(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.changesets.log_bold", mock)
    return mock


@pytest.fixture
def mock_log_err(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.changesets.log_err", mock)
    return mock


@pytest.fixture
def mock_click(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr("cloudlift.deployment.changesets.click", mock)
    return mock


def test_create_change_set_success(
    mock_client, mock_sleep, mock_log, mock_log_bold, mock_click
):
    """
    Test successful creation and execution of a change set
    """
    mock_client.create_change_set.return_value = {"Id": "test-changeset-id"}
    mock_client.describe_change_set.side_effect = [
        {"Status": "CREATE_PENDING"},
        {
            "Status": "CREATE_COMPLETE",
            "Changes": [
                {
                    "ResourceChange": {
                        "Action": "Add",
                        "LogicalResourceId": "TestResource",
                        "ResourceType": "AWS::S3::Bucket",
                        "Details": [],  # Added this line
                    }
                }
            ],
        },
    ]
    mock_click.confirm.return_value = True

    result = create_change_set(
        mock_client,
        "test-template",
        "TemplateBody",
        "test-stack",
        "test-key",
        "test-env",
    )

    assert result["Status"] == "CREATE_COMPLETE"
    mock_client.create_change_set.assert_called_once()
    mock_client.describe_change_set.assert_called()
    mock_click.confirm.assert_called_once()
    mock_log_bold.assert_called_with("Changeset created.. Following are the changes")


def test_create_change_set_failed(mock_client, mock_sleep, mock_log_err, mock_log_bold):
    """
    Test failed creation of a change set
    """
    mock_client.create_change_set.return_value = {"Id": "test-changeset-id"}
    mock_client.describe_change_set.return_value = {
        "Status": "FAILED",
        "StatusReason": "Test failure reason",
    }

    create_change_set(
        mock_client,
        "test-template",
        "TemplateBody",
        "test-stack",
        "test-key",
        "test-env",
    )

    mock_client.create_change_set.assert_called_once()
    mock_client.describe_change_set.assert_called_once()
    mock_log_err.assert_called_with("Changeset creation failed!")
    mock_log_bold.assert_called_with("Test failure reason")
    mock_client.delete_change_set.assert_called_once_with(
        ChangeSetName="test-changeset-id"
    )


def test_create_change_set_no_changes(
    mock_client, mock_sleep, mock_log_bold, mock_click
):
    """
    Test when there are no changes in the change set
    """
    mock_client.create_change_set.return_value = {"Id": "test-changeset-id"}
    mock_client.describe_change_set.return_value = {
        "Status": "FAILED",
        "StatusReason": "The submitted information didn't contain changes.",
    }
    mock_click.confirm.return_value = False

    result = create_change_set(
        mock_client,
        "test-template",
        "TemplateBody",
        "test-stack",
        "test-key",
        "test-env",
    )

    assert result is None
    mock_client.create_change_set.assert_called_once()
    mock_client.describe_change_set.assert_called_once()
    mock_log_bold.assert_called_with(
        "The submitted information didn't contain changes."
    )


def test_create_change_set_template_url_success(
    mock_client, mock_sleep, mock_log, mock_log_bold, mock_click
):
    """
    Test successful creation and execution of a change set with TemplateURL
    """
    mock_client.create_change_set.return_value = {"Id": "test-changeset-id"}
    mock_client.describe_change_set.side_effect = [
        {"Status": "CREATE_PENDING"},
        {
            "Status": "CREATE_COMPLETE",
            "Changes": [
                {
                    "ResourceChange": {
                        "Action": "Modify",
                        "LogicalResourceId": "TestResource",
                        "ResourceType": "AWS::Lambda::Function",
                        "Details": [],
                    }
                }
            ],
        },
    ]
    mock_click.confirm.return_value = True

    result = create_change_set(
        mock_client,
        "https://example.com/test-template",
        "TemplateURL",
        "test-stack",
        "test-key",
        "test-env",
    )

    assert result["Status"] == "CREATE_COMPLETE"
    mock_client.create_change_set.assert_called_once()
    mock_client.describe_change_set.assert_called()
    mock_click.confirm.assert_called_once()
    mock_log_bold.assert_called_with("Changeset created.. Following are the changes")
