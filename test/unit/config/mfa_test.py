import os
from unittest.mock import patch

import boto3
import boto3.session
import pytest

from cloudlift.config.mfa import do_mfa_login, get_mfa_session, get_username

# https://github.com/getmoto/moto/blob/a15f8669908eacff931193bdb4b9123ed7d0294f/moto/sts/responses.py#L109-L121
MOTO_DEFAULT_SESSION_TOKEN = "AQoEXAMPLEH4aoAH0gNCAPyJxz4BlCFFxWNE1OPTgk5TthT+FvwqnKwRcOIfrRh3c/LTo6UDdyJwOOvEVPvLXCrrrUtdnniCEXAMPLE/IvU1dYUg2RVAJBanLiHb4IgRmpRV3zrkuWJOgQs8IZZaIv2BXIa2R4OlgkBN9bkUDNCJiBeb/AXlzBBko7b15fjrBs2+cTQtpZ3CYWFXG8C5zqx37wnOE49mRl/+OtkIKGO7fAE"
MOTO_DEFAULT_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY"
MOTO_DEFAULT_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture(autouse=True)
def clear_env_vars():
    """Clear AWS environment variables before each test."""
    env_vars = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
    ]
    for var in env_vars:
        os.environ.pop(var, None)


def test_do_mfa_login(mocked_boto):
    """
    Tests that do_mfa_login sets the AWS environment variables correctly.
    """
    result = do_mfa_login(mfa_code="123456")
    assert result is not None
    assert "Credentials" in result

    credentials = result["Credentials"]

    assert credentials["AccessKeyId"] == MOTO_DEFAULT_ACCESS_KEY
    assert credentials["SecretAccessKey"] == MOTO_DEFAULT_SECRET_ACCESS_KEY
    assert credentials["SessionToken"] == MOTO_DEFAULT_SESSION_TOKEN

    assert os.environ["AWS_ACCESS_KEY_ID"] == credentials["AccessKeyId"]
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == credentials["SecretAccessKey"]
    assert os.environ["AWS_SESSION_TOKEN"] == credentials["SessionToken"]
    assert os.environ["AWS_DEFAULT_REGION"] == "ap-south-1"


def test_do_mfa_login_without_mfa_code(mocked_boto):
    """
    Tests that do_mfa_login prompts for an MFA code if one is not provided.
    """
    with patch("builtins.input", return_value="123456"):
        result = do_mfa_login()

    assert result is not None
    assert "Credentials" in result


def test_get_mfa_session(mocked_boto):
    """
    Tests that get_mfa_session returns a valid boto3 session.
    """
    session = get_mfa_session(mfa_code="123456")
    assert session is not None
    assert session.region_name == "ap-south-1"

    assert isinstance(session, boto3.session.Session)
    credentials = session.get_credentials()
    assert credentials is not None
    assert credentials.access_key == MOTO_DEFAULT_ACCESS_KEY
    assert credentials.secret_key == MOTO_DEFAULT_SECRET_ACCESS_KEY
    assert credentials.token == MOTO_DEFAULT_SESSION_TOKEN


def test_get_mfa_session_without_mfa_code(mocked_boto):
    """
    Tests that get_mfa_session prompts for an MFA code if one is not provided.
    """
    with patch("builtins.input", return_value="123456"):
        session = get_mfa_session()

    assert session is not None
    assert isinstance(session, boto3.session.Session)
    assert session.get_credentials() is not None
    assert session.region_name == "ap-south-1"


def test_get_username(mocked_boto):
    """
    Tests that get_username returns the correct username.
    """
    expected_username = "moto"
    username = get_username()
    assert username == expected_username
