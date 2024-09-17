# License: MIT
# Copyright © 2023 Frequenz Energy-as-a-Service GmbH

"""Test module for the TimerScheduler class."""

import asyncio
from datetime import datetime, timedelta

import async_solipsism
import time_machine
from pytest import fixture

from frequenz.channels import Broadcast
from frequenz.channels.timer_scheduler import TimerScheduler


@fixture
def event_loop_policy() -> async_solipsism.EventLoopPolicy:
    """Return an event loop policy that uses the async solipsism event loop."""
    return async_solipsism.EventLoopPolicy()


async def test_set_timer() -> None:
    """Test that a scheduled event is dispatched at the correct (mocked) time."""
    # Create a Broadcast channel
    bcast = Broadcast[str](name="test")

    # Create a sender and receiver
    sender = bcast.new_sender()
    receiver = bcast.new_receiver()

    # Initialize the TimerScheduler with the sender
    sched = TimerScheduler(sender)

    # List to collect received events
    received_events = []

    # Define the consumer coroutine
    async def consumer() -> None:
        async for event in receiver:
            received_events.append(event)

    # Start the consumer as an asyncio task
    consumer_task = asyncio.create_task(consumer())

    # Freeze time at 2024-01-01 12:00:00
    with time_machine.travel(datetime(2024, 1, 1, 12, 0, 0)):
        # Schedule 'event1' to fire in 10 seconds
        sched.set_timer(fire_in=timedelta(seconds=10), obj="event1")

        # Advance time to 2024-01-01 12:00:10 to make 'event1' due
        with time_machine.travel(datetime(2024, 1, 1, 12, 0, 10)):
            # Allow some time for the event to be dispatched
            await asyncio.sleep(0.1)

    # Assert that 'event1' was received
    assert (
        "event1" in received_events
    ), "The event 'event1' was not dispatched as expected."

    # Clean up by stopping the scheduler and cancelling the consumer task
    await sched.stop()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


async def test_reschedule_timer() -> None:
    """Test that rescheduling an event updates its dispatch time correctly."""
    # Create a Broadcast channel
    bcast = Broadcast[str](name="test")

    # Create a sender and receiver
    sender = bcast.new_sender()
    receiver = bcast.new_receiver()

    # Initialize the TimerScheduler with the sender
    sched = TimerScheduler(sender)

    # List to collect received events
    received_events = []

    # Define the consumer coroutine
    async def consumer() -> None:
        async for event in receiver:
            received_events.append(event)

    # Start the consumer as an asyncio task
    consumer_task = asyncio.create_task(consumer())

    # Freeze time at 2024-01-01 12:00:00
    with time_machine.travel(datetime(2024, 1, 1, 12, 0, 0)):
        # Schedule 'event1' to fire in 10 seconds
        sched.set_timer(fire_in=timedelta(seconds=10), obj="event1")

        # Reschedule 'event1' to fire in 5 seconds
        sched.set_timer(fire_in=timedelta(seconds=5), obj="event1")

        # Advance time to 2024-01-01 12:00:05 to make 'event1' due
        with time_machine.travel(datetime(2024, 1, 1, 12, 0, 5)):
            # Allow some time for the event to be dispatched
            await asyncio.sleep(0.1)

    # Assert that 'event1' was received only once
    assert (
        received_events.count("event1") == 1
    ), "The event 'event1' was dispatched multiple times."

    # Clean up by stopping the scheduler and cancelling the consumer task
    await sched.stop()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


async def test_unset_timer() -> None:
    """Test that cancelling a scheduled event prevents it from being dispatched."""
    # Create a Broadcast channel
    bcast = Broadcast[str](name="test")

    # Create a sender and receiver
    sender = bcast.new_sender()
    receiver = bcast.new_receiver()

    # Initialize the TimerScheduler with the sender
    sched = TimerScheduler(sender)

    # List to collect received events
    received_events = []

    # Define the consumer coroutine
    async def consumer() -> None:
        async for event in receiver:
            received_events.append(event)

    # Start the consumer as an asyncio task
    consumer_task = asyncio.create_task(consumer())

    # Freeze time at 2024-01-01 12:00:00
    with time_machine.travel(datetime(2024, 1, 1, 12, 0, 0)):
        # Schedule 'event1' to fire in 10 seconds
        sched.set_timer(fire_in=timedelta(seconds=10), obj="event1")

        # Advance time to 2024-01-01 12:00:05
        with time_machine.travel(datetime(2024, 1, 1, 12, 0, 5)):
            # Cancel 'event1' before it's due
            sched.unset_timer("event1")

            # Advance time to 2024-01-01 12:00:10 to reach the original dispatch time
            with time_machine.travel(datetime(2024, 1, 1, 12, 0, 10)):
                # Allow some time for the dispatcher to process
                await asyncio.sleep(0.1)

    # Assert that 'event1' was not received
    assert (
        "event1" not in received_events
    ), "The event 'event1' was dispatched despite being canceled."

    # Clean up by stopping the scheduler and cancelling the consumer task
    await sched.stop()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
