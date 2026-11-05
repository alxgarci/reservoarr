"""In-connection overlap replay is removed before it reaches the reservoir."""
from __future__ import annotations

import pytest

PID = 0x100


def packet(pid, pcr=None):
    if pcr is None:
        return bytes([0x47, pid >> 8 & 0x1F, pid & 0xFF, 0x10]) + b"\xab" * 184
    base = round(pcr * 90000)
    pcr_bytes = bytes([
        base >> 25 & 0xFF, base >> 17 & 0xFF, base >> 9 & 0xFF, base >> 1 & 0xFF,
        (base & 1) << 7 | 0x7E, 0x00,
    ])
    return (bytes([0x47, pid >> 8 & 0x1F, pid & 0xFF, 0x30, 7, 0x10]) + pcr_bytes
            + b"\xcd" * 176)


def ts_stream(start, end, step=0.1):
    out = [packet(0)]
    ticks = round((end - start) / step)
    for k in range(ticks + 1):
        out.append(packet(PID, round(start + k * step, 3)))
        out.extend(packet(PID) for _ in range(3))
    return b"".join(out)


def pcr_offset(data, value):
    return data.index(packet(PID, value))


@pytest.fixture
def enabled(resv, stream):
    resv.REWIND_SKIP = True

    def send(data):
        stream.upstream.pass_rewind(data)

    return send


def test_rewind_after_sync_loss_is_dropped_before_parser(resv, stream, enabled, tmp_path):
    normal = ts_stream(100.0, 140.0)
    enabled(normal)
    before = stream.reservoir.in_total

    replay = b"\x00" + ts_stream(110.0, 145.0)
    enabled(replay)

    expected_tail = replay[pcr_offset(replay, 140.1):]
    assert stream.reservoir.in_total == before + 1 + 188 + len(expected_tail)
    assert stream.parser.pcr_back == 0
    log = (tmp_path / "delaybuf.log").read_text()
    assert "holding 30.0s overlap candidate" in log
    assert "skipped 30.0s" in log


def test_nested_replays_are_all_removed(resv, stream, enabled, tmp_path):
    enabled(ts_stream(100.0, 140.0))
    before = stream.reservoir.in_total
    incoming = (b"\x00" + ts_stream(110.0, 142.0)
                + ts_stream(110.0, 135.0)
                + ts_stream(110.0, 145.0))

    enabled(incoming)

    # Only the corrupt lead byte, the PAT before the first repeated pass,
    # 140.1..142.0 from the first pass and 142.1..145.0 from the final pass
    # are new relative to the already admitted timeline.
    first_new = incoming[pcr_offset(incoming, 140.1):]
    second_rewind = first_new.index(packet(PID, 110.0))
    first_tail = first_new[:second_rewind]
    final_new = incoming[incoming.rindex(packet(PID, 142.1)):]
    expected = 1 + 188 + len(first_tail) + len(final_new)
    assert stream.reservoir.in_total == before + expected
    assert stream.parser.pcr_back == 0
    log = (tmp_path / "delaybuf.log").read_text()
    assert log.count("rewind in connection: skipped") == 2
    assert "repeated across 2 passes" in log


def test_disabled_gate_passes_the_replay_to_parser(resv, stream):
    resv.REWIND_SKIP = False
    stream.upstream.pass_rewind(ts_stream(100.0, 140.0))
    stream.upstream.pass_rewind(ts_stream(110.0, 145.0))
    assert stream.parser.pcr_back > 0
