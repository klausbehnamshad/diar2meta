# diar2meta

diar2meta drafts [IMM-Core](https://github.com/klausbehnamshad/imm-core) metadata records from local interview
transcripts, for example the output of [diar2](https://github.com/klausbehnamshad/diar2). It fills what can be
derived by rules, shows the evidence, and lists what a person must fill in or confirm.

## Principles

- **Local and offline.** Python standard library only; no network access, no upload step.
- **Rules first.** Version 0.1 uses no language model. Every value states where it comes from.
- **Rights stay human.** `consent_status` and `accessRights` are never filled automatically. The tool only points
  to passages at the start of the interview where consent may have been recorded.
- **A draft, not a record.** Values the tool cannot know stay empty, and segment labels start with `TODO`, so the
  draft fails validation until a person has completed it.
- **Manual edits are safe.** An existing draft is never overwritten; `--fresh` keeps the old one as a backup.

## Install

```sh
git clone https://github.com/klausbehnamshad/diar2meta.git ~/Downloads/diar2meta
```

Add one line to `~/.bash_profile` (bash) or `~/.zshrc` (zsh), with your institution code and name, and open a new
Terminal window:

```sh
alias diar2meta='DIAR2META_INSTITUTION=LHI DIAR2META_INTERVIEWER="Jane Doe" python3 ~/Downloads/diar2meta/diar2meta.py'
```

## Everyday use

1. Copy the result folders of diar2 (`~/Downloads/diar2/output/NAME/`) or single transcripts into
   `~/Downloads/diar2meta/input`.
2. Open the Terminal and type `diar2meta`.
3. Find the results in `~/Downloads/diar2meta/output/NAME/`: read `NAME.pruefliste.txt`, complete
   `NAME.imm.draft.json`.

diar2meta processes every transcript in `input/` that has no draft yet and skips the others, so manual edits are
kept. Other ways to call it:

```sh
diar2meta ~/Downloads/diar2/output        # read the diar2 results directly, without copying
diar2meta path/to/NAME.diar2.json         # one file
diar2meta --fresh                         # recompute existing drafts; the old draft is kept as a backup
```

The `input/` and `output/` folders are listed in `.gitignore`.

## Inputs

| Format | Speakers | Notes |
|---|---|---|
| `NAME.diar2.json` | yes | Preferred. Uses sentences, speakers, language and the recording checksum. |
| `NAME.diar2.txt` | yes | Paragraphs `[HH:MM:SS.s] Speaker: text`. |
| `.srt`, `.vtt` | if prefixed (`Speaker: text`) | |
| Whisper `.json`, `.tsv` | no | Segments are split at pauses instead of questions. |

For a folder, diar2meta takes one source per interview and prefers `NAME.diar2.json`.

## Outputs

| File | Purpose |
|---|---|
| `NAME.imm.draft.json` | IMM-Core record draft; edit this file |
| `NAME.imm.provenance.json` | Per field: value, method (`regel`, `konfiguration`, `nur-mensch`, `fehlt`), rule, evidence; source checksums and models; validation result |
| `NAME.pruefliste.txt` | Review list: fill in, confirm, optional, validation |

## What is derived, and how

| Field | Method |
|---|---|
| `record_id` | `YYYY-MM-DD_{INSTITUTION}_{NNNN}` from the date, `DIAR2META_INSTITUTION` and the next free number in the output folder. Only locally unique. |
| `interview_date` | First available of: `creation_time` of the original recording (ffprobe; the file is matched by the checksum diar2 recorded), a date in the file name, a date spoken in the first 3 minutes, the file's modification date. Conflicting candidates are reported. |
| `interviewer` | `DIAR2META_INTERVIEWER`. |
| `language` | Language code of the transcription, mapped to ISO 639-3 (`de` → `deu`, `lb` → `ltz`). |
| `interviewee_display` | Only if `DIAR2META_PSEUDONYM_PREFIX` is set: prefix plus the number of the `record_id`. Never taken from the transcript. |
| `timecoded_segments` | Candidate boundaries are the questions of the person who asks most questions (at least 4 words; short follow-ups such as "Und dann?" do not count). The boundaries with the largest change in vocabulary between the two minutes before and after are chosen, about one per 5 minutes and at least 2 minutes apart. Labels stay `TODO`; the review list shows the question and the distinctive words of each segment as help. |
| `consent_status`, `accessRights`, `title` | Left empty. |
| `spatial`, `keywords`, `abstract`, `related_materials` | Not filled in this version. |

diar2 calls the first voice "Interviewer". If another speaker asks most of the questions, the review list warns that
the roles may be swapped.

## Example

Review list for a synthetic 20-minute dialogue (from the tests):

```text
BESTÄTIGEN
  5. interview_date
       Vorschlag: 2024-05-18   (Datum im Dateinamen)
       weiterer Kandidat: 2024-05-18 (im Gespräch genannt: 00:00:00 „Heute ist der 18. Mai 2024.“)
  8. timecoded_segments
       Grenzen an Fragen mit dem größten Themenwechsel (Ziel 300 s je Abschnitt, mindestens 120 s).
       Labels ersetzen (stehen als TODO im Entwurf):
          1. 00:00:00 - 00:06:53  Beginn
             Stichwörter: bauernhof, kühe, aufgewachsen, dorf, gemolken, halfen
          2. 00:06:53 - 00:13:34  Frage: „Wie war die Schule für Sie?“
             Stichwörter: schule, lehrer, dauerte, familien, kannte, lernten
          3. 00:13:34 - 00:20:14  Frage: „Wie begann Ihre Arbeit im Stahlwerk?“
             Stichwörter: stahlwerk, hochofen, begann, hart, heiß, italien
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DIAR2META_IN` | `input/` in this folder | Where to look when no path is given |
| `DIAR2META_OUT` | `output/` in this folder | Folder for results |
| `DIAR2META_FRESH` | `0` | `1` recomputes existing drafts (old draft kept as `.bak-…`) |
| `DIAR2META_INSTITUTION` | empty | Institution code for `record_id`, e.g. `LHI` |
| `DIAR2META_INTERVIEWER` | empty | Interviewer name, optionally with ORCID in parentheses |
| `DIAR2META_PSEUDONYM_PREFIX` | empty | Prefix for `interviewee_display`, e.g. `Person` |
| `DIAR2META_REC_DIRS` | `~/Downloads/diar2/input:~/Downloads` | Where to look for the original recording |
| `DIAR2META_SEG_TARGET_S` | `300` | Target segment length |
| `DIAR2META_SEG_MIN_S` | `120` | Minimum segment length |
| `DIAR2META_COHESION_WINDOW_S` | `120` | Window on each side for the vocabulary comparison |
| `DIAR2META_QUESTION_MIN_WORDS` | `4` | Shorter questions are follow-ups, not boundaries |
| `DIAR2META_PAUSE_MIN_S` | `2.0` | Minimum pause for a boundary when there are no speakers |
| `DIAR2META_CONSENT_HEAD_S` | `300` | Where to look for consent passages |
| `DIAR2META_DATE_HEAD_S` | `180` | Where to look for a spoken date |

## Limitations

- Segment boundaries are a heuristic. They have been checked on synthetic dialogues and looked at on one real
  interview, without a reference segmentation.
- The stop-word list covers German, English and French only partly; Luxembourgish is not covered.
- `schema/core.schema.json` is IMM-Core 1.0.1 (not yet released); the built-in validator covers the subset of JSON
  Schema that IMM-Core uses and is tested against the IMM-Core examples.
- Profiles (e.g. IMM-Profile-LuxOH) are not yet validated.

## Roadmap

1. Local language model (mlx-lm or Ollama) for `title`, `abstract`, `keywords` and segment labels, with a check
   that every claim cites passages that exist in the transcript, and the IMM-Core lint rules (descriptive, not
   analytic).
2. Warnings for re-identifying details (names, employers, addresses) in title and abstract.
3. Profile validation (LuxOH, Migration).

## Tests

```sh
python3 -m pytest tests
```

The tests use synthetic dialogues only.

## License

Apache-2.0, see [LICENSE](LICENSE).

## Credits

Developed by Klaus Behnam Shad, who designed the method, made all methodological decisions and is responsible for the content.

AI tools assisted with parts of the work:

- **Claude (Anthropic):** planning, design review and documentation
- **Codex (OpenAI):** implementation of code components
- **Muse (Meta):** prototype build from the approved plan

The author reviewed every AI-assisted contribution before it was adopted.

## Kurzfassung

diar2meta erstellt aus lokalen Transkripten (z. B. von diar2) Entwürfe für IMM-Core-Metadaten. Automatisch
ermittelt werden Datum, Sprache, Zeitabschnitte und die record_id. Jeder Wert ist mit seiner Herkunft belegt. Eine
Prüfliste zeigt, was von Hand auszufüllen ist. Einwilligung und Zugangsrechte werden nie automatisch gesetzt. Transkripte in
`~/Downloads/diar2meta/input` legen (z. B. die Ergebnisordner von diar2), `diar2meta` tippen, Ergebnis in
`~/Downloads/diar2meta/output/NAME/`. Das Programm läuft offline und braucht nur die Python-Standardbibliothek.
