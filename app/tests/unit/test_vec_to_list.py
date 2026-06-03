"""
Unit tests: _vec_to_list coerces pgvector values safely.

With the pgvector codec registered, ``vector`` columns load as numpy arrays.
The old ``list(value or [])`` idiom raised "truth value of an array with more
than one element is ambiguous". ``_vec_to_list`` must be None-safe and
array-safe and always yield plain floats.
"""

from __future__ import annotations

import numpy as np

from app.services.factories import _vec_to_list


def test_none_returns_empty_list() -> None:
    assert _vec_to_list(None) == []


def test_plain_list_passthrough_as_floats() -> None:
    out = _vec_to_list([1, 2, 3])
    assert out == [1.0, 2.0, 3.0]
    assert all(isinstance(x, float) for x in out)


def test_numpy_array_does_not_raise_and_coerces() -> None:
    # The exact case that crashed: a multi-element ndarray in a bool context.
    arr = np.array([-0.0089998, -0.00948585, 0.123], dtype=np.float32)
    out = _vec_to_list(arr)
    assert len(out) == 3
    assert all(isinstance(x, float) for x in out)
    assert out[2] == np.float32(0.123).item() or abs(out[2] - 0.123) < 1e-6


def test_empty_array_returns_empty() -> None:
    assert _vec_to_list(np.array([], dtype=np.float32)) == []
