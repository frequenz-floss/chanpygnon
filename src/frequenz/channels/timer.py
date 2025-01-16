# License: MIT
# Copyright © 2023 Frequenz Energy-as-a-Service GmbH

"""A receiver that sends a message regularly.

# Quick Start

Info: Important
    This quick start is provided to have a quick feeling of how to use this module, but
    it is extremely important to understand how timers behave when they are delayed.

    We recommend emphatically to read about [missed ticks and
    drifting](#missed-ticks-and-drifting) before using timers in production.

If you need to do something as periodically as possible (avoiding
[drifts](#missed-ticks-and-drifting)), you can use
a [`Timer`][frequenz.channels.timer.Timer] like this:

Example: Periodic Timer Example
    ```python
    import asyncio
    from datetime import datetime, timedelta

    from frequenz.channels.timer import Timer


    async def main() -> None:
        async for drift in Timer(timedelta(seconds=1.0), TriggerAllMissed()):
            print(f"The timer has triggered at {datetime.now()} with a drift of {drift}")


    asyncio.run(main())
    ```

    This timer will tick as close as every second as possible, even if the loop is busy
    doing something else for a good amount of time. In extreme cases, if the loop was
    busy for a few seconds, the timer will trigger a few times in a row to catch up, one
    for every missed tick.

If, instead, you need a timeout, for example to abort waiting for other receivers after
a certain amount of time, you can use a [`Timer`][frequenz.channels.timer.Timer] like
this:

Example: Timeout Example
    ```python
    import asyncio
    from datetime import timedelta

    from frequenz.channels import Anycast, select, selected_from
    from frequenz.channels.timer import Timer


    async def main() -> None:
        channel = Anycast[int](name="data-channel")
        data_receiver = channel.new_receiver()

        timer = Timer(timedelta(seconds=1.0), SkipMissedAndDrift())

        async for selected in select(data_receiver, timer):
            if selected_from(selected, data_receiver):
                print(f"Received data: {selected.message}")
                timer.reset()
            elif selected_from(selected, timer):
                drift = selected.message
                print(f"No data received for {timer.interval + drift} seconds, giving up")
                break


    asyncio.run(main())
    ```

    This timer will *rearm* itself automatically after it was triggered, so it will
    trigger again after the selected interval, no matter what the current drift was. So
    if the loop was busy for a few seconds, the timer will trigger immediately and then
    wait for another second before triggering again. The missed ticks are skipped.

# Missed Ticks And Drifting

A [`Timer`][frequenz.channels.timer.Timer] can be used to send a messages at regular
time intervals, but there is one fundamental issue with timers in the [asyncio][] world:
the event loop could give control to another task at any time, and that task can take
a long time to finish, making the time it takes the next timer message to be received
longer than the desired interval.

Because of this, it is very important for users to be able to understand and control
how timers behave when they are delayed. Timers will handle missed ticks according to
a *missing tick policy*.

The following built-in policies are available:

* [`SkipMissedAndDrift`][frequenz.channels.timer.SkipMissedAndDrift]:
    {{docstring_summary("frequenz.channels.timer.SkipMissedAndDrift")}}
* [`SkipMissedAndResync`][frequenz.channels.timer.SkipMissedAndResync]:
    {{docstring_summary("frequenz.channels.timer.SkipMissedAndResync")}}
* [`TriggerAllMissed`][frequenz.channels.timer.TriggerAllMissed]:
    {{docstring_summary("frequenz.channels.timer.TriggerAllMissed")}}
"""

from __future__ import annotations

import abc
import asyncio
from datetime import timedelta

from typing_extensions import override

from ._receiver import Receiver, ReceiverStoppedError


def _to_microseconds(time: float | timedelta) -> int:
    """Convert a time or a timedelta to microseconds.

    Args:
        time: The time to convert. If it is a `float`, it is assumed to be in
            seconds.

    Returns:
        The time in microseconds.
    """
    if isinstance(time, timedelta):
        time = time.total_seconds()
    return round(time * 1_000_000)


