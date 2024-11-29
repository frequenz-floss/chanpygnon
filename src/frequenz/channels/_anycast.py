# License: MIT
# Copyright © 2022 Frequenz Energy-as-a-Service GmbH

"""A channel for sending data across async tasks."""

from __future__ import annotations

import logging
from asyncio import Condition
from collections import deque
from typing import Generic, TypeVar

from typing_extensions import override

from ._exceptions import ChannelClosedError
from ._generic import ChannelMessageT
from ._receiver import Receiver, ReceiverStoppedError
from ._sender import Sender, SenderError

_logger = logging.getLogger(__name__)


class Anycast(Generic[ChannelMessageT]):
    """A channel that delivers each message to exactly one receiver.

    # Description

    !!! Tip inline end

        [Anycast][frequenz.channels.Anycast] channels behave like the
        [Golang](https://golang.org/) [channels](https://go.dev/ref/spec#Channel_types).

    [Anycast][frequenz.channels.Anycast] channels support multiple
    [senders][frequenz.channels.Sender] and multiple
    [receivers][frequenz.channels.Receiver]. Each message sent through any of the
    senders will be received by exactly one receiver (but **any** receiver).

    <center>
    ```bob
    .---------. msg1                           msg1  .-----------.
    | Sender  +------.                       .------>| Receiver  |
    '---------'      |      .----------.     |       '-----------'
                     +----->| Channel  +-----+
    .---------.      |      '----------'     |       .-----------.
    | Sender  +------'                       '------>| Receiver  |
    '---------' msg2                           msg2  '-----------'
    ```
    </center>

    !!! Note inline end "Characteristics"

        * **Buffered:** Yes, with a global channel buffer
        * **Buffer full policy:** Block senders
        * **Multiple receivers:** Yes
        * **Multiple senders:** Yes
        * **Thread-safe:** No

    This channel is buffered, and if the senders are faster than the receivers, then the
    channel's buffer will fill up. In that case, the senders will block at the
    [`send()`][frequenz.channels.Sender.send] method until the receivers consume the
    messages in the channel's buffer. The channel's buffer size can be configured at
    creation time via the `limit` argument.

    The first receiver that is awaited will get the next message. When multiple
    receivers are waiting, the [asyncio][] loop scheduler picks a receiver for each next
    massage.

    This means that, in practice, there might be only one receiver receiving all the
    messages, depending on how tasks are schduled.

    If you need to ensure some delivery policy (like round-robin or uniformly random),
    then you will have to implement it yourself.

    To create a new [senders][frequenz.channels.Sender] and
    [receivers][frequenz.channels.Receiver] you can use the
    [`new_sender()`][frequenz.channels.Anycast.new_sender] and
    [`new_receiver()`][frequenz.channels.Anycast.new_receiver] methods
    respectively.

    When the channel is not needed anymore, it should be closed with the
    [`close()`][frequenz.channels.Anycast.close] method. This will prevent further
    attempts to [`send()`][frequenz.channels.Sender.send] data. Receivers will still be
    able to drain the pending messages on the channel, but after that, subsequent
    [`receive()`][frequenz.channels.Receiver.receive] calls will raise a
    [`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError] exception.

    This channel is useful, for example, to distribute work across multiple workers.

    In cases where each message need to be received by every receiver, a
    [broadcast][frequenz.channels.Broadcast] channel may be used.

    # Examples

    Example: Send a few numbers to a receiver
        This is a very simple example that sends a few numbers from a single sender to
        a single receiver.

        ```python
        import asyncio

        from frequenz.channels import Anycast, Sender


        async def send(sender: Sender[int]) -> None:
            for message in range(3):
                print(f"sending {message}")
                await sender.send(message)


        async def main() -> None:
            channel = Anycast[int](name="numbers")

            sender = channel.new_sender()
            receiver = channel.new_receiver()

            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(send(sender))
                for _ in range(3):
                    message = await receiver.receive()
                    print(f"received {message}")
                    await asyncio.sleep(0.1)  # sleep (or work) with the data


        asyncio.run(main())
        ```

        The output should look something like (although the sending and received might
        appear more interleaved):

        ```
        sending 0
        sending 1
        sending 2
        received 0
        received 1
        received 2
        ```

    Example: Send a few number from multiple senders to multiple receivers
        This is a more complex example that sends a few numbers from multiple senders to
        multiple receivers, using a small buffer to force the senders to block.

        ```python
        import asyncio

        from frequenz.channels import Anycast, Receiver, ReceiverStoppedError, Sender


        async def send(name: str, sender: Sender[int], start: int, stop: int) -> None:
            for message in range(start, stop):
                print(f"{name} sending {message}")
                await sender.send(message)


        async def recv(name: str, receiver: Receiver[int]) -> None:
            try:
                async for message in receiver:
                    print(f"{name} received {message}")
                await asyncio.sleep(0.1)  # sleep (or work) with the data
            except ReceiverStoppedError:
                pass


        async def main() -> None:
            acast = Anycast[int](name="numbers", limit=2)

            async with asyncio.TaskGroup() as task_group:
                task_group.create_task(send("sender_1", acast.new_sender(), 10, 13))
                task_group.create_task(send("sender_2", acast.new_sender(), 20, 22))
                task_group.create_task(recv("receiver_1", acast.new_receiver()))
                task_group.create_task(recv("receiver_2", acast.new_receiver()))


        asyncio.run(main())
        ```

        The output should look something like this(although the sending and received
        might appear interleaved in a different way):

        ```
        sender_1 sending 10
        sender_1 sending 11
        sender_1 sending 12
        Anycast channel [Anycast:numbers:_Sender] is full, blocking sender until a receiver
        consumes a message
        sender_2 sending 20
        Anycast channel [Anycast:numbers:_Sender] is full, blocking sender until a receiver
        consumes a message
        receiver_1 received 10
        receiver_1 received 11
        sender_2 sending 21
        Anycast channel [Anycast:numbers:_Sender] is full, blocking sender until a receiver
        consumes a message
        receiver_1 received 12
        receiver_1 received 20
        receiver_1 received 21
        ```
    """

    def __init__(self, *, name: str, limit: int = 10) -> None:
        """Initialize this channel.

        Args:
            name: The name of the channel. This is for logging purposes, and it will be
                shown in the string representation of the channel.
            limit: The size of the internal buffer in number of messages.  If the buffer
                is full, then the senders will block until the receivers consume the
                messages in the buffer.
        """
        self._name: str = name
        """The name of the channel.

        This is for logging purposes, and it will be shown in the string representation
        of the channel.
        """

        self._deque: deque[ChannelMessageT] = deque(maxlen=limit)
        """The channel's buffer."""

        self._send_cv: Condition = Condition()
        """The condition to wait for free space in the channel's buffer.

        If the channel's buffer is full, then the sender waits for messages to
        get consumed using this condition until there's some free space
        available in the channel's buffer.
        """

        self._recv_cv: Condition = Condition()
        """The condition to wait for messages in the channel's buffer.

        If the channel's buffer is empty, then the receiver waits for messages
        using this condition until there's a message available in the channel's
        buffer.
        """

        self._closed: bool = False
        """Whether the channel is closed."""

    @property
    def name(self) -> str:
        """The name of this channel.

        This is for debugging purposes, it will be shown in the string representation
        of this channel.
        """
        return self._name

    @property
    def is_closed(self) -> bool:
        """Whether this channel is closed.

        Any further attempts to use this channel after it is closed will result in an
        exception.
        """
        return self._closed

    @property
    def limit(self) -> int:
        """The maximum number of messages that can be stored in the channel's buffer.

        If the length of channel's buffer reaches the limit, then the sender
        blocks at the [send()][frequenz.channels.Sender.send] method until
        a message is consumed.
        """
        maxlen = self._deque.maxlen
        assert maxlen is not None
        return maxlen

    async def close(self) -> None:
        """Close the channel.

        Any further attempts to [send()][frequenz.channels.Sender.send] data
        will return `False`.

        Receivers will still be able to drain the pending messages on the channel,
        but after that, subsequent
        [receive()][frequenz.channels.Receiver.receive] calls will return `None`
        immediately.
        """
        self._closed = True
        async with self._send_cv:
            self._send_cv.notify_all()
        async with self._recv_cv:
            self._recv_cv.notify_all()

    def new_sender(self) -> Sender[ChannelMessageT]:
        """Return a new sender attached to this channel."""
        return _Sender(self)

    def new_receiver(self) -> Receiver[ChannelMessageT]:
        """Return a new receiver attached to this channel."""
        return _Receiver(self)

    def __str__(self) -> str:
        """Return a string representation of this channel."""
        return f"{type(self).__name__}:{self._name}"

    def __repr__(self) -> str:
        """Return a string representation of this channel."""
        return (
            f"{type(self).__name__}(name={self._name!r}, limit={self.limit!r}):<"
            f"current={len(self._deque)!r}, closed={self._closed!r}>"
        )


