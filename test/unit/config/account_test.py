from unittest.mock import MagicMock

import pytest

from cloudlift.config.account import get_account_id, get_user_id


@pytest.fixture
def sts_client_mock():
    return MagicMock()


def test_account_id(sts_client_mock):
    sts_client_mock.get_caller_identity.return_value = {"Account": "123456789012"}
    result = get_account_id(sts_client=sts_client_mock)
    assert result == "123456789012"


def test_get_user_id(sts_client_mock):
    # Test with mock user
    sts_client_mock.get_caller_identity.return_value = {
        "Arn": "arn:aws:iam::123456789012:user/mock_user",
        "Account": "123456789012",
    }
    username, account = get_user_id(sts_client=sts_client_mock)
    assert username == "mock_user"
    assert account == "123456789012"

    # Test with mock assumed-role
    sts_client_mock.get_caller_identity.return_value = {
        "Arn": "arn:aws:sts::123456789012:assumed-role/mock_role",
        "Account": "123456789012",
    }
    username, account = get_user_id(sts_client=sts_client_mock)
    assert username == "mock_role"
    assert account == "123456789012"
