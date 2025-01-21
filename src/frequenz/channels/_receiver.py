# License: MIT
# Copyright © 2022 Frequenz Energy-as-a-Service GmbH

"""Receiver interface and related exceptions.

# Receivers

Messages are received from [channels](/user-guide/channels/index.md) through
[Receiver][frequenz.channels.Receiver] objects. [Receivers][frequenz.channels.Receiver]
are usually created by calling `channel.new_receiver()` and are [async
iterators][typing.AsyncIterator], so the easiest way to receive messages from them as
a stream is to use `async for`:

```python show_lines="6:"
from frequenz.channels import Anycast

channel = Anycast[int](name="test-channel")
receiver = channel.new_receiver()

async for message in receiver:
    print(message)
```

If you need to receive messages in different places or expecting a particular
sequence, you can use the [`receive()`][frequenz.channels.Receiver.receive] method:

```python show_lines="6:"
from frequenz.channels import Anycast

channel = Anycast[int](name="test-channel")
receiver = channel.new_receiver()

first_message = await receiver.receive()
print(f"First message: {first_message}")

second_message = await receiver.receive()
print(f"Second message: {second_message}")
```

# Message Transformation

If you need to transform the received messages, receivers provide a
[`map()`][frequenz.channels.Receiver.map] method to easily do so:

```python show_lines="6:"
from frequenz.channels import Anycast

channel = Anycast[int](name="test-channel")
receiver = channel.new_receiver()

async for message in receiver.map(lambda x: x + 1):
    print(message)
```

[`map()`][frequenz.channels.Receiver.map] returns a new full receiver, so you can
use it in any of the ways described above.

# Message Filtering

If you need to filter the received messages, receivers provide a
[`filter()`][frequenz.channels.Receiver.filter] method to easily do so:

```python show_lines="6:"
from frequenz.channels import Anycast

channel = Anycast[int](name="test-channel")
receiver = channel.new_receiver()

async for message in receiver.filter(lambda x: x % 2 == 0):
    print(message)  # Only even numbers will be printed
```

As with [`map()`][frequenz.channels.Receiver.map],
[`filter()`][frequenz.channels.Receiver.filter] returns a new full receiver, so you can
use it in any of the ways described above.

# Error Handling

!!! Tip inline end

    For more information about handling errors, please refer to the
    [Error Handling](/user-guide/error-handling/) section of the user guide.

If there is an error while receiving a message,
a [`ReceiverError`][frequenz.channels.ReceiverError] exception is raised for both
[`receive()`][frequenz.channels.Receiver.receive] method and async iteration
interface.

If the receiver has completely stopped (for example the underlying channel was
closed), a [`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError] exception
is raised by [`receive()`][frequenz.channels.Receiver.receive] method.

```python show_lines="6:"
from frequenz.channels import Anycast

channel = Anycast[int](name="test-channel")
receiver = channel.new_receiver()

try:
    await receiver.receive()
except ReceiverStoppedError as error:
    print("The receiver was stopped")
except ReceiverError as error:
    print(f"There was an error trying to receive: {error}")
```

When used as an async iterator, the iteration will just stop without raising an
exception:

```python show_lines="6:"
from frequenz.channels import Anycast

channel = Anycast[int](name="test-channel")
receiver = channel.new_receiver()

try:
    async for message in receiver:
        print(message)
except ReceiverStoppedError as error:
    print("Will never happen")
except ReceiverError as error:
    print(f"There was an error trying to receive: {error}")
# If we get here, the receiver was stopped
```

# Closing receivers

When a particular stream of data is no-longer required, it is a good practice to close
the receiver to free up resources. This is especially important in applications that are
repeatedly creating new receivers.  The
[`Receiver.close()`][frequenz.channels.Receiver.close] method can be used for this
purpose.

After `close()` is called, we can still receive messages that are in the receiver's
buffer, but as soon as the receiver's buffer has been drained, trying to receive further
messages from a *closed* receiver will raise a
[`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError].

# Advanced Usage

!!! Warning inline end

    This section is intended for library developers that want to build other low-level
    abstractions on top of channels. If you are just using channels, you can safely
    ignore this section.

Receivers extend on the [async iterator protocol][typing.AsyncIterator] by providing
a [`ready()`][frequenz.channels.Receiver.ready] and
a [`consume()`][frequenz.channels.Receiver.consume] method.

The [`ready()`][frequenz.channels.Receiver.ready] method is used to await until the
receiver has a new message available, but without actually consuming it. The
[`consume()`][frequenz.channels.Receiver.consume] method consumes the next available
message and returns it.

[`ready()`][frequenz.channels.Receiver.ready] can be called multiple times, and it
will return immediately if the receiver is already ready.
[`consume()`][frequenz.channels.Receiver.consume] must be called only after
[`ready()`][frequenz.channels.Receiver.ready] is done and only once, until the next
call to [`ready()`][frequenz.channels.Receiver.ready].

Exceptions are never raised by [`ready()`][frequenz.channels.Receiver.ready], they
are always delayed until [`consume()`][frequenz.channels.Receiver.consume] is
called.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, Self, TypeGuard, TypeVar, overload

from typing_extensions import override

from ._exceptions import Error
from ._generic import MappedMessageT_co, ReceiverMessageT_co

if TYPE_CHECKING:
    from ._select import Selected

FilteredMessageT_co = TypeVar("FilteredMessageT_co", covariant=True)
"""Type variable for the filtered message type."""


class Receiver(ABC, Generic[ReceiverMessageT_co]):
    """An endpoint to receive messages."""

    # We need the noqa here because ReceiverError can be raised by ready() and consume()
    # implementations.
    async def __anext__(self) -> ReceiverMessageT_co:  # noqa: DOC503
        """Await the next message in the async iteration over received messages.

        Returns:
            The next received message.

        Raises:
            StopAsyncIteration: If the receiver stopped producing messages.
            ReceiverError: If there is some problem with the receiver.
        """
        try:
            await self.ready()
            return self.consume()
        except ReceiverStoppedError as exc:
            raise StopAsyncIteration() from exc

    @abstractmethod
    async def ready(self) -> bool:
        """Wait until the receiver is ready with a message or an error.

        Once a call to `ready()` has finished, the message should be read with
        a call to `consume()` (`receive()` or iterated over). The receiver will
        remain ready (this method will return immediately) until it is
        consumed.

        Returns:
            Whether the receiver is still active.
        """

    @abstractmethod
    def consume(self) -> ReceiverMessageT_co:
        """Return the latest message once `ready()` is complete.

        `ready()` must be called before each call to `consume()`.

        Returns:
            The next message received.

        Raises:
            ReceiverStoppedError: If the receiver stopped producing messages.
            ReceiverError: If there is some problem with the receiver.
        """

    def close(self) -> None:
        """Close the receiver.

        After calling this method, new messages will not be available from the receiver.
        Once the receiver's buffer is drained, trying to receive a message will raise a
        [`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError].
        """
        raise NotImplementedError("close() must be implemented by subclasses")

    def __aiter__(self) -> Self:
        """Get an async iterator over the received messages.

        Returns:
            This receiver, as it is already an async iterator.
        """
        return self

    # We need the noqa here because ReceiverError can be raised by consume()
    # implementations.
    async def receive(self) -> ReceiverMessageT_co:  # noqa: DOC503
        """Receive a message.

        Returns:
            The received message.

        Raises:
            ReceiverStoppedError: If there is some problem with the receiver.
            ReceiverError: If there is some problem with the receiver.
        """
        try:
            received = await anext(self)
        except StopAsyncIteration as exc:
            # If we already had a cause and it was the receiver was stopped,
            # then reuse that error, as StopAsyncIteration is just an artifact
            # introduced by __anext__.
            if (
                isinstance(exc.__cause__, ReceiverStoppedError)
                and exc.__cause__.receiver is self
            ):
                # This is a false positive, we are actually checking __cause__ is a
                # ReceiverStoppedError which is an exception.
                raise exc.__cause__  # pylint: disable=raising-non-exception
            raise ReceiverStoppedError(self) from exc
        return received

    def map(
        self, mapping_function: Callable[[ReceiverMessageT_co], MappedMessageT_co], /
    ) -> Receiver[MappedMessageT_co]:
        """Apply a mapping function on the received message.

        Tip:
            The returned receiver type won't have all the methods of the original
            receiver. If you need to access methods of the original receiver that are
            not part of the `Receiver` interface you should save a reference to the
            original receiver and use that instead.

        Args:
            mapping_function: The function to be applied on incoming messages.

        Returns:
            A new receiver that applies the function on the received messages.
        """
        return _Mapper(receiver=self, mapping_function=mapping_function)

    @overload
    def filter(
        self,
        filter_function: Callable[
            [ReceiverMessageT_co], TypeGuard[FilteredMessageT_co]
        ],
        /,
    ) -> Receiver[FilteredMessageT_co]:
        """Apply a type guard on the messages on a receiver.

        Tip:
            The returned receiver type won't have all the methods of the original
            receiver. If you need to access methods of the original receiver that are
            not part of the `Receiver` interface you should save a reference to the
            original receiver and use that instead.

        Args:
            filter_function: The function to be applied on incoming messages to
                determine if they should be received.

        Returns:
            A new receiver that only receives messages that pass the filter.
        """
        ...  # pylint: disable=unnecessary-ellipsis

    @overload
    def filter(
        self, filter_function: Callable[[ReceiverMessageT_co], bool], /
    ) -> Receiver[ReceiverMessageT_co]:
        """Apply a filter function on the messages on a receiver.

        Tip:
            The returned receiver type won't have all the methods of the original
            receiver. If you need to access methods of the original receiver that are
            not part of the `Receiver` interface you should save a reference to the
            original receiver and use that instead.

        Args:
            filter_function: The function to be applied on incoming messages to
                determine if they should be received.

        Returns:
            A new receiver that only receives messages that pass the filter.
        """
        ...  # pylint: disable=unnecessary-ellipsis

    def filter(
        self,
        filter_function: (
            Callable[[ReceiverMessageT_co], bool]
            | Callable[[ReceiverMessageT_co], TypeGuard[FilteredMessageT_co]]
        ),
        /,
    ) -> Receiver[ReceiverMessageT_co] | Receiver[FilteredMessageT_co]:
        """Apply a filter function on the messages on a receiver.

        Note:
            You can pass a [type guard][typing.TypeGuard] as the filter function to
            narrow the type of the messages that pass the filter.

        Tip:
            The returned receiver type won't have all the methods of the original
            receiver. If you need to access methods of the original receiver that are
            not part of the `Receiver` interface you should save a reference to the
            original receiver and use that instead.

        Args:
            filter_function: The function to be applied on incoming messages to
                determine if they should be received.

        Returns:
            A new receiver that only receives messages that pass the filter.
        """
        return _Filter(receiver=self, filter_function=filter_function)

    def triggered(
        self, selected: Selected[Any]
    ) -> TypeGuard[Selected[ReceiverMessageT_co]]:
        """Check whether this receiver was selected by [`select()`][frequenz.channels.select].

        This method is used in conjunction with the
        [`Selected`][frequenz.channels.Selected] class to determine which receiver was
        selected in `select()` iteration.

        It also works as a [type guard][typing.TypeGuard] to narrow the type of the
        `Selected` instance to the type of the receiver.

        Please see [`select()`][frequenz.channels.select] for an example.

        Args:
            selected: The result of a `select()` iteration.

        Returns:
            Whether this receiver was selected.
        """
        if handled := selected._recv is self:  # pylint: disable=protected-access
            selected._handled = True  # pylint: disable=protected-access
        return handled


class ReceiverError(Error, Generic[ReceiverMessageT_co]):
    """An error that originated in a [Receiver][frequenz.channels.Receiver].

    All exceptions generated by receivers inherit from this exception.
    """

    def __init__(self, message: str, receiver: Receiver[ReceiverMessageT_co]):
        """Initialize this error.

        Args:
            message: The error message.
            receiver: The [Receiver][frequenz.channels.Receiver] where the
                error happened.
        """
        super().__init__(message)
        self.receiver: Receiver[ReceiverMessageT_co] = receiver
        """The receiver where the error happened."""


class ReceiverStoppedError(ReceiverError[ReceiverMessageT_co]):
    """A stopped [`Receiver`][frequenz.channels.Receiver] was used."""

    def __init__(self, receiver: Receiver[ReceiverMessageT_co]):
        """Initialize this error.

        Args:
            receiver: The [Receiver][frequenz.channels.Receiver] where the
                error happened.
        """
        super().__init__(f"Receiver {receiver} was stopped", receiver)


class _Mapper(
    Receiver[MappedMessageT_co], Generic[ReceiverMessageT_co, MappedMessageT_co]
):
    """Apply a transform function on a channel receiver.

    Has two generic types:

    - The input type: message type in the input receiver.
    - The output type: return type of the transform method.
    """

    def __init__(
        self,
        *,
        receiver: Receiver[ReceiverMessageT_co],
        mapping_function: Callable[[ReceiverMessageT_co], MappedMessageT_co],
    ) -> None:
        """Initialize this receiver mapper.

        Args:
            receiver: The input receiver.
            mapping_function: The function to apply on the input data.
        """
        self._receiver: Receiver[ReceiverMessageT_co] = receiver
        """The input receiver."""

        self._mapping_function: Callable[[ReceiverMessageT_co], MappedMessageT_co] = (
            mapping_function
        )
        """The function to apply on the input data."""

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
        return await self._receiver.ready()  # pylint: disable=protected-access

    # We need a noqa here because the docs have a Raises section but the code doesn't
    # explicitly raise anything.
    @override
    def consume(self) -> MappedMessageT_co:  # noqa: DOC502
        """Return a transformed message once `ready()` is complete.

        Returns:
            The next message that was received.

        Raises:
            ReceiverStoppedError: If the receiver stopped producing messages.
            ReceiverError: If there is a problem with the receiver.
        """
        return self._mapping_function(self._receiver.consume())

    @override
    def close(self) -> None:
        """Close the receiver.

        After calling this method, new messages will not be received.  Once the
        receiver's buffer is drained, trying to receive a message will raise a
        [`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError].
        """
        self._receiver.close()

    def __str__(self) -> str:
        """Return a string representation of the mapper."""
        return f"{type(self).__name__}:{self._receiver}:{self._mapping_function}"

    def __repr__(self) -> str:
        """Return a string representation of the mapper."""
        return f"{type(self).__name__}({self._receiver!r}, {self._mapping_function!r})"


class _Sentinel:
    """A sentinel object to represent no value received yet."""

    def __str__(self) -> str:
        """Return a string representation of this sentinel."""
        return "<No message ready to be consumed>"

    def __repr__(self) -> str:
        """Return a string representation of this sentinel."""
        return "<No message ready to be consumed>"


_SENTINEL = _Sentinel()


class _Filter(Receiver[ReceiverMessageT_co], Generic[ReceiverMessageT_co]):
    """Apply a filter function on the messages on a receiver."""

    def __init__(
        self,
        *,
        receiver: Receiver[ReceiverMessageT_co],
        filter_function: Callable[[ReceiverMessageT_co], bool],
    ) -> None:
        """Initialize this receiver filter.

        Args:
            receiver: The input receiver.
            filter_function: The function to apply on the input data.
        """
        self._receiver: Receiver[ReceiverMessageT_co] = receiver
        """The input receiver."""

        self._filter_function: Callable[[ReceiverMessageT_co], bool] = filter_function
        """The function to apply on the input data."""

        self._next_message: ReceiverMessageT_co | _Sentinel = _SENTINEL

        self._recv_closed = False

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
        while await self._receiver.ready():
            message = self._receiver.consume()
            if self._filter_function(message):
                self._next_message = message
                return True
        self._recv_closed = True
        return False

    @override
    def consume(self) -> ReceiverMessageT_co:
        """Return a transformed message once `ready()` is complete.

        Returns:
            The next message that was received.

        Raises:
            ReceiverStoppedError: If the receiver stopped producing messages.
        """
        if self._recv_closed:
            raise ReceiverStoppedError(self)
        assert not isinstance(
            self._next_message, _Sentinel
        ), "`consume()` must be preceded by a call to `ready()`"

        message = self._next_message
        self._next_message = _SENTINEL
        return message

    @override
    def close(self) -> None:
        """Close the receiver.

        After calling this method, new messages will not be received.  Once the
        receiver's buffer is drained, trying to receive a message will raise a
        [`ReceiverStoppedError`][frequenz.channels.ReceiverStoppedError].
        """
        self._receiver.close()

    def __str__(self) -> str:
        """Return a string representation of the filter."""
        return f"{type(self).__name__}:{self._receiver}:{self._filter_function}"

    def __repr__(self) -> str:
        """Return a string representation of the filter."""
        return (
            f"<{type(self).__name__} receiver={self._receiver!r} "
            f"filter={self._filter_function!r} next_message={self._next_message!r}>"
        )
