"""Compatibility imports for the original laboratory and its reproducible model."""

import secrets

from cleverswitch.flow.input_motion import (
    ACK as ACK,
)
from cleverswitch.flow.input_motion import (
    ACTIONS as ACTIONS,
)
from cleverswitch.flow.input_motion import (
    HEADER as HEADER,
)
from cleverswitch.flow.input_motion import (
    MAX_COUNTER as MAX_COUNTER,
)
from cleverswitch.flow.input_motion import (
    MOTION as MOTION,
)
from cleverswitch.flow.input_motion import (
    Codec as Codec,
)
from cleverswitch.flow.input_motion import (
    Motion as Motion,
)
from cleverswitch.flow.input_motion import (
    PathProbe as PathProbe,
)
from cleverswitch.flow.input_motion import (
    Probe as Probe,
)
from cleverswitch.flow.input_motion import (
    Receipt as Receipt,
)
from cleverswitch.flow.input_motion import (
    Receiver as Receiver,
)
from cleverswitch.flow.input_motion import (
    Reliable as Reliable,
)
from cleverswitch.flow.input_motion import (
    Sender as Sender,
)
from cleverswitch.flow.input_motion import (
    counter as counter,
)


def lab_codecs():
    """Fresh synthetic in-memory identities, never the user pairing."""
    secret, session_id = secrets.token_bytes(32), secrets.token_bytes(16)
    return tuple(Codec(secret, session_id, role) for role in ("windows", "mac"))
