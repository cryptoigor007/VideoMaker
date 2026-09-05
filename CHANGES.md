# VideoMaker — r40 (2026-09-05)

## r40 — Phrase karaoke (язык эффекта)

**Название эффекта:** *phrase karaoke* / *chunked captions with progressive word paint*
(в TikTok/Remotion — page of 2–3 words + active word highlight; у нас + scale фразы)

### Правила
1. Важные слова держатся **словосочетанием** (частица «не» + strong вместе).
2. До речи: фраза **белая**, чуть крупнее — **без** neon.
3. При речи: **вся фраза** растёт вместе; **neon только на текущем** слове.
4. Обычные слова: dim→white, без scale.
5. Neon: yellow / orange / pink (без cyan).

---

# VideoMaker — r39 (2026-09-05)

## r39 — ShortsMaker-style emphasis (pre-size + pop)

### Эффект (как в TikTok captions / ShortsMaker)
- **Strong** на экране уже чуть крупнее + neon **до** озвучки
- В момент речи — **ещё крупнее** + короткий pop (`fscx118`)
- Обычные слова: dim → white, **без** scale (не «прыгает каждая буква»)

### Neon (без cyan)
| L | Hex | Цвет | base→active scale |
|---|-----|------|-------------------|
| L1 | #FFA500 | soft orange | 1.04 → 1.10 |
| L2 | #FFFF00 | yellow | 1.06 → 1.16 |
| L3 | #FF5E00 | orange | 1.10 → 1.24 |
| L4 | #FF00FF | pink | 1.12 → 1.32 |

### Анти-баги
- слова < 2 букв не красятся (фикс «буква в»)
- только exact match
- AISIE только keyword-group

---

# VideoMaker — r38 (2026-09-05)

## r38 — чистый neon + точные слова

### Цвета (только neon)
| Level | Hex | Цвет | Scale |
|-------|-----|------|-------|
| L1 | #FFA500 | soft neon orange | 1.08 |
| L2 | #FFFF00 | neon yellow | 1.14 |
| L3 | #FF5E00 | neon orange | 1.22 |
| L4 | #FF00FF | neon pink | 1.32 |

Убраны: cyan, coral #FF3B30, pure red.

### Какие слова красятся
- Gemini `strong_words` — как есть
- AISIE **только** `is_keyword_group`: поддержка L2 + последнее слово punch L3/L4
- Non-keyword groups — **не** красятся (раньше красили почти всё)
- Lookup — **только exact match** (prefix давал ложные срабатывания)

### Размер
Чем выше L — тем крупнее слово в момент произнесения (до ×1.32).

---

# VideoMaker — r37 (2026-09-05)

## r37 — neon AISIE colors + keyword punch

### Цвета (ASS BGR = AISIE styles.py)
| Level | Hex | ASS | Смысл |
|-------|-----|-----|--------|
| L1 | #00FFFF | cyan | лёгкий акцент |
| L2 | #FFFF00 | neon yellow | keyword |
| L3 | #FF3B30 | coral/red | hook |
| L4 | #FF0000 | pure red | climax |

### strong_map
- AISIE `is_keyword_group`: последнее слово → L3/L4, остальные в группе → L2
- Gemini strong_words без изменений
- Лог если hooks=0 и strong не пополнились

### Было не так
- Цвета не neon (тусклый orange/green)
- Все слова semantic_group красились одинаково
- При hooks=0 AISIE не давал strong → «логика не работает»

---

# VideoMaker — r36 (2026-09-05)

## r36 — Clean Pro: SF Pro Display

- ASS Style CleanPro: Fontname **SF Pro Display** (системный Apple на macOS).
- Bold уже включён в стиле (−1).
- Логика highlight r35 без изменений.

---

# VideoMaker — r35 (2026-09-05)

## r35 — AISIE-style karaoke / Clean Pro highlight

### Проблема (лог пользователя)
- Каждое слово «прыгало» (увеличивалось) при произнесении.
- Strong-слова уже были цветными/крупными **до** озвучки (предраскраска строки).

### Как должно быть (ShortsMaker / AISIE)
1. До речи: все слова одинаковые (dim, один размер).
2. Обычное активное: белый, **без** scale.
3. Strong L1–L4: цвет + scale 8–28% **только** в момент произнесения (+ короткий pop).

### Исправление
- `subtitles.py` → r35: `_build_clean_pro_window` + общий karaoke `tags_word`.
- Шрифт Clean Pro: Arial, мягкая тень (как раньше).

---

# VideoMaker — r34 (2026-09-05)

## r34 — CRITICAL: NameError strong_map

### Баг из лога
```
ОШИБКА: name 'strong_map' is not defined
```
В `burn_subtitles` после AISIE логирование и karaoke path использовали `strong_map`,
но переменная не создавалась (определялась только внутри `_build_karaoke_window` / Clean Pro).