class MissedTickPolicy(abc.ABC):
    """A policy to handle timer missed ticks.

    To implement a custom policy you need to subclass
    [`MissedTickPolicy`][frequenz.channels.timer.MissedTickPolicy] and implement the
    [`calculate_next_tick_time`][frequenz.channels.timer.MissedTickPolicy.calculate_next_tick_time]
    method.

    Example:
        This policy will just wait one more second than the original interval if a
        tick is missed:

        ```python
        class WaitOneMoreSecond(MissedTickPolicy):
            def calculate_next_tick_time(
                self, *, interval: int, scheduled_tick_time: int, now: int
            ) -> int:
                return scheduled_tick_time + interval + 1_000_000


        async def main() -> None:
            timer = Timer(
                interval=timedelta(seconds=1),
                missed_tick_policy=WaitOneMoreSecond(),
            )

            async for drift in timer:
                print(f"The timer has triggered with a drift of {drift}")

        asyncio.run(main())
        ```
    """

    @abc.abstractmethod
    def calculate_next_tick_time(
        self, *, interval: int, scheduled_tick_time: int, now: int
    ) -> int:
        """Calculate the next tick time according to `missed_tick_policy`.

        This method is called by `ready()` after it has determined that the
        timer has triggered.  It will check if the timer has missed any ticks
        and handle them according to `missed_tick_policy`.

        Args:
            interval: The interval between ticks (in microseconds).
            scheduled_tick_time: The time the current tick was scheduled to
                trigger (in microseconds).
            now: The current loop time (in microseconds).

        Returns:
            The next tick time (in microseconds) according to
                `missed_tick_policy`.
        """

    def __repr__(self) -> str:
        """Return a string representation of this policy."""
        return f"{type(self).__name__}()"


class TriggerAllMissed(MissedTickPolicy):
    """A policy that triggers all the missed ticks immediately until it catches up.

    The [`TriggerAllMissed`][frequenz.channels.timer.TriggerAllMissed] policy will
    trigger all missed ticks immediately until it catches up with the current time.
    This means that if the timer is delayed for any reason, when it finally gets some
    time to run, it will trigger all the missed ticks in a burst. The drift is not
    accumulated and the next tick will be scheduled according to the original start
    time.

    Example:
        This example represents a timer with interval 1 second.

        1. The first tick, `T0` happens exactly at time 0.

        2. The second tick, `T1`, happens at time 1.2 (0.2 seconds late), so it triggers
            immediately. But it re-syncs, so the next tick is still expected at
            2 seconds. This re-sync happens on every tick, so all ticks are expected at
            multiples of 1 second, not matter how delayed they were.

        3. The third tick, `T2`, happens at time 2.3 (0.3 seconds late), so it also
            triggers immediately.

        4. The fourth tick, `T3`, happens at time 4.3 (1.3 seconds late), so it also
            triggers immediately.

        5. The fifth tick, `T4`, which was also already delayed (by 0.3 seconds),
            triggers immediately too, resulting in a small *catch-up* burst.

        6. The sixth tick, `T5`, happens at 5.1 (0.1 seconds late), so it triggers
            immediately again.

        7. The seventh tick, `T6`, happens at 6.0, right on time.

        <center>
        ```bob
        0         1         2         3         4   T4    5         6
        *---------o-*-------o--*------o---------o--**-----o*--------*-----> time
        T0          T1         T2                  T3      T5       T6

        -o- "Expected ticks"
        -*- "Delivered ticks"
        ```
        </center>
    """

    def calculate_next_tick_time(
        self, *, now: int, scheduled_tick_time: int, interval: int
    ) -> int:
        """Calculate the next tick time.

        This method always returns `scheduled_tick_time + interval`, as all
        ticks need to produce a trigger event.

        Args:
            now: The current loop time (in microseconds).
            scheduled_tick_time: The time the current tick was scheduled to
                trigger (in microseconds).
            interval: The interval between ticks (in microseconds).

        Returns:
            The next tick time (in microseconds).
        """
        return scheduled_tick_time + interval


