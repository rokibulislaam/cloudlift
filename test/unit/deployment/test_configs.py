import os
import pytest
from cloudlift.deployment.configs import deduce_name


def test_deduce_name_with_none():
    # Mock os.path.basename to return a fixed directory name
    def mock_basename(path):
        return "TestService"

    original_basename = os.path.basename
    os.path.basename = mock_basename

    try:
        result = deduce_name(None)
        assert result == "test-service"
    finally:
        # Restore original basename function
        os.path.basename = original_basename


def test_deduce_name_with_camelcase():
    result = deduce_name("TestService")
    assert result == "test-service"