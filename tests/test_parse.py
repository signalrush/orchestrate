import pytest
from orchestrate.core import _parse_json


def test_parse_plain_json():
    result = _parse_json(
        '{"score": 3.5, "name": "test"}', {"score": "float", "name": "str"}
    )
    assert result == {"score": 3.5, "name": "test"}


def test_parse_json_in_markdown_fence():
    text = 'Here is the result:\n```json\n{"score": 3.5}\n```\nDone.'
    result = _parse_json(text, {"score": "float"})
    assert result == {"score": 3.5}


def test_parse_json_embedded_in_text():
    text = 'The answer is {"score": 3.5} as computed.'
    result = _parse_json(text, {"score": "float"})
    assert result == {"score": 3.5}


def test_parse_no_json_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("no json here", {"score": "float"})


def test_parse_empty_string_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("", {"score": "float"})
