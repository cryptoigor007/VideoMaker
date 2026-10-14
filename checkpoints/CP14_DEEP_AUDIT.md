# CP14 — GUI worker boundary and per-platform BGM settings

Date: 2026-10-12

## Purpose

Worker использует снимок значений из главного Tk-потока; BGM YouTube независимо от aggregate add_bgm; источники BGM валидируются до дорогого рендера. Объединены GUI/API и regressions.

## Verification

Pytest: 118 passed, 2 subtests passed; compileall PASS.

## Recovery guarantee

This archive contains the full project tree. It is not an incremental patch. Runtime caches, local `.env`, `_tmp`, `.pyc`, and generated `*.log` files are omitted; source, tests, launch scripts, config examples, docs, checkpoint reports and manifests are included.

## Progress

- Original VideoMaker roadmap: 5/5 complete (100%).
- Deep-audit phases: 3/5 complete (60%).
- Current phase: CP14.


Source manifest: `checkpoints/CP14_SOURCE_MANIFEST.json`.
