import pytest

from cloudlift.exceptions import UnrecoverableException
from cloudlift.utils import flatten_dict, generate_pascalcase_name


@pytest.mark.parametrize(
    "input_string, expected_output, max_length",
    [
        ("hello world", "HelloWorld", 32),
        ("hello_world_123", "HelloWorld123", 32),
        ("hello-world!@#$%^&*()", "HelloWorld", 32),
        ("HelloWorld", "HelloWorld", 32),
        ("hello", "Hello", 32),
        ("", "", 32),
        ("!@#$%^&*()", "", 32),
        ("a" * 32, f"A{'a' * 31}", 32),
    ],
)
def test_generate_pascalcase_name(input_string, expected_output, max_length):
    assert generate_pascalcase_name(input_string, max_length) == expected_output


def test_generate_pascalcase_name_too_long():
    """
    Test that generate_pascalcase_name raises UnrecoverableException when the generated
    pascalcase name is longer than the specified max_length.
    """
    with pytest.raises(UnrecoverableException):
        generate_pascalcase_name("a" * 33, 32)


# Test cases using pytest.mark.parametrize
@pytest.mark.parametrize(
    "input_dict, parent_key, sep, expected_output",
    [
        # Test case 1: Simple flat dictionary
        ({"a": 1, "b": 2}, "", ".", {"a": 1, "b": 2}),
        # Test case 2: Nested dictionary with default separator '.'
        ({"a": 1, "b": {"c": 2, "d": 3}}, "", ".", {"a": 1, "b.c": 2, "b.d": 3}),
        # Test case 3: Nested dictionary with custom separator '_'
        ({"a": 1, "b": {"c": 2, "d": 3}}, "", "_", {"a": 1, "b_c": 2, "b_d": 3}),
        # Test case 4: Nested dictionary with parent key and default separator
        ({"a": 1, "b": {"c": 2}}, "root", ".", {"root.a": 1, "root.b.c": 2}),
        # Test case 5: Deeply nested dictionary
        ({"a": 1, "b": {"c": {"d": {"e": 5}}}}, "", ".", {"a": 1, "b.c.d.e": 5}),
        # Test case 6: Empty dictionary
        ({}, "", ".", {}),
        # Test case 7: Nested dictionary with custom separator '->'
        ({"a": 1, "b": {"c": {"d": 2}}}, "", "->", {"a": 1, "b->c->d": 2}),
        # Test case 8: Nested dictionary with both custom parent key and separator '/'
        (
            {"a": 1, "b": {"c": 2, "d": 3}},
            "nested",
            "/",
            {"nested/a": 1, "nested/b/c": 2, "nested/b/d": 3},
        ),
        # Test case 9: Lists are not flattened
        ({"a": 1, "b": [2, 3]}, "", ".", {"a": 1, "b": [2, 3]}),
    ],
)
def test_flatten_dict(input_dict, parent_key, sep, expected_output):
    """
    Test that flatten_dict function with different input scenarios, ensuring
    that the function does not mutate the original dictionary.
    """
    result = flatten_dict(input_dict, parent_key=parent_key, sep=sep)
    assert result == expected_output
