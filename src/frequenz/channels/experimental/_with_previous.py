# License: MIT
# Copyright © 2024 Frequenz Energy-as-a-Service GmbH

"""Composable predicate to cache and compare with the previous message."""


from typing import Callable, Final, Generic, TypeGuard

from frequenz.channels._generic import ChannelMessageT


class _Sentinel:
    """A sentinel to denote that no value has been received yet."""

    def __str__(self) -> str:
        """Return a string representation of this sentinel."""
        return "<no value received yet>"


_SENTINEL: Final[_Sentinel] = _Sentinel()


class WithPrevious(Generic[ChannelMessageT]):
    """A composable predicate to build predicates that can use also the previous message.

    This predicate can be used to filter messages based on a custom condition on the
    previous and current messages. This can be useful in cases where you want to
    process messages only if they satisfy a particular condition with respect to the
    previous message.

    Example: Receiving only messages that are different from the previous one.
        ```python
        from frequenz.channels import Broadcast
        from frequenz.channels.experimental import WithPrevious

        channel = Broadcast[int](name="example")
        receiver = channel.new_receiver().filter(WithPrevious(lambda old, new: old != new))
        sender = channel.new_sender()

        # This message will be received as it is the first message.
        await sender.send(1)
        assert await receiver.receive() == 1

        # This message will be skipped as it equals to the previous one.
        await sender.send(1)

        # This message will be received as it is a different from the previous one.
        await sender.send(0)
        assert await receiver.receive() == 0
        ```

    Example: Receiving only messages if they are bigger than the previous one.
        ```python
        from frequenz.channels import Broadcast
        from frequenz.channels.experimental import WithPrevious

        channel = Broadcast[int](name="example")
        receiver = channel.new_receiver().filter(
            WithPrevious(lambda old, new: new > old, first_is_true=False)
        )
        sender = channel.new_sender()

        # This message will skipped as first_is_true is False.
        await sender.send(1)

        # This message will be received as it is bigger than the previous one (1).
        await sender.send(2)
        assert await receiver.receive() == 2

        # This message will be skipped as it is smaller than the previous one (1).
        await sender.send(0)

        # This message will be skipped as it is not bigger than the previous one (0).
        await sender.send(0)

        # This message will be received as it is bigger than the previous one (0).
        await sender.send(1)
        assert await receiver.receive() == 1

        # This message will be received as it is bigger than the previous one (1).
        await sender.send(2)
        assert await receiver.receive() == 2
    """

    def __init__(
        self,
        predicate: Callable[[ChannelMessageT, ChannelMessageT], bool],
        /,
        *,
        first_is_true: bool = True,
    ) -> None:
        """Initialize this instance.

        Args:
            predicate: A callable that takes two arguments, the previous message and the
                current message, and returns a boolean indicating whether the current
                message should be received.
            first_is_true: Whether the first message should be considered as satisfying
                the predicate. Defaults to `True`.
        """
        self._predicate = predicate
        self._last_message: ChannelMessageT | _Sentinel = _SENTINEL
        self._first_is_true = first_is_true

    def __call__(self, message: ChannelMessageT) -> bool:
        """Return whether `message` is the first one received or different from the previous one."""

        def is_message(
            value: ChannelMessageT | _Sentinel,
        ) -> TypeGuard[ChannelMessageT]:
            return value is not _SENTINEL

        old_message = self._last_message
        self._last_message = message
        if is_message(old_message):
            return self._predicate(old_message, message)
        return self._first_is_true

    def __str__(self) -> str:
        """Return a string representation of this instance."""
        return f"{type(self).__name__}:{self._predicate.__name__}"

    def __repr__(self) -> str:
        """Return a string representation of this instance."""
        return f"<{type(self).__name__}: {self._predicate!r} first_is_true={self._first_is_true!r}>"
