# VideoMaker — полный аудит и план исправлений (база: r56-platform-bgm)

**Дата:** 22.09.2026
**Что проанализировано:** рабочая сборка r55 (`/Users/dreamstore/VideoMaker`), снапшот r53 (`~/Downloads/VideoMaker_FULL_r53 (3)`), архив r56 (`~/Downloads/VideoMaker_FULL_r56_FINAL.zip`), плюс лог 30 993 строк (50 прогонов), кэши Whisper/Gemini, реальные артефакты продакшена и сохранённые настройки.

**Принцип документа:** ни одна фича не удаляется. Все правки сохраняют текущий функционал; производительность только растёт.

---

## 0. Стратегия: какую версию брать за базу

| Версия | Состояние | Вердикт |
|---|---|---|
| r53 | нет фикса JSON (критичный баг), мёртвые модели в каталоге, игнор настроек хештегов, обложка не в стиле хука | **не использовать** |
| r55 (рабочая папка) | стабильна: JSON-фикс, parity обложки, BGM один раз, переносы вместо копирований, loudnorm всегда | аварийный откат |
| **r56 (архив)** | **новее всех**: раскладка и BGM по соцсетям + все фиксы r54/r55. Но: потеря loudnorm без BGM, ~2,5× лишнего I/O, странность с выключенным YouTube | **берём за базу и патчим** |

**Итоговая цель:** r56 по функционалу (7 площадок, папки, BGM по соцсетям) + производительность **выше, чем у r55** (за счёт отказа от лишних проходов по 4K-файлам).

### Порядок приоритетов

| Приоритет | Что означает | Сколько пунктов |
|---|---|---|
| **P0** | ломает прогон, портит результат или теряет данные | 8 |
| **P1** | функционал работает не так, как обещает UI/докстрока | 14 |
| **P2** | производительность | 9 |
| **P3** | мёртвый код, техдолг, документация | 15 |

### Что уже исправлено в r56 — НЕ переделывать

`analysis.py` (sanitize control chars + рабочий retry + каталог моделей), `subtitles.py` + `short_cover.py` (parity обложки и хука, `HOOK_MARKER_COLORS`, `build_short_hook_cover_ass`), `shorts.py` (передача `clip_start`). Эти файлы в r56 байт-в-байт совпадают с рабочей r55 — трогать их не нужно.

---

# ЧАСТЬ I. КРИТИЧЕСКИЕ ОШИБКИ (P0)

## P0-1. Метаданные Shorts привязываются к чужому видео
**Где:** `pipeline/shorts.py:391-397` (`run`), `pipeline/finalize.py:174-205`
**Симптом:** если хотя бы один клип не отрендерился (ошибка ffmpeg, невалидный тайминг, отмена), все последующие Shorts получают **чужие** заголовок, описание и хештеги. Видео от клипа №3 уезжает в папку `short_002` с текстами клипа №2.
**Причина:** `shorts.py` нумерует папки по исходному индексу клипа (`short_{index:03d}`), но в `results` кладёт только успешные; `finalize` нумерует заново через `enumerate(ctx.shorts, 1)` и берёт `clips[i-1]` по **порядковому** номеру.
**Проверено:** чтением обоих мест; несоответствие индексов гарантировано при любом пропуске.
**Исправление (без изменения сигнатур):** в `finalize.py` брать номер из пути, а не из `enumerate`:

```python
# finalize.py, внутри цикла по ctx.shorts
import re as _re
m = _re.search(r"short_(\d{3})", os.path.basename(os.path.dirname(short_path)))
idx = int(m.group(1)) if m else i          # исходный номер клипа
sdir = os.path.join(shorts_dir, f"short_{idx:03d}")
dst  = os.path.join(sdir, f"short_{idx:03d}.mp4")
...
clip = clips[idx - 1] if 0 <= idx - 1 < len(clips) else {}
```
**Функционал:** сохраняется полностью (нумерация даже становится стабильной между прогонами).
**Быстродействие:** 0.

---

## P0-2. Настройка «Обязательные #» игнорируется для Shorts
**Где:** `pipeline/shorts.py:20` (`REQUIRED_TAGS`), `:23` (`_ensure_hashtags`), `:139`, `:352`, `:355`, `:413`
**Симптом:** пользователь меняет обязательные хештеги в ⚙ → «Описания YouTube», но в `short_XXX_hashtags.txt`, `_upload.txt`, `_meta.txt` всё равно принудительно вставляются брендовые `#ТочкаНаблюдения #ТайныйКризисЧеловечества`.
**Проверено запуском:**
```
вход : '#МойКанал #МойСериал #ниша'
выход: '#ТайныйКризисЧеловечества #ТочкаНаблюдения #МойКанал #МойСериал #ниша'
```
**Причина:** `shorts.py` не знает про `ctx.packaging_opts` и использует собственный хардкод; `analysis.py` (r51/r55) настройку уже учитывает — то есть внутри одного прогона `description.txt` и `hashtags.txt` расходятся.
**Исправление:**

```python
# pipeline/shorts.py — заменить константу на функцию
def _required_tags(packaging_opts: dict | None) -> tuple[str, ...]:
    raw = str((packaging_opts or {}).get("required_hashtags") or "").split()
    tags = tuple(t if t.startswith("#") else f"#{t.lstrip('#')}" for t in raw if t.strip())
    return tags or REQUIRED_TAGS            # пусто → бренд по умолчанию

def _ensure_hashtags(raw: str, required: tuple[str, ...] = REQUIRED_TAGS) -> str:
    ...                                     # тело без изменений, только `for req in required`
```
И передать `required=self._req_tags` в вызовах `:139`, `:352`, `:355`, `:413`, где `self._req_tags = _required_tags(getattr(ctx, "packaging_opts", None))` в `run()`/`_create_short()`.
**Функционал:** сохраняется (при пустой настройке поведение прежнее).
**Быстродействие:** 0.