class SkipMissedAndResync(MissedTickPolicy):
    """A policy that drops all the missed ticks, triggers immediately and resyncs.

    If ticks are missed, the
    [`SkipMissedAndResync`][frequenz.channels.timer.SkipMissedAndResync] policy will
    make the [`Timer`][frequenz.channels.timer.Timer] trigger immediately and it will
    schedule to trigger again on the next multiple of the
    [interval][frequenz.channels.timer.Timer.interval], effectively skipping any missed
    ticks, but re-syncing with the original start time.

    Example:
        This example represents a timer with interval 1 second.

        1. The first tick `T0` happens exactly at time 0.

        2. The second tick, `T1`, happens at time 1.2 (0.2 seconds late), so it triggers
            immediately. But it re-syncs, so the next tick is still expected at
            2 seconds. This re-sync happens on every tick, so all ticks are expected at
            multiples of 1 second, not matter how delayed they were.

        3. The third tick, `T2`, happens at time 2.3 (0.3 seconds late), so it also
            triggers immediately.

        4. The fourth tick, `T3`, happens at time 4.3 (1.3 seconds late), so it also
            triggers immediately, but there was also a tick expected at 4 seconds, `T4`,
            which is skipped according to this policy to avoid bursts of ticks.

        6. The sixth tick, `T5`, happens at 5.1 (0.1 seconds late), so it triggers
            immediately again.

        7. The seventh tick, `T6`, happens at 6.0, right on time.

        <center>
        ```bob
        0         1         2         3         4   T4    5         6
        *---------o-*-------o--*------o---------o--*O-----o-*-------*-----> time
        T0          T1         T2                  T3       T5      T6

        -o- "Expected ticks"
        -*- "Delivered ticks"
        -O- "Undelivered ticks (skipped)"
        ```
        </center>
    """

    def calculate_next_tick_time(
        self, *, now: int, scheduled_tick_time: int, interval: int
    ) -> int:
        """Calculate the next tick time.

        Calculate the next multiple of `interval` after `scheduled_tick_time`.

        Args:
            now: The current loop time (in microseconds).
            scheduled_tick_time: The time the current tick was scheduled to
                trigger (in microseconds).
            interval: The interval between ticks (in microseconds).

        Returns:
            The next tick time (in microseconds).
        """
        # We need to resync (align) the next tick time to the current time
        drift = now - scheduled_tick_time
        delta_to_next_tick = interval - (drift % interval)
        return now + delta_to_next_tick


class SkipMissedAndDrift(MissedTickPolicy):
    """A policy that drops all the missed ticks, triggers immediately and resets.

    The [`SkipMissedAndDrift`][frequenz.channels.timer.SkipMissedAndDrift] policy will
    behave effectively as if the timer was
    [reset][frequenz.channels.timer.Timer.reset] every time it is triggered. This means
    the start time will change and the drift will be accumulated each time a tick is
    delayed. Only the relative drift will be returned on each tick.

    The reset happens only if the delay is larger than the
    [delay tolerance][frequenz.channels.timer.SkipMissedAndDrift.delay_tolerance], so it
    is possible to ignore small delays and not drift in those cases.

    Example:
        This example represents a timer with interval 1 second and delay tolerance of
        0.1 seconds.

        1. The first tick, `T0`, happens exactly at time 0.

        2. The second tick, `T1.2`, happens at time 1.2 (0.2 seconds late), so the timer
            triggers immediately but drifts a bit (0.2 seconds), so the next tick is
            expected at 2.2 seconds.

        3. The third tick, `T2.2`, happens at 2.3 seconds (0.1 seconds late), so it also
            triggers immediately but **it doesn't drift** because the delay is **under
            the `delay_tolerance`**. The next tick is expected at 3.2 seconds.

        4. The fourth tick, `T4.2`, triggers at 4.3 seconds (1.1 seconds late), so it
            also triggers immediately but the timer has drifted by 1.1 seconds, so a
            potential tick `T3.2` is skipped (not triggered).

        5. The fifth tick, `T5.3`, triggers at 5.3 seconds so it is right on time (no
            drift) and the same happens for tick `T6.3`, which triggers at 6.3 seconds.

        <center>
        ```bob
        0         1         2         3         4         5         6
        *---------o-*-------|-o*------|-O-------|-o*------|--*------|--*--> time
        T0          T1.2       T2.2     T3.2       T4.2      T5.3      T6.3

        -o- "Expected ticks"
        -*- "Delivered ticks"
        -O- "Undelivered ticks (skipped)"
        ```
        </center>
    """

    def __init__(self, *, delay_tolerance: timedelta = timedelta(0)):
        """Initialize this policy.

        See the class documentation for more details.

        Args:
            delay_tolerance: The maximum delay that is tolerated before
                starting to drift.  If a tick is delayed less than this, then
                it is not considered a missed tick and the timer doesn't
                accumulate this drift.

        Raises:
            ValueError: If `delay_tolerance` is negative.
        """
        self._tolerance: int = _to_microseconds(delay_tolerance)
        """The maximum allowed delay before starting to drift."""

        if self._tolerance < 0:
            raise ValueError("delay_tolerance must be positive")

    @property
    def delay_tolerance(self) -> timedelta:
        """The maximum delay that is tolerated before starting to drift."""
        return timedelta(microseconds=self._tolerance)

    def calculate_next_tick_time(
        self, *, now: int, scheduled_tick_time: int, interval: int
    ) -> int:
        """Calculate the next tick time.

        If the drift is larger than `delay_tolerance`, then it returns `now +
        interval` (so the timer drifts), otherwise it returns
        `scheduled_tick_time + interval` (we consider the delay too small and
        avoid small drifts).

        Args:
            now: The current loop time (in microseconds).
            scheduled_tick_time: The time the current tick was scheduled to
                trigger (in microseconds).
            interval: The interval between ticks (in microseconds).

        Returns:
            The next tick time (in microseconds).
        """
        drift = now - scheduled_tick_time
        if drift > self._tolerance:
            return now + interval
        return scheduled_tick_time + interval

    def __str__(self) -> str:
        """Return a string representation of this policy."""
        return f"{type(self).__name__}()"

    def __repr__(self) -> str:
        """Return a string representation of this policy."""
        return f"{type(self).__name__}({self.delay_tolerance=})"


