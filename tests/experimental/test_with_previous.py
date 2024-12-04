# License: MIT
# Copyright © 2024 Frequenz Energy-as-a-Service GmbH

"""Tests for the WithPrevious implementation."""

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

import pytest

from frequenz.channels.experimental import WithPrevious

_T = TypeVar("_T")


@dataclass(frozen=True, kw_only=True)
class PredicateTestCase(Generic[_T]):
    """Test case for testing WithPrevious behavior with different predicates."""

    title: str
    messages: list[_T]
    expected_results: list[bool]
    predicate: Callable[[_T, _T], bool]
    first_is_true: bool


def always_true(old: object, new: object) -> bool:  # pylint: disable=unused-argument
    """Return always True."""
    return True


def always_false(old: object, new: object) -> bool:  # pylint: disable=unused-argument
    """Return always False."""
    return False


def is_greater(old: int, new: int) -> bool:
    """Return weather the new value is greater than the old one."""
    return new > old


def is_not_same_instance(old: object, new: object) -> bool:
    """Return weather the new value is not the same instance as the old one."""
    return old is not new


PREDICATE_TEST_CASES = [
    # Basic cases with different predicates
    PredicateTestCase(
        title="Always true predicate",
        messages=[1, 2, 3],
        expected_results=[True, True, True],
        predicate=always_true,
        first_is_true=True,
    ),
    PredicateTestCase(
        title="Always false predicate with first_is_true=False",
        messages=[1, 2, 3],
        expected_results=[False, False, False],
        predicate=always_false,
        first_is_true=False,
    ),
    PredicateTestCase(
        title="Greater than predicate",
        messages=[1, 2, 0, 0, 1, 2],
        expected_results=[False, True, False, False, True, True],
        predicate=is_greater,
        first_is_true=False,
    ),
    # Edge cases
    PredicateTestCase(
        title="Empty sequence",
        messages=[],
        expected_results=[],
        predicate=always_true,
        first_is_true=True,
    ),
    PredicateTestCase(
        title="Single value with first_is_true=True",
        messages=[1],
        expected_results=[True],
        predicate=always_false,
        first_is_true=True,
    ),
    PredicateTestCase(
        title="Single value with first_is_true=False",
        messages=[1],
        expected_results=[False],
        predicate=always_true,
        first_is_true=False,
    ),
    # Instance comparison
    PredicateTestCase(
        title="Same instances",
        messages=[1, 1],
        expected_results=[True, False],
        predicate=is_not_same_instance,
        first_is_true=True,
    ),
    PredicateTestCase(
        title="Different instances of same values",
        messages=[[1], [1]],
        expected_results=[True, True],
        predicate=is_not_same_instance,
        first_is_true=True,
    ),
]


@pytest.mark.parametrize(
    "test_case",
    PREDICATE_TEST_CASES,
    ids=lambda test_case: test_case.title,
)
def test_only_if_previous(test_case: PredicateTestCase[_T]) -> None:
    """Test the WithPrevious with different predicates and sequences.

    Args:
        test_case: The test case containing the input values and expected results.
    """
    only_if_previous = WithPrevious(
        test_case.predicate,
        first_is_true=test_case.first_is_true,
    )
    results = [only_if_previous(msg) for msg in test_case.messages]
    assert results == test_case.expected_results


def test_only_if_previous_state_independence() -> None:
    """Test that multiple WithPrevious instances maintain independent state."""
    only_if_previous1 = WithPrevious(is_greater)
    only_if_previous2 = WithPrevious(is_greater)

    # First message should be accepted (first_is_true default is True)
    assert only_if_previous1(1) is True
    assert only_if_previous2(10) is True

    # Second messages should be evaluated independently
    assert only_if_previous1(0) is False  # 0 is not greater than 1
    assert only_if_previous2(20) is True  # 20 is greater than 10


def test_only_if_previous_str_representation() -> None:
    """Test the string representation of WithPrevious."""
    only_if_previous = WithPrevious(is_greater)
    assert str(only_if_previous) == "WithPrevious:is_greater"
    assert (
        repr(only_if_previous) == f"<WithPrevious: {is_greater!r} first_is_true=True>"
    )


def test_only_if_previous_sentinel_str() -> None:
    """Test the string representation of the sentinel value."""
    only_if_previous = WithPrevious(always_true)

    # Access the private attribute for testing purposes
    # pylint: disable=protected-access
    assert str(only_if_previous._last_message) == "<no value received yet>"