---

## P0-3. Потеряна нормализация громкости (регресс r56)
**Где:** `pipeline/finalize.py:130-146` (`_deliver`), `pipeline/branches.py:122`, `:285`, `:311`
**Симптом:** если у площадки нет папки BGM (частый случай — YouTube без музыки), итоговое видео **не нормализуется** вообще. Настройка «Громкость (LUFS)» молча не работает, замера `[LUFS]` в логе нет.
**Причина:** в r55 `_place_video` делал two-pass `apply_loudnorm(target_lufs)` для любого ненормализованного файла. В r56 этот метод удалён; `_deliver` либо микшует BGM (внутри `mix_bgm` есть `loudnorm=I={target_lufs}`), либо просто копирует. `apply_loudnorm` в r56 не вызывается **нигде** (проверено grep).
**Исправление (интерим, 10 строк):** в `_deliver` для ветки без BGM:

```python
else:
    if not _norm_done(src):             # см. кэш ниже
        from ..engines.audio import apply_loudnorm
        tmp = dst + ".ln.mp4"
        apply_loudnorm(src, tmp, target_lufs=ctx.target_lufs, log_fn=ctx.log)
        _deliver_file(tmp, dst, prefer_move=True)   # см. P2-1
        try: os.remove(tmp)
        except OSError: pass
    else:
        _deliver_file(src, dst)
```
**Функционал:** восстанавливается поведение r55.
**Быстродействие:** ухудшается (аудио-перекодирование на каждый файл) — поэтому целевое решение в **P2-2**: Впекать `loudnorm` в основной энкод, где это бесплатно.

---

## P0-4. Отсутствие программного fallback энкодера
**Где:** `engines/ffmpeg_resilient.py:421-425`, все вызовы `h264_videotoolbox`
**Симптом:** любая ошибка VideoToolbox на 4K («cannot create videotoolbox», «session not found», нехватка сессий) = падение стадии. На 1,5-ГБ 4K-файлах это самый частый фатальный сценарий.
**Причина:** сознательная политика «без libx264»; `VideoEncodeFailed` бросается немедленно.
**Исправление:** добавить управляемую лестницу (по умолчанию включена, отключается настройкой):

```python
# ffmpeg_resilient.py
def vt_encode_args(bitrate="28M", allow_sw_fallback=True):
    return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-allow_sw", "1",
            "-pix_fmt", "yuv420p"]

def sw_encode_args(bitrate="28M"):
    # CRF даёт стабильное качество независимо от битрейта источника
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p"]

# в run_vt_encode / burn-цикле: reason == "encoder" →
#   если allow_sw_fallback: повторить тот же cmd, заменив vt_encode_args на sw_encode_args
#   иначе: прежнее поведение (raise)
```
**Функционал:** только добавляет ветку восстановления.
**Быстродействие:** на успешном пути 0; в аварийной ветке libx264 медленнее, но результат есть.

---

## P0-5. Нет таймаута у ffmpeg
**Где:** `engines/ffmpeg_resilient.py:326` (`run_ffmpeg`, `timeout=None`)
**Симптом:** зависший энкод (битый вход, отвал тома) блокирует пайплайн **навсегда**. Кнопка «Отмена» частично спасает, но только через `psutil`.
**Исправление:**

```python
def run_ffmpeg(cmd, log_fn=None, timeout=None, media_duration=None):
    if timeout is None and media_duration:
        timeout = max(600.0, float(media_duration) * 8.0)   # 8× реального времени
    ...
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise VideoEncodeFailed(f"ffmpeg timeout {timeout:.0f}s: {' '.join(cmd[:6])}…")
```
Передавать `media_duration=ctx.audio_duration` из стадий.
**Функционал:** без изменений.
**Быстродействие:** 0 на нормальных прогонах; убирает бесконечные зависания.

---

## P0-6. Мастер пишется прямо в целевой файл без проверки
**Где:** `engines/video.py:239-362` (`fit_video_to_duration`)
**Симптом:** обрыв тома/диска или падение ffmpeg оставляет «полу-мастер», который затем без проверки уезжает во все ветки — и весь прогон оказывается мусорным, а чекпоинт считает стадию выполненной (probe-валидация отсечёт только по duration, но не по целостности).
**Исправление:** писать в `.vm_master_{uuid}.mp4` рядом, затем `verify_mp4()` + `atomic_replace()` (обе функции уже есть):

```python
tmp = output_path + f".vm_tmp_{uuid.uuid4().hex[:8]}.mp4"
... ffmpeg ... tmp
if rc != 0 or not verify_mp4(tmp, log_fn=log_fn):
    raise RuntimeError("master encode failed / verify failed")
atomic_replace(tmp, output_path)
```
Дополнительно — `ensure_storage(...)` перед запуском (как в `run_vt_encode`).
**Функционал:** без изменений.
**Быстродействие:** +1 переименование (мгновенно).

---

## P0-7. `probe_duration`: кэш без инвалидации
**Где:** `engines/audio.py:13-96` (`_PROBE_CACHE`)
**Симптом:** внутри одного процесса перезаписанный файл по тому же пути возвращает старую длительность. Ломает повторные прогоны и resume.
**Исправление:**

