from unittest.mock import MagicMock, call

import boto3
import pytest

from cloudlift.config.dynamodb_configuration import DynamodbConfiguration


@pytest.fixture
def dynamodb_config(mocked_boto):
    table_name = "test_table"
    kv_pairs = [("id", "value"), ("key", "attribute")]
    return DynamodbConfiguration(table_name, kv_pairs)


def test_get_table_creates_table_when_not_exists(dynamodb_config, mocked_boto, capsys):
    """
    Tests that the _get_table method creates a table when it does not exist
    """
    dynamodb_client = boto3.client("dynamodb")

    # Ensure the table does not exist initially
    assert dynamodb_config.table_name not in dynamodb_client.list_tables()["TableNames"]

    # Call the _get_table method, which should trigger table creation
    table = dynamodb_config._get_table()

    captured = capsys.readouterr()

    # Assertions to confirm table creation was logged
    assert (
        f"Could not find {dynamodb_config.table_name} table, creating one.."
        in captured.out
    )
    assert f"{dynamodb_config.table_name} table created!" in captured.out
    assert f"{dynamodb_config.table_name} table status is ACTIVE" in captured.out

    # Verify that the table now exists
    assert dynamodb_config.table_name in dynamodb_client.list_tables()["TableNames"]
    assert table.table_name == dynamodb_config.table_name


def test_get_table_returns_existing_table(dynamodb_config, mocked_boto, capsys):
    """
    Tests that the _get_table method returns an existing table without creating a new one
    """
    # call get_table method to create the table
    table = dynamodb_config._get_table()

    # Capture the output
    captured = capsys.readouterr()

    # Verify that the table was created
    assert f"{dynamodb_config.table_name} table created!" in captured.out
    assert f"{dynamodb_config.table_name} table status is ACTIVE" in captured.out
    assert table.table_name == dynamodb_config.table_name

    # Call get_table method again to return the existing table
    table = dynamodb_config._get_table()

    # Capture the output
    captured = capsys.readouterr()

    # Verify that the table was not created again
    assert f"{dynamodb_config.table_name} table created!" not in captured.out
    assert f"{dynamodb_config.table_name} table status is ACTIVE" not in captured.out
    assert table.table_name == dynamodb_config.table_name


def test_create_configuration_table_creates_correct_schema(
    dynamodb_config, mocked_boto, capsys
):
    """
    Tests that the _create_configuration_table method creates a table with the expected schema
    """
    # Call _create_configuration_table to create a table with the expected schema
    dynamodb_config._create_configuration_table()

    # Capture the output
    captured = capsys.readouterr()

    # Verify the table creation
    dynamodb_client = boto3.client("dynamodb")
    response = dynamodb_client.describe_table(TableName=dynamodb_config.table_name)
    key_schema = response["Table"]["KeySchema"]

    # Expected key schema based on kv_pairs
    expected_schema = [
        {"AttributeName": "id", "KeyType": "HASH"},
        {"AttributeName": "key", "KeyType": "RANGE"},
    ]
    assert key_schema == expected_schema

    # Check if the output contains the expected table creation log
    assert f"{dynamodb_config.table_name} table created!" in captured.out


def test_table_status_waits_until_active(
    dynamodb_config, mocked_boto, monkeypatch, capsys
):
    """
    Tests that the _table_status method waits until the table status is ACTIVE
    """
    sleep_mock = MagicMock()
    # Mocking the sleep method to speed up the test
    monkeypatch.setattr("cloudlift.config.dynamodb_configuration.sleep", sleep_mock)
    # Setting up mocked DynamoDB client
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.create_table(
        TableName=dynamodb_config.table_name,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    # Mock describe_table to control status sequence
    dynamodb_config.dynamodb_client.describe_table = MagicMock(
        side_effect=[
            {"Table": {"TableStatus": "CREATING"}},
            {"Table": {"TableStatus": "CREATING"}},
            {"Table": {"TableStatus": "ACTIVE"}},
        ]
    )

    # Call the _table_status method, which should wait until the table is ACTIVE
    dynamodb_config._table_status()

    captured = capsys.readouterr()
    assert f"Checking {dynamodb_config.table_name} table status..." in captured.out
    assert f"{dynamodb_config.table_name} table status is ACTIVE" in captured.out
    # sleep should be called 4 times (3 times in the loop + 1 time after the loop)
    assert sleep_mock.call_count == 4
    assert sleep_mock.call_args_list == [call(1), call(1), call(1), call(10)]
