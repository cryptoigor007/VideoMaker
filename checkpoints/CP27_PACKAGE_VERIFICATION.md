# CP27 package verification — final

Complete source/recovery archive through CP27. Includes GUI, API/server, pipeline and engines, tests, docs, benchmark, prior checkpoints and audit records. `.env`, runtime `_tmp`, generated renders, pytest caches and bytecode are excluded; `.env.example` is included.

The ZIP is checked for CRC and unique paths; the source manifest is compared after extraction. Manifest and this verification record are intentionally self-excluded from the source manifest but included in the archive. A fresh `compileall` and complete test suite are run from the extracted tree.

Expected test result for this source revision: 180 passed, 1 display-dependent skip in headless mode, 23 subtests passed. The skip is not a functional failure.