```python
def _cache_key(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return os.path.abspath(path)
```
Ключ заменить на `_cache_key(path)`; ограничить размер (например, 4096 записей, `OrderedDict`).
**Функционал:** без изменений. **Быстродействие:** кэш остаётся, `stat` дешевле `ffprobe` в ~1000 раз.

---

## P0-8. Гонка Tkinter: переменные читаются из рабочего потока
**Где:** `gui/app.py:1749` (`_packaging_opts_from_settings()`), `:1795-1812` (`self._var_get(...)`) — всё внутри `_run_pipeline`, то есть в `threading.Thread`
**Симптом:** случайные/пустые значения настроек, редкие падения `RuntimeError: main thread is not in main loop`.
**Исправление:** заморозить снимок значений **на GUI-потоке** (в `_start`) в обычный dict и передать его в `_run_pipeline`:

```python
# gui/app.py, конец _start(), перед созданием потока:
self._frozen = {
    "packaging_opts": self._packaging_opts_from_settings(),
    "imo": {k: self._var_get(k) for k in (
        "h_intro_path","h_mid_path","h_outro_path","h_intro_duration","h_mid_duration","h_outro_duration",
        "v_intro_path","v_mid_path","v_outro_path","v_intro_duration","v_mid_duration","v_outro_duration",
        "s_intro_path","s_mid_path","s_outro_path","s_intro_duration","s_mid_duration","s_outro_duration")},
}
thread = threading.Thread(target=self._run_pipeline, args=(self._frozen,), daemon=True)
```
В `_run_pipeline(self, frozen)` — использовать `frozen[...]` вместо чтения Tk.
**Функционал:** без изменений. **Быстродействие:** 0 (даже чуть быстрее — нет обращений к Tk из потока).

---

# ЧАСТЬ II. ЛОГИЧЕСКИЕ ОШИБКИ И НЕСООТВЕТСТВИЯ UI (P1)

## P1-1. Выключенный YouTube пропадает из вывода
**Где:** `config/settings.py` — `active_bgm_platforms()`
**Проверено запуском:**

| Настройки | Результат |
|---|---|
| YouTube ✓, папки нет | `youtube` (голос) |
| YouTube ✓ + папка с аудио | `youtube` (BGM) |
| **YouTube ✗, TikTok ✓ + папка с аудио** | **только `tiktok`** |
| всё выключено | `youtube` (fallback) |

При этом GUI пишет «YouTube — всегда», а докстрока называет YouTube базой.
**Исправление:** сделать YouTube безусловной базой (как заявлено):

```python
platforms = [
    ("youtube", "YouTube", True, yt_folder, True),   # всегда
    ("tiktok", "TikTok", self.bgm_tiktok_enable, self.bgm_tiktok_folder.strip(), False),
    ...
]
```
**Функционал:** соответствует обещанному; лишняя галочка YouTube перестаёт быть ловушкой (её можно оставить как «миксовать BGM для YouTube»). Если нужен режим «только TikTok» — добавить отдельную галочку «не выводить YouTube» и явно подписать.

## P1-2. `validate()` блокирует запуск из-за выключенных площадок
**Где:** `config/settings.py` (`validate`, блок проверки BGM-папок)
**Симптом:** один «висящий» путь от старой флешки останавливает пайплайн, хотя площадка выключена.
**Исправление:** проверять папку только если `enable=True` **и** папка непустая:

```python
for _lab, _en, _folder in (
    ("YouTube", True, self.bgm_youtube_folder),
    ("TikTok", self.bgm_tiktok_enable, self.bgm_tiktok_folder),
    ...
):
    f = (_folder or "").strip()
    if _en and f and not os.path.exists(f):
        errors.append(f"Папка BGM {_lab} не найдена: {f}")
```

## P1-3. `add_bgm` разошёлся со смыслом
**Где:** `gui/app.py` (`self.settings.add_bgm = any([...])`), `pipeline/stages.py`, `pipeline/shorts.py:404`
**Симптом:** `add_bgm=True` почти всегда, даже когда ни в одной папке нет аудио; флаг используется в `shorts_audio_normalized` и проверках, которые теперь ничего не значат.
**Исправление:** `add_bgm = any(p["folder"] for p in self.settings.active_bgm_platforms())` — то есть «BGM реально будет».

## P1-4. BGM разный на разных площадках и между прогонами
**Где:** `engines/audio.py:171` — `random.choice(bgm_files)`
**Симптом:** один и тот же выпуск на YouTube и TikTok получает разные треки; Shorts внутри одной площадки — тоже. Прогоны невоспроизводимы.
**Исправление:** детерминированный выбор от содержимого:
```python
seed = hashlib.sha256(f"{os.path.basename(video_path)}|{len(bgm_files)}".encode()).digest()
track = bgm_files[int.from_bytes(seed[:4], "big") % len(bgm_files)]
```
Плюс — при мультиплатформе микшировать **один раз на уникальную папку** (см. P2-3), тогда трек гарантированно совпадает.

