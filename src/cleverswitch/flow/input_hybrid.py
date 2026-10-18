"""Join datagrams, reliable action positions and the existing focus lifecycle.

Every method belongs to the input channel thread. Capture callbacks only append
Packets to its bounded outbox; the discovery thread only appends ciphertext.
"""

from .input_motion import ACTIONS, Motion, PathProbe, Probe, Receipt, Receiver, Reliable, Sender
from .input_protocol import Kind, Packet, Routed


class MotionLane:
    def __init__(self, codec, datagram, reliable, deliver, focus):
        self.codec, self.datagram, self.reliable = codec, datagram, reliable
        self.deliver, self.focus = deliver, focus
        self.probe = PathProbe(codec, self._datagram)
        self.receiver = Receiver(codec, self._datagram, deliver)
        self.sender = None
        self.fast_sender = False
        self.sent = self.received = 0
        self.received_coalesced = 0

    def _datagram(self, raw):
        self.datagram(raw)
        self.sent += 1

    def _reliable(self, frame):
        values = frame.position if frame.kind == Kind.MOVE else frame.values
        self.reliable(Routed(Packet(frame.kind, frame.epoch, values), frame.action, frame.serial, frame.position))

    def start(self, now):
        self.probe.start(now)

    def sync(self):
        phase, epoch = self.focus()
        if phase != "receiving" or self.receiver.epoch != epoch:
            self.receiver.end()

    def _fallback(self):
        if self.fast_sender and self.sender.mode == "reliable":
            # A known failed path stays reliable until a new authenticated
            # connection. A later focus must not repeat a failed UDP search.
            self.probe.state = "unavailable"
            self.fast_sender = False

    def outgoing(self, packet, now):
        self.sync()
        if packet.kind == Kind.ENTER:
            if self.sender is not None:
                self.sender.stop()
            self.reliable(packet)
            self.sender = Sender(
                self.codec, packet.epoch, packet.values, self._datagram, self._reliable, probe=self.probe
            )
            self.fast_sender = self.sender.mode == "datagram"
        elif packet.kind == Kind.MOVE or packet.kind in ACTIONS:
            if self.sender is not None and self.sender.epoch == packet.epoch:
                if packet.kind == Kind.MOVE:
                    # Before ACCEPT, motion follows ENTER on TLS so a UDP
                    # packet cannot outrun focus and cost a retry round trip.
                    self.sender.move(packet.values, now, reliable=self.focus()[0] == "requesting")
                else:
                    self.sender.send_action(packet.kind, *packet.values)
                self._fallback()
        else:
            if packet.kind in (Kind.LEAVE, Kind.RESET) and self.sender is not None:
                if packet.epoch == self.sender.epoch:
                    self.sender.stop()
            self.reliable(packet)

    def receive_reliable(self, packet):
        self.sync()
        if isinstance(packet, Routed):
            values = () if packet.kind == Kind.MOVE else packet.packet.values
            self.receiver.receive_reliable(
                Reliable(packet.kind, packet.epoch, packet.action, packet.serial, packet.position, values)
            )
        elif packet.kind == Kind.MOVE or packet.kind in ACTIONS:
            raise ValueError("Input position barrier is missing")
        else:
            self.deliver(packet)
            phase, epoch = self.focus()
            cancelled = (
                packet.kind in (Kind.RESET, Kind.REJECT)
                and self.sender is not None
                and packet.epoch == self.sender.epoch
            )
            peer_focus = packet.kind == Kind.ENTER and phase == "receiving" and epoch == packet.epoch
            if self.sender is not None and (cancelled or peer_focus):
                # A peer cancellation supersedes queued source input. A normal
                # local LEAVE is different: its already queued actions must be
                # emitted in order before processing that outgoing boundary.
                self.sender.stop()
            if packet.kind == Kind.ENTER and phase == "receiving" and epoch == packet.epoch:
                if self.receiver.epoch != epoch:
                    self.receiver.end()
                    self.receiver.begin(epoch, packet.values)
            self.sync()

    def decode_datagrams(self, packets):
        """Select fresh motion from one already available, bounded inbox batch.

        Authenticate every datagram before comparing its motion metadata. Keep
        action/epoch boundaries and probe/receipt order. No timer gathers input.
        """
        messages = []
        for raw in packets:
            message = self.codec.open(raw)
            if message is None:
                continue
            self.received += 1
            if (
                isinstance(message, Motion)
                and messages
                and isinstance(messages[-1], Motion)
                and message.epoch == messages[-1].epoch
                and message.after_action == messages[-1].after_action
            ):
                if message.serial > messages[-1].serial:
                    messages[-1] = message
                self.received_coalesced += 1
            else:
                messages.append(message)
        return messages

    def receive_datagram(self, raw, now):
        for message in self.decode_datagrams((raw,)):
            self.receive_message(message, now)

    def receive_message(self, message, now):
        self.sync()
        if isinstance(message, Probe):
            if message.response:
                self.probe.receive_message(message, now)
            else:
                self.receiver.receive_message(message)
        elif isinstance(message, Receipt):
            if self.sender is not None:
                self.sender.receive_message(message, now)
        elif isinstance(message, Motion):
            self.receiver.receive_message(message)

    def tick(self, now):
        self.sync()
        self.probe.tick(now)
        if self.sender is not None:
            phase, epoch = self.focus()
            if phase in ("requesting", "sending") and epoch == self.sender.epoch:
                self.sender.tick(now)
            self._fallback()

    @property
    def deadline(self):
        deadlines = [self.probe.deadline]
        phase, epoch = self.focus()
        if self.sender is not None and phase in ("requesting", "sending") and epoch == self.sender.epoch:
            deadlines.append(self.sender.deadline)
        return min((d for d in deadlines if d is not None), default=None)

    def diagnostic(self):
        return {
            "negotiated": True,
            "path": self.probe.state,
            "sending": self.sender.mode if self.sender is not None else "idle",
            "datagrams_sent": self.sent,
            "datagrams_received": self.received,
            "received_moves_coalesced": self.received_coalesced,
        }

    def close(self):
        self.receiver.end()
        if self.sender is not None:
            self.sender.stop()
        self.probe.state = "closed"
        self.codec.close()
