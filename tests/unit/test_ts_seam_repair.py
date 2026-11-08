"""An unmarked clock restart must keep all media packets in their input order."""


def _timestamp(value, prefix):
    value = round(value * 90000)
    return bytes((prefix | ((value >> 30) & 7) << 1 | 1,
                  value >> 22 & 255, ((value >> 15) & 127) << 1 | 1,
                  value >> 7 & 255, (value & 127) << 1 | 1))


def _packet(pid, cc, pcr=None, pts=None, dts=None, marker=0xA5):
    pusi = pts is not None
    head = bytearray((0x47, 0x40 if pusi else 0, 0, 0x10 | cc))
    head[1] |= pid >> 8 & 31
    head[2] = pid & 255
    if pcr is not None:
        base = round(pcr * 90000)
        head[3] |= 0x20
        head += bytes((7, 0x10, base >> 25 & 255, base >> 17 & 255,
                       base >> 9 & 255, base >> 1 & 255,
                       (base & 1) << 7 | 0x7E, 0))
    if pusi:
        both = dts is not None
        head += b"\x00\x00\x01\xe0\x00\x00\x80" + bytes((0xC0 if both else 0x80, 10 if both else 5))
        head += _timestamp(pts, 0x30 if both else 0x20)
        if both:
            head += _timestamp(dts, 0x10)
    assert len(head) <= 188
    return bytes(head) + bytes((marker,)) * (188 - len(head))


def test_clock_restart_is_stitched_without_losing_packets(resv, monkeypatch, tmp_path):
    monkeypatch.setattr(resv, "TS_SEAM_REPAIR", True)
    video, audio = 256, 257
    packets = [
        _packet(video, 0, 100.0, 100.16, 100.0, 1),
        _packet(audio, 3, pts=100.0, marker=2),
        _packet(video, 1, 100.08, 100.24, 100.08, 3),
        _packet(video, 0, 0.0, 0.04, 0.0, 4),
        _packet(audio, 0, pts=0.004, marker=5),
        _packet(video, 1, 0.08, 0.12, 0.08, 6),
    ]
    gate = resv.TsSeamRepair()
    out = b"".join(gate.feed(packet) for packet in packets)
    assert gate.seams == 1
    assert len(out) == len(packets) * 188
    assert [out[i * 188 + 187] for i in range(len(packets))] == [1, 2, 3, 4, 5, 6]
    assert [out[i * 188 + 3] & 15 for i in (0, 2, 3, 5)] == [0, 1, 2, 3]
    assert [out[i * 188 + 3] & 15 for i in (1, 4)] == [3, 4]
    assert resv.packet_pcr(out, 3 * 188)[1] > resv.packet_pcr(out, 2 * 188)[1]
    assert resv.pes_time(out, 3 * 188 + 12 + 14) > resv.pes_time(out, 2 * 188 + 12 + 14)
    assert "preserving all packets" in (tmp_path / "delaybuf.log").read_text()


def test_a_pcr_blip_without_pes_restart_passes_unchanged(resv, monkeypatch):
    monkeypatch.setattr(resv, "TS_SEAM_REPAIR", True)
    packets = (_packet(256, 0, 100.0, 100.16, 100.0)
               + _packet(256, 1, 100.08, 100.24, 100.08)
               + _packet(256, 2, 0.0, 100.32, 100.16))
    gate = resv.TsSeamRepair()
    assert gate.feed(packets) == packets
    assert gate.seams == 0


def test_repair_is_opt_in(resv):
    packet = _packet(256, 0, 100.0, 100.16, 100.0)
    assert resv.TsSeamRepair().feed(packet) is packet


def test_short_clock_restart_is_repaired_before_rewind_gate(resv, stream, monkeypatch, tmp_path):
    """A 25-second clock epoch must not make the rewind filter drop fresh video."""
    monkeypatch.setattr(resv, "TS_SEAM_REPAIR", True)
    monkeypatch.setattr(resv, "REWIND_SKIP", True)
    data = b"".join((
        _packet(256, 0, 25.0, 25.16, 25.0, 1),
        _packet(256, 1, 25.08, 25.24, 25.08, 2),
        _packet(256, 0, 0.0, 0.04, 0.0, 3),
        _packet(256, 1, 0.08, 0.12, 0.08, 4),
        _packet(256, 2, 0.16, 0.20, 0.16, 5),
    ))
    stream.upstream.pass_rewind(data)
    assert stream.reservoir.in_total == len(data)
    assert stream.parser.pcr_back == 0
    assert stream.upstream.rewind.seam is None
    assert [stream.reservoir.take(0)[0][-1]] == [5]
    log = (tmp_path / "delaybuf.log").read_text()
    assert "TS seam repair: clock reset" in log
    assert "rewind in connection: holding" not in log
