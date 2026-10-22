# Eval: эталонные сцены VideoMaker

Дата: 2026-10-22 20:43 UTC  
Gold: `/Users/dreamstore/VideoMaker-r57cp34/eval/gold.jsonl`  
Режим retrieval: `fresh`  
Сцен: **40**, из них с приемлемым клипом в библиотеке: **12**
VLM при сборке: **12** вызовов (0.30/сцену); на ручную проверку: **31**

## Главное

| Срез | Сцен | Recall-сцен | Recall@1 | Recall@3 | Recall@10 | literal@1 | literal@10 | fallback rate | retrieval, мс/сцену | VLM/сцену |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **всего** | 40 | 12 | 8.3% | 58.3% | 66.7% | 0.0% | 50.0% | 77.5% | 15550.48 | 0.3 |
| split: train | 30 | 7 | 0.0% | 42.9% | 57.1% | 0.0% | 50.0% | 80.0% | 15803.56 | 0.233 |
| split: holdout | 10 | 5 | 20.0% | 80.0% | 80.0% | 0.0% | 50.0% | 70.0% | 14791.25 | 0.5 |

literal-сцен в знаменателе literal hit-rate: **4** (только сцены с приемлемым клипом в библиотеке).

## По классам сцен

| Класс | Сцен | Recall-сцен | Recall@1 | Recall@3 | Recall@10 | literal@1 | literal@10 | fallback rate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| literal | 16 | 4 | 0.0% | 25.0% | 50.0% | 0.0% | 50.0% | 81.2% |
| associative | 20 | 8 | 12.5% | 75.0% | 75.0% | n/a | n/a | 70.0% |
| abstract | 4 | 0 | n/a | n/a | n/a | n/a | n/a | 100.0% |

## Метки пригодности

| Метка | Всего | train | holdout |
|---|---:|---:|---:|
| accept | 9 | 6 | 3 |
| fallback_ok | 27 | 21 | 6 |
| must_graphic | 4 | 3 | 1 |

## Где приемлемый клип не найден в top-10

| Сцена | split | класс | метка | выбранный | приемлемые | top-3 retrieval |
|---|---|---|---|---|---|---|
| `часть_02_046` | holdout | literal | accept | `confused_person_many_choices_01_pixabay.mp4` | confused_person_many_choices_01_pixabay.mp4 | thoughtful_man_making_decision_4_pixabay.mp4, lonely_man_dark_bedroom_01_pixabay.mp4, notebook_writing_hand_closeup_111289_pixabay.mp4 |
| `часть_02_052` | train | associative | accept | `close_up_face_looking_01_pixabay.mp4` | close_up_face_looking_01_pixabay.mp4 | phone_call_alone_dark_204421_pixabay.mp4, person_inside_elevator_looking_2_pixabay.mp4, intense_close_up_sad_01_pixabay.mp4 |
| `часть_02_000` | train | associative | accept | `hand_swiping_dating_app_01_pixabay.mp4` | hand_swiping_dating_app_01_pixabay.mp4 | working_late_laptop_52823_pixabay.mp4, teenagers_sitting_together_staring_01_pixabay.mp4, person_eating_breakfast_looking_1_pixabay.mp4 |
| `часть_02_073` | train | literal | accept | `young_professional_working_late_01_pixabay.mp4` | young_professional_working_late_01_pixabay.mp4 | library_books_122234_pixabay.mp4, office_workers_headphones_monitors_02_pixabay.mp4, books_study_139804_pixabay.mp4 |

## Как читать

- **Recall@K** — доля recall-сцен, где хотя бы один приемлемый клип (VLM ≥ 6/10) оказался в топ-K. Сцены без приемлемого клипа в библиотеке (чистый сток / must_graphic) в знаменатель не входят.
- **literal@1** — доля literal-сцен, где top-1 сразу приемлемый. Это самый чувствительный показатель к «красиво, но не то».
- **fallback rate** — доля сцен, где выбранный клип не принят: нужен другой клип (fallback_ok) или графика (must_graphic).
- **VLM/сцену** — реальные вызовы Vision при сборке gold (top-3 кандидата на сцену), а не при прогоне метрик.

## Ограничения

- Holdout — 10 сцен, поэтому доверительные интервалы широкие; метрики holdout нельзя использовать для подбора порогов.
- Кандидаты — только библиотека (без stock-API и скачивания). Стоковые клипы в офлайн-recall не участвуют.
- VLM проверяет только top-3 кандидата на сцену; Recall@10 — нижняя оценка (приемлемый клип на ранге 4-10 мог не проверяться).
- Классы и метки предварительные: см. `eval/README.md`, поля `confidence` и `needs_human_review`.

## Воспроизвести

```bash
.venv/bin/python3 eval/make_gold.py --limit 40 --verify-top 3
.venv/bin/python3 eval/run_eval.py --retrieval gold   # быстро
.venv/bin/python3 eval/run_eval.py --retrieval fresh  # честный текущий поиск
.venv/bin/python3 -m pytest eval/tests -q
```