### Исправление
- `video_maker/engines/subtitles.py` → r34:
  `strong_map = _strong_map(analysis) if enable_strong_words else {}`
  сразу после `words = _words_from_transcription(...)`.

### Сохранено
- r33: 1 mp4 на short (temp), снова 4–5 clips
- r31: karaoke на full video
- r30: Clean Pro 2–3 + AISIE strong

---

# VideoMaker — r33 (2026-09-05)

## r33 — один mp4 на short + снова 4–5 клипов

### Shorts
- В `shorts/short_00N/` **только** `short_00N.mp4` + txt (title/hook/…).
- `*_cut.mp4` / `*_subs.mp4` **не пишутся** в папку (только temp → удаляются).
- Gemini снова **4–5** клипов (лимит «1 short» снят).

### Сохранено
- r31: karaoke на full video
- r30: Clean Pro 2–3 слова + AISIE strong
- heartbeat → debug

---

# VideoMaker — r32 (2026-09-05)

## r32 — один Short + тихий heartbeat

### Shorts
Раньше Gemini просил 4–5 клипов → 4 encode (в т.ч. почти весь ролик).
Сейчас: промпт + hard limit → **1** лучший short. Остальные не создаются.

### Лог
`LIFECYCLE heartbeat` переведён на **debug** — не засоряет INFO.

---

# VideoMaker — r31 (2026-09-05)

## r31 — CRITICAL: karaoke на full video

### Причина бага (лог пользователя)
```
[СУБТИТРЫ] fallback segment subs=10 (нет word-timings)
```
Код ошибочно входил в fallback, когда **clip=None** (полный ролик), даже если words были.
Karaoke/Clean Pro/AISIE strong работали **только** для clip (шорт re-burn).

### Исправлено
1. Karaoke строится при **любых** words (full + clip).
2. Fallback segment — только если words реально пусты.
3. Whisper cache HIT с words=0 → пересчёт с word_timestamps.

Файлы: `subtitles.py`, `transcription.py` → **r31**

---

# VideoMaker — r30 (2026-09-05)

## r30 — Clean Pro 2–3 слова + заметный AISIE strong

### Исправлено (по логу продакшена)
1. **Группы субтитров**: жёстко 2–3 слова (4 только если все слова ≤4 символов).
   Раньше `max_chars` от ширины 4K раздувал «простыни».
2. **Clean Pro**: белый + мягкая тень (bord0/shad3) сохранён; active белый, dim серый.
3. **AISIE/Gemini strong**: L2 жёлтый +12%, L3 оранжевый +20%, L4 красный +28% + pop.
4. **Match strong**: ё→е, prefix-match ключей.

Файл: `video_maker/engines/subtitles.py` → **r30**

---

# VideoMaker — r29 (2026-09-04)

## r29 — словосочетания на vertical = границы шортов Gemini

### Сделано
После `clips_for_shorts` от Gemini при burn субтитров на vertical:
- для каждого шорта находятся **первое и последнее слово** (по word timings);
- группы на экране (karaoke / Clean Pro / AISIE-стили) **начинаются** на первом слове шорта и **заканчиваются** на последнем;
- между шортами текст идёт своим чередом;
- cut с vertical больше не показывает середину словосочетания на in/out.

Файлы: `video_maker/engines/subtitles.py` → **r29**

---

# VideoMaker — r28 (2026-09-04)

## r28 — субтитры: позиция + AISIE strong + окно речи шорцов

### Исправлено
1. **Позиция vertical/shorts**: центр текста на **56%** высоты (`\an5`), т.е. 4–6% ниже середины.
   Причина «выше половины»: часть путей ставила `\an2` (низ бокса) на mid → тело строки уезжало вверх.
2. **Окно речи клипа**: в шорце только слова внутри [start, end]; ничего до первого и после последнего слова.
3. **strong_map / AISIE**: нормализация ключей; Gemini `strong_words` + AISIE `semantic_groups` → цвет/scale в karaoke.
4. **aisie_integration**: дописывает strong_words из semantic_groups даже если hooks уже от Gemini.
5. **Группировка**: не оставлять одно слово-сироту после группы.

### Файлы
- `video_maker/engines/subtitles.py` → r28
- `video_maker/engines/aisie_integration.py` → r28

---

# VideoMaker — r27-clean (2026-09-04)

## r27-clean — дистрибутив без дубликатов + полный VERSION.md

### Сделано
1. **Удалены** корневые `engines/`, `gui/`, `pipeline/` (устаревшие дубликаты r12–r13).
2. **Удалены** из дистрибутива логи (`*.log`) и `cache/`.
3. **start.command** и **VideoMaker.app** launcher — относительные пути (без `/Users/dreamstore/...`).
4. **requirements.txt** / **README.md** — MLX Whisper (не WhisperX).
5. **VERSION.md** — версия или дата git **для каждого файла** проекта.
6. **.gitignore** — `cache/`, `*.log`.

