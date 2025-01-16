# License: MIT
# Copyright © 2022 Frequenz Energy-as-a-Service GmbH

"""A receiver for watching for new, modified or deleted files.

!!! Tip inline end

    Read the [`FileWatcher`][frequenz.channels.file_watcher.FileWatcher]
    documentation for more information.

This module contains the following:

* [`FileWatcher`][frequenz.channels.file_watcher.FileWatcher]:
    {{docstring_summary("frequenz.channels.file_watcher.FileWatcher")}}
* [`Event`][frequenz.channels.file_watcher.Event]:
    {{docstring_summary("frequenz.channels.file_watcher.Event")}}
* [`EventType`][frequenz.channels.file_watcher.EventType]:
    {{docstring_summary("frequenz.channels.file_watcher.EventType")}}
"""

import asyncio
import pathlib
from collections import abc
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from typing_extensions import override
from watchfiles import Change, awatch
from watchfiles.main import FileChange

from ._receiver import Receiver, ReceiverStoppedError


class EventType(Enum):
    """The types of file events that can be observed."""

    CREATE = Change.added
    """The file was created."""

    MODIFY = Change.modified
    """The file was modified."""

    DELETE = Change.deleted
    """The file was deleted."""


@dataclass(frozen=True)
class Event:
    """A file change event."""

    type: EventType
    """The type of change that was observed."""

    path: pathlib.Path
    """The path where the change was observed."""


