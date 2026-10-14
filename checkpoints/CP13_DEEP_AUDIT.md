# CP13 — Shorts duplication and metadata

Date: 2026-10-12

## Purpose

Добавлены проверяемые дубли одинаковых Shorts/BGM между папками площадок, отдельное сохранение разных музыкальных вариантов и сохранение пользовательских required_hashtags в sidecar/metadata.

## Verification

Pytest: 107 passed, 2 subtests passed; compileall PASS.

## Recovery guarantee

This archive contains the full project tree. It is not an incremental patch. Runtime caches, local `.env`, `_tmp`, `.pyc`, and generated `*.log` files are omitted; source, tests, launch scripts, config examples, docs, checkpoint reports and manifests are included.

## Progress

- Original VideoMaker roadmap: 5/5 complete (100%).
- Deep-audit phases: 2/5 complete (40%).
- Current phase: CP13.


Source manifest: `checkpoints/CP13_SOURCE_MANIFEST.json`.
