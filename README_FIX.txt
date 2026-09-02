VideoMaker FULL snapshot + FIX 2026.09.02-r13

Источник: github.com/cryptoigor007/VideoMaker (latest) + патч outro.

ГЛАВНОЕ ИСПРАВЛЕНИЕ r13 (wide / final_16x9):
• Аутро ЗАМЕНЯЕТ последние N секунд основного видео
  (N = реальная длительность файла outro, probe).
• НЕ приклеивается в конец (старый concat убран).
• Длительность wide = длительности master.
• Речь (аудио master) идёт под визуалом аутро.
• Intro по-прежнему prepend; при intro — pad silence в аудио.
• -shortest больше не отрезает аутро.

Файлы с патчем:
  video_maker/engines/video.py
  engines/video.py  (дубликат на корне, синхронизирован)

Как ставить:
1. Распаковать поверх своей папки VideoMaker
   ИЛИ использовать эту папку как новый корень проекта.
2. head -5 video_maker/engines/video.py
   → должна быть строка 2026.09.02-r13
3. Запускать тот же Python/venv, что смотрит в эту папку.

Проверка в логе:
  [IMO] outro REPLACE last XX.XXs of main (not append)