_T = TypeVar("_T")


class _Sender(Sender[_T]):
    """A sender to send messages to an Anycast channel.

    Should not be created directly, but through the `Anycast.new_sender()`
    method.
    """

    def __init__(self, channel: Anycast[_T], /) -> None:
        """Initialize this sender.

        Args:
            channel: A reference to the channel that this sender belongs to.
        """
        self._channel: Anycast[_T] = channel
        """The channel that this sender belongs to."""

    @override
    async def send(self, message: _T, /) -> None:
        """Send a message across the channel.

        To send, this method inserts the message into the Anycast channel's
        buffer.  If the channel's buffer is full, waits for messages to get
        consumed, until there's some free space available in the buffer.  Each
        message will be received by exactly one receiver.

        Args:
            message: The message to be sent.

        Raises:
            SenderError: If the underlying channel was closed.
                A [ChannelClosedError][frequenz.channels.ChannelClosedError] is
                set as the cause.
        """
        # pylint: disable=protected-access
        if self._channel._closed:
            raise SenderError("The channel was closed", self) from ChannelClosedError(
                self._channel
            )
        if len(self._channel._deque) == self._channel._deque.maxlen:
            _logger.warning(
                "Anycast channel [%s] is full, blocking sender until a receiver "
                "consumes a message",
                self,
            )
            while len(self._channel._deque) == self._channel._deque.maxlen:
                async with self._channel._send_cv:
                    await self._channel._send_cv.wait()
            _logger.info(
                "Anycast channel [%s] has space again, resuming the blocked sender",
                self,
            )
        self._channel._deque.append(message)
        async with self._channel._recv_cv:
            self._channel._recv_cv.notify(1)
        # pylint: enable=protected-access

    def __str__(self) -> str:
        """Return a string representation of this sender."""
        return f"{self._channel}:{type(self).__name__}"

    def __repr__(self) -> str:
        """Return a string representation of this sender."""
        return f"{type(self).__name__}({self._channel!r})"


