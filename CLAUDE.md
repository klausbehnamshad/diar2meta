# Regeln für Arbeiten an diesem Repository

- Commits und Pull-Request-Texte enthalten keine Claude-Attribution und keine Emojis.
  Die Zusammenarbeit wird nur im README unter Acknowledgements genannt.
- Pull Requests legt der Autor selbst an.
- Keine persönlichen Daten, keine echten Interviewdaten und keine Pfade echter
  Aufnahmen im Repository. Tests arbeiten nur mit synthetischen Daten.
- `diar2meta.py` bleibt reine Standardbibliothek und läuft offline.
- `consent_status` und `accessRights` werden nie automatisch gefüllt.
- Vorhandene Entwürfe werden nie überschrieben; `--fresh` legt eine Sicherung an.
- `schema/core.schema.json` ist eine Kopie aus imm-core und wird nur von dort übernommen.
- Vor jedem Commit: `python3 -m pytest tests`.
