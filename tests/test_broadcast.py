# License: MIT
# Copyright © 2022 Frequenz Energy-as-a-Service GmbH

"""Tests for the Broadcast implementation."""


import asyncio
from dataclasses import dataclass
from typing import TypeGuard, assert_never

import pytest

from frequenz.channels import (
    Broadcast,
    ChannelClosedError,
    Receiver,
    ReceiverStoppedError,
    Sender,
    SenderError,
)


async def test_broadcast() -> None:
    """Ensure sent messages are received by all receivers."""
    bcast: Broadcast[int] = Broadcast(name="meter_5")

    num_receivers = 5
    num_senders = 5
    expected_sum = num_senders * num_receivers * num_receivers * (num_receivers + 1) / 2

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

    receivers = []
    for ctr in range(num_receivers):
        receivers.append(update_tracker_on_receive(ctr, bcast.new_receiver()))

    receivers_runs = asyncio.gather(*receivers)
    senders = []
    for ctr in range(num_senders):
        senders.append(send_msg(bcast.new_sender()))

    await asyncio.gather(*senders)
    await bcast.close()
    await receivers_runs

    actual_sum = 0
    for ctr in recv_trackers:
        # ensure all receivers have got messages
        assert ctr > 0
        actual_sum += ctr
    assert actual_sum == expected_sum


async def test_broadcast_none_messages() -> None:
    """Ensure None messages can be sent and received."""
    bcast: Broadcast[int | None] = Broadcast(name="any_channel")

    sender = bcast.new_sender()
    receiver = bcast.new_receiver()

    await sender.send(5)
    assert await receiver.receive() == 5

    await sender.send(None)
    assert await receiver.receive() is None

    await sender.send(10)
    assert await receiver.receive() == 10


async def test_broadcast_after_close() -> None:
    """Ensure closed channels can't get new messages."""
    bcast: Broadcast[int] = Broadcast(name="meter_5")

    receiver = bcast.new_receiver()
    sender = bcast.new_sender()

    await bcast.close()

    with pytest.raises(SenderError):
        await sender.send(5)
    with pytest.raises(ReceiverStoppedError) as excinfo:
        await receiver.receive()
    assert excinfo.value.receiver is receiver
    assert isinstance(excinfo.value.__cause__, ChannelClosedError)
    assert excinfo.value.__cause__.channel is bcast


async def test_broadcast_overflow() -> None:
    """Ensure messages sent to full broadcast receivers get dropped."""
    from frequenz.channels._broadcast import (  # pylint: disable=import-outside-toplevel
        _Receiver,
    )

    bcast: Broadcast[int] = Broadcast(name="meter_5")

    big_recv_size = 10
    small_recv_size = int(big_recv_size / 2)
    sender = bcast.new_sender()

    big_receiver = bcast.new_receiver(name="named-recv", limit=big_recv_size)
    assert isinstance(big_receiver, _Receiver)
    small_receiver = bcast.new_receiver(limit=small_recv_size)
    assert isinstance(small_receiver, _Receiver)

    async def drain_receivers() -> tuple[int, int]:
        big_sum = 0
        small_sum = 0
        while len(big_receiver) > 0:
            msg = await big_receiver.receive()
            assert msg is not None
            big_sum += msg

        while len(small_receiver) > 0:
            msg = await small_receiver.receive()
            assert msg is not None
            small_sum += msg
        return (big_sum, small_sum)

    # we send `big_recv_size` messages first, then drain the receivers, then
    # send `big_recv_size` messages again.  Then get the sum.
    total_messages = 2 * big_recv_size
    big_sum = 0
    small_sum = 0
    for ctr in range(total_messages):
        await sender.send(ctr + 1)
        if (ctr + 1) % big_recv_size == 0:
            big, small = await drain_receivers()
            big_sum += big
            small_sum += small

    assert big_sum == total_messages * (total_messages + 1) / 2

    # small_sum should be sum of `small_recv_size+1 .. big_recv_size`, and
    # big_sum should be the numbers from `big_recv_size+small_recv_size+1` to
    # `2 * big_recv_size`.
    assert small_sum == (
        small_recv_size * (small_recv_size + big_recv_size + 1) / 2
    ) + (
        small_recv_size
        * (2 * big_recv_size + (small_recv_size + big_recv_size + 1))
        / 2
    )


async def test_broadcast_resend_latest() -> None:
    """Check if new receivers get the latest message when resend_latest is set."""
    bcast: Broadcast[int] = Broadcast(name="new_recv_test", resend_latest=True)

    sender = bcast.new_sender()
    old_recv = bcast.new_receiver()
    for val in range(0, 10):
        await sender.send(val)
    new_recv = bcast.new_receiver()

    await sender.send(100)

    assert await old_recv.receive() == 0
    assert await new_recv.receive() == 9
    assert await new_recv.receive() == 100


async def test_broadcast_no_resend_latest() -> None:
    """Ensure new receivers don't get the latest message when resend_latest isn't set."""
    bcast: Broadcast[int] = Broadcast(name="new_recv_test", resend_latest=False)

    sender = bcast.new_sender()
    old_recv = bcast.new_receiver()
    for val in range(0, 10):
        await sender.send(val)
    new_recv = bcast.new_receiver()

    await sender.send(100)

    assert await old_recv.receive() == 0
    assert await new_recv.receive() == 100


