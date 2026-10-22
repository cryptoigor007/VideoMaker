"""Синтетические проверки панели «Проверка подбора» без запуска Tk.

Создаём scenes.jsonl на 3 сцены в типовой раскладке
``output/broll_smart/<series>/``, проверяем загрузку/фильтрацию, запись
review_feedback.jsonl и экспорт в один JSONL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from video_maker.gui import review_feedback as review


def _scene(
    idx: int,
    text: str,
    score: int | None,
    *,
    fallback: str = "none",
    clip: str = "",
) -> dict:
    return {
        "scene_id": f"scene_{idx:03d}",
        "scene_index": idx,
        "scene_text": text,
        "queries": [text],
        "candidate_ids": [],
        "candidate_scores": [],
        "selected_clip": clip or f"/pool/{idx + 1:02d}_scene/clip_{idx + 1:02d}.mp4",
        "selected_score": score,
        "fallback_used": fallback,
        "search_ms": 12,
        "vlm_calls": 1,
        "cache_hits": 0,
        "ts": "2026-10-20T10:00:00+00:00",
    }


def _write_scenes(path: Path, scenes: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(sc, ensure_ascii=False) for sc in scenes) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def isolated_review_dir(tmp_path, monkeypatch):
    """Не читать/писать настоящий ~/video_maker/review в тестах."""
    monkeypatch.setattr(review, "REVIEW_DIR", tmp_path / "review_home")
    return tmp_path / "review_home"


def test_load_rows_filter_and_latest_series(tmp_path, isolated_review_dir):
    out = tmp_path / "output"
    old_dir = out / "broll_smart" / "old_series"
    new_dir = out / "broll_smart" / "new_series"
    old_scenes = _write_scenes(
        old_dir / "scenes.jsonl",
        [_scene(0, "старая сцена", 9), _scene(1, "ещё старая", 8),
         _scene(2, "третья", 7)],
    )
    new_scenes = _write_scenes(
        new_dir / "scenes.jsonl",
        [
            _scene(0, "герой входит в офис", 8),
            _scene(1, "дождь на пустой остановке", 4, fallback="literal_proxy"),
            _scene(2, "крупный план глаз", None, fallback="no_file"),
        ],
    )
    os.utime(old_scenes, (1_000, 1_000))
    os.utime(new_scenes, (2_000, 2_000))

    found = review.find_latest_scenes([out])
    assert found == new_scenes, "панель должна брать самый свежий прогон"
    assert review.series_for(found) == "new_series"

    rows = review.load_scene_rows(found)
    assert [row["scene_no"] for row in rows] == [1, 2, 3]
    assert rows[0]["scene_text"] == "герой входит в офис"
    assert rows[1]["score"] == 4
    assert rows[1]["fallback"] == "literal_proxy"
    assert rows[2]["score"] is None

    fallback_rows = review.filter_scene_rows(rows, "fallback")
    assert [r["scene_no"] for r in fallback_rows] == [2, 3]
    weak_rows = review.filter_scene_rows(rows, "low_score")
    # низкий балл и отсутствие клипа одинаково важны для обучения
    assert [r["scene_no"] for r in weak_rows] == [2, 3]
    assert len(review.filter_scene_rows(rows, "all")) == 3

    # Дубли (resume/консолидация) не должны размножать строки: побеждает
    # последняя запись с тем же scene_id.
    with new_scenes.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_scene(1, "обновлённый текст", 6), ensure_ascii=False) + "\n")
    deduped = review.load_scene_rows(new_scenes)
    assert len(deduped) == 3
    assert deduped[1]["scene_text"] == "обновлённый текст"
    assert deduped[1]["score"] == 6


def test_append_feedback_writes_required_fields(tmp_path, isolated_review_dir):
    scenes_path = _write_scenes(
        tmp_path / "output" / "broll_smart" / "серия_07" / "scenes.jsonl",
        [
            _scene(0, "сцена один", 7),
            _scene(1, "сцена два", 3, fallback="repeat_clip"),
            _scene(2, "сцена три", None, fallback="no_file"),
        ],
    )
    feedback_path = review.feedback_path_for(scenes_path)
    assert feedback_path == scenes_path.parent / "review_feedback.jsonl"

    row = review.load_scene_rows(scenes_path)[1]
    rec = review.append_feedback(
        scenes_path,
        row["raw"],
        reason="wrong_action",
        series="серия_07",
        ts="2026-10-22T12:00:00+00:00",
    )
    assert set(rec) == {
        "ts", "series", "scene_id", "scene_text", "selected_clip",
        "score", "fallback", "reason", "source",
    }
    assert rec["scene_id"] == "scene_001"
    assert rec["scene_text"] == "сцена два"
    assert rec["score"] == 3
    assert rec["fallback"] == "repeat_clip"
    assert rec["reason"] == "wrong_action"
    assert rec["source"] == "ui"

    lines = [
        json.loads(line)
        for line in feedback_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lines == [rec]
    assert lines[0]["selected_clip"].endswith("clip_02.mp4")
    archive_path = isolated_review_dir / review.FEEDBACK_FILENAME
    assert review.load_feedback(archive_path) == [rec], (
        "архив в ~/video_maker/review должен переживать очистку папки прогона"
    )

    # Удобный alias для UI/API → канонический repeat/wrong_scene.
    alias = review.append_feedback(
        scenes_path, row["raw"], reason="repeat", series="серия_07",
    )
    assert alias["reason"] == "repeat/wrong_scene"
    assert len(review.load_feedback(feedback_path)) == 2
    assert len(review.load_feedback(archive_path)) == 2

    with pytest.raises(ValueError):
        review.append_feedback(scenes_path, row["raw"], reason="because_i_said_so")


def test_collect_export_dedup_and_config(tmp_path, isolated_review_dir):
    out = tmp_path / "output"
    first = _write_scenes(
        out / "broll_smart" / "series_a" / "scenes.jsonl",
        [_scene(0, "первая", 2)],
    )
    second = _write_scenes(
        out / "broll_smart" / "series_b" / "scenes.jsonl",
        [_scene(0, "вторая", 9)],
    )
    rec_a = review.append_feedback(
        first, review.load_scene_rows(first)[0]["raw"],
        reason="low_quality", ts="2026-10-22T10:00:00+00:00",
    )
    rec_b = review.append_feedback(
        second, review.load_scene_rows(second)[0]["raw"],
        reason="wrong_atmosphere", ts="2026-10-22T10:01:00+00:00",
    )
    # Повторная запись того же отзыва не должна попасть в экспорт дважды.
    review.append_feedback(
        first, review.load_scene_rows(first)[0]["raw"],
        reason="low_quality", ts=rec_a["ts"],
    )

    collected = review.collect_all_feedback(
        search_dirs=[out], current_scenes=second,
    )
    assert [r["ts"] for r in collected] == [rec_a["ts"], rec_b["ts"]]
    assert {r["reason"] for r in collected} == {"low_quality", "wrong_atmosphere"}

    dest = tmp_path / "ledger" / "feedback_export.jsonl"
    exported = review.export_feedback(dest, collected)
    assert exported == dest
    lines = [
        json.loads(line)
        for line in dest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2
    assert {r["source"] for r in lines} == {"ui"}

    config = tmp_path / "review_home" / "config.json"
    review.save_review_config({"scenes_path": str(second)}, config)
    assert review.load_review_config(config)["scenes_path"] == str(second)

    # Явный выбор пользователя приоритетнее самого свежего прогона.
    assert review.find_latest_scenes([out], explicit=str(first)) == first


def test_review_panel_renders_and_records_from_ui(tmp_path, isolated_review_dir, monkeypatch):
    """Лёгкая проверка панели: 3 сцены видны, кнопка причины пишет feedback."""
    import tkinter as tk

    from video_maker.gui.app import App

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover — display-only skip
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    app = None
    try:
        scenes = _write_scenes(
            tmp_path / "output" / "broll_smart" / "ui_series" / "scenes.jsonl",
            [
                _scene(0, "первая сцена", 8),
                _scene(1, "вторая сцена", 3, fallback="literal_proxy"),
                _scene(2, "третья сцена", None, fallback="no_file"),
            ],
        )
        app = App(root)
        app.output_var.set(str(tmp_path / "output"))
        app._refresh_review_panel()
        root.update_idletasks()

        assert len(app._review_scenes) == 3
        assert "Сцен: 3" in app.review_summary_var.get()
        assert "ui_series" in app.review_summary_var.get()
        assert len(app._review_shown_rows) == 3

        app._record_review_feedback(app._review_shown_rows[1], "wrong_action")
        root.update_idletasks()

        stored = [
            json.loads(line)
            for line in (scenes.parent / review.FEEDBACK_FILENAME)
            .read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(stored) == 1
        assert stored[0]["scene_id"] == "scene_001"
        assert stored[0]["reason"] == "wrong_action"
        assert stored[0]["source"] == "ui"
        assert app._review_feedback_index["scene_001"]["reason"] == "wrong_action"
    finally:
        root.destroy()

