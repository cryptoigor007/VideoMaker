# CP17 — тайм-ауты внешних процессов и атомарные результаты

**Статус:** завершён; полный snapshot сохранён после регрессии.

## Причина
Аудит CP16 нашёл FFmpeg/ffprobe вызовы без timeout и фиксированные короткие лимиты для обработки целого видео. Зависший процесс мог остановить конвейер неопределённо долго, а неатомарный вывод мог оставить повреждённый файл по конечному пути.

## Изменения
- `video_maker/engines/media_process.py`: `media_timeout_for_duration` и `run_ffmpeg_atomic`.
- `audio.py`: атомарные выходы для замены аудио, фильтра улучшения голоса и encode loudnorm; тайм-аут для измерительного прохода loudnorm и `measure_loudness`.
- `branches.py`: адаптивные timeout на извлечение голоса и перенос аудиодорожки.
- `video.py`: fixed timeout для cache одного фонового кадра; duration-aware limit для vstack/trim, retry/fallback при timeout.
- `subtitles.py`: 30-секундные лимиты для probe resolution/duration.
- `main.py`: `osascript` для фокуса окна ограничен 15 секундами.
- Добавлен `tests/test_media_process.py`: finite duration policy, cap, успешная atomic replace, timeout cleanup, сохранение прежнего результата, failure cleanup.

## Проверки
- `python -m compileall -q video_maker tests`: PASS.
- `python -m pytest tests/ -q`: **126 passed, 7 subtests passed**.
- `python tests/synthetic_ffmpeg_smoke.py`: PASS; synthetic audio WPM 120.0; word timestamps preserved.
- `python tests/synthetic_bgm_mix_smoke.py`: PASS; H.264/AAC; full audio decode PASS.
- AST scan subprocess calls: **0** вызовов `subprocess.run/check_call/check_output/call` без явного timeout.
- ZIP structure, source manifest and hash validation: performed before publishing CP17.

## Особенности/ограничения
Timeout scaled as `max(minimum, duration × 8)`, capped at six hours. Probe/background-frame operations use shorter fixed limits. Timeout values may need production tuning after testing long source videos and external drives on the target Mac. This is not a native Apple Silicon/MLX end-to-end test.
