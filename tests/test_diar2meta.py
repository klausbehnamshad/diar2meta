"""Tests for diar2meta. Synthetic data only."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import diar2meta as d  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
SCHEMA = json.loads(d.SCHEMA_PATH.read_text(encoding="utf-8"))

TOPICS = [
    ("Wo sind Sie aufgewachsen?",
     "Ich bin in einem Dorf aufgewachsen. Der Bauernhof hatte Kühe und Schweine. "
     "Auf dem Bauernhof halfen alle Kinder. Die Kühe mussten morgens gemolken werden."),
    ("Wie war die Schule für Sie?",
     "Die Schule lag im Nachbarort. Der Lehrer war streng. In der Schule lernten wir Rechnen. "
     "Der Schulweg dauerte eine Stunde. Der Lehrer kannte alle Familien."),
    ("Wie begann Ihre Arbeit im Stahlwerk?",
     "Im Stahlwerk begann ich mit sechzehn. Die Schichtarbeit im Stahlwerk war hart. "
     "Am Hochofen war es heiß. Die Kollegen am Hochofen kamen aus Italien."),
]


def synthetic_diar2(swap_roles=False, topic_s=400.0, consent=True, intro=None):
    """diar2-like JSON: per topic one question, then answers for topic_s seconds."""
    asker, answerer = ("Interviewee", "Interviewer") if swap_roles else ("Interviewer", "Interviewee")
    sentences, t = [], 0.0

    def say(speaker, text, dur):
        nonlocal t
        sentences.append({"i": len(sentences), "start": t, "end": t + dur, "speaker": speaker, "text": text})
        t += dur + 0.3

    if intro:
        say(asker, intro, 5)
    if consent:
        say(asker, "Sind Sie einverstanden, dass wir das Gespräch aufnehmen?", 4)
        say(answerer, "Ja, ich bin einverstanden.", 2)
    for q, a in TOPICS:
        start = t
        say(asker, q, 3)
        while t - start < topic_s:
            say(answerer, a, 20)
            say(asker, "Mhm, und dann?", 1.5)
    return {"tool": "diar2-merge 0.1.0", "meta": {"input": "synthetisch.wav"}, "language": "de",
            "speakers": [{"label": "speaker_1", "name": "Interviewer"},
                         {"label": "speaker_2", "name": "Interviewee"}],
            "words": [], "sentences": sentences, "turns": [], "hoerliste": []}


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    for k in ("DIAR2META_INSTITUTION", "DIAR2META_INTERVIEWER", "DIAR2META_PSEUDONYM_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DIAR2META_REC_DIRS", str(tmp_path / "keine-aufnahmen"))


def run(tmp_path, src, **kw):
    out = tmp_path / "out"
    status, draft = d.process(src, out, **kw)
    name = draft.name.replace(".imm.draft.json", "")
    return (status, json.loads(draft.read_text(encoding="utf-8")),
            json.loads((draft.parent / f"{name}.imm.provenance.json").read_text(encoding="utf-8")),
            (draft.parent / f"{name}.pruefliste.txt").read_text(encoding="utf-8"))


# --- validator agrees with the IMM-Core fixtures -------------------------------------

@pytest.mark.parametrize("path", sorted(FIX.glob("*.json")), ids=lambda p: p.name)
def test_valid_fixtures_pass(path):
    assert d.validate(json.loads(path.read_text(encoding="utf-8")), SCHEMA) == []


@pytest.mark.parametrize("path", sorted((FIX / "invalid").glob("*.json")), ids=lambda p: p.name)
def test_invalid_fixtures_fail(path):
    assert d.validate(json.loads(path.read_text(encoding="utf-8")), SCHEMA)


def test_validator_matches_jsonschema_if_available():
    jsonschema = pytest.importorskip("jsonschema")
    v = jsonschema.Draft7Validator(SCHEMA, format_checker=jsonschema.FormatChecker())
    for path in sorted(FIX.glob("*.json")) + sorted((FIX / "invalid").glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert bool(d.validate(rec, SCHEMA)) == bool(list(v.iter_errors(rec))), path.name


# --- fields -------------------------------------------------------------------------

def test_rights_fields_are_never_filled(tmp_path):
    _, rec, prov, pl = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2()))
    assert rec["consent_status"] == "" and rec["accessRights"] == ""
    assert prov["fields"]["consent_status"]["method"] == "nur-mensch"
    assert "consent_status" in pl.split("BESTÄTIGEN")[0]


def test_consent_passage_is_shown_as_evidence_only(tmp_path):
    _, rec, prov, pl = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2()))
    assert prov["derived"]["consent_hints"][0]["start"] == "00:00:00"
    assert "einverstanden" in pl
    assert rec["consent_status"] == ""


def test_language_is_mapped_to_iso639_3(tmp_path):
    _, rec, _, _ = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2()))
    assert rec["language"] == "deu"


@pytest.mark.parametrize("raw,code", [("de", "deu"), ("lb", "ltz"), ("French", "fra"), ("ltz", "ltz"),
                                      ("xx", ""), (None, "")])
def test_language_codes(raw, code):
    t = d.Transcript("n", Path("n.json"), "whisper-json", [], language=raw)
    assert d.language_field(t)["value"] == code


def test_segments_start_at_topic_questions(tmp_path):
    _, rec, prov, _ = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2()))
    segs = rec["timecoded_segments"]
    assert segs[0]["start"] == "00:00:00"
    assert len(segs) == 3  # three topics; short follow-ups ("Mhm, und dann?") are no boundaries
    reasons = [e["reason"] for e in prov["fields"]["timecoded_segments"]["evidence"]]
    assert "Schule" in " ".join(reasons) and "Stahlwerk" in " ".join(reasons)
    for a, b in zip(segs, segs[1:]):
        assert a["end"] == b["start"]
    assert all(s["label"].startswith("TODO") for s in segs)


def test_hint_words_are_distinctive(tmp_path):
    _, _, prov, _ = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2()))
    words = [w for e in prov["fields"]["timecoded_segments"]["evidence"] for w in e["stichwoerter"]]
    assert "stahlwerk" in words or "hochofen" in words


def test_swapped_roles_are_flagged(tmp_path):
    _, rec, prov, pl = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2(swap_roles=True)))
    assert prov["derived"]["questioner"] == "Interviewee"
    assert "WARNUNG" in pl and "vertauscht" in pl
    assert len(rec["timecoded_segments"]) > 1


def test_draft_is_valid_once_humans_fill_their_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("DIAR2META_INSTITUTION", "TST")
    monkeypatch.setenv("DIAR2META_INTERVIEWER", "Test Person")
    src = write(tmp_path, "2024-05-18_int.diar2.json", synthetic_diar2())
    _, rec, prov, _ = run(tmp_path, src)
    assert rec["record_id"] == "2024-05-18_TST_0001"
    assert set(prov["validation"]["errors"]) == {
        "$.consent_status: leer", "$.accessRights: '' nicht in open | restricted | closed", "$.title: leer"}
    rec.update(consent_status="public", accessRights="open", title="Interview über Kindheit und Arbeit")
    for s in rec["timecoded_segments"]:
        s["label"] = "Abschnitt"
    assert d.validate(rec, SCHEMA) == []
    assert d.todo_paths(rec) == []


def test_record_ids_count_up(tmp_path, monkeypatch):
    monkeypatch.setenv("DIAR2META_INSTITUTION", "TST")
    _, r1, _, _ = run(tmp_path, write(tmp_path, "2024-05-18_a.diar2.json", synthetic_diar2()))
    _, r2, _, _ = run(tmp_path, write(tmp_path, "2024-05-19_b.diar2.json", synthetic_diar2()))
    assert r1["record_id"].endswith("_0001") and r2["record_id"] == "2024-05-19_TST_0002"


def test_dates_from_name_and_talk_conflict_is_reported(tmp_path):
    data = synthetic_diar2(intro="Heute ist der 18. Mai 2024, wir sind in Esch.")
    _, rec, prov, pl = run(tmp_path, write(tmp_path, "2024-05-17_int.diar2.json", data))
    assert rec["interview_date"] == "2024-05-17"
    assert "2024-05-18" in json.dumps(prov["fields"]["interview_date"]["evidence"])
    assert "widersprechen" in pl


def test_dates_in_text():
    assert d.dates_in_text("am 3. März 1999 und 12.10.2001, 2020-02-30") == ["2001-10-12", "1999-03-03"]


def test_missing_config_leaves_fields_empty_with_hint(tmp_path):
    _, rec, prov, _ = run(tmp_path, write(tmp_path, "a.diar2.json", synthetic_diar2()))
    assert rec["record_id"] == "" and rec["interviewer"] == ""
    assert "DIAR2META_INSTITUTION" in prov["fields"]["record_id"]["note"]


def test_pseudonym_never_uses_transcript_content(tmp_path, monkeypatch):
    monkeypatch.setenv("DIAR2META_INSTITUTION", "TST")
    monkeypatch.setenv("DIAR2META_PSEUDONYM_PREFIX", "Person")
    _, rec, _, _ = run(tmp_path, write(tmp_path, "2024-05-18_a.diar2.json", synthetic_diar2()))
    assert rec["interviewee_display"] == "Person 0001"


# --- runs ---------------------------------------------------------------------------

def test_existing_draft_is_not_overwritten(tmp_path):
    src = write(tmp_path, "a.diar2.json", synthetic_diar2())
    _, rec, _, _ = run(tmp_path, src)
    draft = tmp_path / "out" / "a" / "a.imm.draft.json"
    rec["title"] = "von Hand"
    draft.write_text(json.dumps(rec), encoding="utf-8")
    assert d.process(src, tmp_path / "out")[0] == "übersprungen"
    assert json.loads(draft.read_text())["title"] == "von Hand"
    d.process(src, tmp_path / "out", fresh=True)
    backups = list(draft.parent.glob("a.imm.draft.bak-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text())["title"] == "von Hand"


def test_whisper_json_uses_pauses(tmp_path):
    segs, t = [], 0.0
    for q, a in TOPICS:
        for _ in range(20):
            segs.append({"start": t, "end": t + 19, "text": a})
            t += 19.5
        t += 5  # long pause at topic change
    _, rec, prov, _ = run(tmp_path, write(tmp_path, "w_clean.json",
                                          {"text": "", "segments": segs, "language": "de"}))
    assert len(rec["timecoded_segments"]) >= 2
    assert "Pause" in prov["fields"]["timecoded_segments"]["evidence"][1]["reason"]


def test_srt_with_speaker_prefix(tmp_path):
    srt = ("1\n00:00:00,500 --> 00:00:03,000\nInterviewer: Wo sind Sie aufgewachsen?\n\n"
           "2\n00:00:03,500 --> 00:00:09,000\nInterviewee: In einem Dorf.\n")
    p = tmp_path / "s.diar2.srt"
    p.write_text(srt, encoding="utf-8")
    t = d.read_transcript(p)
    assert [u.speaker for u in t.units] == ["Interviewer", "Interviewee"]
    assert t.units[1].end == 9.0 and t.name == "s"


def test_diar2_txt(tmp_path):
    p = tmp_path / "x.diar2.txt"
    p.write_text("[00:00:00.5] Interviewer: Guten Tag?\n\n[00:00:07.1] Interviewee: Hallo "
                 "[Interviewer: Mhm.] ja.\n", encoding="utf-8")
    t = d.read_transcript(p)
    assert [(u.start, u.speaker) for u in t.units] == [(0.5, "Interviewer"), (7.1, "Interviewee")]
    assert t.units[0].end == 7.1


def test_collect_prefers_diar2_json(tmp_path):
    (tmp_path / "a").mkdir()
    write(tmp_path / "a", "a.diar2.json", synthetic_diar2())
    (tmp_path / "a" / "a.diar2.srt").write_text("", encoding="utf-8")
    assert [p.name for p in d.collect([str(tmp_path)])] == ["a.diar2.json"]


def test_main_reports_errors_and_continues(tmp_path, capsys):
    good = write(tmp_path, "g.diar2.json", synthetic_diar2())
    bad = write(tmp_path, "b.json", {"foo": 1})
    rc = d.main([str(good), str(bad), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 1 and "fertig" in out and "FEHLER" in out


def test_standard_library_only():
    src = Path(d.__file__).read_text(encoding="utf-8")
    import re
    mods = set(re.findall(r"^(?:from|import) (\w+)", src, re.MULTILINE)) - {"__future__"}
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_default_input_folder_and_hint_when_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DIAR2META_IN", str(tmp_path / "input"))
    (tmp_path / "input").mkdir()
    assert d.main(["--out", str(tmp_path / "out")]) == 1
    assert "diar2meta ~/Downloads/diar2/output" in capsys.readouterr().err
    folder = tmp_path / "input" / "a"
    folder.mkdir()
    write(folder, "a.diar2.json", synthetic_diar2())
    assert d.main(["--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "a" / "a.imm.draft.json").exists()
