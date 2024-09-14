from terminaltables import SingleTable

from cloudlift.config.diff import print_json_changes, print_parameter_changes


# @pytest.mark.parametrize("operation_type", ["change", "add", "remove"])
def test_print_parameter_changes(
    capsys,
):
    # Test with change operation
    differences = [("change", "param1", ("old_value", "new_value"))]
    expected_table = SingleTable(
        [
            ["Type", "Config", "Old val", "New val"],
            ["change", "param1", "old_value", "new_value"],
        ]
    ).table
    print_parameter_changes(differences)
    change_capture = capsys.readouterr()
    assert expected_table in change_capture.out

    # Test with add operation
    differences = [("add", "param2", [("key1", "val1"), ("key2", "val2")])]
    expected_table = SingleTable(
        [
            ["Type", "Config", "Old val", "New val"],
            ["add", "key1", "", "val1"],
            ["add", "key2", "", "val2"],
        ]
    ).table
    print_parameter_changes(differences)
    add_capture = capsys.readouterr()
    assert expected_table in add_capture.out

    # Test with remove operation
    differences = [("remove", "param3", [("key3", "val3"), ("key4", "val4")])]

    expected_table = SingleTable(
        [
            ["Type", "Config", "Old val", "New val"],
            ["remove", "key3", "val3", ""],
            ["remove", "key4", "val4", ""],
        ]
    ).table
    print_parameter_changes(differences)
    remove_capture = capsys.readouterr()
    assert expected_table in remove_capture.out


def test_print_json_changes(capsys):
    # Test with change operation
    differences = [("change", "param1", ("old_value", "new_value"))]
    expected_table = SingleTable(
        [
            ["Type", "Config", "Old val", "New val"],
            ["change", "param1", "old_value", "new_value"],
        ]
    ).table
    print_json_changes(differences)
    change_capture = capsys.readouterr()
    assert expected_table in change_capture.out

    # Test with add operation
    differences = [("add", "param2", [("key1", "val1"), ("key2", "val2")])]
    expected_table = SingleTable(
        [
            ["Type", "Config", "Old val", "New val"],
            ["add", "param2", "", 'key1 : "val1"'],
            ["add", "param2", "", 'key2 : "val2"'],
        ]
    ).table
    print_json_changes(differences)
    add_capture = capsys.readouterr()
    assert expected_table in add_capture.out

    # Test with remove operation
    differences = [("remove", "param3", [("key3", "val3"), ("key4", "val4")])]
    expected_table = SingleTable(
        [
            ["Type", "Config", "Old val", "New val"],
            ["remove", "param3", 'key3 : "val3"', ""],
            ["remove", "param3", 'key4 : "val4"', ""],
        ]
    ).table
    print_json_changes(differences)
    remove_capture = capsys.readouterr()
    assert expected_table in remove_capture.out