## P1-5. BGM без fade и без ducking (докстрока обещает sidechain)
**Где:** `engines/audio.py:150-224` (`mix_bgm`)
**Симптом:** щелчок на стыке лупа, резкое появление/обрыв музыки, музыка не приседает под речь (только статический вес 0.3 ≈ −10,5 дБ).
**Исправление (функционал сохраняется, качество растёт):**
```
[1:a]aloop=loop=-1:size=2e9,loudnorm=I=-18:TP=-3:LRA=11,
     afade=t=in:st=0:d=1.5,atrim=0:{DUR},asetpts=N/SR/TB[bg];
[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=250[bgd];
[0:a][bgd]amix=inputs=2:duration=first:weights=1 0.9:dropout_transition=3,
     afade=t=out:st={DUR-2}:d=2,loudnorm=I={target}:TP=-1.5:LRA=11[out]
```
**Быстродействие:** обработка только аудио — доли процента от времени видео.

## P1-6. `mix_bgm`: мёртвый `.aac` и падение на отсутствующей папке
**Где:** `engines/audio.py:162-169`
**Симптом:** двойная фильтрация расширений отсекает `.aac`; `os.listdir` по несуществующей папке бросает необработанный `FileNotFoundError`.
**Исправление:** один список расширений + `os.path.isdir()` перед `listdir` + возврат `video_path` с логом.

## P1-7. `move_to_used` выравнивает подпапки
**Где:** `engines/video.py:185-236`
**Симптом:** тематические подпапки B-roll сваливаются в плоскую `used/`; round-robin по темам со временем деградирует.
**Исправление:** сохранять относительный путь:
```python
rel = os.path.relpath(src_a, root_abs)
dst = os.path.join(used_root, rel)
os.makedirs(os.path.dirname(dst), exist_ok=True)
```
Плюс обратная совместимость: старые плоские файлы уже в `used/` — они и так исключаются из выборки.

## P1-8. Кэш фона не инвалидируется
**Где:** `engines/video.py:372-399` (`_cached_bg_half`, ключ = path|mtime|size|WxH)
**Симптом:** при совпадении mtime/size отредактированная картинка не перечитается.
**Исправление:** добавить `st_ino` (и `st_ctime_ns`) в ключ.

## P1-9. UI обещает управление Whisper, которого нет
**Где:** `gui/app.py:1165+`, `engines/transcription.py:77`
**Симптом:** выбор модели `tiny…large-v3-turbo`, `device`, `compute_type`, `whisperx_path` **игнорируются** — всегда `mlx-community/whisper-large-v3-turbo`. Кнопки «Найти/Вручную» для WhisperX влияют только на надпись.
**Исправление (функционал расширяется, не сужается):** прокинуть `model_name` в `mlx_whisper.transcribe(path_or_hf_repo=repo_for(model_name))`, где
```python
_MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base",
    "small": "mlx-community/whisper-small",
    "medium": "mlx-community/whisper-medium",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
```
Ключ кэша уже включает `model_name` — инвалидация корректна. WhisperX-блок либо реализовать, либо убрать из UI (сейчас это ложь интерфейса).

## P1-10. `vstack_top_ratio` — мёртвый параметр
**Где:** `engines/video.py:407`, `branches.py:231`, `settings.py:81`
**Симптом:** сплит вертикали всегда 50/50, хотя в настройках и контексте лежит 0.6.
**Исправление:** либо реализовать (`top_h = _even(int(play_h * top_ratio))`, `bot_h = play_h - top_h`), либо убрать из кода и настроек. Реализация ничего не ломает и делает параметр осмысленным.

## P1-11. Gemini генерирует `intro`/`outro`, которые никто не читает
**Где:** `engines/analysis.py:1204-1206`, потребитель — только `middle` (`video.py:615`)
**Симптом:** при включённом «Интро: Gemini выбирает» тратятся токены и секунды на бесполезную секцию промпта.
**Исправление:** либо использовать `intro.start/end` и `outro.start/end` как окна для вставки IMO (сохранив приоритет явных путей), либо убрать секцию 6 из промпта и чекбокс. Первое — функциональный прирост, второе — экономия ~30 с на генерации в редких случаях.

## P1-12. `hooks_wide` подменяется вертикальным хуком
**Где:** `engines/analysis.py:1032-1033`
**Симптом:** промпт требует **другой** текст для 16:9, но при пустом `hooks_wide` он молча копируется из `hooks_vertical`; wide-рендер предпочитает `hooks_wide` → на 16:9 уходит вертикальный хук.
**Исправление:** при пустом `hooks_wide` использовать собственный fallback (например, `package_hook` или первый `hooks`), а не vertical.

## P1-13. Баг дедупликации хуков в AISIE
**Где:** `external/aisie/pipeline.py:162`
```python
if any(s < e and start < e for s, e in taken_ranges):   # s < e — тавтология
```
**Симптом:** отбрасываются корректные хуки, идущие раньше уже принятого; не проверяется `end` нового хука.
**Исправление:**
```python
if any(start < e and end > s for s, e in taken_ranges):
```

## P1-14. Ложный эмоциональный балл в Attention Score
**Где:** `external/aisie/scoring.py` — `_stem` + `EMOTIONAL_STEMS`
**Симптом:** `_stem("больше") == "боль"` → «свободы стало больше» получает эмоцию 0.76 и total 99 (band hook вместо accent).
**Исправление:** матчить стем только как целое слово (`\b{stem}\w{0,3}\b`) либо добавить стоп-лист форм (`больше`, `большой`, `большинство`) перед применением эмоционального стема.

## P1-15. Модель из GUI не сохраняется в цепочку fallback
**Где:** `gui/app.py` (`_refresh_models_from_api` → только combobox), `engines/analysis.py:25-34`
**Симптом:** каталог в коде устаревает; по логу **только `gemini-3.6-flash` отвечает 200**, остальные записи — 404 (`3.6-flash-lite` не существует, `2.5-flash`/`2.0-flash` выведены из эксплуатации). Каждый прогон платит 1–2 лишних round-trip.
**Исправление:** сохранять живой список, полученный кнопкой, в `~/video_maker/cache/gemini_models.json` и строить цепочку `[выбранная] + live-список + (аварийный хардкод)`. Плюс включить нативный JSON-режим Gemini (`response_mime_type="application/json"`) — это убирает почти все retry по JSON.

### Прочие P1 (кратко)
- **P1-16.** GUI-поле API-ключей не сохраняется; Keychain не реализован (README и дизайн-спека обещают). → Либо реализовать Keychain через `security add-generic-password`/`find-generic-password`, либо убрать поле и упоминания из доков.
- **P1-17.** `series_var` не заполняется → имя серии не сохраняется между запусками.
- **P1-18.** Длительность Middle нельзя задать из UI (всегда 1.0 с): в `_build_imo_tab` нет поля, `_var_get("h_mid_duration")` всегда `""`.
- **P1-19.** `imo_folder_var` есть в списке сохранения, но виджета нет — папка IMO недоступна из GUI.
- **P1-20.** `clean_pro` в `PRESETS` (`subtitles.py:1216`) недостижим — перехватывается веткой parity на `:1164`.
- **P1-21.** Список моделей продублирован в `gui/app.py` (fallback при ImportError) — расходится с `analysis.py`.
- **P1-22.** `whisperx_status_var` создаётся дважды; `prompt_extra_var`/`_prompt_extra_default` мертвы; `HOOK_HELP` перечисляет стили, недостижимые из комбобокса.
- **P1-23.** Окно «Настройки» открывается многократно; `_load_settings` обёрнут в общий `try` и при одной битой записи теряет **все** остальные настройки.
- **P1-24.** `_report_audio_selection` синхронно прогоняет `probe_duration` по всем аудиофайлам на GUI-потоке → фриз UI на большой папке/сетевом томе.
- **P1-25.** `os._exit(0)` в `_on_close` → пропускается остановка `caffeinate` (процесс может остаться).
- **P1-26.** `shutdown_computer(delay_sec=30)` фактически ждёт ≤5 с; «Отмена» не отзывает запланированное выключение.
- **P1-27.** Чекпоинт не проверяет, что аудиофайл не изменился: sidecar `checkpoint_analysis.json` подхватится даже если файл перезаписали (путь тот же). → Хранить в чекпоинте `audio_fp = sha256(path|size|mtime)` и сравнивать (та же функция, что в кэше Whisper).
- **P1-28.** При падении прохода с ASS `vstack` молча отдаёт видео **без субтитров** (`video.py:485-506`) — единственный признак в логе.
- **P1-29.** Дублирование записи sidecar-файлов Shorts: `shorts.py:_write_sidecar_files` и `finalize._write_packaging_files` пишут одни и те же имена с разной нормализацией → «последний победил» (усугубляется P0-2).

---

# ЧАСТЬ III. ПРОИЗВОДИТЕЛЬНОСТЬ (P2) — «максимально возможное»

## Бюджет I/O сегодня (оценка по реальным размерам вашего прогона)

Файлы с диска: `final_16x9` 1,52 ГБ · `master_16x9` 1,39 ГБ · `final_9x16` 1,39 ГБ · `master_9x16` 1,39 ГБ · 5 Shorts ≈ 0,93 ГБ. Аудио 393 с, 4K.

| Версия | Что происходит | Оценка записи за прогон (1 площадка) |
|---|---|---|
| r55 | master 1,39 + burn wide 1,52 + BGM-микс wide 1,52 + vstack 1,40 + копия аудио vertical 1,40 + cut Shorts 0,93 + burn Shorts 0,93; финализация — переносы (0) | **≈ 9,1 ГБ** |
| r56 (1 площадка) | то же, минус reuse аудио vertical; **плюс** в финализации: микс wide 1,52 + микс vertical 1,40 + микс 5 Shorts 0,93 + **копия master_16x9 1,39** + **копия master_9x16 1,39** (= дубликат final_9x16!) | **≈ 12,8 ГБ** |
| r56 (4 площадки) | 6,17 (энкоды) + 4 × 6,63 (доставка) | **≈ 33 ГБ** |

Дополнительно r56: **`_place_file(..., prefer_copy=True)` всегда копирует** (в r55 при `keep_temp_files=False` был мгновенный `os.replace`), а `master_9x16` в r56 — **побайтовый дубликат** `final_9x16` (в r55 они различались из-за BGM-микса; в r56 микс отложен, `ctx.master_vertical is ctx.final_vertical`).

---

## Целевая архитектура: один проход на каждый продукт

**Правило:** каждый 4K-файл читается и пишется **ровно один раз** за прогон; всё остальное — ссылки и аудио-ремуксы.

```
1. master_16x9        : 1 энкод (concat)                     [как есть]
2. final_16x9         : 1 энкод = ASS + BGM + loudnorm       [было: burn + микс + луфнорм = 3 прохода]
3. final_9x16         : 1 энкод = vstack + ASS + BGM + loudnorm  [было: vstack + микс = 2 прохода]
4. short_00N          : 1 энкод = seek + ASS, аудио copy     [было: cut + burn = 2 прохода × 5]
5. Финализация        : hardlink / move / аудио-ремукс       [было: полные копии и повторные миксы]
```

