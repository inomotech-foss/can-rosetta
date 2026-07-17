"""OTA updater: version math, official-source guard, and the update flow.

Network + pip are injected, so nothing here touches the network or installs.
"""

import subprocess

import pytest

from canrosetta_edge import updater


def test_parse_and_compare_versions():
    assert updater.parse_version("edge-v1.2.3") == (1, 2, 3)
    assert updater.parse_version("v0.10.0") == (0, 10, 0)
    assert updater.is_newer("0.2.0", "0.1.9")
    assert not updater.is_newer("0.1.0", "0.1.0")


def test_version_status_flags_update(monkeypatch):
    monkeypatch.setattr(updater, "current_version", lambda: "0.1.0")
    st = updater.version_status(fetch_latest=lambda repo: {"tag": "edge-v0.3.0", "version": "0.3.0"})
    assert st.update_available is True
    assert st.latest == "0.3.0"
    st2 = updater.version_status(fetch_latest=lambda repo: {"tag": "edge-v0.1.0", "version": "0.1.0"})
    assert st2.update_available is False


def test_update_refuses_non_official_source():
    with pytest.raises(updater.UpdateError):
        updater.update(target_tag="edge-v0.2.0", repo="evil/fork")


def test_update_refuses_when_disabled():
    with pytest.raises(updater.UpdateError):
        updater.update(target_tag="edge-v0.2.0", allow_remote=False)


def test_update_runs_pip_for_official_tag(monkeypatch):
    monkeypatch.setattr(updater, "current_version", lambda: "0.1.0")
    calls = {}

    def fake_runner(cmd):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    res = updater.update(target_tag="edge-v0.2.0", runner=fake_runner)
    assert res["to"] == "0.2.0" and res["from"] == "0.1.0"
    # it must install the official git spec at the pinned tag
    joined = " ".join(calls["cmd"])
    assert "git+https://github.com/inomotech-foss/can-rosetta@edge-v0.2.0" in joined
    assert "subdirectory=edge/autopi" in joined


def test_update_raises_on_pip_failure():
    def failing(cmd):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    with pytest.raises(updater.UpdateError):
        updater.update(target_tag="edge-v0.2.0", runner=failing)


def test_update_latest_resolves_tag(monkeypatch):
    monkeypatch.setattr(updater, "current_version", lambda: "0.1.0")
    seen = {}

    def fake_runner(cmd):
        seen["cmd"] = " ".join(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    res = updater.update(target_tag=None,
                         fetch_latest=lambda repo: {"tag": "edge-v0.5.0", "version": "0.5.0"},
                         runner=fake_runner)
    assert res["to"] == "0.5.0"
    assert "edge-v0.5.0" in seen["cmd"]
