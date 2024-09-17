# Frequenz channels Release Notes

## New Features

- A new class `frequenz.channels.time_scheduler.TimeScheduler` was added. It's optimized for scenarios where events get added, rescheduled or canceled frequently.

## Bug Fixes

- `FileWatcher`: Fixed `ready()` method to return False when an error occurs. Before this fix, `select()` (and other code using `ready()`) never detected the `FileWatcher` was stopped and the `select()` loop was continuously waking up to inform the receiver was ready.
