# License: MIT
# Copyright © 2024 Frequenz Energy-as-a-Service GmbH

"""Timer scheduler to schedule and events at specific times."""

import asyncio
import heapq
import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Generic, TypeVar

from frequenz.channels import Sender

T = TypeVar("T")  # Generic type for the object associated with events


@dataclass(order=True)
class ScheduledEvent(Generic[T]):
    """Represents an event scheduled to be dispatched at a specific time."""

    scheduled_time: datetime
    obj: T = field(compare=False)
    unique_id: int = field(compare=False, default=0)
    canceled: bool = field(compare=False, default=False)


class TimerScheduler(Generic[T]):
    """Class to schedule and dispatch events at specific times.

    Usage example:
    ```python

    import asyncio
    from frequenz.channels.timer_scheduler import TimerScheduler
    from frequenz.channels import Broadcast
    from datetime import timedelta

    async def main():
        event_channel = Broadcast[str](name="events")
        sender = event_channel.new_sender()
        receiver = event_channel.new_receiver()

        scheduler = TimerScheduler[str](sender)

        scheduler.set_timer(fire_in=timedelta(seconds=5), obj="event1")
        scheduler.set_timer(fire_in=timedelta(seconds=10), obj="event2")
        scheduler.set_timer(fire_in=timedelta(seconds=10), obj="event3")

        # Waits 5 seconds and returns "event1"
        assert await receiver.receive() == "event1"

        # Remove the "event2" timer
        scheduler.unset_timer("event2")

        # Reschedule "event3" to fire in 15 seconds
        scheduler.set_timer(fire_in=timedelta(seconds=15), obj="event3")

        # Waits 15 more seconds and returns "event3"
        assert await receiver.receive() == "event3"
    """

    def __init__(self, sender: Sender[T]) -> None:
        """Initialize the TimerScheduler with the given sender.

        Parameters:
            sender: The sender to dispatch the events.
        """
        self._sender = sender
        self._event_heap: list[ScheduledEvent[T]] = []
        self._obj_to_event: Dict[T, ScheduledEvent[T]] = {}
        self._counter = itertools.count()
        self._current_task: asyncio.Task[None] | None = None
        self._stopped = False

    def set_timer(
        self,
        *,
        obj: T,
        fire_in: timedelta | None = None,
        fire_at: datetime | None = None,
    ) -> bool:
        """
        Schedule a new event or reschedule an existing one.

        Args:
            obj: The object associated with the event.
            fire_in: Time after which the event should be dispatched. Conflicts with fire_at.
            fire_at: Time at which the event should be dispatched. Conflicts with fire_in.

        Returns:
            True if the event was successfully scheduled; False otherwise.
        """
        now = datetime.now(timezone.utc)

        scheduled_time = (now + fire_in) if fire_in else fire_at
        assert scheduled_time, "Either 'fire_in' or 'fire_at' must be provided."

        if scheduled_time < now:
            return False

        # Check if the object is already scheduled
        if obj in self._obj_to_event:
            existing_event = self._obj_to_event[obj]
            existing_event.canceled = True  # Mark the existing event as canceled

        # Create a new scheduled event
        unique_id = next(self._counter)
        new_event = ScheduledEvent(
            scheduled_time=scheduled_time, unique_id=unique_id, obj=obj
        )
        heapq.heappush(self._event_heap, new_event)
        self._obj_to_event[obj] = new_event

        # If the new event is the earliest, reset the waiting task
        if self._event_heap[0] == new_event:
            if self._current_task:
                self._current_task.cancel()
            self._current_task = asyncio.create_task(self._wait_and_dispatch())

        return True

    def unset_timer(self, obj: T) -> bool:
        """
        Cancel a scheduled event associated with the given object.

        Args:
            obj: The object associated with the event to cancel.

        Returns:
            True if the event was found and canceled; False otherwise.
        """
        if obj in self._obj_to_event:
            existing_event = self._obj_to_event[obj]
            existing_event.canceled = True  # Mark the event as canceled
            del self._obj_to_event[obj]

            # If the canceled event was the next to be dispatched, reset the waiting task
            if self._event_heap and self._event_heap[0].obj == obj:
                if self._current_task:
                    self._current_task.cancel()
                self._current_task = asyncio.create_task(self._wait_and_dispatch())

            return True

        return False

    async def _wait_and_dispatch(self) -> None:
        """Wait for the next event to be due and dispatch it."""
        while not self._stopped:
            if not self._event_heap:
                self._current_task = None
                return

            next_event = self._event_heap[0]
            now = datetime.now(timezone.utc)
            delay = (next_event.scheduled_time - now).total_seconds()

            if delay <= 0:
                # Check if the event still exists
                if next_event.obj not in self._obj_to_event:
                    # Skip canceled events
                    heapq.heappop(self._event_heap)
                    continue

                # Event is due
                heapq.heappop(self._event_heap)
                del self._obj_to_event[next_event.obj]

                if next_event.canceled:
                    # Skip canceled events
                    continue

                # Dispatch the event
                await self._sender.send(next_event.obj)
                continue  # Check for the next event

            try:
                # Wait until the next event's scheduled_time
                self._current_task = asyncio.create_task(asyncio.sleep(delay))
                await self._current_task
            except asyncio.CancelledError:
                # A new earlier event was scheduled; exit to handle it
                return

    async def stop(self) -> None:
        """Stop the scheduler and cancel any pending tasks."""
        self._stopped = True
        if self._current_task:
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass
