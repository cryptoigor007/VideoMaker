# VideoMaker checkpoints — restore and continue

Each released CP12–CP19 archive is a complete source-tree snapshot including client GUI, API/server, pipeline, engines, settings, tests, docs and checkpoint notes. It is not a diff.

## Historical archives issued before the deep audit

| Checkpoint | Integrity/recovery status |
|---|---|
| CP07 original | INVALID — previously issued ZIP file was 0 bytes, cannot restore |
| CP08 original | ZIP was readable; historical snapshot predates deep-audit fixes |
| CP09 original | ZIP was readable; historical snapshot predates deep-audit fixes |
| CP10 original | INVALID — previously issued ZIP file was 0 bytes, cannot restore |
| CP11 original | ZIP was readable; embedded manifest matched 103 project files |

The original standalone CP07 and CP10 ZIP files were each 0 bytes. They are recorded as invalid; they are not included as valid restore points. The invalid historical CP07/CP10 snapshots cannot be reconstructed byte-for-byte. CP19 is the latest valid full source snapshot for continuing work; CP16 is retained as a historical full snapshot; it is not a claim that the old CP07/CP10 historical states were recreated.

## Deep-audit checkpoints

| Checkpoint | Purpose | Report | Standalone full archive |
|---|---|---|---|
| CP12 | CLI lifecycle and finalization hardening | `checkpoints/CP12_DEEP_AUDIT.md` | `VideoMaker_FULL_R57_CP12_AUDIT_CLI_AND_FINALIZE.zip`
| CP13 | Shorts duplication and metadata | `checkpoints/CP13_DEEP_AUDIT.md` | `VideoMaker_FULL_R57_CP13_SHORTS_DEDUP_AND_METADATA.zip`
| CP14 | GUI worker boundary and per-platform BGM settings | `checkpoints/CP14_DEEP_AUDIT.md` | `VideoMaker_FULL_R57_CP14_BGM_GUI_SETTINGS.zip`
| CP15 | Docs, compatibility and installation hygiene | `checkpoints/CP15_DEEP_AUDIT.md` | `VideoMaker_FULL_R57_CP15_DOCS_AND_COMPATIBILITY.zip`
| CP16 | Final regression, source manifest and release package | `checkpoints/CP16_FINAL_REGRESSION_AND_PACKAGE.md` | `VideoMaker_FULL_R57_CP16_FINAL_SOURCE.zip`

| CP17 | External process deadlines and atomic audio output | `checkpoints/CP17_TIMEOUT_POLICY.md` | `VideoMaker_FULL_R57_CP17_TIMEOUT_POLICY.zip` |
| CP18 | LUFS scope/range and API/GUI validation | `checkpoints/CP18_LUFS_SCOPE.md` | `VideoMaker_FULL_R57_CP18_LUFS_SCOPE.zip` |
| CP19 | API token/log redaction, strict config, bounded JSON request bodies | `checkpoints/CP19_API_SECURITY.md` | `VideoMaker_FULL_R57_CP19_API_SECURITY.zip` |

## Restore procedure

1. Extract the selected ZIP into a new folder.
2. Read `checkpoints/ROADMAP_STATUS.json` and the report for that checkpoint.
3. Validate hashes in the matching manifest: CP16 uses `checkpoints/CP16_SOURCE_MANIFEST.json`; CP17 uses `checkpoints/CP17_SOURCE_MANIFEST.json`; CP18 uses `checkpoints/CP18_SOURCE_MANIFEST.json`; CP19 uses `checkpoints/CP19_SOURCE_MANIFEST.json`. Historical manifests are retained for history and do not describe later snapshots.
4. Run `python -m compileall -q video_maker tests` and `python -m pytest tests/ -q`.
5. For any later change, start with a new checkpoint that copies the full tree and runs regression checks before the ZIP is considered valid.


## CP30 — API lifecycle fixes

See `CP30_API_LIFECYCLE.md`. Full source archive checkpoint `VideoMaker_FULL_R57_CP30_API_LIFECYCLE.zip`; tested from the extracted archive.

| CP30 | API lifecycle / truthful run status | `CP30_API_LIFECYCLE.md` | `VideoMaker_FULL_R57_CP30_API_LIFECYCLE.zip` |
| CP31 | Result readiness, B-roll preflight, strict bool types | `CP31_READINESS_AND_STRICT_TYPES.md` | `VideoMaker_FULL_R57_CP31_READINESS_TYPES.zip` |
| CP32 | First-run bootstrap and runtime dependency installation | `CP32_FIRST_RUN_BOOTSTRAP.md` | `VideoMaker_FULL_R57_CP32_FIRST_RUN_BOOTSTRAP.zip` |
| CP33 | Atomic checkpoint artifacts and dependency-aware resume fingerprints | `CP33_CHECKPOINT_INTEGRITY.md` | `VideoMaker_FULL_R57_CP33_CHECKPOINT_INTEGRITY.zip` |

| CP33 | Atomic checkpoint artifacts and dependency-aware resume fingerprints | `CP33_CHECKPOINT_INTEGRITY.md` | `VideoMaker_FULL_R57_CP33_CHECKPOINT_INTEGRITY.zip` |
| CP34 | TkBridge timeout cancellation and graceful GUI shutdown | `CP34_BRIDGE_TIMEOUT_AND_SHUTDOWN.md` | `VideoMaker_FULL_R57_CP34_BRIDGE_SHUTDOWN.zip` |
