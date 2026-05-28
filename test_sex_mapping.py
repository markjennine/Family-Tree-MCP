"""Unit tests for sex field mapping — no database required."""
from tools import SEX_NAMES


def test_male_maps_correctly():
    assert SEX_NAMES[0] == "Male"


def test_female_maps_correctly():
    assert SEX_NAMES[1] == "Female"


def test_unknown_maps_correctly():
    assert SEX_NAMES[2] == "Unknown"


def test_default_fallback():
    assert SEX_NAMES.get(99, "Unknown") == "Unknown"


if __name__ == "__main__":
    test_male_maps_correctly()
    test_female_maps_correctly()
    test_unknown_maps_correctly()
    test_default_fallback()
    print("All sex mapping tests passed.")