### P2-1. Доставка без копирования: move → hardlink → copy
**Где:** `pipeline/finalize.py` (`_deliver`, `_place_file`)
```python
def _deliver_file(src: str, dst: str, prefer_move: bool = False) -> str:
    """0-cost доставка: move → hardlink → copy. Возвращает 'move'|'hardlink'|'copy'|'skip'."""
    if not src or not os.path.isfile(src):
        return "skip"
    a, b = os.path.abspath(src), os.path.abspath(dst)
    if a == b:
        return "skip"
    os.makedirs(os.path.dirname(b) or ".", exist_ok=True)
    if os.path.exists(b):
        try: os.remove(b)
        except OSError: pass
    if prefer_move:
        try:
            os.replace(a, b); return "move"
        except OSError:
            pass
    try:
        os.link(a, b)          # та же ФС: 0 байт копирования
        return "hardlink"
    except OSError:
        shutil.copy2(a, b)
        return "copy"
```
**Эффект:** площадки на одном томе получают файлы **бесплатно**; дубликат `master_9x16 == final_9x16` становится одной инодой; `prefer_move=True` для последнего потребителя возвращает мгновенные переносы r55. `shutil.rmtree(_tmp)` после хардлинков безопасен.
**Оговорка:** если папка вывода на другом томе — автоматически `copy2`. Для облачных/сетевых папок, не поддерживающих hardlink, — тот же fallback.

### P2-2. BGM и loudnorm внутри основного энкода (убирает 2 полных прохода)
**Где:** `pipeline/branches.py` (FinalHorizontal/FinalVertical), `engines/subtitles.py` (`burn_subtitles`).
Добавить в `burn_subtitles` и `vstack_video_image` параметры `bgm: dict | None` и `loudnorm: dict | None` и собирать `filter_complex`:

```
[0:v]subtitles='…ass'[v];
[1:a]aloop=loop=-1:size=2e9,loudnorm=I=-18:TP=-3:LRA=11,afade=t=in:st=0:d=1.5,
     atrim=0:DUR,asetpts=N/SR/TB[bg];
[bg][0:a]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=250[bgd];
[0:a][bgd]amix=inputs=2:duration=first:weights=1 0.9:dropout_transition=3,
     loudnorm=I=-14:TP=-1.5:LRA=11[outa]
```
`-map "[v]" -map "[outa]" -c:v h264_videotoolbox -b:v … -c:a aac -b:a 192k`

Для точного two-pass: измерить **аудио мастера один раз** (`ffmpeg -i master -af loudnorm=…:print_format=json -f null -` — это аудио-only, дёшево), затем подставить `measured_I/measured_TP/measured_LRA/measured_thresh/offset` в цепочку выше. Точность сохраняется, дополнительных проходов по 4K — ноль.
**Эффект:** −1 полный проход по wide (~1,5 ГБ × 2) и −1 по vertical (~1,4 ГБ × 2) на каждую площадку; P0-3 решается бесплатно.

### P2-3. Shorts: нарезка и прожиг одним проходом
**Где:** `pipeline/shorts.py:_create_short`
Убрать `cut_segment` + отдельный burn:
```python
ffmpeg -ss {start} -hwaccel videotoolbox -i {final_9x16} -t {duration} \
       -filter_complex "[0:v]ass='…ass'[v]" -map "[v]" -map 0:a \
       -c:v h264_videotoolbox -b:v {br} -allow_sw 1 -pix_fmt yuv420p \
       -c:a copy {final_path}
```
`-ss` до `-i` — быстрый и (для транскодинга) точный seek. Аудио копируется, поэтому BGM/loudnorm наследуются от `final_9x16` автоматически.
**Эффект:** −5 промежуточных файлов (−0,93 ГБ записи и чтения), минус один ffprobe-цикл re-trim.

### P2-4. Микшировать BGM один раз на уникальную папку
**Где:** `pipeline/finalize.py:_deliver`
```python
mixed: dict[tuple[str, str], str] = {}     # (src, folder) → готовый файл
key = (os.path.abspath(src), folder)
cached = mixed.get(key)
if cached is None:
    cached = os.path.join(tmp_cache, f"bgm_{uuid.uuid4().hex[:8]}.mp4")
    mix_bgm(src, folder, cached, log_fn=ctx.log, loudnorm=True, target_lufs=ctx.target_lufs)
    mixed[key] = cached
_deliver_file(cached, dst)
```
**Эффект:** 4 площадки с одинаковой музыкой = 1 микс вместо 4 (и одинаковый трек — закрывает P1-4).

### P2-5. Не дублировать мастера по площадкам
**Варианты (по возрастанию радикальности):**
1. `master_16x9`/`master_9x16` класть **один раз** в `{series}/_masters/`, а в площадках — hardlink (0 I/O).
2. Сделать выгрузку мастеров опцией (галочка «сохранять мастер-файлы»), по умолчанию — hardlink из `_masters/`.
**Эффект:** −1,39 ГБ записи на каждую площадку.

### P2-6. Gemini параллельно со сборкой мастера
**Где:** `gui/app.py:_run_pipeline` (порядок стадий)
Анализ Gemini (~31 с по логу, медиана) и `MasterBuilder` (~64 с) **независимы**: мастеру нужны только аудио-длительность и B-roll.
```python
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=1) as ex:
    fut = ex.submit(GeminiStage().run, ctx)      # сеть, не трогает ffmpeg
    ctx = MasterBuilder().run(ctx)               # локальный CPU/GPU
    ctx = fut.result()
```
Сохранить порядок в `STAGE_ORDER`/чекпоинтах (писать чекпоинт Gemini по завершении future).
**Эффект:** −25…30 с на каждый файл (≈15–20 % общего времени), без изменения результата. Риск: при отмене нужно дождаться future.