async def test_broadcast_async_iterator() -> None:
    """Check that the broadcast receiver works as an async iterator."""
    bcast: Broadcast[int] = Broadcast(name="iter_test")

    sender = bcast.new_sender()
    receiver = bcast.new_receiver()

    async def send_messages() -> None:
        for val in range(0, 10):
            await sender.send(val)
        await bcast.close()

    sender_task = asyncio.create_task(send_messages())

    received = []
    async for recv in receiver:
        received.append(recv)

    assert received == list(range(0, 10))

    await sender_task


async def test_broadcast_map() -> None:
    """Ensure map runs on all incoming messages."""
    chan = Broadcast[int](name="input-chan")
    sender = chan.new_sender()

    # transform int receiver into bool receiver.
    receiver: Receiver[bool] = chan.new_receiver().map(lambda num: num > 10)

    await sender.send(8)
    await sender.send(12)

    assert (await receiver.receive()) is False
    assert (await receiver.receive()) is True


async def test_broadcast_map_close_receiver() -> None:
    """Ensure closing a map stops the receiver."""
    chan = Broadcast[int](name="input-chan")
    sender = chan.new_sender()

    receiver_1 = chan.new_receiver()
    receiver_2 = chan.new_receiver()
    plus_100_rx = receiver_1.map(lambda num: num + 100)

    await sender.send(1)

    assert (await plus_100_rx.receive()) == 101
    assert (await receiver_2.receive()) == 1

    plus_100_rx.close()

    await sender.send(2)

    with pytest.raises(ReceiverStoppedError):
        _ = await plus_100_rx.receive()

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_1.receive()

    assert (await receiver_2.receive()) == 2

    await sender.send(3)

    assert (await receiver_2.receive()) == 3

    receiver_2.close()

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_2.receive()


async def test_broadcast_filter() -> None:
    """Ensure filter keeps only the messages that pass the filter."""
    chan = Broadcast[int](name="input-chan")
    sender = chan.new_sender()

    # filter out all numbers less than 10.
    receiver: Receiver[int] = chan.new_receiver().filter(lambda num: num > 10)

    await sender.send(8)
    await sender.send(12)
    await sender.send(5)
    await sender.send(15)

    assert (await receiver.receive()) == 12
    assert (await receiver.receive()) == 15


async def test_broadcast_filter_close_receiver() -> None:
    """Ensure closing a filter stops the receiver."""
    chan = Broadcast[int](name="input-chan")
    sender = chan.new_sender()

    receiver_1 = chan.new_receiver()
    receiver_2 = chan.new_receiver()

    gt_10_rx = receiver_1.filter(lambda num: num > 10)

    await sender.send(1)
    assert (await receiver_2.receive()) == 1

    await sender.send(100)
    assert (await gt_10_rx.receive()) == 100
    assert (await receiver_2.receive()) == 100

    gt_10_rx.close()

    await sender.send(2)

    with pytest.raises(ReceiverStoppedError):
        _ = await gt_10_rx.receive()
    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_1.receive()

    assert (await receiver_2.receive()) == 2

    await sender.send(3)
    assert (await receiver_2.receive()) == 3

    receiver_2.close()

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_2.receive()


async def test_broadcast_filter_type_guard() -> None:
    """Ensure filter type guard works."""
    chan = Broadcast[int | str](name="input-chan")
    sender = chan.new_sender()

    def _is_int(num: int | str) -> TypeGuard[int]:
        return isinstance(num, int)

    # filter out objects that are not integers.
    receiver = chan.new_receiver().filter(_is_int)

    await sender.send("hello")
    await sender.send(8)

    message = await receiver.receive()
    assert message == 8
    is_int = False
    match message:
        case int():
            is_int = True
        case unexpected:
            assert_never(unexpected)
    assert is_int


async def test_broadcast_receiver_drop() -> None:
    """Ensure deleted receivers get cleaned up."""
    chan = Broadcast[int](name="input-chan")
    sender = chan.new_sender()

    receiver1 = chan.new_receiver()
    receiver2 = chan.new_receiver()

    await sender.send(10)

    assert 10 == await receiver1.receive()
    assert 10 == await receiver2.receive()

    # pylint: disable=protected-access
    assert len(chan._receivers) == 2

    del receiver2

    await sender.send(20)

    assert len(chan._receivers) == 1
    # pylint: enable=protected-access


async def test_type_variance() -> None:
    """Ensure that the type variance of Broadcast is working."""

    @dataclass
    class Broader:
        """A broad class."""

        value: int

    class Actual(Broader):
        """Actual class."""

    class Narrower(Actual):
        """A narrower class."""

    chan = Broadcast[Actual](name="input-chan")

    sender: Sender[Narrower] = chan.new_sender()
    receiver: Receiver[Broader] = chan.new_receiver()

    await sender.send(Narrower(10))
    assert (await receiver.receive()).value == 10


async def test_broadcast_close_receiver() -> None:
    """Ensure closing a receiver stops the receiver."""
    chan = Broadcast[int](name="input-chan")
    sender = chan.new_sender()

    receiver_1 = chan.new_receiver()
    receiver_2 = chan.new_receiver()

    await sender.send(1)

    assert (await receiver_1.receive()) == 1
    assert (await receiver_2.receive()) == 1

    receiver_1.close()

    await sender.send(2)

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_1.receive()

    assert (await receiver_2.receive()) == 2

    await sender.send(3)

    assert (await receiver_2.receive()) == 3

    receiver_2.close()

    with pytest.raises(ReceiverStoppedError):
        _ = await receiver_2.receive()
