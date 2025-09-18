import decimal
import json

import pytest

from cloudlift.config.decimal_encoder import DecimalEncoder


def test_decimal_encoder():
    """
    Test that DecimalEncoder correctly converts Decimal objects to float or int.
    """
    # Create an object with Decimal values
    data = {
        "str": "String Value",
        "dec_1": decimal.Decimal("100.5"),
        "dec_2": decimal.Decimal("100.00"),
    }

    # Convert the object to JSON using DecimalEncoder
    json_data = json.dumps(data, cls=DecimalEncoder)

    # Parse the JSON string
    decoded_data = json.loads(json_data)

    # Check if Decimal values are correctly converted to float or int
    assert decoded_data["str"] == "String Value"
    assert isinstance(decoded_data["dec_1"], float)
    assert isinstance(decoded_data["dec_2"], int)


def test_decimal_encoder_non_decimal():
    """
    Test that DecimalEncoder correctly handles Decimal objects and raises TypeError for non-Decimal
    objects.
    """
    encoder = DecimalEncoder()

    # Test with a Decimal (should not raise TypeError)
    assert encoder.default(decimal.Decimal("1.5")) == 1.5
    assert encoder.default(decimal.Decimal("10")) == 10

    # Test with a string (should raise TypeError)
    with pytest.raises(TypeError):
        encoder.default("test")

    # Test with a list (should raise TypeError)
    with pytest.raises(TypeError):
        encoder.default([1, 2, 3])

    # Test with a dict (should raise TypeError)
    with pytest.raises(TypeError):
        encoder.default({"key": "value"})

    # Test with a float (should raise TypeError)
    with pytest.raises(TypeError):
        encoder.default(1.5)

    # Test with an int (should raise TypeError)
    with pytest.raises(TypeError):
        encoder.default(10)

    # Test with a custom object (should raise TypeError)
    class TestObject:
        pass

    test_obj = TestObject()

    with pytest.raises(TypeError):
        encoder.default(test_obj)
