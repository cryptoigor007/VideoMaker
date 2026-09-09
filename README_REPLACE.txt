VideoMaker r43-tail — файлы для замены

Куда класть (относительно корня проекта VideoMaker):
  video_maker/pipeline/shorts.py   ← заменить целиком
  video_maker/engines/video.py     ← заменить целиком
  start.command                    ← опционально (относительные пути)

Что сделано:
  • cut_end = max(gemini_end, last_word_end) + 0.40s, clamp к длительности файла
  • лог: [SHORTS] #N tail: gemini_end=... last_word=... → cut_end=...
  • если stream copy короче нужного >0.15s → один VT re-trim (точный -ss)

Проверка в логе после прогона:
  [SHORTS] #1 tail: gemini_end=39.60 last_word=... pad=0.40 → cut_end=40.10 (limit=40.10)