### P2-7. `_collect_vertical_files`: 774 ffprobe на старте
**Где:** `pipeline/master.py:44-55`
**Симптом:** для папки с 774 файлами — 774 вызова ffprobe (≈10–15 с) только чтобы отобрать вертикальные.
**Исправление:** (а) постоянный кэш `{path: (mtime,size,w,h)}` в `~/video_maker/cache/probe.json`; (б) ранний выход, когда набрана длительность ≥ аудио; (в) при отсутствии `broll_vertical` файл вообще не сканировать (сейчас так и есть — но при заданном он сканирует всё).
**Эффект:** −10…15 с на старте при большом B-roll.

### P2-8. Мелочи с измеримым эффектом
- **Хардлинки вместо копий обложек** (`_copy_covers`) — 5 × ~200 КБ, несущественно, но бесплатно.
- **Параллельный рендер обложек Shorts** (5 независимых ffmpeg) — `ThreadPoolExecutor(3)`; на 4K не влияет, экономит ~1–2 с.
- **Не добавлять `-movflags +faststart`** — для загрузки на YouTube он не нужен, а это лишний полный проход по файлу. Если файлы отдаются по HTTP — включить опцией.
- **Параллелить площадки при разных томах**: `_deliver` на разные тома можно распараллелить (I/O-bound), но если том один — не надо (будет трэшинг).
- **Отключить `-allow_sw 0` там, где код и так допускает software (`video.py:337,465,565`)** — согласовать политику (см. P0-4).

### Итог по быстродействию (оценка, 1 площадка, BGM, 5 Shorts)

| Метрика | r55 | r56 сейчас | Целевая |
|---|---|---|---|
| Полных проходов по 4K | 5 | 5 + 6 в финализации | **4** |
| Запись, ГБ (1 площадка) | ≈ 9,1 | ≈ 12,8 | **≈ 5,2** |
| I/O всего (чтение+запись), ГБ | ≈ 18 | ≈ 25,6 | **≈ 10,5** |
| Копирование мастеров | 0 (перенос) | 2,8 ГБ на площадку | **0 (hardlink)** |
| 4 площадки, ГБ записи | — | ≈ 33 | **≈ 5,2** (одна папка BGM, хардлинки) / **≈ 21** (4 разные папки BGM → аудио-ремуксы) |
| Стенное время (оценка) | база | +30…90 с | **−30 с** (P2-6) **−40…60 с** (P2-2/2-3) |

---

# ЧАСТЬ IV. НАДЁЖНОСТЬ И УСТОЙЧИВОСТЬ (сводит предыдущие части)

| # | Проблема | Файл | Фикс |
|---|---|---|---|
| R-1 | Классификация ошибок ffmpeg не различает «нет места» и «нет файла» — оба `io` | `ffmpeg_resilient.py:31-46` | добавить `no space left`/`disk quota` в отдельный класс `disk` с понятным сообщением и без 4 ретраев |
| R-2 | `ensure_storage` не умеет auto-remount и не проверяет свободное место | `ffmpeg_resilient.py:110-165` | перед энкодом проверять `shutil.disk_usage(...).free > ожидаемый_размер × 1,5`, иначе понятная ошибка |
| R-3 | Убийство ffmpeg зависит от `psutil` | `gui/app.py:1998-2014` | fallback: `subprocess` с `start_new_session=True` + `os.killpg` при отмене |
| R-4 | Отмена действует только между стадиями | `gui/app.py:1854` | передавать `cancel_event` внутрь тяжёлых функций и завершать дочерний ffmpeg по сигналу (уже частично есть через psutil) |
| R-5 | Полный диск на финализации → staging удаляется (`rmtree root/shorts`) | `finalize.py` | удалять staging только если **все** площадки получили файлы (проверять `os.path.isfile(dst)`) |
| R-6 | `mix_bgm` failure глушится и подменяется копией — молча теряется музыка | `finalize.py:_deliver` | логировать уровень WARNING + писать в итоговую сводку, сколько площадок осталось без BGM |
| R-7 | `ffprobe` на сетевых томах в цикле | `audio.py`, `master.py` | см. P0-7/P2-7 (кэш) |

---

# ЧАСТЬ V. МЁРТВЫЙ КОД И ТЕХДОЛГ (P3)

