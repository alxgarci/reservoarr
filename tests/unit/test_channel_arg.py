"""The optional channelId argument identifies every journal line."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

RESERVOIR_PATH = Path(__file__).resolve().parent.parent.parent / "reservoarr.py"


def load(tmp_path, *args):
    os.environ["RESV_LOG_DIR"] = str(tmp_path)
    previous = os.environ.pop("RESV_TS_CHANNEL", None)
    sys.argv = ["reservoarr", "http://offline/getstream", *args]
    try:
        spec = importlib.util.spec_from_file_location("resv_channel_arg", str(RESERVOIR_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if previous is not None:
            os.environ["RESV_TS_CHANNEL"] = previous
    return mod


def test_channel_argument_drives_timeline_and_log_tag(tmp_path):
    resv = load(tmp_path, "Agent", "31")
    resv.log("example", stderr=False)

    assert resv.TS_CHANNEL == "31"
    assert "[getstream channel=31] example" in (tmp_path / "delaybuf.log").read_text()


def test_channel_is_omitted_when_not_supplied(tmp_path):
    resv = load(tmp_path, "Agent")
    resv.log("example", stderr=False)

    line = (tmp_path / "delaybuf.log").read_text()
    assert "[getstream] example" in line
    assert "channel=" not in line


def test_environment_channel_remains_a_compatibility_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("RESV_TS_CHANNEL", "legacy")
    os.environ["RESV_LOG_DIR"] = str(tmp_path)
    sys.argv = ["reservoarr", "http://offline/getstream", "Agent"]
    spec = importlib.util.spec_from_file_location("resv_channel_env", str(RESERVOIR_PATH))
    resv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resv)

    assert resv.TS_CHANNEL == "legacy"
