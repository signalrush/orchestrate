import pytest
from orchestrate.core import _parse_json, _validate_schema


def test_parse_plain_json():
    result = _parse_json('{"score": 3.5, "name": "test"}')
    assert result == {"score": 3.5, "name": "test"}


def test_parse_json_in_markdown_fence():
    text = 'Here is the result:\n```json\n{"score": 3.5}\n```\nDone.'
    result = _parse_json(text)
    assert result == {"score": 3.5}


def test_parse_json_embedded_in_text():
    text = 'The answer is {"score": 3.5} as computed.'
    result = _parse_json(text)
    assert result == {"score": 3.5}


def test_parse_no_json_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("no json here")


def test_parse_empty_string_raises():
    with pytest.raises(ValueError, match="No valid JSON"):
        _parse_json("")


def test_validate_schema_passes():
    _validate_schema({"score": 3.5, "name": "test"}, {"score": "float", "name": "str"})


def test_validate_schema_type_mismatch():
    with pytest.raises(ValueError, match="Schema validation failed"):
        _validate_schema({"score": "oops"}, {"score": "float"})


def test_validate_schema_missing_key():
    with pytest.raises(ValueError, match="Schema validation failed"):
        _validate_schema({}, {"score": "float"})
