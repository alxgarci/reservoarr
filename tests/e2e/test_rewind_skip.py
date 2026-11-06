"""Exercise the production rewind gate on a real H.264/AC3 MPEG-TS video."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest


def pcr_points(resv, data):
    points = []
    i = resv.ts_sync(data, 0)
    while i >= 0 and i + 188 <= len(data):
        if data[i] != 0x47:
            i = resv.ts_sync(data, i + 1)
            continue
        found = resv.packet_pcr(data, i)
        if found:
            points.append((i, found[1]))
        i += 188
    return points


def offset_after(points, seconds):
    target = points[0][1] + seconds
    return next(offset for offset, pcr in points if pcr >= target)


@pytest.mark.e2e
def test_real_video_repeated_spans_are_removed_and_logged(resv, stream, synth_ts, tmp_path):
    raw = synth_ts.read_bytes()
    points = pcr_points(resv, raw)

    def at(seconds):
        return offset_after(points, seconds)

    # One unflagged, 20-second PCR overlap with new content in the same burst.
    # This is the exact narrow case the gate handles.
    regressed = raw[:at(60)] + raw[at(40):at(75)]

    unfiltered = resv.TsParser()
    for start in range(0, len(regressed), resv.CHUNK):
        unfiltered.feed(regressed[start:start + resv.CHUNK])
    assert unfiltered.pcr_back > 0
    # Keep the journal assertion below about the gate itself; the control
    # parser deliberately logged the regressions it was meant to demonstrate.
    (tmp_path / "delaybuf.log").unlink(missing_ok=True)

    resv.REWIND_SKIP = True
    for start in range(0, len(regressed), resv.CHUNK):
        stream.upstream.pass_rewind(regressed[start:start + resv.CHUNK])
    assert stream.upstream.rewind.seam is None
    assert stream.parser.pcr_back == 0
    assert stream.reservoir.in_total < len(regressed)

    filtered = b"".join(stream.reservoir.chunks)
    output = tmp_path / "rewind-filtered.ts"
    output.write_bytes(filtered)
    ffprobe = os.getenv("FFPROBE_BIN") or shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe not found")
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(output)],
        text=True, capture_output=True, check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert {"video", "audio"}.issubset(set(probe.stdout.splitlines()))

    journal = (tmp_path / "delaybuf.log").read_text()
    assert "rewind in connection: holding" in journal
    assert "rewind in connection: skipped" in journal
    assert "kept post-seam tail" in journal
