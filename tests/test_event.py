# License: MIT
# Copyright © 2023 Frequenz Energy-as-a-Service GmbH

"""Tests for the select implementation."""

import asyncio as _asyncio

import pytest as _pytest

from frequenz.channels import ReceiverStoppedError
from frequenz.channels.event import Event


async def test_event() -> None:
    """Test the event implementation."""
    event = Event()
    assert not event.is_set
    assert not event.is_stopped

    is_ready = False

    async def wait_for_event() -> None:
        nonlocal is_ready
        await event.ready()
        is_ready = True

    event_task = _asyncio.create_task(wait_for_event())

    await _asyncio.sleep(0)  # Yield so the wait_for_event task can run.

    assert not is_ready
    assert not event.is_set
    assert not event.is_stopped

    event.set()

    await _asyncio.sleep(0)  # Yield so the wait_for_event task can run.
    assert is_ready
    assert event.is_set
    assert not event.is_stopped

    event.consume()
    assert not event.is_set
    assert not event.is_stopped
    assert event_task.done()
    assert event_task.result() is None
    assert not event_task.cancelled()

    event.stop()
    assert not event.is_set
    assert event.is_stopped

    await event.ready()
    with _pytest.raises(ReceiverStoppedError):
        event.consume()
    assert event.is_stopped
    assert not event.is_set

    await event_task


async def test_event_close_receiver() -> None:
    """Ensure that closing an event stops the receiver."""
    event = Event()
    assert not event.is_set
    assert not event.is_stopped

    is_ready = False

    async def wait_for_event() -> None:
        nonlocal is_ready
        await event.ready()
        is_ready = True

    event_task = _asyncio.create_task(wait_for_event())

    await _asyncio.sleep(0)  # Yield so the wait_for_event task can run.

    assert not is_ready
    assert not event.is_set
    assert not event.is_stopped

    event.set()

    await _asyncio.sleep(0)  # Yield so the wait_for_event task can run.
    assert is_ready
    assert event.is_set
    assert not event.is_stopped

    event.consume()
    assert not event.is_set
    assert not event.is_stopped
    assert event_task.done()
    assert event_task.result() is None
    assert not event_task.cancelled()

    event.close()
    assert not event.is_set
    assert event.is_stopped

    await event.ready()
    with _pytest.raises(ReceiverStoppedError):
        event.consume()
    assert event.is_stopped
    assert not event.is_set

    await event_task
