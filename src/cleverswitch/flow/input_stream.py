"""One persistent TLS connection, with one owner for all OpenSSL operations.

No native input is imported here. The bounded queue never blocks a capture
callback. TLS is established/pinned by Link before constructing a Channel.
"""

import select
import socket
import ssl
import threading
import time

from .input_protocol import Decoder, Kind, Outbox, Packet


class Channel:
    def __init__(self, connection, on_packet, on_close, on_tick=None, active_interval=0.1, active_timeout=0.6):
        if not 0.01 <= active_interval < active_timeout <= 5:
            raise ValueError("Plazos de entrada inválidos")
        self.connection, self.on_packet, self.on_close = connection, on_packet, on_close
        self.on_tick = on_tick or (lambda: None)
        self.interval, self.timeout = active_interval, active_timeout
        self.outbox = Outbox()
        self.stop = threading.Event()
        self.finished = threading.Event()
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

    def start(self):
        if self.worker is not None or self.stop.is_set():
            raise RuntimeError("Canal de entrada ya iniciado o cerrado")
        self.connection.setblocking(False)
        self.worker = threading.Thread(target=self._run, name="flow-input-tls", daemon=True)
        self.worker.start()

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
                if (
                    packet.kind == Kind.MOVE
                    and packets
                    and packets[-1].kind == Kind.MOVE
                    and packet.epoch == packets[-1].epoch
                ):
                    packets[-1] = packet
                    self.received_coalesced += 1
                else:
                    packets.append(packet)
            if total >= 16384:
                break
        return packets, needs_write

    def _run(self):
        decoder = Decoder()
        tx = b""
        tx_count = 0
        sequence = ping = 0
        pending_ping = None
        was_active = False
        now = time.monotonic()
        next_ping, last_rx, active_since = now + 15, now, now
        write_waits_read = read_waits_write = False
        try:
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
                if (pending_ping and now >= pending_ping[1]) or (
                    active and now - max(last_rx, active_since) >= self.timeout
                ):
                    raise TimeoutError
                if now >= next_ping and pending_ping is None:
                    ping += 1
                    self.send(Packet(Kind.PING, ping))
                    pending_ping = ping, now + (self.timeout if active else 45), now
                    next_ping = now + (self.interval if active else 15)
                if not tx:
                    items = self.outbox.take()
                    tx_count = len(items)
                    tx = b"".join(item.encode(sequence + i) for i, item in enumerate(items, 1))
                    sequence += tx_count
                # Heartbeats only run at the interactive rate during a focus
                # transfer. An idle connected session sleeps for up to 15 s.
                deadlines = [now + (self.interval if active else 15)]
                deadlines.append(pending_ping[1] if pending_ping else next_ping)
                timeout = max(0, min(deadlines) - now)
                pending = self.connection.pending() if hasattr(self.connection, "pending") else 0
                readable, writable, _ = select.select(
                    [self.connection, self.wake_reader],
                    [self.connection] if (tx and not write_waits_read) or read_waits_write else [],
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
                                self.on_packet(packet)
                    except (ssl.SSLWantReadError, BlockingIOError):
                        read_waits_write = False
                    except ssl.SSLWantWriteError:
                        read_waits_write = True
                if self.stop.is_set():
                    break
                if tx and (self.connection in writable or (write_waits_read and self.connection in readable)):
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
        self.outbox.close()
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
        if self.worker is None:
            self._finalize()
        elif self.worker is not threading.current_thread():
            self.worker.join(timeout=2)
            if self.worker.is_alive():
                raise RuntimeError("El trabajador de entrada no ha terminado")

    def request_close(self):
        """Safe from a native callback: never join another thread here."""
        self.stop.set()
        self._wake()