class _Empty:
    """A sentinel to indicate that a message has not been set."""


class _Receiver(Receiver[_T]):
    """A receiver to receive messages from an Anycast channel.

    Should not be created directly, but through the `Anycast.new_receiver()`
    method.
    """

    def __init__(self, channel: Anycast[_T], /) -> None:
        """Initialize this receiver.

        Args:
            channel: A reference to the channel that this receiver belongs to.
        """
        self._channel: Anycast[_T] = channel
        """The channel that this receiver belongs to."""

        self._closed: bool = False
        """Whether the receiver is closed."""

        self._next: _T | type[_Empty] = _Empty

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
        # if a message is already ready, then return immediately.
        if self._next is not _Empty:
            return True

        if self._closed:
            return False

        # pylint: disable=protected-access
        while len(self._channel._deque) == 0:
            if self._channel._closed:
                return False
            async with self._channel._recv_cv:
                await self._channel._recv_cv.wait()
        self._next = self._channel._deque.popleft()
        async with self._channel._send_cv:
            self._channel._send_cv.notify(1)
        # pylint: enable=protected-access
        return True

    @override
    def consume(self) -> _T:
        """Return the latest message once `ready()` is complete.

        Returns:
            The next message that was received.

        Raises:
            ReceiverStoppedError: If the receiver stopped producing messages.
        """
        if (  # pylint: disable=protected-access
            self._next is _Empty and self._channel._closed
        ):
            raise ReceiverStoppedError(self) from ChannelClosedError(self._channel)

        if self._next is _Empty and self._closed:
            raise ReceiverStoppedError(self)

        assert (
            self._next is not _Empty
        ), "`consume()` must be preceded by a call to `ready()`"
        # mypy doesn't understand that the assert above ensures that self._next is not
        # _Sentinel.  So we have to use a type ignore here.
        next_val: _T = self._next  # type: ignore[assignment]
        self._next = _Empty

        return next_val

    @override
    def close(self) -> None:
        """Close this receiver.

        After closing, the receiver will not be able to receive any more messages.
        """
        self._closed = True

    def __str__(self) -> str:
        """Return a string representation of this receiver."""
        return f"{self._channel}:{type(self).__name__}"

    def __repr__(self) -> str:
        """Return a string representation of this receiver."""
        return f"{type(self).__name__}({self._channel!r})"
