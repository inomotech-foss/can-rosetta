"""Knowledge base: confirm/reject persistence, rejection memory, coverage."""

from __future__ import annotations

from canrosetta.identify import identify_session
from canrosetta.kb import (
    KnowledgeBase,
    apply_rejections,
    coverage,
    platform_of,
    write_annotation,
)
from canrosetta.session import load_session
from canrosetta.synth import generate


def test_confirm_reject_persist_and_reload(tmp_path):
    kb = KnowledgeBase.load(tmp_path / "kb.json")
    kb.confirm("VW Golf", "gps_speed_kmh",
               {"label": "0x3C0[1:3]BEu", "byte_offset": 1}, r=0.999, vehicle="golf-1")
    kb.reject("VW Golf", "obd_coolant_temp", "0x3C0#bit12")
    kb.save()

    reloaded = KnowledgeBase.load(tmp_path / "kb.json")
    assert "gps_speed_kmh" in reloaded.signals("VW Golf")
    assert reloaded.is_rejected("VW Golf", "obd_coolant_temp", "0x3C0#bit12")
    assert not reloaded.is_rejected("VW Golf", "gps_speed_kmh", "0x3C0[1:3]BEu")
    summ = {s["platform"]: s for s in reloaded.summary()}
    assert summ["VW Golf"]["signals"] == 1 and summ["VW Golf"]["vehicles"] == 1


def test_rejection_memory_filters_hypotheses(tmp_path):
    session = load_session(generate(tmp_path / "s", duration_s=60.0))
    result = identify_session(session)
    ref = "gps_speed_kmh"
    top_label = result.per_reference[ref][0].candidate.label

    kb = KnowledgeBase.load(tmp_path / "kb.json")
    kb.reject(platform_of(session), ref, top_label)
    apply_rejections(result, kb, platform_of(session))
    assert all(h.candidate.label != top_label for h in result.per_reference[ref])


def test_coverage_in_unit_interval(tmp_path):
    session = load_session(generate(tmp_path / "s", duration_s=60.0))
    result = identify_session(session)
    cov = coverage(session, result)
    assert cov["dynamic_fields"] > 0
    assert 0.0 <= cov["coverage"] <= 1.0
    assert cov["confirmed_fields"] >= 1  # speed at least


def test_write_annotation(tmp_path):
    write_annotation(tmp_path, "gps_speed_kmh", {"label": "0x3C0[1:3]BEu"}, 0.999)
    import json
    ann = json.loads((tmp_path / "labels" / "annotations.json").read_text())
    assert ann["signals"][0]["reference"] == "gps_speed_kmh"
