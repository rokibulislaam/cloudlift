from cloudlift.config.account import get_user_id
from cloudlift.config.banner import (highlight_production,
                                     highlight_user_account_details)


def test_highlight_production(capsys):
    highlight_production()
    captured = capsys.readouterr()
    assert "Careful!" in captured.out
    assert "PRODUCTION" in captured.out


def test_highlight_user_account_details(capsys, mocked_sts_client):
    account_id = mocked_sts_client.get_caller_identity()["Account"]
    username, _ = get_user_id(sts_client=mocked_sts_client)
    highlight_user_account_details()
    captured = capsys.readouterr()
    assert f"Using Aws account {account_id} and Username {username}" in captured.out
