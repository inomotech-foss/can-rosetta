"""Tests for the speaker chirps: WAV synthesis and backend gating (no sound)."""

import wave

from canrosetta_edge import audio
from canrosetta_edge.config import EdgeConfig


def test_chirp_wavs_are_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.tempfile, "gettempdir", lambda: str(tmp_path))
    for name in ("ready", "connected"):
        path = audio.chirp_path(name)
        with wave.open(path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # 16-bit
            assert wf.getframerate() == 44100
            assert wf.getnframes() > 0
        # cached: a second call reuses the same file
        assert audio.chirp_path(name) == path


def test_ready_chirp_longer_than_connected(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.tempfile, "gettempdir", lambda: str(tmp_path))
    with wave.open(audio.chirp_path("ready"), "rb") as ready, \
            wave.open(audio.chirp_path("connected"), "rb") as connected:
        assert ready.getnframes() > connected.getnframes()


def test_player_prefers_audio_play_on_autopi(monkeypatch):
    monkeypatch.setattr(audio, "_looks_like_autopi", lambda: True)
    monkeypatch.setattr(audio.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    assert audio._player() == ["/usr/bin/salt-call", "--local", "audio.play"]


def test_player_none_off_autopi_without_opt_in(monkeypatch):
    monkeypatch.setattr(audio, "_looks_like_autopi", lambda: False)
    monkeypatch.delenv("CANROSETTA_CHIRP", raising=False)
    assert audio._player() is None


def test_chirps_are_noops_when_disabled(monkeypatch):
    consulted = []
    monkeypatch.setattr(audio, "_player", lambda: consulted.append("player") or None)
    cfg = EdgeConfig(chirp=False)
    audio.chirp_ready(cfg)
    audio.chirp_connected(cfg)
    assert consulted == []  # the backend is never even consulted


def test_chirps_are_noops_off_autopi(monkeypatch):
    # no backend -> no thread is spawned, so no subprocess can ever run
    monkeypatch.setattr(audio, "_looks_like_autopi", lambda: False)
    monkeypatch.delenv("CANROSETTA_CHIRP", raising=False)
    played = []
    monkeypatch.setattr(audio, "_play", lambda cmd, name: played.append(name))
    audio.chirp_ready(EdgeConfig())
    audio.chirp_connected(EdgeConfig())
    assert played == []
