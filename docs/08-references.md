# Референс-проекты

Пять open-source проектов, изучаемые в контексте «Ассистента ПБ». Локально
лежат в `references/` (не в git — большие). Здесь только карточки: что взяли
и почему.

## Как их читать

- **Не копируем код** — берём паттерны, данные-схемы, UX-решения.
- Каждый архив в `references/<project>/` содержит `README_reference.md`
  с нашей записью «что применимо / что нет».

---

## 1. gpt4all — UX/UI reference for the project

Ссылка: https://github.com/nomic-ai/gpt4all · Лицензия: MIT

Референс десктоп-чата с локальной моделью (Qt + WebEngine). Смотрим на:
раскладку окна, стриминг ответа, менеджер моделей, sidebar с историей.

## 2. docquery — Answer extraction from scans and PDFs

Ссылка: https://github.com/impira/docquery · Лицензия: MIT

Референс для **структурного извлечения** из документов: parse → layout →
QA. Идея для будущего: извлекать номер договора / дату / стороны как
структурированные поля, а не только полнотекстовый анализ.

## 3. OpenContracts — Deep legal contract analysis

Ссылка: https://github.com/JSv4/OpenContracts · Лицензия: Apache-2.0

Референс полного цикла договора: parse → annotate → extract clauses →
review → diff. Полезная модель данных `Document → Section → Clause →
Annotation` — цель для нашего Sprint 5+ (сейчас у нас всё в памяти).

## 4. private-gpt — Local RAG assistant for documents

Ссылка: https://github.com/imartinez/privateGPT · Лицензия: Apache-2.0

Ближайший к нам по архитектуре: локальный LLM + локальный векторный
стор + офлайн. Полезные паттерны: расширенный ingestion (много форматов),
рекурсивный chunker с сохранением границ предложений, source-citations
рядом с ответом.

## 5. languagetool — Offline spelling, grammar and style check for Russian

Ссылка: https://github.com/languagetool-org/languagetool · Лицензия: LGPL-2.1

Правило-ориентированный чекер для русского языка. Идея гибрида:
LanguageTool делает первый быстрый pass по грамматике/пунктуации,
LLM — только стилистические улучшения. Плюс: словари для отраслевых
терминов (АПС, СОУЭ, дренчерная и т.п.) для устранения false positives.

---

## Дерево локальных копий (не в git)

```
references/
├── gpt4all-main/           # 34 МБ распакованных
├── docquery-main/          # 44 КБ
├── OpenContracts-main/     # 89 МБ
├── private-gpt-main/       # 25 МБ
└── languagetool-master/    # 98 МБ (~ 300 МБ распакованных)
```

Общий объём ~450 МБ распакованных исходников. Каталог `references/` целиком
в `.gitignore` — на GitHub идёт только этот файл.

Как обновить копии:
1. Скачать свежий zip с GitHub оригинала.
2. Распаковать в `references/<project>-main/`.
3. Дополнить `README_reference.md` в подкаталоге, если появилось что-то
   новое интересное.
