"""diar2meta: draft IMM-Core metadata records from local interview transcripts.

Reads the output of diar2 (NAME.diar2.json, preferred) or plain Whisper
output (JSON, SRT, VTT, TSV) and writes per interview:

  NAME.imm.draft.json       IMM-Core record draft, for a person to complete
  NAME.imm.provenance.json  where each value comes from, with evidence
  NAME.pruefliste.txt       what a person must fill in or confirm

Rules first, no language model in this version. Fields that describe rights
(consent_status, accessRights) are never filled automatically. Standard
library only; runs offline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "diar2meta 0.1.0"
HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema" / "core.schema.json"
IMM_CORE_VERSION = "1.0.1"

MEDIA_EXT = (".mp4", ".mov", ".m4a", ".wav", ".mp3", ".aac", ".flac")


def env(name, default):
    return os.environ.get(name, default)


def env_float(name, default):
    return float(os.environ.get(name, default))


# Segmentation: aim for one segment per SEG_TARGET_S seconds, none shorter
# than SEG_MIN_S. Cohesion is compared over COHESION_WINDOW_S on each side.
SEG_TARGET_S = env_float("DIAR2META_SEG_TARGET_S", 300)
SEG_MIN_S = env_float("DIAR2META_SEG_MIN_S", 120)
COHESION_WINDOW_S = env_float("DIAR2META_COHESION_WINDOW_S", 120)
# Questions shorter than this are follow-ups, not topic openers.
QUESTION_MIN_WORDS = int(env("DIAR2META_QUESTION_MIN_WORDS", "4"))
# Without speakers, a pause of at least this length is a candidate boundary.
PAUSE_MIN_S = env_float("DIAR2META_PAUSE_MIN_S", 2.0)
# Consent is usually recorded at the start; search this many seconds.
CONSENT_HEAD_S = env_float("DIAR2META_CONSENT_HEAD_S", 300)
DATE_HEAD_S = env_float("DIAR2META_DATE_HEAD_S", 180)

TODO = "TODO"

# ISO 639-1 (as reported by Whisper) -> ISO 639-3 (as required by IMM-Core).
ISO639_1_TO_3 = {
    "af": "afr", "am": "amh", "ar": "ara", "az": "aze", "be": "bel", "bg": "bul", "bn": "ben",
    "bs": "bos", "ca": "cat", "cs": "ces", "cy": "cym", "da": "dan", "de": "deu", "el": "ell",
    "en": "eng", "es": "spa", "et": "est", "eu": "eus", "fa": "fas", "fi": "fin", "fr": "fra",
    "ga": "gle", "gl": "glg", "he": "heb", "hi": "hin", "hr": "hrv", "hu": "hun", "hy": "hye",
    "id": "ind", "is": "isl", "it": "ita", "ja": "jpn", "ka": "kat", "kk": "kaz", "ko": "kor",
    "ku": "kur", "lb": "ltz", "lt": "lit", "lv": "lav", "mk": "mkd", "ms": "msa", "mt": "mlt",
    "nl": "nld", "nn": "nno", "no": "nor", "pl": "pol", "ps": "pus", "pt": "por", "ro": "ron",
    "ru": "rus", "sk": "slk", "sl": "slv", "so": "som", "sq": "sqi", "sr": "srp", "sv": "swe",
    "sw": "swa", "ta": "tam", "th": "tha", "ti": "tir", "tl": "tgl", "tr": "tur", "uk": "ukr",
    "ur": "urd", "uz": "uzb", "vi": "vie", "yi": "yid", "zh": "zho",
}
WHISPER_NAMES = {"german": "de", "english": "en", "french": "fr", "luxembourgish": "lb",
                 "dutch": "nl", "italian": "it", "spanish": "es", "portuguese": "pt"}

STOPWORDS = set("""
aber alle allem allen aller alles also auch auf aus bei beim bin bis bisschen bist dann damit
dann darauf darum dass daß dein deine dem den denen der deren des dich die dies diese diesem
diesen dieser dieses doch dort durch eben eigentlich ein eine einem einen einer eines einfach
einmal etwa etwas euch euer eure für gab gibt ganz gar geht gehabt gesagt gewesen gibt habe
haben hast hat hatte hatten hätte hätten heute hier hin hinter ihm ihn ihnen ihr ihre ihrem
ihren ihrer immer ist jetzt jede jedem jeden jeder jedes jemand kann kannst kein keine keinem
keinen keiner können könnte machen macht mal man manche mehr mein meine meinem meinen meiner
mich mir mit muss musste nach nicht nichts noch nun nur oder ohne quasi schon sehr sein seine
seinem seinen seiner seit selbst sich sie sind so sogar solche soll sollte sondern sowie
sozusagen über überhaupt um und uns unser unsere unter viel viele vielleicht vom von vor war
waren wäre wären warum was weil weiter weiß welche welchem welchen welcher wenn wer werde
werden wie wieder will wir wird wirklich wissen wo wohl wollen wollte worden wurde wurden würde
zum zur zwar zwischen ähm äh öhm hmm mhm genau okay also ja nein naja halt irgendwie
the and that this with from have has had were was what when where which who will would there
their they them then than been being into just like know yeah well really about because could
should very some more also only other over such your you're it's don't didn't
les des une dans pour avec que qui pas sur est sont mais comme tout très bien alors donc
""".split())

GERMAN_MONTHS = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5,
                 "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
                 "november": 11, "dezember": 12}
FRENCH_MONTHS = {"janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
                 "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
                 "décembre": 12}
ENGLISH_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                  "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
                  "december": 12}
MONTHS = {**ENGLISH_MONTHS, **FRENCH_MONTHS, **GERMAN_MONTHS}

CONSENT_RE = re.compile(
    r"einverst|einverständ|einwillig|zustimm|aufnahme|aufzeichn|aufnehmen|datenschutz|anonym"
    r"|consent|agree|recording|record it|d'accord|consentement|enregistr|averstan",
    re.IGNORECASE)


# --- reading transcripts --------------------------------------------------------

@dataclass
class Unit:
    """A sentence (diar2) or segment (Whisper) with time and optional speaker."""
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass
class Transcript:
    name: str
    path: Path
    kind: str                      # diar2-json | whisper-json | srt | vtt | tsv | diar2-txt
    units: list[Unit]
    language: str | None = None    # as reported by the source (ISO 639-1 or name)
    speakers: list[str] = field(default_factory=list)
    interviewer: str | None = None  # speaker name of the interviewer role
    meta: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max((u.end for u in self.units), default=0.0)

    @property
    def timed(self) -> bool:
        return any(u.end > 0 for u in self.units)


def base_name(path: Path) -> str:
    name = path.name
    for suffix in (".diar2.json", ".diar2.txt", ".diar2.srt", ".json", ".srt", ".vtt", ".tsv", ".txt"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def read_diar2_json(path: Path, data: dict) -> Transcript:
    names = [s["name"] for s in data.get("speakers", [])]
    interviewer = "Interviewer" if "Interviewer" in names else (names[0] if names else None)
    units = [Unit(float(s["start"]), float(s["end"]), s["text"].strip(), s.get("speaker"))
             for s in data.get("sentences", []) if s.get("text", "").strip()]
    return Transcript(base_name(path), path, "diar2-json", units,
                      language=data.get("language") or data.get("meta", {}).get("language"),
                      speakers=names, interviewer=interviewer, meta=data.get("meta", {}))


def read_whisper_json(path: Path, data: dict) -> Transcript:
    units = [Unit(float(s["start"]), float(s["end"]), s["text"].strip())
             for s in data.get("segments", []) if s.get("text", "").strip()]
    return Transcript(base_name(path), path, "whisper-json", units, language=data.get("language"))


def parse_clock(s: str) -> float:
    s = s.strip().replace(",", ".")
    parts = [float(p) for p in s.split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def read_subtitles(path: Path, text: str, kind: str) -> Transcript:
    units = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        for k, ln in enumerate(lines):
            m = re.match(r"\s*([\d:.,]+)\s*-->\s*([\d:.,]+)", ln)
            if m:
                body = " ".join(lines[k + 1:]).strip()
                speaker = None
                sm = re.match(r"^\[?([^\]:]{1,40})\]?:\s+(.*)$", body)
                if sm and not re.search(r"\d", sm.group(1)):
                    speaker, body = sm.group(1).strip(), sm.group(2)
                if body:
                    units.append(Unit(parse_clock(m.group(1)), parse_clock(m.group(2)), body, speaker))
                break
    return _with_speakers(Transcript(base_name(path), path, kind, units))


def read_tsv(path: Path, text: str) -> Transcript:
    units = []
    for ln in text.splitlines()[1:]:
        parts = ln.split("\t")
        if len(parts) >= 3 and parts[0].strip().isdigit():
            # Whisper writes milliseconds
            units.append(Unit(int(parts[0]) / 1000, int(parts[1]) / 1000, parts[2].strip()))
    return Transcript(base_name(path), path, "tsv", units)


def read_diar2_txt(path: Path, text: str) -> Transcript:
    """Paragraphs '[HH:MM:SS.s] Speaker: text'; a paragraph ends where the next starts."""
    units = []
    for m in re.finditer(r"^\[([\d:.]+)\]\s+([^:\n]{1,40}):\s+(.*)$", text, re.MULTILINE):
        units.append(Unit(parse_clock(m.group(1)), 0.0, m.group(3).strip(), m.group(2).strip()))
    for a, b in zip(units, units[1:]):
        a.end = b.start
    if units:
        units[-1].end = units[-1].start + max(1.0, len(units[-1].text.split()) / 2.5)  # ~150 words/min
    return _with_speakers(Transcript(base_name(path), path, "diar2-txt", units))


def _with_speakers(t: Transcript) -> Transcript:
    seen = []
    for u in t.units:
        if u.speaker and u.speaker not in seen:
            seen.append(u.speaker)
    t.speakers = seen
    if seen:
        t.interviewer = "Interviewer" if "Interviewer" in seen else seen[0]
    return t


def read_transcript(path: Path) -> Transcript:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        if "sentences" in data and "turns" in data:
            return read_diar2_json(path, data)
        if "segments" in data:
            return read_whisper_json(path, data)
        raise ValueError("JSON ist weder diar2- noch Whisper-Ausgabe")
    if suffix == ".srt":
        return read_subtitles(path, text, "srt")
    if suffix == ".vtt":
        return read_subtitles(path, text, "vtt")
    if suffix == ".tsv":
        return read_tsv(path, text)
    if suffix == ".txt":
        t = read_diar2_txt(path, text)
        if t.units:
            return t
        raise ValueError("TXT ohne Zeitmarken: keine Segmentierung möglich; bitte JSON, SRT oder VTT nutzen")
    raise ValueError(f"Format {suffix} wird nicht unterstützt")


# --- helpers --------------------------------------------------------------------

def hms(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def excerpt(text: str, n: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W\d_]{4,}", text.lower()) if w not in STOPWORDS]


def bag(units) -> dict:
    b = {}
    for u in units:
        for w in tokens(u.text):
            b[w] = b.get(w, 0) + 1
    return b


def cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- f: fields ------------------------------------------------------------------

def language_field(t: Transcript) -> dict:
    raw = (t.language or "").strip().lower()
    raw = WHISPER_NAMES.get(raw, raw)
    code = ISO639_1_TO_3.get(raw) or (raw if re.fullmatch(r"[a-z]{3}", raw) else None)
    if not code:
        return {"value": "", "method": "fehlt",
                "note": f"Quelle nennt keine erkennbare Sprache ({t.language!r})."}
    return {"value": code, "method": "regel",
            "rule": f"Sprachcode der Quelle {t.language!r} -> ISO 639-3 {code!r}",
            "note": "Die Transkription nimmt eine Sprache für das ganze Interview an. "
                    "Code-Switching (z. B. Luxemburgisch, Französisch) prüfen; "
                    "IMM-Core 1.0 erlaubt nur eine Sprache."}


def find_recording(t: Transcript) -> Path | None:
    dirs = [Path(p).expanduser() for p in
            env("DIAR2META_REC_DIRS", "~/Downloads/diar2/input:~/Downloads").split(":") if p]
    want_hash = t.meta.get("input_sha256")
    stem = Path(t.meta.get("input") or t.name).stem
    for d in dirs:
        for ext in MEDIA_EXT:
            for cand in (d / f"{stem}{ext}", d / f"{stem}{ext.upper()}"):
                if cand.is_file():
                    if want_hash and sha256_file(cand) != want_hash:
                        continue
                    return cand
    return None


def ffprobe_creation(path: Path) -> str | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time:stream_tags=creation_time",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None
    tags = [data.get("format", {}).get("tags", {})] + [s.get("tags", {}) for s in data.get("streams", [])]
    for tg in tags:
        v = tg.get("creation_time")
        if v and not v.startswith("1970") and not v.startswith("1904"):
            return v[:10]
    return None


def valid_date(y, m, d) -> str | None:
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def dates_in_text(text: str) -> list[str]:
    found = []
    for m in re.finditer(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text):
        found.append(valid_date(*m.groups()))
    for m in re.finditer(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})(?!\d)", text):
        found.append(valid_date(m.group(3), m.group(2), m.group(1)))
    for m in re.finditer(r"\b(\d{1,2})\.?\s+([^\W\d_]+)\s+(\d{4})\b", text):
        month = MONTHS.get(m.group(2).lower())
        if month:
            found.append(valid_date(m.group(3), month, m.group(1)))
    return [d for d in found if d]


def date_field(t: Transcript) -> dict:
    cands = []  # (value, source, evidence)
    rec = find_recording(t)
    if rec:
        v = ffprobe_creation(rec)
        if v:
            cands.append((v, "Aufnahmedatum in der Originaldatei (ffprobe creation_time)", rec.name))
    for v in dates_in_text(t.name):
        cands.append((v, "Datum im Dateinamen", t.name))
    for u in t.units:
        if u.start > DATE_HEAD_S:
            break
        for v in dates_in_text(u.text):
            cands.append((v, "im Gespräch genannt", f"{hms(u.start)} „{excerpt(u.text)}“"))
    if rec and not cands:
        v = dt.date.fromtimestamp(rec.stat().st_mtime).isoformat()
        cands.append((v, "Änderungsdatum der Originaldatei (schwach: ändert sich beim Kopieren)", rec.name))
    if not cands:
        return {"value": "", "method": "fehlt",
                "note": "Kein Datum gefunden (keine Originalaufnahme, kein Datum im Namen oder am Anfang)."}
    value, source, _ = cands[0]
    distinct = sorted({c[0] for c in cands})
    return {"value": value, "method": "regel", "rule": source,
            "evidence": [{"value": c[0], "source": c[1], "where": c[2]} for c in cands],
            "note": ("Kandidaten widersprechen sich: " + ", ".join(distinct)) if len(distinct) > 1 else None}


def record_id_field(date: str, out_root: Path) -> dict:
    inst = env("DIAR2META_INSTITUTION", "")
    if not inst:
        return {"value": "", "method": "fehlt",
                "note": "DIAR2META_INSTITUTION setzen (z. B. LHI), dann wird "
                        "YYYY-MM-DD_{INSTITUTION}_{NNNN} vorgeschlagen."}
    if not date:
        return {"value": "", "method": "fehlt", "note": "Ohne interview_date kein Vorschlag für record_id."}
    seq = next_sequence(out_root, inst)
    return {"value": f"{date}_{inst}_{seq:04d}", "method": "regel",
            "rule": "interview_date + DIAR2META_INSTITUTION + nächste freie Nummer im Ausgabeordner",
            "note": "Nummer nur lokal eindeutig. Mit zentraler Vergabe abgleichen; "
                    "eine vergebene record_id nie mehr ändern."}


def next_sequence(out_root: Path, inst: str) -> int:
    used = [0]
    for p in out_root.glob("*/*.imm.draft.json"):
        try:
            rid = json.loads(p.read_text(encoding="utf-8")).get("record_id", "")
        except (OSError, json.JSONDecodeError):
            continue
        m = re.search(rf"_{re.escape(inst)}_(\d+)$", rid)
        if m:
            used.append(int(m.group(1)))
    return max(used) + 1


def consent_hints(t: Transcript) -> list[dict]:
    return [{"start": hms(u.start), "speaker": u.speaker, "text": excerpt(u.text, 140)}
            for u in t.units if u.start <= CONSENT_HEAD_S and CONSENT_RE.search(u.text)]


# --- segmentation ---------------------------------------------------------------

def is_question(u: Unit) -> bool:
    return u.text.rstrip().endswith("?")


def questioner(t: Transcript) -> str | None:
    """The speaker who asks most questions; diar2 only guesses the role by order."""
    counts = {sp: sum(1 for u in t.units if u.speaker == sp and is_question(u)) for sp in t.speakers}
    if not counts or max(counts.values()) < 2:
        return None
    return max(t.speakers, key=lambda sp: (counts[sp], sp == t.interviewer))


def candidate_boundaries(t: Transcript) -> list[tuple[float, int, str]]:
    """(time, unit index, reason) where a new segment may start."""
    cands = []
    asker = questioner(t)
    if asker:
        for i in range(1, len(t.units)):
            u, prev = t.units[i], t.units[i - 1]
            if u.speaker != asker or prev.speaker == asker:
                continue
            # the whole turn; its first substantial question marks the boundary.
            # Short follow-ups ("Und dann?") continue a topic rather than open one.
            turn = []
            for x in t.units[i:]:
                if x.speaker != asker:
                    break
                turn.append(x)
            q = next((x for x in turn if is_question(x) and len(x.text.split()) >= QUESTION_MIN_WORDS), None)
            if q:
                cands.append((q.start, t.units.index(q), f"Frage: „{excerpt(q.text, 90)}“"))
    elif t.speakers:
        for i in range(1, len(t.units)):
            if t.units[i].speaker != t.units[i - 1].speaker:
                cands.append((t.units[i].start, i, f"Sprecherwechsel zu {t.units[i].speaker}"))
    else:
        for i in range(1, len(t.units)):
            gap = t.units[i].start - t.units[i - 1].end
            if gap >= PAUSE_MIN_S:
                cands.append((t.units[i].start, i, f"Pause {gap:.1f} s"))
    return cands


def depth(t: Transcript, time: float) -> float:
    """1 - lexical similarity of the windows before and after time."""
    before = [u for u in t.units if time - COHESION_WINDOW_S <= u.start < time]
    after = [u for u in t.units if time <= u.start < time + COHESION_WINDOW_S]
    return 1.0 - cosine(bag(before), bag(after))


def segment(t: Transcript) -> list[dict]:
    dur = t.duration
    if not t.timed or dur <= 0:
        return []
    n_target = max(1, round(dur / SEG_TARGET_S))
    cands = [(depth(t, time), time, i, why) for time, i, why in candidate_boundaries(t)
             if SEG_MIN_S <= time <= dur - SEG_MIN_S]
    chosen = []
    for score, time, i, why in sorted(cands, key=lambda c: (-c[0], c[1])):
        if len(chosen) >= n_target - 1:
            break
        if all(abs(time - c[1]) >= SEG_MIN_S for c in chosen):
            chosen.append((score, time, i, why))
    chosen.sort(key=lambda c: c[1])
    starts = [(0.0, 0, "Beginn")] + [(c[1], c[2], c[3]) for c in chosen]
    ends = [s[0] for s in starts[1:]] + [dur]
    segs = []
    for k, ((start, i, why), end) in enumerate(zip(starts, ends), 1):
        units = [u for u in t.units if start <= u.start < end]
        segs.append({"n": k, "start": start, "end": end, "reason": why,
                     "depth": round(depth(t, start), 3) if k > 1 else None, "units": units})
    add_hint_words(segs)
    return segs


def add_hint_words(segs: list[dict], k: int = 6) -> None:
    """Words frequent in one segment and rare in the others (tf-idf)."""
    bags = [bag(s["units"]) for s in segs]
    n = len(bags)
    df = {}
    for b in bags:
        for w in b:
            df[w] = df.get(w, 0) + 1
    for s, b in zip(segs, bags):
        scored = sorted(((c * math.log((n + 1) / df[w]), w) for w, c in b.items() if c >= 2),
                        key=lambda x: (-x[0], x[1]))
        s["hint_words"] = [w for _, w in scored[:k]]


# --- validation (subset of JSON Schema draft-07 used by IMM-Core) ----------------

def validate(inst, schema, path="$") -> list[str]:
    errs = []
    typ = schema.get("type")
    types = {"string": str, "array": list, "object": dict, "boolean": bool}
    if typ and not isinstance(inst, types[typ]):
        return [f"{path}: erwartet {typ}"]
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: muss {schema['const']!r} sein")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} nicht in {' | '.join(schema['enum'])}")
    if isinstance(inst, str):
        if len(inst) < schema.get("minLength", 0):
            errs.append(f"{path}: leer")
        elif "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: {inst!r} passt nicht zu {schema['pattern']}")
        elif schema.get("format") == "date":
            try:
                dt.date.fromisoformat(inst)
            except ValueError:
                errs.append(f"{path}: {inst!r} ist kein gültiges Datum")
    if isinstance(inst, list) and "items" in schema:
        for k, item in enumerate(inst):
            errs += validate(item, schema["items"], f"{path}[{k}]")
    if isinstance(inst, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in inst:
                errs.append(f"{path}.{req}: fehlt")
        for key, val in inst.items():
            if key in props:
                errs += validate(val, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{key}: Feld gehört nicht zu IMM-Core")
    for sub in schema.get("allOf", []):
        if "if" in sub:
            if not validate(inst, sub["if"], path):
                errs += validate(inst, sub.get("then", {}), path)
        else:
            errs += validate(inst, sub, path)
    return errs


def todo_paths(obj, path="$") -> list[str]:
    if isinstance(obj, str):
        return [path] if obj.startswith(TODO) else []
    if isinstance(obj, list):
        return [p for k, v in enumerate(obj) for p in todo_paths(v, f"{path}[{k}]")]
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in todo_paths(v, f"{path}.{k}")]
    return []


# --- building the draft -----------------------------------------------------------

HUMAN_ONLY = {
    "consent_status": "Was die interviewte Person zugestimmt hat. Nur aus der Einwilligungserklärung, "
                      "nie aus dem Transkript ableiten. Vokabular: vocabs/consent_status.md.",
    "accessRights": "open | restricted | closed. Entscheidung des Projekts oder Archivs.",
}


def build(t: Transcript, out_root: Path) -> tuple[dict, dict, dict]:
    fields = {}
    fields["interview_date"] = date_field(t)
    fields["record_id"] = record_id_field(fields["interview_date"]["value"], out_root)
    interviewer = env("DIAR2META_INTERVIEWER", "")
    fields["interviewer"] = ({"value": interviewer, "method": "konfiguration", "rule": "DIAR2META_INTERVIEWER"}
                             if interviewer else
                             {"value": "", "method": "fehlt", "note": "DIAR2META_INTERVIEWER setzen."})
    for f, note in HUMAN_ONLY.items():
        fields[f] = {"value": "", "method": "nur-mensch", "note": note}
    fields["title"] = {"value": "", "method": "fehlt",
                       "note": "Beschreibender Titel; wird in einer späteren Version vorgeschlagen."}
    fields["language"] = language_field(t)
    prefix = env("DIAR2META_PSEUDONYM_PREFIX", "")
    rid = fields["record_id"]["value"]
    if prefix and rid:
        fields["interviewee_display"] = {"value": f"{prefix} {rid.rsplit('_', 1)[-1]}", "method": "regel",
                                         "rule": "DIAR2META_PSEUDONYM_PREFIX + Nummer der record_id",
                                         "note": "Nie einen echten Namen eintragen."}

    segs = segment(t)
    if segs:
        fields["timecoded_segments"] = {
            "value": [{"start": hms(s["start"]), "end": hms(s["end"]), "label": f"{TODO}: Abschnitt {s['n']}"}
                      for s in segs],
            "method": "regel",
            "rule": ("Grenzen an Fragen mit dem größten Themenwechsel " if questioner(t) else
                     "Grenzen an Sprecherwechseln mit dem größten Themenwechsel " if t.speakers else
                     "Grenzen an Pausen mit dem größten Themenwechsel ")
                    + f"(Ziel {SEG_TARGET_S:.0f} s je Abschnitt, mindestens {SEG_MIN_S:.0f} s)",
            "evidence": [{"n": s["n"], "start": hms(s["start"]), "reason": s["reason"],
                          "themenwechsel": s["depth"], "stichwoerter": s["hint_words"]} for s in segs],
            "note": "Labels sachlich beschreiben, was im Abschnitt geschieht (docs §9.5), nicht analytisch.",
        }

    order = ["record_id", "interview_date", "interviewer", "consent_status", "accessRights", "title",
             "interviewee_display", "language", "timecoded_segments"]
    record = {k: fields[k]["value"] for k in order if k in fields}
    derived = {
        "duration": hms(t.duration) if t.timed else None,
        "duration_s": round(t.duration, 1) if t.timed else None,
        "speakers": t.speakers,
        "interviewer_label": t.interviewer,
        "questioner": questioner(t),
        "questions_by_speaker": {sp: sum(1 for u in t.units if u.speaker == sp and is_question(u))
                                 for sp in t.speakers},
        "units": len(t.units),
        "words": sum(len(u.text.split()) for u in t.units),
        "consent_hints": consent_hints(t),
    }
    return record, fields, {"segments": segs, "derived": derived}


def provenance(t: Transcript, fields: dict, derived: dict) -> dict:
    return {
        "tool": VERSION,
        "imm_core": IMM_CORE_VERSION,
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"file": t.path.name, "kind": t.kind, "sha256": sha256_file(t.path),
                   "recording_sha256": t.meta.get("input_sha256"),
                   "transcription_model": t.meta.get("whisper_model"),
                   "diarization_model": t.meta.get("diar_model")},
        "status": "entwurf",
        "fields": {k: {kk: vv for kk, vv in v.items() if vv is not None} for k, v in fields.items()},
        "derived": derived,
        "not_filled": {
            "spatial": "Ort des Interviews; erwähnte Orte sind nicht der Interviewort.",
            "keywords": "Beschreibende Schlagwörter, keine analytischen Codes (docs §9.6).",
            "abstract": "2–3 Sätze, beschreibend (methodological-positions/abstracts.md).",
            "related_materials": "URL oder DOI; lokale Pfade sind keine gültigen Verweise.",
        },
    }


# --- review list ------------------------------------------------------------------

def render_pruefliste(t: Transcript, record: dict, fields: dict, extra: dict, errors: list[str]) -> str:
    d = extra["derived"]
    out = [f"Prüfliste {t.name}  ({VERSION}, IMM-Core {IMM_CORE_VERSION})",
           f"Quelle: {t.path.name} ({t.kind})"]
    info = []
    if d["duration"]:
        info.append(f"Dauer {d['duration']}")
    if t.speakers:
        info.append(f"{len(t.speakers)} Sprecher ({', '.join(t.speakers)})")
    info.append(f"{d['words']} Wörter")
    out.append("       " + ", ".join(info))
    if len(t.speakers) > 2:
        out.append(f"       Hinweis: {len(t.speakers)} Sprecher erkannt; diar2 ist für zwei Personen gebaut.")
    if d["questioner"] and t.interviewer and d["questioner"] != t.interviewer:
        qs = ", ".join(f"{sp} {c}" for sp, c in d["questions_by_speaker"].items())
        out.append(f"       WARNUNG: Die meisten Fragen stellt „{d['questioner']}“, nicht „{t.interviewer}“ "
                   f"(Fragen: {qs}).")
        out.append("       Sind die Rollen im Transkript vertauscht? diar2 nennt die erste Stimme "
                   "„Interviewer“; siehe Hörliste, Prüfung der ersten 60 s.")
    out.append("")

    n = 0

    def item(title, lines):
        nonlocal n
        n += 1
        out.append(f"{n:3d}. {title}")
        out.extend("       " + ln for ln in lines if ln)

    out.append("VON HAND AUSFÜLLEN")
    for f in ("consent_status", "accessRights"):
        lines = [fields[f]["note"]]
        if f == "consent_status" and d["consent_hints"]:
            lines.append("Mögliche Einwilligungsstellen im Gespräch (nur als Beleg, nicht als Wert):")
            lines += [f"  {h['start']}  {h['speaker'] + ': ' if h['speaker'] else ''}{h['text']}"
                      for h in d["consent_hints"]]
        item(f, lines)
    for f in ("record_id", "interview_date", "interviewer", "title", "language"):
        if not record.get(f):
            item(f, [fields[f].get("note", "")])
    out.append("")

    out.append("BESTÄTIGEN")
    for f in ("record_id", "interview_date", "interviewer", "interviewee_display", "language"):
        if record.get(f):
            fl = fields[f]
            lines = [f"Vorschlag: {record[f]}   ({fl.get('rule', fl['method'])})"]
            for ev in fl.get("evidence", [])[1:]:
                lines.append(f"weiterer Kandidat: {ev['value']} ({ev['source']}: {ev['where']})")
            lines.append(fl.get("note") or "")
            item(f, lines)
    segs = extra["segments"]
    if segs:
        lines = [fields["timecoded_segments"]["rule"] + ".", "Labels ersetzen (stehen als TODO im Entwurf):"]
        for s in segs:
            lines.append(f"  {s['n']:2d}. {hms(s['start'])} - {hms(s['end'])}  {s['reason']}")
            if s["hint_words"]:
                lines.append(f"      Stichwörter: {', '.join(s['hint_words'])}")
        item("timecoded_segments", lines)
    elif not t.timed:
        item("timecoded_segments", ["Quelle ohne Zeitmarken: keine Abschnitte vorgeschlagen."])
    out.append("")

    out.append("OPTIONAL")
    item("spatial, keywords, abstract, related_materials",
         ["Nicht automatisch gefüllt. Abstract und Keywords beschreibend halten, nicht analytisch.",
          "Vor Weitergabe prüfen, ob Titel oder Abstract Personen wiedererkennbar machen."])
    out.append("")

    out.append(f"VALIDIERUNG gegen IMM-Core {IMM_CORE_VERSION}")
    todos = todo_paths(record)
    if not errors and not todos:
        out.append("       gültig, keine TODO-Einträge")
    for e in errors:
        out.append(f"       offen: {e}")
    if todos:
        out.append(f"       offen: {len(todos)} TODO-Einträge ({', '.join(todos[:3])}{' …' if len(todos) > 3 else ''})")
    out.append("")
    out.append("Dateien: NAME.imm.draft.json bearbeiten; NAME.imm.provenance.json zeigt Herkunft und Belege.")
    return "\n".join(out) + "\n"


# --- driver -----------------------------------------------------------------------

def process(src: Path, out_root: Path, fresh: bool = False) -> tuple[str, Path]:
    t = read_transcript(src)
    out_dir = out_root / t.name
    draft = out_dir / f"{t.name}.imm.draft.json"
    if draft.exists() and not fresh:
        return "übersprungen", draft
    out_dir.mkdir(parents=True, exist_ok=True)
    if draft.exists():
        # never lose manual edits: keep the previous draft next to the new one
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        draft.rename(out_dir / f"{t.name}.imm.draft.bak-{stamp}.json")
    record, fields, extra = build(t, out_root)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = validate(record, schema)
    draft.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prov = provenance(t, fields, extra["derived"])
    prov["validation"] = {"errors": errors, "todo": todo_paths(record)}
    (out_dir / f"{t.name}.imm.provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / f"{t.name}.pruefliste.txt").write_text(
        render_pruefliste(t, record, fields, extra, errors), encoding="utf-8")
    return "fertig", draft


def collect(paths: list[str]) -> list[Path]:
    """Files given directly, or per folder: *.diar2.json, else other transcripts."""
    found = []
    for p in map(lambda s: Path(s).expanduser(), paths):
        if p.is_file():
            found.append(p)
            continue
        if not p.is_dir():
            print(f"FEHLER  {p}: nicht gefunden", file=sys.stderr)
            continue
        diar2 = sorted(p.glob("*.diar2.json")) + sorted(p.glob("*/*.diar2.json"))
        if diar2:
            found += diar2
        else:
            for ext in ("*.json", "*.vtt", "*.srt"):
                found += sorted(q for q in p.glob(ext) if not q.name.endswith((".imm.draft.json",
                                                                             ".imm.provenance.json")))
    # one source per interview: prefer diar2 JSON over other formats
    rank = {".json": 0, ".vtt": 1, ".srt": 2, ".tsv": 3, ".txt": 4}
    best = {}
    for f in found:
        key = base_name(f)
        r = (0 if f.name.endswith(".diar2.json") else 1, rank.get(f.suffix.lower(), 9))
        if key not in best or r < best[key][0]:
            best[key] = (r, f)
    return [v[1] for v in sorted(best.values(), key=lambda x: str(x[1]))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="IMM-Core-Entwürfe aus lokalen Transkripten.")
    ap.add_argument("paths", nargs="*", help="Dateien oder Ordner (Standard: input/ bzw. DIAR2META_IN)")
    ap.add_argument("--out", default=env("DIAR2META_OUT", str(HERE / "output")))
    ap.add_argument("--fresh", action="store_true", default=env("DIAR2META_FRESH", "0") == "1",
                    help="vorhandene Entwürfe neu erzeugen (alter Entwurf bleibt als .bak)")
    args = ap.parse_args(argv)
    paths = args.paths or [env("DIAR2META_IN", str(HERE / "input"))]
    out_root = Path(args.out).expanduser()
    sources = collect(paths)
    if not sources:
        print(f"Keine Transkripte in {', '.join(paths)}.\n"
              "Ergebnisordner von diar2 (z. B. ~/Downloads/diar2/output/NAME) dorthin kopieren "
              "oder direkt: diar2meta ~/Downloads/diar2/output", file=sys.stderr)
        return 1
    failed = 0
    for src in sources:
        try:
            status, where = process(src, out_root, args.fresh)
            print(f"{status:12s} {src.name} -> {where.parent}")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
            failed += 1
            print(f"{'FEHLER':12s} {src.name}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
