# License: MIT
# Copyright © 2022 Frequenz Energy-as-a-Service GmbH

"""Tests for the Channel implementation."""


import asyncio

import pytest

from frequenz.channels import (
    Anycast,
    ChannelClosedError,
    Receiver,
    ReceiverStoppedError,
    Sender,
    SenderError,
)


async def test_anycast() -> None:
    """Ensure sent messages are received by one receiver."""
    acast: Anycast[int] = Anycast(name="test")

    num_receivers = 5
    num_senders = 5
    expected_sum = num_senders * num_receivers * (num_receivers + 1) / 2

    # a list of `num_receivers` elements, where each element with get
    # incremented by values the corresponding receiver receives.  Once the run
    # finishes, we will check if their sum equals `expected_sum`.
    recv_trackers = [0] * num_receivers

    async def send_msg(chan: Sender[int]) -> None:
        # send one message for each receiver
        for ctr in range(num_receivers):
            await chan.send(ctr + 1)

    async def update_tracker_on_receive(receiver_id: int, recv: Receiver[int]) -> None:
        while True:
            try:
                msg = await recv.receive()
            except ReceiverStoppedError as err:
                assert err.receiver is recv
                assert isinstance(err.__cause__, ChannelClosedError)
                return
            recv_trackers[receiver_id] += msg
            # without the sleep, decommissioning receivers temporarily, all
            # messages go to the first receiver.
            await asyncio.sleep(0)

    receivers = []
    for ctr in range(num_receivers):
        receivers.append(update_tracker_on_receive(ctr, acast.new_receiver()))

    # get one more sender and receiver to test channel operations after the
    # channel is closed.
    after_close_receiver = acast.new_receiver()
    after_close_sender = acast.new_sender()

    receivers_runs = asyncio.gather(*receivers)
    senders = []
    for ctr in range(num_senders):
        senders.append(send_msg(acast.new_sender()))

    await asyncio.gather(*senders)
    await acast.close()
    await receivers_runs

    with pytest.raises(SenderError):
        await after_close_sender.send(5)
    with pytest.raises(ReceiverStoppedError) as excinfo:
        await after_close_receiver.receive()
    assert excinfo.value.receiver is after_close_receiver
    assert isinstance(excinfo.value.__cause__, ChannelClosedError)
    assert excinfo.value.__cause__.channel is acast

    actual_sum = 0
    for ctr in recv_trackers:
        # ensure all receivers have got messages
        assert ctr > 0
        actual_sum += ctr
    assert actual_sum == expected_sum


async def test_anycast_after_close() -> None:
    """Ensure closed channels can't get new messages."""
    acast: Anycast[int] = Anycast(name="test")

    receiver = acast.new_receiver()
    sender = acast.new_sender()

    await sender.send(2)

    await acast.close()

    with pytest.raises(SenderError):
        await sender.send(5)

    assert await receiver.receive() == 2
    with pytest.raises(ReceiverStoppedError):
        await receiver.receive()


async def test_anycast_full() -> None:
    """Ensure send calls to a full channel are blocked."""
    buffer_size = 10
    timeout = 0.2
    acast: Anycast[int] = Anycast(name="test", limit=buffer_size)

    receiver = acast.new_receiver()
    sender = acast.new_sender()

    timeout_at = 0
    for ctr in range(buffer_size + 1):
        try:
            await asyncio.wait_for(sender.send(ctr), timeout)
        except asyncio.TimeoutError:
            # expect timeout once the buffer is full
            timeout_at = ctr

    assert timeout_at == buffer_size

    timeout_at = 0
    for ctr in range(buffer_size + 1):
        try:
            msg = await asyncio.wait_for(receiver.receive(), timeout)
            assert ctr == msg
        except asyncio.TimeoutError:
            # expect timeout once the buffer is empty
            timeout_at = ctr

    assert timeout_at == buffer_size

    try:
        await asyncio.wait_for(sender.send(100), timeout)
    except asyncio.TimeoutError:
        # should not timeout now, because we've just drained the channel
        assert False

    try:
        msg = await asyncio.wait_for(receiver.receive(), timeout)
        assert msg == 100
    except asyncio.TimeoutError:
        # should not timeout now, because we've just sent a message to the
        # channel.
        assert False


async def test_anycast_none_messages() -> None:
    """Ensure None messages can be sent and received."""
    acast: Anycast[int | None] = Anycast(name="test")

    sender = acast.new_sender()
    receiver = acast.new_receiver()

    await sender.send(5)
    assert await receiver.receive() == 5

    await sender.send(None)
    assert await receiver.receive() is None

    await sender.send(10)
    assert await receiver.receive() == 10


async def test_anycast_async_iterator() -> None:
    """Check that the anycast receiver works as an async iterator."""
    acast: Anycast[str] = Anycast(name="test")

    sender = acast.new_sender()
    receiver = acast.new_receiver()

    async def send_messages() -> None:
        for val in ["one", "two", "three", "four", "five"]:
            await sender.send(val)
        await acast.close()

    sender_task = asyncio.create_task(send_messages())

    received = []
    async for recv in receiver:
        received.append(recv)

    assert received == ["one", "two", "three", "four", "five"]

    await sender_task


async def test_anycast_map() -> None:
    """Ensure map runs on all incoming messages."""
    chan: Anycast[int] = Anycast(name="test")
    sender = chan.new_sender()

    # transform int receiver into bool receiver.
    receiver: Receiver[bool] = chan.new_receiver().map(lambda num: num > 10)

    await sender.send(8)
    await sender.send(12)

    assert (await receiver.receive()) is False
    assert (await receiver.receive()) is True


async def test_anycast_filter() -> None:
    """Ensure filter keeps only the messages that pass the filter."""
    chan = Anycast[int](name="input-chan")
    sender = chan.new_sender()

    # filter out all numbers less than 10.
    receiver: Receiver[int] = chan.new_receiver().filter(lambda num: num > 10)

    await sender.send(8)
    await sender.send(12)
    await sender.send(5)
    await sender.send(15)

    assert (await receiver.receive()) == 12
    assert (await receiver.receive()) == 15


async def test_anycast_close_receiver() -> None:
    """Ensure closing a receiver stops the receiver."""
    chan = Anycast[int](name="input-chan")
    sender = chan.new_sender()

    receiver_1 = chan.new_receiver()
    receiver_2 = chan.new_receiver()

    await sender.send(1)

    assert (await receiver_1.receive()) == 1

    receiver_1.close()

    await sender.send(2)

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_1.receive()

    assert (await receiver_2.receive()) == 2

    receiver_2.close()

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_2.receive()