### Не тронуто
- Логика пайплайна (r21–r27): one_encode vertical, shorts Hook+CTA, BGM reuse, IMO-галочки и т.д.

---

# VideoMaker — r27 (2026-09-04)

## r27 — GUI: галочки Intro/Middle/Outro + VERSION.md

### Исправлено
1. **Галочки IMO на вкладке «Основные»** (`video_maker/gui/app.py` → r27):
   - файл есть (isfile) → ACTIVE + auto ON; пусто/нет файла → DISABLED + OFF;
   - путь пустой → DISABLED + False;
   - смена intro/mid/outro любого формата пересчитывает все независимо;
   - баг: «выбрал Intro на вкладке IMO — галочка на Основных серая» — закрыт.
2. **settings.py**: validate() больше не блокирует запуск из‑за старого
   пути WhisperX (движок транскрипции = MLX Whisper).
3. **VERSION.md** — единый текстовый контроль версий всех файлов и сборок.

### Не тронуто
- pipeline/engines (shorts r26, video r25, branches r22, …)
- REPLACE-семантика, resume, пути с пробелами/кириллицей

---

# VideoMaker — r21 (2026-09-02)

## r21 — vertical one_encode + safe BGM + shorts only Hook+CTA

### Исправлено
1. **Vertical dual-pass убран**: больше нет `geometry-only` + `geometry+ASS`.
   Строго один encode: geometry + ASS → `final_9x16`, `one_encode=True`.
2. **Shorts**: cut stream-copy из `final_9x16`; по умолчанию burn только Hook+CTA
   (`s_enable_subtitles=False`, `s_enable_strong_words=False`).
3. **BGM reuse**: не копируем аудио с wide на vertical, если:
   - на wide был IMO, а на vertical — нет, или
   - длительности расходятся > 0.5 с.
   Иначе — безопасный copy с `-t` по длительности video.
4. **PipelineContext**: явные поля `bgm_mixed`, `bgm_source_video`, `h_did_imo`.
5. **Checkpoint**: сохраняет/восстанавливает `bgm_mixed`, `h_did_imo`.

### Не тронуто
- FAST IMO (`path=FAST`)
- 1 Hook + 1 CTA на long wide/vertical
- REPLACE-семантика, resume, пути с пробелами/кириллицей

---

# VideoMaker — итоговые исправления (29.08.2026)

## Что исправлено

### 1. B-roll (`engines/video.py`)
- Последовательный набор: первый клип ≥ audio → только он + trim.
- Иначе добавляем следующий, пока сумма ≥ target.
- Без shuffle, без 50–100 лишних клипов.

### 2. Whisper (`engines/transcription.py` + `config/settings.py` + GUI)
- Default: **large-v3**
- macOS: сразу cpu + int8 (без MPS)
- batch_size 8/16/24 по RAM, --threads = число ядер
- Один вызов Whisper на весь пайплайн

### 3. Cliffhanger стиль (`engines/subtitles.py`)
- Новый стиль «Cliffhanger (Tension)» для субтитров и хуков
- Хуки: 3–5 слов (макс 7), мин. длительность ~0.45 с/слово
- Красный акцент, сильный scale-pop

### 4. GUI (`gui/app.py`)
- Исправлен NameError в окне ошибки (`lambda err=e: ...`)
- Убрана упрощённая строка «Intro / Middle / Outro» с главной вкладки
  (полный выбор остаётся на вкладке «Intro / Middle / Outro»)
- Default модели Whisper в GUI = large-v3

### 5. BGM (`engines/audio.py`)
- mix_bgm всегда пишет во временный файл через mkstemp,
  затем os.replace — больше нет ffmpeg exit 234 на in-place записи

## Как установить

Скопируй файлы поверх своих в проект VideoMaker:

```bash
# из папки VideoMaker_fixes:
cp video_maker/engines/video.py          /путь/к/VideoMaker/video_maker/engines/
cp video_maker/engines/transcription.py  /путь/к/VideoMaker/video_maker/engines/
cp video_maker/engines/subtitles.py      /путь/к/VideoMaker/video_maker/engines/
cp video_maker/engines/audio.py          /путь/к/VideoMaker/video_maker/engines/
cp video_maker/config/settings.py        /путь/к/VideoMaker/video_maker/config/
cp video_maker/gui/app.py                /путь/к/VideoMaker/video_maker/gui/
```

Перезапусти приложение.

## Проверка после установки
1. GUI: модель Whisper = large-v3
2. Главная вкладка — нет строки «Intro / Middle / Outro» (только на 2-й вкладке)
3. При ошибке пайплайна — появляется нормальное окно с текстом ошибки
4. В логе B-roll: «Выбрано клипов: 1–3», Whisper — один запуск без MPS fallback
