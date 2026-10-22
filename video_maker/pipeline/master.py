"""MasterBuilder — только master_16x9; вертикаль собирается в Final (один encode).

Cinematic Enhancement: optional kwargs to fit_video_to_duration.
When cinematic_enabled=False (default), behavior is identical to baseline.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)

# Единый источник per_scene для smart_broll: GUI читает это же значение,
# чтобы «нужно = сцены × per_scene» совпадало с фактическим подбором.
SMART_BROLL_PER_SCENE = 1


def _manifest_scene_flags(manifest: dict | None) -> dict[int, dict]:
    """1-based scene → P0-2 флаги из манифеста smart_broll."""
    out: dict[int, dict] = {}
    for sc in ((manifest or {}).get("scenes") or []):
        try:
            si = int(sc.get("idx", -1)) + 1
        except (TypeError, ValueError):
            continue
        if si <= 0:
            continue
        out[si] = {
            "fallback": str(sc.get("fallback") or "none"),
            "treated": bool(sc.get("treated")),
            "graphic_fallback": bool(sc.get("graphic_fallback")),
            "selected_score": sc.get("selected_score"),
        }
    return out


def _log_fallback_summary(flags: dict[int, dict], ctx: PipelineContext,
                          prefix: str = "baseline") -> None:
    if not flags:
        return
    counts = Counter(str(v.get("fallback") or "none") for v in flags.values())
    treated = sum(1 for v in flags.values() if v.get("treated"))
    graphic = sum(1 for v in flags.values() if v.get("graphic_fallback"))
    ctx.log(f"[SB] {prefix}: fallback " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))
    if treated:
        ctx.log(f"[SB] {prefix}: treated (лёгкая обработка) — {treated} сцен")
    if graphic:
        ctx.log(f"[SB] {prefix}: graphic_fallback — {graphic} сцен без клипа; "
                f"рендер ставит нейтральный фон/карточку (B4)")


def _disable_dobor() -> bool:
    """[PERF-BUDGET] Отключён ли добор слабых сцен (fix_scenes).

    Приоритет: env VM_DISABLE_DOBOR → ~/.video_maker_settings.json:disable_dobor.
    По умолчанию выключено (добор работает как раньше).
    """
    raw = os.environ.get("VM_DISABLE_DOBOR")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "да"}
    try:
        settings_path = os.path.join(os.path.expanduser("~"), ".video_maker_settings.json")
        with open(settings_path, encoding="utf-8") as f:
            data = json.load(f)
        value = data.get("disable_dobor", False) if isinstance(data, dict) else False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "да"}
        return bool(value)
    except Exception:
        return False


def _run_fix_scenes_filtered(script: str, manifest_path: str, log_fn) -> None:
    """fix_scenes только для сцен НЕ в graphic_fallback.

    fix_scenes иначе добивает пустые graphic-сцены клипами 2-4/10 и обходит
    политику P0-2. Оригинальный манифест обновляем только после успеха.
    """
    import subprocess
    import sys

    mp = Path(manifest_path)
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
    except Exception as e:
        log_fn(f"[MASTER] fix_scenes: манифест не прочитан: {str(e)[:80]}")
        return
    scenes = man.get("scenes") or []
    keep = [s for s in scenes if not s.get("graphic_fallback")]
    if len(keep) == len(scenes):
        subprocess.run([sys.executable, script, str(mp)], timeout=5400)
        return
    if not keep:
        log_fn("[MASTER] fix_scenes пропущен: все сцены graphic_fallback")
        return
    tmp = mp.with_name("smart_broll_manifest.fix.json")
    try:
        tmp_man = dict(man)
        tmp_man["scenes"] = keep
        # [VM-FIX m5] атомарная запись temp-манифеста: не оставляем полуфайл,
        # если процесс упадёт между write_text и запуском fix_scenes.
        _tmp_write = tmp.with_name(f"{tmp.name}.{os.getpid()}.tmp")
        try:
            _tmp_write.write_text(json.dumps(tmp_man, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            os.replace(_tmp_write, tmp)
        finally:
            try:
                if _tmp_write.exists():
                    _tmp_write.unlink()
            except Exception:
                pass
        subprocess.run([sys.executable, script, str(tmp)], timeout=5400)
        fixed = json.loads(tmp.read_text(encoding="utf-8"))
        by_idx = {
            int(s.get("idx", -1)): s
            for s in (fixed.get("scenes") or [])
            if isinstance(s, dict)
        }
        for sc in scenes:
            if sc.get("graphic_fallback"):
                continue
            upd = by_idx.get(int(sc.get("idx", -1)))
            if isinstance(upd, dict):
                sc.update(upd)
        mp.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        log_fn(f"[MASTER] fix_scenes: graphic_fallback оставлены без клипа: "
               f"{len(scenes) - len(keep)}")
    except Exception as e:
        log_fn(f"[MASTER] fix_scenes с фильтром пропущен: {str(e)[:100]}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _ce_for_ctx(ctx: PipelineContext, video_files: list[str], output_format: str = "16:9"):
    """Resolve CE settings only. EditPlan is built in fit_video_to_duration
    on the FINAL selected list (after probe/skip/repeat) — avoids index mismatch.
    """
    try:
        from video_maker.cinematic.vm_bridge import settings_from_vm

        ce = settings_from_vm(ctx)
        ctx.cinematic_enabled = bool(ce.enabled)
        ctx.cinematic_settings = ce
        # Stash transcript segments for engine-side plan rebuild
        segs = []
        tr = getattr(ctx, "transcription", None) or {}
        if isinstance(tr, dict):
            segs = list(tr.get("segments") or [])
        ctx.cinematic_transcript_segments = segs
        ctx.cinematic_output_format = output_format
        if ce.enabled:
            ctx.log(f"[CE] enabled profile={ce.profile} format={output_format} "
                    f"candidates={len(video_files)} (plan after selection)")
        return ce, None
    except Exception as e:
        ctx.log(f"[CE] skip ({e})")
        return None, None


class MasterBuilder(Stage):
    """Создаёт master_16x9. master_9x16 больше не кодируется здесь (экономия 1 encode)."""

    def name(self) -> str:
        return "Master"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.log("[MASTER] Создание master_16x9 (вертикаль — в Final, один encode)...")

        ctx.master_horizontal = self._build_master_horizontal(ctx)
        ctx.log(f"[MASTER] master_16x9.mp4: {ctx.master_horizontal}")

        ctx.master_vertical = ""
        if ctx.broll_vertical:
            vertical_files = self._collect_vertical_files(ctx.broll_vertical)
            if vertical_files:
                ctx.master_vertical = self._build_master_vertical_native(ctx, vertical_files)
                ctx.log(f"[MASTER] master_9x16 native: {ctx.master_vertical}")
            else:
                ctx.log("[MASTER] VSTACK отложен → FinalVertical (вместе с субтитрами)")
        elif ctx.vertical_background:
            ctx.log("[MASTER] VSTACK отложен → FinalVertical (вместе с субтитрами)")
        else:
            raise RuntimeError(
                "Для вертикального видео нужен broll_vertical или vertical_background"
            )

        ctx.progress = 45.0
        return ctx

    def _collect_vertical_files(self, folder: str) -> list[str]:
        from ..engines.video import collect_video_files, _ffprobe_video_info
        all_files = collect_video_files(folder)
        vertical = []
        _img = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
        for f in all_files:
            ext = os.path.splitext(f)[1].lower()
            if ext in _img:
                # Stills always allowed in vertical pool (Ken Burns / scale)
                vertical.append(f)
                continue
            try:
                w, h, _ = _ffprobe_video_info(f)
                if h > w * 0.8:
                    vertical.append(f)
            except Exception:
                pass
        return vertical

    def _build_master_horizontal(self, ctx: PipelineContext) -> str:
        from ..engines.video import (
            collect_video_files,
            collect_video_files_scene_order,
            fit_video_to_duration,
        )

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "master_16x9.mp4")

        # [VM-SB] «Умный B-roll» в самом приложении: пул собирается из большой базы
        # по смыслу сцен (library-first → докачка → VLM-проверка). Выключен — как раньше.
        _sb_on = True
        try:
            import json as _json
            _sp = os.path.join(os.path.expanduser("~"), ".video_maker_settings.json")
            if os.path.exists(_sp):
                with open(_sp, encoding="utf-8") as _f:
                    _sb_on = bool(_json.load(_f).get("cinematic_smart_broll", True))
        except Exception:
            pass
        video_files: list[str] = []
        _used_smart = False
        # [ORD] scene_idx (1-based) → лучшие файлы из манифеста (порядок = приоритет)
        _scene_preferred: dict[int, list[str]] = {}
        # [P0-2] scene_idx (1-based) → fallback/treated/graphic-флаги
        _scene_flags: dict[int, dict] = {}
        if _sb_on and getattr(ctx, "audio_path", ""):
            try:
                from ..semantic.smart_broll import run_smart_broll
                _pool_root = os.path.join(ctx.output_folder, "broll_smart")
                _man = run_smart_broll(audio_path=ctx.audio_path, out_root=_pool_root,
                                       per_scene=SMART_BROLL_PER_SCENE, orientation="wide", verify=True,
                                       library_first=True, log=ctx.log)
                _dir = str(_man.get("out_dir") or "")
                if _dir:
                    _scene_flags = _manifest_scene_flags(_man)
                    _log_fallback_summary(_scene_flags, ctx, prefix="baseline")
                    try:
                        for _sc in (_man.get("scenes") or []):
                            _si = int(_sc.get("idx", -1)) + 1
                            _sf = [str(x) for x in (_sc.get("files") or []) if x]
                            if _si > 0 and _sf:
                                _scene_preferred[_si] = _sf
                        if _scene_preferred:
                            ctx.log(f"[ORD] манифест: лучшие клипы для "
                                    f"{len(_scene_preferred)} сцен")
                    except Exception as _sp_e:
                        ctx.log(f"[ORD] манифест-приоритет пропущен: {str(_sp_e)[:80]}")
                    # [VM-SB] добор слабых сцен: докачка со стока до порога (как в тестах).
                    # [P0-2] graphic_fallback-сцены в fix_scenes не отдаём: иначе
                    # пустые сцены добьются клипами 2-4/10 в обход политики.
                    try:
                        _fx = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                            os.path.abspath(__file__)))), "tools", "fix_scenes.py")
                        _mp = os.path.join(_dir, "smart_broll_manifest.json")
                        if _disable_dobor():
                            ctx.log("[MASTER] добор отключён настройкой")
                        elif os.path.exists(_fx) and os.path.exists(_mp):
                            ctx.log("[MASTER] добор слабых сцен (докачка до порога)…")
                            _run_fix_scenes_filtered(_fx, _mp, ctx.log)
                            try:
                                _fixed_man = json.loads(Path(_mp).read_text(encoding="utf-8"))
                                _scene_flags = _manifest_scene_flags(_fixed_man)
                                _log_fallback_summary(_scene_flags, ctx,
                                                      prefix="baseline после fix")
                            except Exception as _rfe:
                                ctx.log(f"[MASTER] флаги после fix не перечитаны: {str(_rfe)[:80]}")
                    except Exception as _fe:
                        ctx.log(f"[MASTER] добор пропущен: {str(_fe)[:80]}")
                    # [ORD] Сценный пул: папки 01_, 02_… строго по порядку.
                    # collect_video_files перемежает подпапки (round-robin) —
                    # клип сцены 02 оказывался на месте сцены 01.
                    video_files = collect_video_files_scene_order(_dir)
                    if video_files:
                        # [VM-SB] локальная копия пула: 60+ входов с SMB роняют
                        # фильтр ffmpeg (EAGAIN/-35) и читаются медленно —
                        # рендерим с локального SSD (структура папок сохраняется).
                        # [VM-FIX] Копия нужна только для СЕТЕВОГО тома (SMB):
                        # та же ФС — копия бессмысленна (лишняя нагрузка и риск
                        # обрыва на внешнем диске при 10ГБ записи).
                        _same_vol = False
                        try:
                            _out_dev = os.stat(ctx.output_folder).st_dev
                            _devs = set()
                            for _f in (video_files or [])[:50]:
                                try:
                                    _devs.add(os.stat(os.path.realpath(_f)).st_dev)
                                except Exception:
                                    pass
                            # «Тот же том» только если РЕАЛЬНЫЕ файлы (а не симлинки
                            # на SMB) лежат на том же устройстве, что и вывод.
                            _same_vol = bool(_devs) and _devs == {_out_dev}
                        except Exception:
                            _same_vol = False
                        if _same_vol:
                            ctx.log("[MASTER] пул на том же томе — локальная копия не нужна")
                        else:
                            try:
                                import shutil as _sh
                                _local = os.path.join(ctx.output_folder, "_tmp", "pool_local")
                                _sh.rmtree(_local, ignore_errors=True)
                                _copied = 0
                                for _f in video_files:
                                    _rel = os.path.relpath(_f, _dir)
                                    _dst = os.path.join(_local, _rel)
                                    os.makedirs(os.path.dirname(_dst), exist_ok=True)
                                    _sh.copy2(_f, _dst)
                                    _copied += 1
                                _lf = collect_video_files_scene_order(_local)
                                if _lf and len(_lf) == len(video_files):
                                    video_files = _lf
                                    _dir = _local
                                    ctx.log(f"[MASTER] пул скопирован локально: "
                                            f"{_copied} клипов (SMB не мешает рендеру)")
                            except Exception as _ce2:
                                ctx.log(f"[MASTER] локальная копия пула не удалась: {str(_ce2)[:90]}")
                        ctx.broll_horizontal = _dir
                        _used_smart = True
                        ctx.log(f"[MASTER] умный B-roll: пул из базы — {len(video_files)} клипов")
            except Exception as _e:
                ctx.log(f"[MASTER] умный B-roll пропущен: {str(_e)[:100]}")
        if not video_files:
            video_files = collect_video_files(ctx.broll_horizontal)
        if not video_files:
            raise FileNotFoundError(f"Нет медиа (видео/картинки) в {ctx.broll_horizontal}")
        ctx.log(f"[MASTER] B-roll H: {len(video_files)} медиа")

        ce, plan = _ce_for_ctx(ctx, video_files, output_format="16:9")
        if ce is not None:
            # Planner получает флаги через тот же объект настроек (без нового
            # параметра fit_video_to_duration); пустой словарь сбрасывает stale.
            try:
                setattr(ce, "scene_flags", dict(_scene_flags))
            except Exception:
                pass
        if _used_smart:
            # пул уже сценно-упорядочен (папки 01_, 02_…) — не перетасовывать матчером
            try:
                ce.smart_broll = False
                ctx.log("[MASTER] умный B-roll: перетасовка отключена (порядок сцен сохранён)")
            except Exception:
                pass

        fit_video_to_duration(
            video_files=video_files,
            target_duration=ctx.audio_duration,
            output_path=output_path,
            audio_file=ctx.narration_path or ctx.audio_path,
            log_fn=ctx.log,
            broll_root=ctx.broll_horizontal,
            move_used=True,
            cinematic_settings=ce,
            edit_plan=plan,
            transcript_segments=getattr(ctx, "cinematic_transcript_segments", None),
            output_format="16:9",
            width=3840,
            height=2160,
            scene_bound=bool(_used_smart),
            scene_preferred=_scene_preferred if _used_smart else None,
        )
        return output_path

    def _build_master_vertical_native(self, ctx: PipelineContext, video_files: list[str]) -> str:
        from ..engines.video import fit_video_to_duration

        output_dir = os.path.join(ctx.output_folder, "_tmp")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "master_9x16.mp4")

        ce, plan = _ce_for_ctx(ctx, video_files, output_format="9:16")

        fit_video_to_duration(
            video_files=video_files,
            target_duration=ctx.audio_duration,
            output_path=output_path,
            audio_file=ctx.narration_path or ctx.audio_path,
            log_fn=ctx.log,
            broll_root=ctx.broll_vertical,
            move_used=True,
            cinematic_settings=ce,
            edit_plan=plan,
            transcript_segments=getattr(ctx, "cinematic_transcript_segments", None),
            output_format="9:16",
            width=2160,
            height=3840,
        )
        return output_path
