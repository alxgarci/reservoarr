"""An early source reconnect must preserve the complete buffered TS sequence."""


class Ticks:
    def __init__(self, clock, count, on_tick=None):
        self.clock, self.count, self.on_tick = clock, count, on_tick

    def wait(self, seconds):
        self.clock[0] += seconds
        self.count -= 1
        if self.on_tick:
            self.on_tick()
        return self.count < 0


def packet(number):
    return bytes((0x47, 0x01, 0x00, 0x10 | number % 16)) + bytes((number,)) * 184


def test_gap_recovery_keeps_buffered_packets_in_order(resv, stream, monkeypatch, tmp_path):
    up = stream.upstream
    clock = [1000.0]
    monkeypatch.setattr(resv.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(resv, "GAP_RECOVER_S", 6.0)
    aborted = []
    monkeypatch.setattr(resv, "abort_read", lambda response: aborted.append(response))
    first = b"".join(packet(n) for n in (1, 2, 3))
    rest = b"".join(packet(n) for n in (4, 5, 6))
    up.ingest(first)
    up.arrived_total = len(first)
    up.last_arrival = clock[0]
    response = object()
    up.response = response
    up.stop = Ticks(clock, 8)
    up.stall_watchdog()

    assert aborted == [response]
    assert up.force.is_set()
    assert up.take_request() is False  # recovery must not flush the reservoir
    up.ingest(rest)
    assert stream.reservoir.take(0)[0] + stream.reservoir.take(0)[0] == first + rest
    assert "reconnecting early, buffer kept" in (tmp_path / "delaybuf.log").read_text()


def test_gap_recovery_does_not_reconnect_a_healthy_or_empty_source(resv, stream, monkeypatch):
    up = stream.upstream
    clock = [1000.0]
    monkeypatch.setattr(resv.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(resv, "GAP_RECOVER_S", 6.0)
    monkeypatch.setattr(resv, "STALL_S", 0.0)
    up.last_arrival = clock[0]
    up.response = object()
    up.stop = Ticks(clock, 10)
    up.stall_watchdog()
    assert not up.force.is_set()  # no buffered data to preserve

    up.ingest(packet(1))
    up.arrived_total = 188
    up.last_arrival = clock[0]
    up.stop = Ticks(clock, 10, on_tick=lambda: setattr(up, "arrived_total", up.arrived_total + 188))
    up.stall_watchdog()
    assert not up.force.is_set()  # bytes keep arriving
