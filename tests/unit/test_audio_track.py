"""RESV_AUDIO_TRACK selects one source audio track without changing the default."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

RESERVOIR_PATH = Path(__file__).resolve().parent.parent.parent / "reservoarr.py"


def load(tmp_path, **env):
    os.environ["RESV_LOG_DIR"] = str(tmp_path)
    os.environ.update(env)
    sys.argv = ["reservoarr", "http://offline/unit-test"]
    try:
        spec = importlib.util.spec_from_file_location("resv_audio_track", str(RESERVOIR_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for key in env:
            os.environ.pop(key, None)
    return mod


def audio_map(cmd):
    maps = [cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token == "-map"]
    return next(value for value in maps if value.startswith("0:a:"))


def journal(resv):
    path = Path(resv.LOG_FILE)
    return path.read_text() if path.exists() else ""


def test_default_still_selects_the_first_audio_track(tmp_path):
    assert audio_map(load(tmp_path).FFMPEG_CMD) == "0:a:0"


@pytest.mark.parametrize(("track", "stream_spec"), [("1", "0:a:0"), ("3", "0:a:2"), ("12", "0:a:11")])
def test_track_number_is_one_based(tmp_path, track, stream_spec):
    assert audio_map(load(tmp_path, RESV_AUDIO_TRACK=track).FFMPEG_CMD) == stream_spec


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "three", "3x", "+3", "  "])
def test_invalid_track_falls_back_to_the_first(tmp_path, value):
    assert audio_map(load(tmp_path, RESV_AUDIO_TRACK=value).FFMPEG_CMD) == "0:a:0"


def test_non_default_selection_is_logged(tmp_path, capsys):
    resv = load(tmp_path, RESV_AUDIO_TRACK="3")

    resv.announce_audio_track()

    assert "selecting source audio track 3 (ffmpeg map 0:a:2)" in capsys.readouterr().err
    assert "selecting source audio track 3" in journal(resv)


def test_invalid_selection_is_logged(tmp_path, capsys):
    resv = load(tmp_path, RESV_AUDIO_TRACK="zero")

    resv.announce_audio_track()

    assert "RESV_AUDIO_TRACK='zero'" in capsys.readouterr().err
    assert "selecting source audio track 1" in journal(resv)


def test_empty_selection_logs_nothing(tmp_path, capsys):
    resv = load(tmp_path, RESV_AUDIO_TRACK="")

    resv.announce_audio_track()

    assert "audio track" not in capsys.readouterr().err
    assert "audio track" not in journal(resv)


def test_timeline_offset_keeps_the_selected_track(tmp_path):
    resv = load(tmp_path, RESV_AUDIO_TRACK="3")

    cmd = resv.ffmpeg_cmd(12.5)

    assert audio_map(cmd) == "0:a:2"
    assert cmd[-3:] == ["-output_ts_offset", "12.500000", "pipe:1"]
