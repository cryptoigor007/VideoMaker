"""MasterBuilder — только master_16x9; вертикаль собирается в Final (один encode).

Cinematic Enhancement: optional kwargs to fit_video_to_duration.
When cinematic_enabled=False (default), behavior is identical to baseline.
"""
from __future__ import annotations

import logging
import os

from .stages import PipelineContext, Stage

log = logging.getLogger(__name__)

# Единый источник per_scene для smart_broll: GUI читает это же значение,
# чтобы «нужно = сцены × per_scene» совпадало с фактическим подбором.
SMART_BROLL_PER_SCENE = 1


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
        if _sb_on and getattr(ctx, "audio_path", ""):
            try:
                from ..semantic.smart_broll import run_smart_broll
                _pool_root = os.path.join(ctx.output_folder, "broll_smart")
                _man = run_smart_broll(audio_path=ctx.audio_path, out_root=_pool_root,
                                       per_scene=SMART_BROLL_PER_SCENE, orientation="wide", verify=True,
                                       library_first=True, log=ctx.log)
                _dir = str(_man.get("out_dir") or "")
                if _dir:
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
                    # [VM-SB] добор слабых сцен: докачка со стока до порога (как в тестах)
                    try:
                        import subprocess as _sp
                        import sys as _sys
                        _fx = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                            os.path.abspath(__file__)))), "tools", "fix_scenes.py")
                        _mp = os.path.join(_dir, "smart_broll_manifest.json")
                        if os.path.exists(_fx) and os.path.exists(_mp):
                            ctx.log("[MASTER] добор слабых сцен (докачка до порога)…")
                            _sp.run([_sys.executable, _fx, _mp], timeout=5400)
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
                            _same_vol = os.stat(_dir).st_dev == os.stat(ctx.output_folder).st_dev
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
