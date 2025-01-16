# License: MIT
# Copyright © 2023 Frequenz Energy-as-a-Service GmbH

"""Integration tests for the `util` module."""

import os
import pathlib
from datetime import timedelta

import pytest

from frequenz.channels import ReceiverStoppedError, select, selected_from
from frequenz.channels.file_watcher import EventType, FileWatcher
from frequenz.channels.timer import SkipMissedAndDrift, Timer


@pytest.mark.integration
async def test_file_watcher(tmp_path: pathlib.Path) -> None:
    """Ensure file watcher is returning paths on file events.

    Args:
        tmp_path: A tmp directory to run the file watcher on. Created by pytest.
    """
    filename = tmp_path / "test-file"

    number_of_writes = 0
    expected_number_of_writes = 3

    file_watcher = FileWatcher(
        paths=[str(tmp_path)], polling_interval=timedelta(seconds=0.05)
    )
    timer = Timer(timedelta(seconds=0.1), SkipMissedAndDrift())

    async for selected in select(file_watcher, timer):
        if selected_from(selected, timer):
            filename.write_text(f"{selected.message}")
        elif selected_from(selected, file_watcher):
            event_type = EventType.CREATE if number_of_writes == 0 else EventType.MODIFY
            event = selected.message
            # If we receive updates for the directory itself, they should be only
            # modifications, we only check that because we can have ordering issues if
            # we try check also the order compared to events in the file.
            if event.path == tmp_path:
                assert event.type == EventType.MODIFY
            elif event.path == filename:
                assert event.type == event_type
                number_of_writes += 1
                # After receiving a write 3 times, unsubscribe from the writes channel
                if number_of_writes == expected_number_of_writes:
                    break

    assert number_of_writes == expected_number_of_writes


@pytest.mark.integration
async def test_file_watcher_deletes(tmp_path: pathlib.Path) -> None:
    """Ensure file watcher is returning paths only on the DELETE change.

    Also ensures that DELETE events are sent even if the file was recreated and even if
    the file doesn't exist.

    Args:
        tmp_path: A tmp directory to run the file watcher on. Created by pytest.
    """
    filename = tmp_path / "test-file"
    file_watcher = FileWatcher(
        paths=[str(tmp_path)],
        event_types={EventType.DELETE},
        force_polling=False,
        polling_interval=timedelta(seconds=0.05),
    )
    write_timer = Timer(timedelta(seconds=0.1), SkipMissedAndDrift())
    deletion_timer = Timer(timedelta(seconds=0.25), SkipMissedAndDrift())

    number_of_write = 0
    number_of_deletes = 0
    number_of_events = 0
    # We want to write to a file and then removed, and then write again (create it
    # again) and remove it again and then stop.
    # Because awatch takes some time to get notified by the OS, we need to stop writing
    # while a delete was done, to make sure the file is not recreated before the
    # deletion event arrives.
    # For the second round of writes and then delete, we allow writing after the delete
    # was done as an extra test.
    #
    # This is an example timeline for this test:
    #
    # |-----|--.--|-----|---o-|-----|--.--|-----|--o--|-----|-----|-----|-----|-----|
    # W     W  D            E W     W  D  W     W  E
    #
    # Where:
    # W: Write
    # D: Delete
    # E: FileWatcher Event
    async for selected in select(file_watcher, write_timer, deletion_timer):
        if selected_from(selected, write_timer):
            if number_of_write >= 2 and number_of_events == 0:
                continue
            filename.write_text(f"{selected.message}")
            number_of_write += 1
        elif selected_from(selected, deletion_timer):
            # Avoid removing the file twice
            if not pathlib.Path(filename).is_file():
                continue
            os.remove(filename)
            number_of_deletes += 1
        elif selected_from(selected, file_watcher):
            number_of_events += 1
            if number_of_events >= 2:
                break

    assert number_of_deletes == 2
    # Can be more because the watcher could take some time to trigger
    assert number_of_write >= 3
    assert number_of_events == 2


@pytest.mark.integration
async def test_file_watcher_exit_iterator(tmp_path: pathlib.Path) -> None:
    """Test breaking the file watcher iterator.

    Args:
        tmp_path: A tmp directory to run the file watcher on. Created by pytest.
    """
    filename = tmp_path / "test-file"

    number_of_writes = 0
    expected_number_of_writes = 3

    file_watcher = FileWatcher(
        paths=[str(tmp_path)],
        force_polling=True,
        polling_interval=timedelta(seconds=0.05),
    )
    timer = Timer(timedelta(seconds=0.1), SkipMissedAndDrift())

    async for selected in select(file_watcher, timer):
        if selected_from(selected, timer):
            filename.write_text(f"{selected.message}")
        elif selected_from(selected, file_watcher):
            number_of_writes += 1
            if number_of_writes == expected_number_of_writes:
                file_watcher._stop_event.set()  # pylint: disable=protected-access
                break

    ready = await file_watcher.ready()
    assert ready is False

    with pytest.raises(ReceiverStoppedError):
        file_watcher.consume()

    assert number_of_writes == expected_number_of_writes


@pytest.mark.integration
async def test_file_watcher_close_receiver(tmp_path: pathlib.Path) -> None:
    """Ensure closing the file watcher stops the receiver.

    Args:
        tmp_path: A tmp directory to run the file watcher on. Created by pytest.
    """
    filename = tmp_path / "test-file"

    number_of_writes = 0
    expected_number_of_writes = 3

    file_watcher = FileWatcher(
        paths=[str(tmp_path)],
        force_polling=True,
        polling_interval=timedelta(seconds=0.05),
    )
    timer = Timer(timedelta(seconds=0.1), SkipMissedAndDrift())

    async for selected in select(file_watcher, timer):
        if selected_from(selected, timer):
            filename.write_text(f"{selected.message}")
        elif selected_from(selected, file_watcher):
            number_of_writes += 1
            if number_of_writes == expected_number_of_writes:
                file_watcher.close()
                break

    ready = await file_watcher.ready()
    assert ready is False

    with pytest.raises(ReceiverStoppedError):
        file_watcher.consume()

    assert number_of_writes == expected_number_of_writes
