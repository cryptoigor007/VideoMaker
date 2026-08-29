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
