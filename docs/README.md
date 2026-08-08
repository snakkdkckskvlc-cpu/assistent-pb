# Документация «Ассистент ПБ»

Полный справочник по проекту — от видения до эксплуатации.

## 01 · Видение
- [Elevator Pitch](01-vision/elevator-pitch.md)
- [Lean Canvas](01-vision/lean-canvas.md)
- [Портреты пользователей](01-vision/personas.md)

## 02 · Продукт
- [User Stories](02-product/user-stories.md)
- [Приоритизация по RICE](02-product/rice-prioritization.md)
- [Дорожная карта](02-product/roadmap.md)
- [**Плановые работы** — что делаем, что не делаем и почему](02-product/backlog-plan.md)
- [**Разбор карты процессов ООО «ПожСервис»**](02-product/process-map-analysis.md) —
  37 шагов, узкие места, что чинится регламентом, а что программой
- [Модуль «Транспорт»](02-product/transport-module.md)
- [Чек-лист по транспорту](02-product/transport-checklist.md)

## 03 · Архитектура
- [**Проект CRM «ПожСервис»**](03-architecture/crm-target-design.md) — целевой
  процесс вместо 37 шагов карты, модель данных, встраивание в приложение,
  ограничения офлайна, этапы. Главный проектный документ по CRM.
- [Схема CRM в SQL](03-architecture/crm-schema.sql) — 31 таблица, 68 индексов,
  10 представлений. Выполняется на штатном sqlite3, в приложение не подключена
- [C4 · Контекст системы](03-architecture/c4-context.md)
- [C4 · Контейнеры](03-architecture/c4-container.md)
- [DDD · Ограниченные контексты](03-architecture/ddd-bounded-contexts.md)
- [ER-диаграмма модели данных](03-architecture/er-diagram.md)
- [ADR — журнал архитектурных решений](03-architecture/adr/)

## 04 · Дизайн
- [**Редизайн — август 2026**](04-design/redesign-2026-08.md) — аудит на 20 находок,
  фирменный стиль из маркетинг-кита, новая навигация и разбор экранов
- [Токены и базовые компоненты](04-design/tokens.css) — готовый CSS к переносу
- [Wireframes трёх экранов](04-design/wireframes/)
- [Дизайн-система](04-design/design-system.md) — текущее состояние, до внедрения редизайна

## 05 · Качество
- [BDD-сценарии](05-quality/bdd-scenarios.feature)
- [Стратегия тестирования](05-quality/test-strategy.md)
- [Матрица рисков](05-quality/risk-matrix.md)

## 06 · Команда
- [План найма](06-team/hiring-plan.md)
- [Ритуалы команды](06-team/team-rituals.md)
- [Матрица ответственности RACI](06-team/raci.md)

## 07 · Эксплуатация
- [Установка на Windows](07-ops/install-windows.md)
- [Установка на macOS/Linux (dev)](07-ops/install-macos.md)
- [Диагностика проблем](07-ops/troubleshooting.md)

## 08 · Референсы
- [Пять open-source проектов, изучаемых в контексте нашего](08-references.md)

## 09 · Платформа
- [**Скелет и кастомизация под клиента**](09-platform/README.md) — архитектура,
  каталог из 47 функций, разбор ~60 проектов