1. `branches.py:122,285,311` и `shorts.py:269` — `if False and …` вместо удаления. Убрать блоки целиком (после P2-2 BGM живёт в финализации/энкоде).
2. `finalize.py:_measure` — не вызывается (вернуть в P0-3 или удалить).
3. `audio.py:apply_loudnorm` — не вызывается в r56 (вернуть в P0-3/P2-2).
4. `gui/app.py:_browse_dir_into` — новый метод, нигде не используется.
5. `gui/app.py:573` — `add_bgm_var` создаётся, виджета нет.
6. `pipeline/stages.py` — `horizontal_audio_normalized`/`vertical_audio_normalized`/`bgm_mixed`/`bgm_source_video` больше не устанавливаются; либо вернуть (P0-3), либо вычистить вместе с полями чекпоинта.
7. `subtitles.py` — недостижимый `PRESETS["clean_pro"]`; `VISUAL_WEIGHTS` использует цвета вне `validate.BRAND_PALETTE` (даёт OFF_PALETTE warn).
8. `highlight_lexicon.py` — дубли ключей словаря (`F601` ×2) и дубли элементов множеств (`B033` ×7).
9. `power.py:5` — неиспользуемый `import os`; `power.py:104` — f-string без плейсхолдеров.
10. `video.py:10,15,16` — неиспользуемые `random`, `VideoEncodeFailed`, `calculate_adaptive_bitrate`; `video.py:793,806,819,825` — f-строки без плейсхолдеров.
11. `gui/app.py:955` (`subprocess`), `:1156` (`re`), `:1340` (`re`), `:1790` (`FinalHorizontal`/`FinalVertical`) — неиспользуемые импорты; `:972` — мёртвый `script` AppleScript.
12. `finalize.py:130-143` — `B023`: `_deliver` захватывает `folder`/`label` цикла. Сейчас безопасно (вызов в той же итерации), но хрупко — привязать явно: `def _deliver(src, dst, tag, folder=folder, label=label)`.
13. `pipeline/shorts.py:149` — неиспользуемая `temps`.
14. `finalize.py:216-218` — `shutil.rmtree(staging)` с комментарием «только если это staging» без фактической проверки.
15. **Инфраструктура:**
    - `start.command` в r56 — старая версия (нет `export PATH`, проверки `mlx_whisper`, `venv/`-fallback, `tee`-лога). Вернуть версию из рабочей r55.
    - `CHANGES.md` не содержит раздела r56, при заявлении «полная история без пробелов».
    - `VERSION.md` дублирует строки `gui/app.py`, `settings.py`, `finalize.py` (r56 и r51 одновременно).
    - Папка внутри архива называется `videomaker_r53/` при содержимом r56.
    - `.env.example` не документирует `GEMINI_API_KEYS` и `GEMINI_MODEL`, которые реально используются.
    - `README.md` заявляет «r43.3-stable» и Keychain.
    - `requirements.txt` помечен «r27», `README_FIX.txt` — «26/26 tests» (сейчас 40).
    - `*.command` и `*.app` в `.gitignore` → лаунчер и сборка не попадают в репозиторий; git-история — 5 сквошнутых коммитов без тегов.
    - Тесты: 40 мок-тестов, **нет** покрытия `finalize`, `checkpoint`, `settings.active_bgm_platforms`, `_deliver`, `_ensure_hashtags`. Добавить как минимум: `test_active_bgm_platforms` (6 сценариев из P1-1), `test_short_index_mapping` (P0-1), `test_ensure_hashtags_uses_opts` (P0-2), `test_deliver_hardlink` (P2-1).

---

# ЧАСТЬ VI. ДОРОЖНАЯ КАРТА

### Фаза 0 — «чтобы не портило результат» (полдня)
P0-1 (индексы Shorts) · P0-2 (хештеги) · P0-3 (loudnorm, интерим) · P0-5 (таймаут ffmpeg) · P0-6 (verify мастера) · P0-7 (кэш probe) · P1-2 (validate) · P1-3 (`add_bgm`)
**Проверка:** `pytest -q` (40 тестов) + 4 новых теста из P3-15.

### Фаза 1 — «производительность» (1–2 дня)
P2-1 (hardlink/move) · P2-4 (микс раз на папку) · P2-5 (мастера) · P2-3 (Shorts одним проходом) · P2-7 (probe-кэш B-roll)
**Ожидание:** запись за прогон 12,8 → ~7,2 ГБ; 4 площадки — почти без роста (хардлинки).
**Проверка:** прогон 40-секундной пробы, сверить структуру папок и `info_metadata.txt`, замерить `du` и время финализации.

### Фаза 2 — «максимум» (2–3 дня)
P2-2 (BGM+loudnorm внутрь энкода) · P0-4 (fallback энкодера) · P2-6 (Gemini ∥ Master) · P1-5 (ducking/fade) · P1-9 (модель Whisper) · P1-11 (intro/outro)
**Ожидание:** запись ~5,2 ГБ, −30 с на файл, BGM/loudnorm корректны на всех площадках.

### Фаза 3 — «чистота»
P1-1, P1-4, P1-6…P1-28, весь P3.

---

# ПРИЛОЖЕНИЕ. Чек-лист приёмки

1. Прогон на 40-секундной пробе: структура `{series}/{platform}/{wide,vertical,shorts}` для всех включённых площадок.
2. Для каждой площадки: `final_16x9.mp4`, `final_9x16.mp4`, 5 Shorts, `*_title/description/hook/hashtags.txt`, обложки.
3. `short_00N` видео и `short_00N_title.txt` соответствуют **одному** клипу (P0-1) — специально сломать один клип (невалидный тайминг) и проверить.
4. Пользовательские «Обязательные #» присутствуют в `hashtags.txt` **всех** Shorts, брендовые — не добавлены (P0-2).
5. `[LUFS]` в логе для всех итоговых файлов; значение в пределах ±1 от `target_lufs` (P0-3).
6. Один и тот же трек BGM на всех площадках (P1-4).
7. `du -sh` папки серии: при 4 площадках рост относительно 1 площадки — только аудио-ремуксы для разных папок BGM, не кратный.
8. `df` до/после: пиковая занятость не превышает ~2× размера продуктов (P2-1/P2-5).
9. Отмена на середине финализации: ни один `short_00N.mp4` не потерян.
10. `pytest -q` — 44+ теста зелёные; новый тест `active_bgm_platforms` покрывает все 6 сценариев.
