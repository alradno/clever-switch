"""One persistent TLS connection, with one owner for all OpenSSL operations.

No native input is imported here. The bounded queue never blocks a capture
callback. TLS is established/pinned by Link before constructing a Channel.
"""

import select
import socket
import ssl
import threading
import time

from .input_hybrid import MotionLane
from .input_motion_link import bootstrap
from .input_protocol import Decoder, Kind, Outbox, Packet, replace_motion
from .input_timing import Duration


class Channel:
    def __init__(
        self, connection, on_packet, on_close, on_tick=None, active_interval=0.1, active_timeout=0.6,
        *, motion_link=None, focus=None, timed_clicks=False,
    ):
        if not 0.01 <= active_interval < active_timeout <= 5:
            raise ValueError("Plazos de entrada inválidos")
        self.connection, self.on_packet, self.on_close = connection, on_packet, on_close
        self.on_tick = on_tick or (lambda: None)
        self.interval, self.timeout = active_interval, active_timeout
        self.outbox = Outbox()
        self.wirebox = Outbox() if motion_link is not None else self.outbox
        self.motion_link, self.focus = motion_link, focus
        self.timed_clicks = timed_clicks
        self.negotiation = self.lane = self.inbox = self.transport = None
        self._start_lock = threading.RLock()
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.ready = threading.Event()
        self.active = threading.Event()
        self.wake_reader, self.wake_writer = socket.socketpair()
        self.wake_reader.setblocking(False)
        self.wake_writer.setblocking(False)
        self.worker = None
        self.failure = None
        self.sent = self.received = 0
        self.writes = 0
        self.received_coalesced = 0
        self._timing = None
        self._finalized = False
        self._finalize_lock = threading.Lock()
        self.routing_duration = Duration()
        self.receiving_duration = Duration()
        self.write_duration = Duration()

    def start(self):
        with self._start_lock:
            if self.worker is not None or self.stop.is_set():
                raise RuntimeError("Canal de entrada ya iniciado o cerrado")
            try:
                if self.motion_link is not None:
                    self.transport = self.motion_link.motion_transport
                    if self.transport is None or not self.transport.motion_ready() or self.focus is None:
                        raise ConnectionError("Transporte de movimiento no disponible")
                    self.negotiation = bootstrap(self.connection, self.motion_link)
                    peer = self.connection.getpeername()[0], self.transport.peer_port
                    self.inbox = self.transport.motion.register(
                        self.negotiation.codec, peer, self.motion_link.config["network"], self._wake,
                        testing=self.motion_link.pairing.testing,
                    )
                    self.lane = MotionLane(
                        self.negotiation.codec, lambda raw: self.transport.send_motion(raw, peer),
                        self.wirebox.put, self.on_packet, self.focus,
                    )
                if self.stop.is_set():
                    raise ConnectionError("Canal de entrada cancelado")
                self.connection.setblocking(False)
                worker = threading.Thread(target=self._run, name="flow-input-tls", daemon=True)
                worker.start()
                self.worker = worker
            except Exception:
                self._finalize()
                raise

    def _wake(self):
        try:
            self.wake_writer.send(b"x")
        except (BlockingIOError, OSError):
            pass  # A full pipe already guarantees a wakeup.

    def set_active(self, active):
        if self.active.is_set() != bool(active):
            self.active.set() if active else self.active.clear()
            self._wake()

    def send(self, packet):
        try:
            wake = self.outbox.put(packet)
        except (BufferError, ConnectionError):
            self.failure = "backpressure"
            self.stop.set()
            self._wake()
            raise
        if wake:
            self._wake()

    def _round_trip(self, seconds, now):
        # One immutable aggregate, no samples or event payloads retained. This
        # measures queueing + TLS + peer scheduling, not physical input latency.
        if self._timing is None:
            self._timing = (1, seconds, seconds, seconds, seconds, 0.0, now)
        else:
            count, last, low, high, total, variation, _ = self._timing
            self._timing = (
                count + 1,
                seconds,
                min(low, seconds),
                max(high, seconds),
                total + seconds,
                variation + abs(seconds - last),
                now,
            )

    def timing(self):
        sample = self._timing
        if sample is None:
            return None
        count, last, low, high, total, variation, checked = sample
        return {
            "samples": count,
            "latest_ms": round(last * 1000, 3),
            "minimum_ms": round(low * 1000, 3),
            "average_ms": round(total / count * 1000, 3),
            "maximum_ms": round(high * 1000, 3),
            "mean_variation_ms": round(variation / max(1, count - 1) * 1000, 3),
            "age_seconds": round(max(0, time.monotonic() - checked), 3),
        }

    def _read_packets(self, decoder):
        packets, total, needs_write = [], 0, False
        for _ in range(64):
            # Drain already available TLS records, without waiting for another
            # packet. One write often becomes one record; recv(8192) alone does
            # not combine them. Bound work even if the peer sends continuously.
            pending = self.connection.pending() if hasattr(self.connection, "pending") else 0
            if total and not pending and not select.select([self.connection], [], [], 0)[0]:
                break
            try:
                raw = self.connection.recv(min(8192, 16384 - total))
            except (ssl.SSLWantReadError, BlockingIOError):
                break
            except ssl.SSLWantWriteError:
                needs_write = True
                break
            if not raw:
                raise ConnectionError
            total += len(raw)
            for packet in decoder.feed(raw):
                if replace_motion(packets, packet):
                    self.received_coalesced += 1
                else:
                    packets.append(packet)
            if total >= 16384:
                break
        return packets, needs_write

    def performance(self):
        return {
            "routing_and_datagrams": self.routing_duration.snapshot(),
            "tls_read_and_delivery": self.receiving_duration.snapshot(),
            "tls_write": self.write_duration.snapshot(),
            "datagrams_dropped_at_receiver": self.inbox.dropped if self.inbox is not None else 0,
        }

    def _run(self):
        decoder = Decoder(routed=self.lane is not None, timed=self.timed_clicks)
        tx = b""
        tx_count = 0
        sequence = ping = 0
        pending_ping = None
        was_active = False
        now = time.monotonic()
        next_ping, last_rx, active_since = now + 15, now, now
        write_waits_read = read_waits_write = False
        try:
            if self.lane is not None:
                self.lane.start(now)
            self.ready.set()
            while not self.stop.is_set():
                now = time.monotonic()
                active = self.active.is_set()
                if active != was_active:
                    was_active, active_since = active, now
                    next_ping = now if active else now + 15
                    if active and pending_ping:
                        pending_ping = pending_ping[0], min(pending_ping[1], now + self.timeout), pending_ping[2]
                self.on_tick()
                if self.stop.is_set():
                    break
                if self.lane is not None:
                    for message in self.lane.decode_datagrams(self.inbox.take()):
                        if self.stop.is_set():
                            break
                        self.lane.receive_message(message, time.monotonic())
                    self.lane.tick(time.monotonic())
                    for packet in self.outbox.take():
                        self.lane.outgoing(packet, time.monotonic())
                # Routing, decoding and native delivery can consume this turn.
                # A retry deadline is absolute: do not add that work's duration
                # again to the socket wait or grant a stale liveness deadline.
                completed = time.monotonic()
                self.routing_duration.observe(completed - now)
                now = completed
                if (pending_ping and now >= pending_ping[1]) or (
                    active and now - max(last_rx, active_since) >= self.timeout
                ):
                    raise TimeoutError
                if now >= next_ping and pending_ping is None:
                    ping += 1
                    self.send(Packet(Kind.PING, ping))
                    pending_ping = ping, now + (self.timeout if active else 45), now
                    next_ping = now + (self.interval if active else 15)
                # Heartbeats only run at the interactive rate during a focus
                # transfer. An idle connected session sleeps for up to 15 s.
                deadlines = [now + (self.interval if active else 15)]
                deadlines.append(pending_ping[1] if pending_ping else next_ping)
                if self.lane is not None:
                    if self.lane.deadline is not None:
                        deadlines.append(self.lane.deadline)
                    if self.outbox.pending():
                        deadlines.append(now)
                timeout = max(0, min(deadlines) - time.monotonic())
                pending = self.connection.pending() if hasattr(self.connection, "pending") else 0
                wants_write = (tx or self.wirebox.pending()) and not write_waits_read
                readable, writable, _ = select.select(
                    [self.connection, self.wake_reader],
                    [self.connection] if wants_write or read_waits_write else [],
                    [],
                    0 if pending else timeout,
                )
                if self.wake_reader in readable:
                    try:
                        self.wake_reader.recv(8192)
                    except BlockingIOError:
                        pass
                can_read = self.connection in readable or pending or (read_waits_write and self.connection in writable)
                if can_read:
                    reading_started = time.monotonic()
                    try:
                        packets, read_waits_write = self._read_packets(decoder)
                        for packet in packets:
                            if self.stop.is_set():
                                break
                            last_rx = time.monotonic()
                            if packet.kind == Kind.PING:
                                self.send(Packet(Kind.PONG, packet.epoch))
                            elif packet.kind == Kind.PONG:
                                if pending_ping and packet.epoch == pending_ping[0]:
                                    self._round_trip(last_rx - pending_ping[2], last_rx)
                                    pending_ping = None
                            else:
                                self.received += 1
                                if self.lane is None:
                                    self.on_packet(packet)
                                else:
                                    self.lane.receive_reliable(packet)
                    except (ssl.SSLWantReadError, BlockingIOError):
                        read_waits_write = False
                    except ssl.SSLWantWriteError:
                        read_waits_write = True
                    finally:
                        self.receiving_duration.observe(time.monotonic() - reading_started)
                if self.stop.is_set():
                    break
                if not tx and self.connection in writable:
                    # Keep unsent motion coalescible while the socket is busy.
                    # Freeze a batch only just before its first write; after a
                    # TLS retry or partial write the exact bytes must survive.
                    items = self.wirebox.take()
                    tx_count = len(items)
                    tx = b"".join(item.encode(sequence + i) for i, item in enumerate(items, 1))
                    sequence += tx_count
                if tx and (self.connection in writable or (write_waits_read and self.connection in readable)):
                    write_started = time.monotonic()
                    try:
                        count = self.connection.send(tx)
                        if not count:
                            raise ConnectionError
                        self.writes += 1
                        tx = tx[count:]
                        write_waits_read = False
                        if not tx:
                            self.sent += tx_count
                    except ssl.SSLWantReadError:
                        write_waits_read = True
                    except (ssl.SSLWantWriteError, BlockingIOError):
                        write_waits_read = False
                    finally:
                        self.write_duration.observe(time.monotonic() - write_started)
        except TimeoutError:
            self.failure = "timeout"
        except (ValueError, BufferError):
            self.failure = "protocol"
        except Exception:
            self.failure = self.failure or "connection"
        finally:
            decoder.clear()
            tx = b""
            self._finalize()

    def _finalize(self):
        with self._finalize_lock:
            if self._finalized:
                return
            self._finalized = True
        self.stop.set()
        self.ready.clear()
        self.outbox.close()
        self.wirebox.close()
        if self.inbox is not None:
            self.transport.motion.unregister(self.inbox)
        if self.lane is not None:
            self.lane.close()
        if self.negotiation is not None:
            self.negotiation.close()
        for sock in (self.connection, self.wake_reader, self.wake_writer):
            try:
                sock.close()
            except OSError:
                pass
        try:
            self.on_close()
        except Exception:
            self.failure = "cleanup"
        finally:
            self.finished.set()

    def close(self):
        self.request_close()
        with self._start_lock:
            worker = self.worker
            if worker is None:
                self._finalize()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2)
            if worker.is_alive():
                raise RuntimeError("El trabajador de entrada no ha terminado")

    def request_close(self):
        """Safe from a native callback: never join another thread here."""
        self.stop.set()
        self._wake()
