"""Container narrowing must preserve mutable snapshot identity and shape rules."""
from __future__ import annotations

import pytest

from app.core.value_types import dictionary_value, list_value


@pytest.mark.parametrize("value", [None, False, 12, "wrong-shape"])
def test_noncontainers_become_empty(value):
    assert dictionary_value(value) == {}
    assert list_value(value) == []


def test_dictionary_is_not_copied_or_coerced():
    source = {"enabled": False, "nested": []}
    assert dictionary_value(source) is source
    assert list_value(source) == []


def test_list_is_not_copied_or_coerced():
    source = [None, {"enabled": False}]
    assert list_value(source) is source
    assert dictionary_value(source) == {}
