# License: MIT
# Copyright © 2024 Frequenz Energy-as-a-Service GmbH

"""A Sender for sending messages to multiple senders.

The `RelaySender` class takes multiple senders and forwards all the messages sent to it,
to the senders it was created with.
"""

import typing

from typing_extensions import override

from .._generic import SenderMessageT_contra
from .._sender import Sender


class RelaySender(typing.Generic[SenderMessageT_contra], Sender[SenderMessageT_contra]):
    """A Sender for sending messages to multiple senders.

    The `RelaySender` class takes multiple senders and forwards all the messages sent to
    it, to the senders it was created with.

    Example:
        ```python
        from frequenz.channels import Broadcast
        from frequenz.channels.experimental import RelaySender

        channel1: Broadcast[int] = Broadcast(name="channel1")
        channel2: Broadcast[int] = Broadcast(name="channel2")

        receiver1 = channel1.new_receiver()
        receiver2 = channel2.new_receiver()

        tee_sender = RelaySender(channel1.new_sender(), channel2.new_sender())

        await tee_sender.send(5)
        assert await receiver1.receive() == 5
        assert await receiver2.receive() == 5
        ```
    """

    def __init__(self, *senders: Sender[SenderMessageT_contra]) -> None:
        """Create a new RelaySender.

        Args:
            *senders: The senders to send messages to.
        """
        self._senders = senders

    @override
    async def send(self, message: SenderMessageT_contra, /) -> None:
        """Send a message.

        Args:
            message: The message to be sent.
        """
        for sender in self._senders:
            await sender.send(message)
