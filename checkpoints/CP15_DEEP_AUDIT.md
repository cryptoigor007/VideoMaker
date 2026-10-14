# CP15 — Docs, compatibility and installation hygiene

Date: 2026-10-12

## Purpose

Обновлены README/CHANGES/VERSION, исправлены требования MLX/macOS, устаревшие patch-notes перемещены в архив, `.command` launchers больше не игнорируются gitignore.

## Verification

- Pytest: 118 passed, 2 subtests passed.
- Python compilation: `python -m compileall -q video_maker tests` — PASS.

## Recovery guarantee

Этот ZIP содержит полное дерево проекта: GUI, API/server, pipeline, engines, config, тесты, launch scripts, docs, история и checkpoint reports. Не включаются лишь локальный `.env`, runtime caches, `.pyc`, `_tmp` и сгенерированные `*.log`.

## Progress

- Original VideoMaker roadmap: 5/5 complete (100%).
- Deep-audit phases: 4/5 complete (80%).
- Current phase: CP15; final full regression/package remains.

Source manifest: `checkpoints/CP15_SOURCE_MANIFEST.json`.