class FileWatcher(Receiver[Event]):
    """A receiver that watches for file events.

    # Usage

    A [`FileWatcher`][frequenz.channels.file_watcher.FileWatcher] receiver can be used
    to watch for changes in a set of files. It will generate an
    [`Event`][frequenz.channels.file_watcher.Event] message every time a file is
    created, modified or deleted, depending on the type of events that it is configured
    to watch for.

    The [event][frequenz.channels.file_watcher.EventType] message contains the
    [`type`][frequenz.channels.file_watcher.Event.type] of change that was observed and
    the [`path`][frequenz.channels.file_watcher.Event.path] where the change was
    observed.

    Note:
        The owner of the [`FileWatcher`][frequenz.channels.file_watcher.FileWatcher]
        receiver is responsible for recreating the `FileWatcher` after it has been
        cancelled or stopped.
        For example, if a [`Task`][asyncio.Task] uses an asynchronous iterator to consume
        events from the `FileWatcher` and the task is cancelled, the `FileWatcher` will
        also stop. Therefore, the same `FileWatcher` instance cannot be reused for a new
        task to consume events. In this case, a new FileWatcher instance must be created.

    # Event Types

    The following event types are available:

    * [`CREATE`][frequenz.channels.file_watcher.EventType.CREATE]:
        {{docstring_summary("frequenz.channels.file_watcher.EventType.CREATE")}}
    * [`MODIFY`][frequenz.channels.file_watcher.EventType.MODIFY]:
        {{docstring_summary("frequenz.channels.file_watcher.EventType.MODIFY")}}
    * [`DELETE`][frequenz.channels.file_watcher.EventType.DELETE]:
        {{docstring_summary("frequenz.channels.file_watcher.EventType.DELETE")}}

    # Example

    Example: Watch for changes and exit after the file is modified
        ```python
        import asyncio

        from frequenz.channels.file_watcher import EventType, FileWatcher

        PATH = "/tmp/test.txt"
        file_watcher = FileWatcher(paths=[PATH], event_types=[EventType.MODIFY])


        async def update_file() -> None:
            await asyncio.sleep(1)
            with open(PATH, "w", encoding="utf-8") as file:
                file.write("Hello, world!")


        async def main() -> None:
            # Create file
            with open(PATH, "w", encoding="utf-8") as file:
                file.write("Hello, world!")
            async with asyncio.TaskGroup() as group:
                group.create_task(update_file())
                async for event in file_watcher:
                    print(f"File {event.path}: {event.type.name}")
                    break


        asyncio.run(main())
        ```
    """

    def __init__(
        self,
        paths: list[pathlib.Path | str],
        event_types: abc.Iterable[EventType] = frozenset(EventType),
        *,
        force_polling: bool = True,
        polling_interval: timedelta = timedelta(seconds=1),
    ) -> None:
        """Initialize this file watcher.

        Args:
            paths: The paths to watch for changes.
            event_types: The types of events to watch for. Defaults to watch for
                all event types.
            force_polling: Whether to explicitly force file polling to check for
                changes. Note that even if set to False, file polling will still
                be used as a fallback when the underlying file system does not
                support event-based notifications.
            polling_interval: The interval to poll for changes. Only relevant if
                polling is enabled.
        """
        self.event_types: frozenset[EventType] = frozenset(event_types)
        """The types of events to watch for."""

        self._stop_event: asyncio.Event = asyncio.Event()
        self._paths: list[pathlib.Path] = [
            path if isinstance(path, pathlib.Path) else pathlib.Path(path)
            for path in paths
        ]
        self._awatch: abc.AsyncGenerator[set[FileChange], None] = awatch(
            *self._paths,
            stop_event=self._stop_event,
            watch_filter=self._filter_events,
            force_polling=force_polling,
            poll_delay_ms=int(polling_interval.total_seconds() * 1_000),
        )
        self._awatch_stopped_exc: Exception | None = None
        self._changes: set[FileChange] = set()

    def _filter_events(
        self,
        change: Change,
        path: str,  # pylint: disable=unused-argument
    ) -> bool:
        """Filter events based on the event type and path.

        Args:
            change: The type of change to be notified.
            path: The path of the file that changed.

        Returns:
            Whether the event should be notified.
        """
        return change in [event_type.value for event_type in self.event_types]

    def __del__(self) -> None:
        """Finalize this file watcher."""
        # We need to set the stop event to make sure that the awatch background task
        # is stopped.
        self._stop_event.set()

    @override
    async def ready(self) -> bool:
        """Wait until the receiver is ready with a message or an error.

        Once a call to `ready()` has finished, the message should be read with
        a call to `consume()` (`receive()` or iterated over). The receiver will
        remain ready (this method will return immediately) until it is
        consumed.

        Returns:
            Whether the receiver is still active.
        """
        # if there are messages waiting to be consumed, return immediately.
        if self._changes:
            return True

        # if it was already stopped, return immediately.
        if self._awatch_stopped_exc is not None:
            return False

        try:
            self._changes = await anext(self._awatch)
        except StopAsyncIteration as err:
            self._awatch_stopped_exc = err
            return False

        return True

    @override
    def consume(self) -> Event:
        """Return the latest event once `ready` is complete.

        Returns:
            The next event that was received.

        Raises:
            ReceiverStoppedError: If there is some problem with the receiver.
        """
        if not self._changes and self._awatch_stopped_exc is not None:
            raise ReceiverStoppedError(self) from self._awatch_stopped_exc

        assert self._changes, "`consume()` must be preceded by a call to `ready()`"
        # Tuple of (Change, path) returned by watchfiles
        change, path_str = self._changes.pop()
        return Event(type=EventType(change), path=pathlib.Path(path_str))

    @override
    def close(self) -> None:
        """Close this receiver."""
        self._stop_event.set()

    def __str__(self) -> str:
        """Return a string representation of this receiver."""
        if len(self._paths) > 3:
            paths = [str(p) for p in self._paths[:3]]
            paths.append("…")
        else:
            paths = [str(p) for p in self._paths]
        event_types = [event_type.name for event_type in self.event_types]
        return f"{type(self).__name__}:{','.join(event_types)}:{','.join(paths)}"

    def __repr__(self) -> str:
        """Return a string representation of this receiver."""
        return f"{type(self).__name__}({self._paths!r}, {self.event_types!r})"
