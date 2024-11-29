# License: MIT
# Copyright © 2023 Frequenz Energy-as-a-Service GmbH

"""A receiver that can be made ready directly.

!!! Tip inline end

    Read the [`Event`][frequenz.channels.event.Event] documentation for more
    information.

This module contains the following:

* [`Event`][frequenz.channels.event.Event]:
    {{docstring_summary("frequenz.channels.event.Event")}}
"""

import asyncio as _asyncio

from typing_extensions import override

from frequenz.channels._receiver import Receiver, ReceiverStoppedError


class Event(Receiver[None]):
    """A receiver that can be made ready directly.

    # Usage

    There are cases where it is useful to be able to send a signal to
    a [`select()`][frequenz.channels.select] loop, for example, to stop a loop from
    outside the loop itself.

    To do that, you can use an [`Event`][frequenz.channels.event.Event] receiver and
    call [`set()`][frequenz.channels.event.Event.set] on it when you want to make it
    ready.

    # Stopping

    The receiver will be re-activated (will keep blocking) after the current set
    event is received. To stop the receiver completely, you can call
    [`stop()`][frequenz.channels.event.Event.stop].

    # Example

    Example: Exit after printing the first 5 numbers
        ```python
        import asyncio

        from frequenz.channels import Anycast, select, selected_from
        from frequenz.channels.event import Event

        channel: Anycast[int] = Anycast(name="channel")
        receiver = channel.new_receiver()
        sender = channel.new_sender()
        stop_event = Event(name="stop")


        async def do_work() -> None:
            async for selected in select(receiver, stop_event):
                if selected_from(selected, receiver):
                    print(selected.message)
                elif selected_from(selected, stop_event):
                    print("Stop event triggered")
                    stop_event.stop()
                    break


        async def send_stuff() -> None:
            for i in range(10):
                if stop_event.is_stopped:
                    break
                await asyncio.sleep(1)
                await sender.send(i)


        async def main() -> None:
            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(do_work(), name="do_work")
                task_group.create_task(send_stuff(), name="send_stuff")
                await asyncio.sleep(5.5)
                stop_event.set()


        asyncio.run(main())
        ```
    """

    def __init__(self, *, name: str | None = None) -> None:
        """Initialize this event.

        Args:
            name: The name of the receiver.  If `None` an `id(self)`-based name will be
                used. This is only for debugging purposes, it will be shown in the
                string representation of the receiver.
        """
        self._event: _asyncio.Event = _asyncio.Event()
        """The event that is set when the receiver is ready."""

        self._name: str = f"{id(self):_}" if name is None else name
        """The name of the receiver.

        This is for debugging purposes, it will be shown in the string representation
        of the receiver.
        """

        self._is_set: bool = False
        """Whether the receiver is ready to be consumed.

        This is used to differentiate between when the receiver was stopped (the event
        is triggered too) but still there is an event to be consumed and when it was
        stopped but was not explicitly set().
        """

        self._is_stopped: bool = False
        """Whether the receiver is stopped."""

    @property
    def name(self) -> str:
        """The name of this receiver.

        This is for debugging purposes, it will be shown in the string representation
        of this receiver.
        """
        return self._name

    @property
    def is_set(self) -> bool:
        """Whether this receiver is set (ready)."""
        return self._is_set

    @property
    def is_stopped(self) -> bool:
        """Whether this receiver is stopped."""
        return self._is_stopped

    def stop(self) -> None:
        """Stop this receiver."""
        self._is_stopped = True
        self._event.set()

    def set(self) -> None:
        """Trigger the event (make the receiver ready)."""
        self._is_set = True
        self._event.set()

    @override
    async def ready(self) -> bool:
        """Wait until this receiver is ready.

        Returns:
            Whether this receiver is still running.
        """
        if self._is_stopped:
            return False
        await self._event.wait()
        return not self._is_stopped

    @override
    def consume(self) -> None:
        """Consume the event.

        This makes this receiver wait again until the event is set again.

        Raises:
            ReceiverStoppedError: If this receiver is stopped.
        """
        if not self._is_set and self._is_stopped:
            raise ReceiverStoppedError(self)

        assert self._is_set, "calls to `consume()` must be follow a call to `ready()`"

        self._is_set = False
        self._event.clear()

    @override
    def close(self) -> None:
        """Close this receiver."""
        self.stop()

    def __str__(self) -> str:
        """Return a string representation of this event."""
        return f"{type(self).__name__}({self._name!r})"

    def __repr__(self) -> str:
        """Return a string representation of this event."""
        return (
            f"<{type(self).__name__} name={self._name!r} is_set={self.is_set!r} "
            f"is_stopped={self.is_stopped!r}>"
        )
