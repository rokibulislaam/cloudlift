import decimal
import json

from cloudlift.config.decimal_encoder import DecimalEncoder


def test_decimal_encoder():
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
