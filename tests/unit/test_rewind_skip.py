"""PCR-only in-connection rewind gate: byte-exact simulations."""
import pytest

PID = 0x100


def packet(pcr, disc=False):
    base = round(pcr * 90000)
    clock = bytes([base >> 25 & 255, base >> 17 & 255, base >> 9 & 255,
                   base >> 1 & 255, (base & 1) << 7 | 0x7e, 0])
    return bytes([0x47, 1, 0, 0x30, 7, 0x90 if disc else 0x10]) + clock + b"x" * 176


def span(start, end, step=0.1, disc=False):
    count = round((end - start) / step)
    filler = (b"\x47\x01\x00\x10" + b"y" * 184) * 70
    return b"".join(packet(round(start + n * step, 3), disc and n == 0) + filler
                    for n in range(count + 1))


def feed(stream, data, chunk=94000):
    for i in range(0, len(data), chunk):
        stream.upstream.pass_rewind(data[i:i + chunk])


def output(stream):
    return b"".join(stream.reservoir.chunks)


def test_disabled_is_byte_exact(resv, stream, tmp_path):
    resv.REWIND_SKIP = False
    incoming = span(100, 140) + span(120, 145)
    feed(stream, incoming)
    assert output(stream) == incoming
    assert "rewind in connection:" not in (tmp_path / "delaybuf.log").read_text()


@pytest.mark.parametrize("chunk", [94000, 188 * 17])
def test_small_overlap_drops_only_prefix_and_keeps_same_burst_suffix(
        resv, stream, tmp_path, chunk):
    resv.REWIND_SKIP = True
    normal = span(100, 140)
    replay = span(119.2, 145)
    first_new = replay.index(packet(140.1))
    feed(stream, normal, chunk)
    feed(stream, replay, chunk)
    assert output(stream) == normal + replay[first_new:]
    assert stream.parser.pcr_back == 0
    assert stream.upstream.rewind.seam is None
    assert stream.upstream.rewind.seen_bytes == len(normal) + len(replay)
    journal = (tmp_path / "delaybuf.log").read_text()
    assert "holding 20.8s PCR overlap" in journal
    assert "previous_pcr=140.000 current_pcr=119.200" in journal
    assert "hold progress new_input=" in journal
    assert "skipped 20.8s" in journal
    assert "kept post-seam tail" in journal
    assert "resume_pcr=140.100" in journal
    assert "forwarded=" in journal


def test_remote_epoch_is_passed_through(resv, stream, tmp_path):
    resv.REWIND_SKIP = True
    normal, reset = span(360, 387), span(0, 2)
    feed(stream, normal + reset)
    assert output(stream) == normal + reset
    journal = tmp_path / "delaybuf.log"
    assert "reason=beyond 40s gate" in journal.read_text()
    assert "holding" not in journal.read_text()


def test_flagged_discontinuity_is_passed_through(resv, stream, tmp_path):
    resv.REWIND_SKIP = True
    normal, reset = span(100, 140), span(120, 122, disc=True)
    feed(stream, normal + reset)
    assert output(stream) == normal + reset
    journal = tmp_path / "delaybuf.log"
    assert "reason=source flag" in journal.read_text()
    assert "holding" not in journal.read_text()


def test_unresolved_overlap_releases_when_connection_ends(resv, stream, tmp_path):
    resv.REWIND_SKIP = True
    normal, replay = span(100, 140), span(120, 125)
    feed(stream, normal + replay)
    assert stream.upstream.rewind.seam is not None
    feed(stream, stream.upstream.rewind.release_held())
    assert output(stream) == normal + replay
    assert "connection ended before the seam" in (tmp_path / "delaybuf.log").read_text()


def test_unresolved_overlap_fails_open_at_reserve(resv, stream, tmp_path):
    resv.REWIND_SKIP = True
    normal, replay = span(100, 140), span(120, 125)
    feed(stream, normal + replay)
    while stream.reservoir.bytes > len(normal) / 5:
        data, _ = stream.reservoir.take(timeout=0)
        assert data is not None
    feed(stream, span(125.1, 126))
    assert stream.upstream.rewind.bypass
    assert "10s safety reserve" in (tmp_path / "delaybuf.log").read_text()
    assert "filter bypassed until next connection" in (tmp_path / "delaybuf.log").read_text()


def test_enabled_heartbeat_explains_idle_and_bypassed_states(resv, stream, tmp_path):
    resv.REWIND_SKIP = True
    rewind = stream.upstream.rewind
    rewind.on_reconnect(announce=True)
    feed(stream, span(100, 101))
    rewind.last_status_at -= 16
    feed(stream, span(101.1, 102))
    journal = (tmp_path / "delaybuf.log").read_text()
    assert "connection armed; awaiting PCR lock" in journal
    assert "PCR lock pid=256 first_pcr=100.000" in journal
    assert "status=watching" in journal
    assert "scanned_packets=" in journal
    rewind.bypass = True
    rewind.last_status_at -= 16
    feed(stream, span(102.1, 103))
    assert "status=bypassed until reconnect" in (tmp_path / "delaybuf.log").read_text()
