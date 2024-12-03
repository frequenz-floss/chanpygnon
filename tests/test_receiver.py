# License: MIT
# Copyright © 2024 Frequenz Energy-as-a-Service GmbH

"""Tests for the Receiver class."""

import asyncio
from collections.abc import Sequence
from typing import TypeGuard, assert_type

import pytest
from typing_extensions import override

from frequenz.channels import Receiver, ReceiverError, ReceiverStoppedError


class _MockReceiver(Receiver[int | str]):
    def __init__(self, messages: Sequence[int | str] = ()) -> None:
        self.messages = list(messages)
        self.stopped = False

    @override
    async def ready(self) -> bool:
        """Return True if there are messages to consume or the receiver is stopped."""
        if self.stopped:
            return False
        if self.messages:
            await asyncio.sleep(0)
            return True
        return False

    @override
    def consume(self) -> int | str:
        """Return the next message."""
        if self.stopped or not self.messages:
            raise ReceiverStoppedError(self)
        return self.messages.pop(0)

    def stop(self) -> None:
        """Stop the receiver."""
        self.stopped = True


async def test_receiver_take_while() -> None:
    """Test the take_while method."""
    receiver = _MockReceiver([1, 2, 3, 4, 5])

    filtered_receiver = receiver.take_while(lambda x: x % 2 == 0)
    async with asyncio.timeout(1):
        assert await filtered_receiver.receive() == 2
        assert await filtered_receiver.receive() == 4

        with pytest.raises(ReceiverStoppedError):
            await filtered_receiver.receive()


async def test_receiver_take_type_guard() -> None:
    """Test the take_while method using a TypeGuard."""
    receiver = _MockReceiver([1, "1", 2, "2", 3, "3", 4, 5])

    def is_even(x: int | str) -> TypeGuard[int]:
        if not isinstance(x, int):
            return False
        return x % 2 == 0

    filtered_receiver = receiver.take_while(is_even)
    async with asyncio.timeout(1):
        received = await filtered_receiver.receive()
        assert_type(received, int)
        assert received == 2
        assert await filtered_receiver.receive() == 4

        with pytest.raises(ReceiverStoppedError):
            await filtered_receiver.receive()


async def test_receiver_drop_while() -> None:
    """Test the drop_while method."""
    receiver = _MockReceiver([1, 2, 3, 4, 5])

    filtered_receiver = receiver.drop_while(lambda x: x % 2 == 0)
    async with asyncio.timeout(1):
        assert await filtered_receiver.receive() == 1
        assert await filtered_receiver.receive() == 3
        assert await filtered_receiver.receive() == 5

        with pytest.raises(ReceiverStoppedError):
            await filtered_receiver.receive()