class Timer(Receiver[timedelta]):
    """A receiver that sends a message regularly.

    [`Timer`][frequenz.channels.timer.Timer]s are started by default after they are
    created. This can be disabled by using `auto_start=False` option when creating
    them. In this case, the timer will not be started until
    [`reset()`][frequenz.channels.timer.Timer.reset] is called. Receiving from the timer
    (either using [`receive()`][frequenz.channels.timer.Timer.receive] or using the
    async iterator interface) will also start the timer at that point.

    Timers need to be created in a context where
    a [`asyncio`][] loop is already running. If no
    [`loop`][frequenz.channels.timer.Timer.loop] is specified, the current running loop
    is used.

    Timers can be stopped by calling [`stop()`][frequenz.channels.timer.Timer.stop].
    A stopped timer will raise
    a [`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError] or stop the async
    iteration as usual.

    Once a timer is explicitly stopped, it can only be started again by explicitly
    calling [`reset()`][frequenz.channels.timer.Timer.reset] (trying to receive from it
    or using the async iterator interface will keep failing).

    Timer messages are [`timedelta`][datetime.timedelta]s containing the drift of the
    timer, i.e. the difference between when the timer should have triggered and the time
    when it actually triggered.

    This drift will likely never be `0`, because if there is a task that is
    running when it should trigger, the timer will be delayed. In this case the
    drift will be positive. A negative drift should be technically impossible,
    as the timer uses [`asyncio`][]s loop monotonic clock.

    Warning:
        Even when the [`asyncio`][] loop's monotonic clock is a [`float`][], timers use
        `int`s to represent time internally. The internal time is tracked in
        microseconds, so the timer resolution is 1 microsecond
        ([`interval`][frequenz.channels.timer.Timer.interval] must be at least
         1 microsecond).

        This is to avoid floating point errors when performing calculations with time,
        which can lead to issues that are very hard to reproduce and debug.

    If the timer is delayed too much, then it will behave according to the
    [`missed_tick_policy`][frequenz.channels.timer.Timer.missed_tick_policy]. Missing
    ticks might or might not trigger a message and the drift could be accumulated or not
    depending on the chosen policy.
    """

    # We need the noqa here because RuntimeError is raised indirectly.
    def __init__(  # noqa: DOC503 pylint: disable=too-many-arguments
        self,
        interval: timedelta,
        missed_tick_policy: MissedTickPolicy,
        /,
        *,
        auto_start: bool = True,
        start_delay: timedelta = timedelta(0),
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Initialize this timer.

        See the [class documentation][frequenz.channels.timer.Timer] for details.

        Args:
            interval: The time between timer ticks. Must be at least
                1 microsecond.
            missed_tick_policy: The policy of the timer when it misses a tick.
                Commonly one of `TriggerAllMissed`, `SkipMissedAndResync`, `SkipMissedAndDrift`
                or a custom class deriving from `MissedTickPolicy`. See the
                documentation of the each class for more details.
            auto_start: Whether the timer should be started when the
                instance is created. This can only be `True` if there is
                already a running loop or an explicit `loop` that is running
                was passed.
            start_delay: The delay before the timer should start. If `auto_start` is
                `False`, an exception is raised. This has microseconds resolution,
                anything smaller than a microsecond means no delay.
            loop: The event loop to use to track time. If `None`,
                `asyncio.get_running_loop()` will be used.

        Raises:
            RuntimeError: If it was called without a loop and there is no
                running loop.
            ValueError: If `interval` is not positive or is smaller than 1
                microsecond; if `start_delay` is negative or `start_delay` was specified
                but `auto_start` is `False`.
        """
        if interval < timedelta(microseconds=1):
            raise ValueError(
                f"The `interval` must be positive and at least 1 microsecond, not {interval}"
            )

        if start_delay > timedelta(0) and auto_start is False:
            raise ValueError(
                "`auto_start` must be `True` if a `start_delay` is specified"
            )

        self._interval: int = _to_microseconds(interval)
        """The time to between timer ticks."""

        self._missed_tick_policy: MissedTickPolicy = missed_tick_policy
        """The policy of the timer when it misses a tick.

        See the documentation of `MissedTickPolicy` for details.
        """

        self._reset_event = asyncio.Event()

        self._loop: asyncio.AbstractEventLoop = (
            loop if loop is not None else asyncio.get_running_loop()
        )
        """The event loop to use to track time."""

        self._stopped: bool = True
        """Whether the timer was requested to stop.

        If this is `False`, then the timer is running.

        If this is `True`, then it is stopped or there is a request to stop it
        or it was not started yet:

        * If `_next_msg_time` is `None`, it means it wasn't started yet (it was
          created with `auto_start=False`).  Any receiving method will start
          it by calling `reset()` in this case.

        * If `_next_msg_time` is not `None`, it means there was a request to
          stop it.  In this case receiving methods will raise
          a `ReceiverClosedError`.
        """

        self._next_tick_time: int | None = None
        """The absolute (monotonic) time when the timer should trigger.

        If this is `None`, it means the timer didn't start yet, but it should
        be started as soon as it is used.
        """

        self._current_drift: timedelta | None = None
        """The difference between `_next_msg_time` and the triggered time.

        This is calculated by `ready()` but is returned by `consume()`. If
        `None` it means `ready()` wasn't called and `consume()` will assert.
        `consume()` will set it back to `None` to tell `ready()` that it needs
        to wait again.
        """

        if auto_start:
            self.reset(start_delay=start_delay)

    @property
    def interval(self) -> timedelta:
        """The interval between timer ticks."""
        return timedelta(microseconds=self._interval)

    @property
    def missed_tick_policy(self) -> MissedTickPolicy:
        """The policy of the timer when it misses a tick."""
        return self._missed_tick_policy

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The event loop used by the timer to track time."""
        return self._loop

    @property
    def is_running(self) -> bool:
        """Whether the timer is running."""
        return not self._stopped

    # We need the noqa here because RuntimeError is raised indirectly.
    def reset(  # noqa: DOC503
        self,
        *,
        interval: timedelta | None = None,
        start_delay: timedelta = timedelta(0),
    ) -> None:
        """Reset the timer to start timing from now (plus an optional delay).

        If the timer was stopped, or not started yet, it will be started.

        This can only be called with a running loop, see the class documentation for
        more details.

        Args:
            interval: The new interval between ticks. If `None`, the current
                interval is kept.
            start_delay: The delay before the timer should start. This has microseconds
                resolution, anything smaller than a microsecond means no delay.

        Raises:
            RuntimeError: If it was called without a running loop.
            ValueError: If `start_delay` is negative.
        """
        start_delay_ms = _to_microseconds(start_delay)

        if start_delay_ms < 0:
            raise ValueError(f"`start_delay` can't be negative, got {start_delay}")

        if interval is not None:
            self._interval = _to_microseconds(interval)

        self._next_tick_time = self._now() + start_delay_ms + self._interval

        if self.is_running:
            self._reset_event.set()

        self._stopped = False
        self._current_drift = None

    def stop(self) -> None:
        """Stop the timer.

        Once `stop` has been called, all subsequent calls to `ready()` will
        immediately return False and calls to `consume()` / `receive()` or any
        use of the async iterator interface will raise
        a `ReceiverStoppedError`.

        You can restart the timer with `reset()`.
        """
        self._stopped = True
        # We need to make sure it's not None, otherwise `ready()` will start it
        self._next_tick_time = self._now()
        self._reset_event.set()

    # We need a noqa here because the docs have a Raises section but the documented
    # exceptions are raised indirectly.
    @override
    async def ready(self) -> bool:  # noqa: DOC502
        """Wait until the timer `interval` passed.

        Once a call to `ready()` has finished, the resulting tick information
        must be read with a call to `consume()` (`receive()` or iterated over)
        to tell the timer it should wait for the next interval.

        The timer will remain ready (this method will return immediately)
        until it is consumed.

        Returns:
            Whether the timer was started and it is still running.

        Raises:
            RuntimeError: If it was called without a running loop.
        """
        # If there are messages waiting to be consumed, return immediately.
        if self._current_drift is not None:
            return True

        # If `_next_tick_time` is `None`, it means it was created with
        # `auto_start=False` and should be started.
        if self._next_tick_time is None:
            self.reset()
            assert (
                self._next_tick_time is not None
            ), "This should be assigned by reset()"

        # If a stop was explicitly requested, we bail out.
        if self._stopped:
            return False

        now = self._now()
        time_to_next_tick = self._next_tick_time - now

        # If we didn't reach the tick yet, sleep until we do.
        # We need to do this in a loop also reacting to the reset event, as the timer
        # could be reset while we are sleeping, in which case we need to recalculate
        # the time to the next tick and try again.
        while time_to_next_tick > 0:
            _, pending = await asyncio.wait(
                [
                    asyncio.create_task(asyncio.sleep(time_to_next_tick / 1_000_000)),
                    asyncio.create_task(self._reset_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            self._reset_event.clear()
            now = self._now()
            time_to_next_tick = self._next_tick_time - now

        # If a stop was explicitly requested during the sleep, we bail out.
        if self._stopped:
            return False

        self._current_drift = timedelta(microseconds=now - self._next_tick_time)
        self._next_tick_time = self._missed_tick_policy.calculate_next_tick_time(
            now=now,
            scheduled_tick_time=self._next_tick_time,
            interval=self._interval,
        )

        return True

    @override
    def consume(self) -> timedelta:
        """Return the latest drift once `ready()` is complete.

        Once the timer has triggered (`ready()` is done), this method returns the
        difference between when the timer should have triggered and the time when
        it actually triggered. See the class documentation for more details.

        Returns:
            The difference between when the timer should have triggered and the
                time when it actually did.

        Raises:
            ReceiverStoppedError: If the timer was stopped via `stop()`.
        """
        # If it was stopped and there it no pending result, we raise
        # (if there is a pending result, then we still want to return it first)
        if self._stopped and self._current_drift is None:
            raise ReceiverStoppedError(self)

        assert (
            self._current_drift is not None
        ), "calls to `consume()` must be follow a call to `ready()`"
        drift = self._current_drift
        self._current_drift = None
        return drift

    @override
    def close(self) -> None:
        """Close the timer."""
        self.stop()

    def _now(self) -> int:
        """Return the current monotonic clock time in microseconds.

        Returns:
            The current monotonic clock time in microseconds.
        """
        return _to_microseconds(self._loop.time())

    def __str__(self) -> str:
        """Return a string representation of this timer."""
        return f"{type(self).__name__}({self.interval})"

    def __repr__(self) -> str:
        """Return a string with the internal representation of this timer."""
        return (
            f"{type(self).__name__}<{self.interval=}, {self.missed_tick_policy=}, "
            f"{self.loop=}, {self.is_running=}>"
        )
