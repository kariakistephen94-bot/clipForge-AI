import pytest

from app.utils.timeutil import format_timestamp, parse_fraction, parse_timestamp, srt_timestamp


@pytest.mark.parametrize(
    "value,expected",
    [
        (12, 12.0),
        (12.5, 12.5),
        ("12.5", 12.5),
        ("12.5s", 12.5),
        ("01:30", 90.0),
        ("1:02:03", 3723.0),
        ("00:14:31.250", 871.25),
        ("00:00:05,5", 5.5),
        (-3, 0.0),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [None, "", "abc", True, float("nan")])
def test_parse_timestamp_rejects(bad):
    with pytest.raises((ValueError, TypeError)):
        parse_timestamp(bad)


def test_format_roundtrip():
    assert format_timestamp(871) == "00:14:31"
    assert format_timestamp(871.2506, millis=True) == "00:14:31.251"
    assert srt_timestamp(3723.5) == "01:02:03,500"
    assert parse_timestamp(format_timestamp(5025.4, millis=True)) == pytest.approx(5025.4)


def test_parse_fraction():
    assert parse_fraction("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert parse_fraction("25") == 25
    assert parse_fraction("0/0") == 0
    assert parse_fraction(None) == 0
