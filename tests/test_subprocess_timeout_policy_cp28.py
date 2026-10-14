"""CP28 static safety/performance guardrails for subprocesses and dead BGM paths."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_subprocess_run_style_calls_have_explicit_timeouts():
    """Every potentially blocking subprocess API call must bound its wait."""
    checked = {"run", "call", "check_call", "check_output"}
    calls = []
    missing = []
    for path in (ROOT / "video_maker").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id == "subprocess" and fn.attr in checked):
                calls.append((path, node.lineno, fn.attr))
                if not any(arg.arg == "timeout" for arg in node.keywords):
                    missing.append((path, node.lineno, fn.attr))
    assert calls, "audit unexpectedly found no subprocess calls"
    assert not missing, "subprocess calls missing timeout: " + repr(missing)


def test_pipeline_has_no_disabled_legacy_bgm_mix_blocks():
    for relative in ("video_maker/pipeline/branches.py", "video_maker/pipeline/shorts.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "if False and ctx.add_bgm" not in source, relative
        assert "elif False and ctx.add_bgm" not in source, relative


def test_bgm_metadata_cache_is_bounded_and_invalidation_key_uses_mtime_and_size():
    source = (ROOT / "video_maker/engines/bgm_validation.py").read_text(encoding="utf-8")
    assert "_PROBE_CACHE_MAX = 2048" in source
    assert "stat.st_mtime_ns" in source
    assert "size)" in source
    assert "_PROBE_CACHE.move_to_end" in source
