// Общая утилита: каркас страницы, опрос статуса задачи, рендер результата.

// Сессия истекла или сервер перезапустили — уводим на форму входа один раз.
// Перехват стоит на самом fetch, а не в каждом вызове: обращений к API больше
// двадцати по всем страницам, и забытая проверка означала бы «кнопка молча не
// работает» вместо понятного «войдите заново».
(function guardAgainstExpiredSession() {
  const original = window.fetch;
  let redirecting = false;
  window.fetch = async function (...args) {
    const response = await original.apply(this, args);
    if (response.status === 401 && !redirecting && !location.pathname.endsWith("/login.html")) {
      redirecting = true;
      window.location.href = "/login.html";
    }
    return response;
  };
})();

// --- Иконки ---
// Штриховой SVG прямо в разметке. Эмодзи убраны намеренно: они рисуются
// шрифтом системы, на Windows выглядят иначе, чем на машине разработчика, и
// в деловом документообороте читаются как несерьёзность. Иконочный шрифт с
// внешнего CDN подключить нельзя — приложение офлайн.
const ICONS = {
  today: '<rect x="2.5" y="2.5" width="6" height="6" rx="1.5"/><rect x="11.5" y="2.5" width="6" height="6" rx="1.5"/><rect x="2.5" y="11.5" width="6" height="6" rx="1.5"/><rect x="11.5" y="11.5" width="6" height="6" rx="1.5"/>',
  doc: '<path d="M4 2.5h7l5 5v10.5H4z"/><path d="M11 2.5v5h5"/><path d="M7 12h6M7 15h4"/>',
  scales: '<path d="M10 3v14M4.5 6h11"/><path d="M4.5 6 2.5 11h4zM15.5 6l-2 5h4z"/>',
  search: '<circle cx="9" cy="9" r="5.5"/><path d="m13 13 4.5 4.5"/>',
  stack: '<path d="M2.5 6 10 2.5 17.5 6 10 9.5z"/><path d="m2.5 10 7.5 3.5 7.5-3.5"/><path d="m2.5 14 7.5 3.5 7.5-3.5"/>',
  mail: '<rect x="2.5" y="4.5" width="15" height="11" rx="1.5"/><path d="m3 5.5 7 5 7-5"/>',
  truck: '<path d="M1.5 5.5h10v8h-10zM11.5 8h3.5l3 3v2.5h-6.5z"/><circle cx="5" cy="15" r="1.8"/><circle cx="14.5" cy="15" r="1.8"/>',
  sheet: '<rect x="3.5" y="2.5" width="13" height="15" rx="1.5"/><path d="M6.5 6.5h7M6.5 10h7M6.5 13.5h4"/>',
  clock: '<circle cx="10" cy="10" r="7.5"/><path d="M10 5.5V10l3 2"/>',
  user: '<circle cx="10" cy="7" r="3.2"/><path d="M3.8 17c.6-3.2 3.2-5 6.2-5s5.6 1.8 6.2 5"/>',
  compare: '<path d="M3.5 4.5h5.5v11H3.5zM11 4.5h5.5v11H11z"/><path d="M6 8h1M6 11h1M13.5 8h1M13.5 11h1"/>',
  chart: '<path d="M3 17V8M8 17V4M13 17v-6M18 17v-9"/>',
  shield: '<path d="M10 2.5 3.5 5v5.5c0 3.4 2.7 6.3 6.5 7 3.8-.7 6.5-3.6 6.5-7V5z"/><path d="m7.3 9.8 2 2 3.4-3.6"/>',
  book: '<path d="M3.5 3.5h5.5a2 2 0 0 1 2 2v11a1.6 1.6 0 0 0-1.6-1.6H3.5z"/><path d="M16.5 3.5H11a2 2 0 0 0-2 2v11a1.6 1.6 0 0 1 1.6-1.6h5.9z"/>',
  // Три остановки на линии: документ идёт по рукам. Рисунок выбран за то, что
  // читается на 15 пикселях — «папка с часами» на этом размере сливается в
  // пятно, а от иконки нужно отличие от соседних, а не подробность.
  route: '<circle cx="3.4" cy="10" r="2.2"/><circle cx="10" cy="10" r="2.2"/><circle cx="16.6" cy="10" r="2.2"/><path d="M5.6 10h2.2M12.2 10h2.2"/>',
  // Карточка с номером: реквизит — это всегда номер в чьей-то карточке.
  // Галочку внутрь не кладу намеренно — она уже занята «Защитой данных», а на
  // 15 пикселях две иконки с галочкой различаются хуже, чем без неё.
  id: '<rect x="2.5" y="4.5" width="15" height="11" rx="1.5"/><circle cx="7" cy="9.3" r="1.7"/><path d="M4.5 13.5c.4-1.2 1.3-1.9 2.5-1.9s2.1.7 2.5 1.9"/><path d="M12 8.5h4M12 11.5h3"/>',
  // Калькулятор, а не знак суммы: «Σ» половина сотрудников читает как незнакомый
  // значок, а кнопочная коробка узнаётся без объяснений. Окошко сверху оставлено
  // крупным — на 15 пикселях кнопки сливаются в точки, и отличает иконку от
  // соседнего «листа» именно оно.
  calc: '<rect x="4.5" y="2.5" width="11" height="15" rx="1.5"/><rect x="7" y="5" width="6" height="2.5" rx="0.6"/><circle cx="7.6" cy="11" r="0.9"/><circle cx="10" cy="11" r="0.9"/><circle cx="12.4" cy="11" r="0.9"/><circle cx="7.6" cy="14.2" r="0.9"/><circle cx="10" cy="14.2" r="0.9"/><circle cx="12.4" cy="14.2" r="0.9"/>',
};

function icon(name) {
  return `<svg viewBox="0 0 20 20" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

// --- Каркас: боковая панель ---
// Все четыре группы функций в ежедневном обороте, значит человек переключается
// между ними много раз за день. Раньше каждое переключение стоило двух шагов
// («назад на главную» → плитка) — теперь навигация всегда на экране.
const NAV = [
  { items: [["/", "Сегодня", "today"]] },
  { group: "Документы", items: [
    ["/spellcheck.html", "Проверка текста", "doc"],
    ["/legal.html", "Анализ договора", "scales"],
    ["/ask.html", "Вопрос по файлу", "search"],
    ["/batch.html", "Проверить пачкой", "stack"],
    ["/compare.html", "Сверка таблиц", "compare"],
    ["/arithmetic.html", "Проверить счёт", "calc"],
    ["/requisites.html", "Проверка реквизитов", "id"],
    // Имя пункта совпадает с заголовком страницы («Где документ»), а не с
    // названием функции в плане («журнал прохождения»): человек ищет ответ на
    // свой вопрос, а не термин. Стоит в «Документах», хотя отвечает на другой
    // вопрос — не «что сделать с файлом», а «где он сейчас»; отдельная группа
    // ради одной строки добавила бы заголовок дороже самой строки. Когда к
    // журналу добавятся остальные экраны CRM, группу имеет смысл выделить.
    ["/doc-flow.html", "Где документ", "route"],
  ] },
  { group: "Переписка", items: [["/letter.html", "Письма", "mail"]] },
  { group: "Транспорт", items: [
    ["/transport.html", "Машины и рейсы", "truck"],
    ["/waybill.html", "Путевые листы", "sheet"],
  ] },
  // Справочники отдельно от работы: парк и точки заводят раз в полгода, а
  // машину выдают каждое утро. На одной странице редкое заставляло листать
  // себя ради частого.
  { group: "Справочники", items: [
    ["/reference-fleet.html", "Парк и точки", "book"],
    ["/reference-people.html", "Водители и прицепы", "user"],
  ] },
];

function renderShell() {
  const topbar = document.querySelector(".topbar");
  if (!topbar || document.querySelector(".sidebar")) return;

  // Логотип и имя компании в шапке: приложение внутреннее, но фирменное.
  const brand = topbar.querySelector(".brand");
  if (brand) {
    brand.innerHTML =
      '<a href="/"><img src="/static/logo.png" alt=""><span>ПожСервис</span>' +
      '<span class="product">· Ассистент</span></a>';
  }

  const here = location.pathname === "/" ? "/" : location.pathname;
  const aside = document.createElement("aside");
  aside.className = "sidebar";
  let html = "";
  for (const block of NAV) {
    if (block.group) html += `<div class="group">${block.group}</div>`;
    for (const [href, text, ic] of block.items) {
      const current = href === here ? ' aria-current="page"' : "";
      html += `<a href="${href}"${current}>${icon(ic)}<span class="nav-text">${text}</span></a>`;
    }
  }
  // Внизу — то, что открывают редко и по поводу: журнал задач и состояние
  // защиты данных. Второе нужно в основном ИТ-администратору, но прятать его
  // от сотрудника нельзя: именно ему объяснять, почему документ не сохранился.
  const bottom = [
    ["/history.html", "История", "clock"],
    ["/stats.html", "Что происходит", "chart"],
    ["/security.html", "Защита данных", "shield"],
  ];
  html += '<div class="bottom">' + bottom.map(([href, text, ic]) =>
    `<a href="${href}"${href === here ? ' aria-current="page"' : ""}>${icon(ic)}<span class="nav-text">${text}</span></a>`
  ).join("") + "</div>";
  aside.innerHTML = html;
  topbar.insertAdjacentElement("afterend", aside);
}

async function renderAuthBar() {
  const bar = document.querySelector(".topbar");
  if (!bar || document.getElementById("whoami")) return;
  let data;
  try {
    data = await (await fetch("/api/auth/me")).json();
  } catch (e) {
    return;
  }
  if (!data || !data.login) return;

  const box = document.createElement("div");
  box.id = "whoami";
  box.className = "status";
  box.textContent = data.login + " · ";
  const out = document.createElement("a");
  out.href = "#";
  out.textContent = "выйти";
  out.addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login.html";
  });
  box.appendChild(out);
  bar.appendChild(box);
}

async function updateHealth() {
  renderShell();
  renderAuthBar();
  const el = document.getElementById("health");
  if (!el) return;
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    // Сломанное шифрование важнее остальных статусов: в этом состоянии
    // приложение отказывается сохранять документы, и узнать об этом из
    // ошибки посреди работы — хуже, чем увидеть заранее.
    if (data.security && data.security.encryption_broken) {
      el.textContent = "⚠ Шифрование не работает — документы не сохраняются";
      el.className = "status err";
      return;
    }
    if (data.ok) {
      // Сотруднику нужен один факт: можно работать или нет. Имя модели,
      // состояние RAG и LanguageTool нужны ИТ-администратору (US-4.3) —
      // они остаются, но уезжают в подсказку по наведению и на страницу
      // «Защита данных». Раньше вся эта строка висела в шапке у секретаря.
      const details = [`Модель: ${data.ollama.model}`];
      details.push(data.rag_ready
        ? "Нормативная база подключена"
        : (data.rag_warning || "Нормативная база не подключена"));
      if (data.languagetool_ready) {
        details.push("LanguageTool подключен");
      } else if (data.languagetool_installed === false) {
        details.push("LanguageTool не установлен, орфография идёт через модель");
      }
      // Неполная готовность — не «всё хорошо»: без нормативной базы разбор
      // договора теряет ссылки на закон, и молчать об этом нельзя.
      const degraded = !data.rag_ready;
      el.textContent = degraded ? "● Готов, но без нормативной базы" : "● Готов к работе";
      el.className = degraded ? "status err" : "status ok";
      el.title = details.join("\n");
    } else {
      el.textContent = "⚠ " + (data.ollama.warning || "Модель недоступна");
      el.className = "status err";
    }
  } catch (e) {
    el.textContent = "⚠ Нет связи с программой";
    el.className = "status err";
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- Открыть посчитанное раньше ---
//
// Результат жил только в открытой вкладке: ушёл со страницы — потерял, а
// история хранила лишь сводку. При этом на сервере он есть: очередь держит
// последние двести задач в памяти, остальные лежат в базе (task_store), и
// /api/tasks/{id} отдаёт их одинаково.
//
// Открывается результат НА СВОЕЙ ЖЕ странице, через ?task=<id>. Так работает
// родной для функции разборщик — заводить отдельную страницу задачи значило бы
// продублировать пять разных рендеров и развести их поведение.

const TASK_PAGE = {
  spellcheck: "/spellcheck.html",
  legal: "/legal.html",
  letter: "/letter.html",
  batch: "/batch.html",
  ask: "/ask.html",
};

function taskLink(kind, taskId, text) {
  const page = TASK_PAGE[kind];
  if (!page || !taskId) return escapeHtml(text);
  return `<a href="${page}?task=${encodeURIComponent(taskId)}">${escapeHtml(text)}</a>`;
}

// Вызывается страницей после того, как она объявила свой renderResult.
async function openSavedTask(renderResult, container) {
  const taskId = new URLSearchParams(location.search).get("task");
  if (!taskId || !container) return;
  container.innerHTML = '<p class="empty">Открываю сохранённый результат…</p>';
  try {
    const r = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (r.status === 404) {
      // Чужая задача и несуществующая отвечают одинаково — и текст здесь
      // общий: подсказывать «эта задача не ваша» значит подтверждать, что она
      // существует.
      container.innerHTML = '<p class="empty">Этот результат не найден. Возможно, рабочие файлы уже удалены.</p>';
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const task = await r.json();
    if (task.status !== "done" || task.result == null) {
      container.innerHTML = `<p class="empty">${
        task.status === "error" ? "Эта задача завершилась ошибкой." :
        task.status === "cancelled" ? "Эта задача была отменена." :
        "Эта задача ещё считается — загляните на экран «Сегодня»."}</p>`;
      return;
    }
    container.innerHTML = "";
    renderResult(task.result, container, taskId);
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = "Это результат прошлой задачи. Чтобы посчитать заново, заполните форму выше.";
    container.prepend(note);
  } catch (e) {
    container.innerHTML = '<p class="empty">Не удалось открыть сохранённый результат.</p>';
  }
}

// --- Помощники страниц транспорта ---
// Лежали копиями в transport.html и waybill.html. С выносом справочников на
// отдельные страницы копий стало бы четыре, а правка «в одной из них» —
// обычным способом развести поведение экранов.

// Ошибку от backend'а показываем словами, а не «HTTP 409»: сообщения сервиса
// написаны для секретаря («Машина уже в рейсе»), и подменять их кодом значило
// бы выбросить единственную понятную подсказку.
async function api(url, options) {
  const r = await fetch(url, options);
  if (r.status === 204) return null;
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error(_текстОшибки(data, r.status));
  return data;
}

// Замечания pydantic приходят по-английски, а тексты интерфейса в проекте
// русские. Переводятся ровно те виды, которые дают НАШИ модели: длина строки,
// пропущенное поле, не-число. Остальные оставляются как есть — выдуманный
// перевод чужого текста хуже честного английского, а список видов растёт
// вместе с pydantic, и полный словарь разошёлся бы молча.
function _поРусски(e) {
  const тип = e.type || "";
  const ctx = e.ctx || {};
  if (тип === "string_too_short") {
    return ctx.min_length === 1 ? "заполните поле" : `не короче ${ctx.min_length} символов`;
  }
  if (тип === "string_too_long") return `не длиннее ${ctx.max_length} символов`;
  if (тип === "missing") return "поле обязательно";
  if (тип.startsWith("int_") || тип.startsWith("float_")) return "нужно число";
  if (тип === "bool_parsing") return "нужно «да» или «нет»";
  return e.msg || "неверное значение";
}

// Ошибка от backend'а словами. Сервисы кидают ValueError с человеческим
// текстом, и роутер отдаёт его строкой в `detail` — это показываем как есть.
//
// Но pydantic на 422 кладёт в `detail` СПИСОК объектов, и прежний код
// подставлял его в строку: на экране появлялось «[object Object]». Секретарь
// видел это на любой опечатке в форме и не мог понять, что от него хотят.
function _текстОшибки(data, status) {
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    // Имя поля в ответе техническое и английское («full_name»), показывать его
    // секретарю незачем. Когда замечание одно, и так понятно, о чём речь —
    // ошибка появляется рядом с только что отправленной формой. Когда их
    // несколько, без различения не обойтись, и техническое имя
    // оказывается меньшим злом, чем «две ошибки, догадайтесь какие».
    if (detail.length === 1) return _поРусски(detail[0]);
    return detail
      .map((e) => {
        const поле = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : "";
        return `${поле ? поле + ": " : ""}${_поРусски(e)}`;
      })
      .join("; ");
  }
  return `Не получилось (код ${status})`;
}

// Объявление showError здесь убрано: ниже, в разделе «Ошибки», лежала вторая
// функция с тем же именем и другой сигнатурой. Побеждала нижняя, и вызов
// showError(e) — а так её зовут три страницы — уходил в неё же, где объект
// ошибки оказывался на месте плашки. Падало на box.style: плашка не
// показывалась вовсе, человек не видел НИЧЕГО. Разбор — при самой функции.

function fmtWhen(iso) {
  if (!iso) return "—";
  const s = String(iso);
  // Форматов отметки времени в базе ДВА, и это не недосмотр:
  //   «2026-08-07 22:57:14»              — CURRENT_TIMESTAMP у SQLite: всегда
  //                                        UTC, зона не указана;
  //   «2026-08-07T18:09:02.442361+00:00» — то, что пишет Python: зона указана.
  // Первому зону надо дописать (иначе Safari и Firefox дают Invalid Date, а
  // Chrome молча считает время местным и врёт на три часа). Второму дописывать
  // нельзя ни в коем случае: «Z» в хвосте делает строку неразбираемой, и
  // человек видит её целиком — «2026-08-07T18:09:02.442361+00:00» вместо
  // времени. Прежняя версия дописывала всегда и на втором формате ломалась.
  const с_зоной = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s);
  const d = new Date(с_зоной ? s : s.replace(" ", "T") + "Z");
  return isNaN(d) ? escapeHtml(s) : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

// Чистая дата без времени — «2026-08-07» превращается в «07.08.2026».
// Через `new Date` её гонять нельзя: строка без зоны разбирается как UTC, и в
// поясах западнее Гринвича дата уезжает на день назад. Липецку это ничем не
// грозит (UTC+3), но цена ошибки здесь — не тот срок в путевом листе, а к
// таким местам «у нас-то работает» не применяется. Поэтому просто перестановка
// частей строки, без часовых поясов вовсе.
function fmtDate(iso) {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso).slice(0, 10));
  return m ? `${m[3]}.${m[2]}.${m[1]}` : escapeHtml(String(iso));
}

// Разряды разделяются пробелом: «142 500 км» вместо «142500 км». Пробег и
// расстояния доходят до сотен тысяч, и без разделителя число приходится
// пересчитывать глазами по цифрам. В карточке машины разделитель уже стоял, в
// журнале рейсов нет — одно и то же число показывалось двумя способами.
// Дробную часть не режем: расстояние от базы вводят с десятыми долями.
const num = (v, suffix) =>
  v === null || v === undefined
    ? "—"
    : `${typeof v === "number" ? v.toLocaleString("ru-RU", { maximumFractionDigits: 3 }) : v} ${suffix}`;

// Типы полей и сборка одного поля формы. Общие, потому что справочники
// водителей и прицепов уехали со страницы путевых листов на свою, а разметку
// поля обе строят одинаково: разойдись она — разошлись бы и подписи, и то,
// как выглядит «только для чтения».
const T = { text: "text", num: "number", date: "date", dt: "datetime-local", area: "area", check: "check" };

function fieldHtml(name, label, type, opts) {
  const id = `f-${name}`;
  const ro = opts && opts.readonly ? " disabled" : "";
  if (type === T.check) {
    return `<div class="field"><label><input type="checkbox" id="${id}"> ${escapeHtml(label)}</label></div>`;
  }
  if (type === T.area) {
    return `<div class="field"><label for="${id}">${escapeHtml(label)}</label>
      <textarea id="${id}" style="min-height:70px"${ro}></textarea></div>`;
  }
  if (["org", "driver", "vehicle", "card", "status"].includes(type)) {
    return `<div class="field"><label for="${id}">${escapeHtml(label)}</label>
      <select id="${id}"${ro}></select></div>`;
  }
  const step = type === T.num ? ' step="0.01"' : "";
  return `<div class="field"><label for="${id}">${escapeHtml(label)}</label>
    <input id="${id}" type="${type}"${step}${ro}></div>`;
}

// Реквизиты водителя и прицепа — те же на странице справочника и в путевом
// листе, поэтому список полей тоже общий.
const DRIVER_FIELDS = [
  ["full_name", "ФИО полностью", T.text],
  ["tab_number", "Табельный номер", T.text],
  ["licence_series", "Удостоверение: серия", T.text],
  ["licence_number", "номер", T.text],
  ["licence_issued_at", "выдано", T.date],
  ["licence_class", "Класс (категории)", T.text],
  ["snils", "СНИЛС", T.text],
  ["licence_card", "Лицензионная карточка", "card"],
];

const TRAILER_FIELDS = [
  ["mark", "Марка", T.text],
  ["reg_number", "Регистрационный номер", T.text],
  ["series", "Серия", T.text],
  ["code", "Код марки", T.text],
];

async function pollTask(taskId, onProgress) {
  while (true) {
    const r = await fetch(`/api/tasks/${taskId}`);
    if (!r.ok) throw new Error("Ошибка опроса задачи");
    const data = await r.json();
    if (onProgress) onProgress(data);
    if (data.status === "done") return data.result;
    // Отменённая — не ошибка и не успех. Без отдельной ветки цикл крутился бы
    // вечно на неизвестном статусе, и «Отменить» выглядело бы как зависание.
    if (data.status === "cancelled") {
      const e = new Error("Задача отменена");
      e.cancelled = true;
      throw e;
    }
    if (data.status === "error") throw new Error(data.error || "Ошибка");
    await new Promise(res => setTimeout(res, 1500));
  }
}

// Скачивание файла. Приложение обычно работает в окне pywebview (десктоп),
// а не в браузере — встроенный webview (WebView2 на Windows) НЕ поддерживает
// обычный браузерный механизм <a download>: клик молча ничего не делает,
// без ошибки. Поэтому внутри pywebview идём через нативный мост (js_api,
// см. fire_safety_desktop/main.py::_Api.save_file) с системным диалогом
// «Сохранить как»; в обычном браузере (или если моста нет) — штатный <a download>.
async function downloadFile(url, filename) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
    const res = await window.pywebview.api.save_file(url, filename);
    return res;
  }
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  return { ok: true };
}

// --- Обратная связь ---
// Раньше требовалось три действия: выбрать оценку, написать комментарий,
// нажать «Отправить». Обратной связи в итоге не собрали ни одной. Теперь
// оценка уходит по первому же клику, комментарий — необязательное дополнение.

function renderFeedbackBlock(container, functionName, taskId) {
  const box = document.createElement("div");
  box.className = "feedback-box";
  box.innerHTML = `
    <span class="feedback-label">Результат полезен?</span>
    <button type="button" class="feedback-vote" data-rating="up">Да</button>
    <button type="button" class="feedback-vote" data-rating="down">Нет</button>
    <span class="feedback-status"></span>
  `;
  container.appendChild(box);

  const statusEl = box.querySelector(".feedback-status");
  const votes = box.querySelectorAll(".feedback-vote");

  async function send(rating, comment) {
    const r = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        function: functionName || "unknown",
        task_id: taskId,
        rating,
        comment: comment || "",
      }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  }

  votes.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const rating = btn.getAttribute("data-rating");
      votes.forEach(b => { b.disabled = true; });
      btn.classList.add("active");
      try {
        await send(rating);
        statusEl.textContent = "Спасибо, записано.";
        // Комментарий предлагаем только после «Нет»: когда всё хорошо,
        // человеку нечего дописывать, и лишнее поле только мешает.
        if (rating === "down") askComment();
      } catch (e) {
        statusEl.textContent = "Не удалось отправить";
        votes.forEach(b => { b.disabled = false; });
      }
    });
  });

  function askComment() {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "feedback-comment";
    input.placeholder = "Что оказалось не так? (по желанию, Enter — отправить)";
    input.maxLength = 1000;
    box.insertBefore(input, statusEl);
    input.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter" || !input.value.trim()) return;
      input.disabled = true;
      try {
        await send("down", input.value);
        statusEl.textContent = "Спасибо, записано.";
      } catch (err) {
        statusEl.textContent = "Не удалось отправить";
        input.disabled = false;
      }
    });
  }
}

// --- Ожидание ---
// Состояние, которое видят все и каждый день: задача на CPU идёт минутами, и
// это не лечится. Значит ожидание надо не прятать, а обставить — понятной
// стадией, остатком времени, разрешением уйти и возможностью отменить.

function renderProgress(container, label, percent, taskId) {
  const pct = Math.max(0, Math.min(100, Math.round(percent || 0)));

  // Блок перерисовывается раз в полторы секунды. Если каждый раз заново
  // собирать разметку, кнопка отмены теряет своё состояние: нажатое «Отменяю…»
  // сбрасывалось обратно, и человек жал ещё раз, думая, что не сработало.
  const bar = container.querySelector(".progress-bar-fill");
  if (bar && container.dataset.taskId === (taskId || "")) {
    container.querySelector(".progress-text").textContent = label;
    container.querySelector(".progress-percent").textContent = `${pct}%`;
    bar.style.width = `${pct}%`;
    return;
  }
  container.dataset.taskId = taskId || "";

  const cancel = taskId
    ? `<div class="progress-actions"><button type="button" class="btn secondary" data-cancel="${escapeHtml(taskId)}">Отменить задачу</button></div>`
    : "";
  container.innerHTML = `
    <div class="progress-label"><span class="spinner"></span><span class="progress-text">${escapeHtml(label)}</span><span class="progress-percent">${pct}%</span></div>
    <div class="progress-bar"><div class="progress-bar-fill" style="width:${pct}%"></div></div>
    <p class="progress-leave">Окно можно закрыть — задача считается на сервере, результат будет ждать на экране «Сегодня». Первый запуск после включения сервера всегда дольше: модель прогревается около минуты.</p>
    ${cancel}
  `;
  const btn = container.querySelector("[data-cancel]");
  if (btn) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Отменяю…";
      try {
        await fetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
      } catch (e) {
        btn.disabled = false;
        btn.textContent = "Отменить задачу";
      }
    });
  }
}

// Сколько ждать, по-человечески. Сервер отдаёт секунды или null, когда
// статистики по этому виду задач ещё нет — в этом случае молчим, а не
// выдумываем число.
function humanEta(seconds) {
  if (seconds == null || seconds <= 0) return "";
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return "меньше минуты";
  if (minutes < 60) return `примерно ${minutes} мин`;
  return `примерно ${Math.round(minutes / 60)} ч`;
}

// Что показать, пока задача ждёт своей очереди. На сервере работают несколько
// человек, а задача на CPU идёт минутами: без позиции интерфейс просто молчит,
// и человек не понимает, работает программа или зависла.
function queueLabel(t) {
  if (t.status !== "queued") return t.progress || "Обработка";
  if (!t.position || t.position <= 1) return "Следующая в очереди";
  const eta = humanEta(t.eta_sec);
  return `В очереди: ${t.position}-я из ${t.queue_length}` + (eta ? ` · ${eta}` : "");
}

// --- Ошибки ---
// В плашку уходил сырой ответ сервера, то есть текст для программиста.
// Сообщение об ошибке пишется для секретаря; техническая строка остаётся,
// но мелко и отдельно — её просят прислать разработчику.
//
// Зовут эту функцию двумя способами, и оба настоящие:
//   showError(ошибка)        — со страниц справочников и транспорта;
//   showError(плашка, ошибка) — из submitForm, где плашка уже под рукой.
// Раньше на два способа было два объявления с одним именем. Побеждало нижнее,
// поэтому вызов одним аргументом клал объект ошибки на место плашки и падал
// на `box.style`: сообщение не показывалось ВОВСЕ. Отказ становился
// невидимым — человек нажимал «Сохранить», ничего не происходило, и он
// нажимал ещё раз.
//
// Различаются вызовы по аргументу, а не по их числу: `undefined` вторым
// параметром — обычное дело, а вот плашка всегда узел DOM.
function showError(boxOrError, maybeError) {
  const с_плашкой = Boolean(boxOrError) && typeof boxOrError.appendChild === "function";
  const box = с_плашкой ? boxOrError : document.getElementById("error");
  const error = с_плашкой ? maybeError : boxOrError;
  if (!box) return;
  const raw = (error && error.message) || String(error || "");
  let human = "Не получилось. Попробуйте ещё раз.";
  if (/413|too large|слишком велик/i.test(raw)) {
    human = "Файл слишком большой — программа его не приняла.";
  } else if (/415|unsupported|формат/i.test(raw)) {
    human = "Такой формат файла не поддерживается. Подойдут DOCX, PDF, скан или текст.";
  } else if (/пуст|empty|no text/i.test(raw)) {
    human = "В файле не нашлось текста. Если это скан, проверьте, что страница не пустая.";
  } else if (/503|Ollama|модель|model/i.test(raw)) {
    human = "Модель не отвечает. Скорее всего, сервер ещё запускается — подождите минуту и попробуйте снова.";
  } else if (/NetworkError|Failed to fetch|опроса задачи/i.test(raw)) {
    human = "Связь с программой прервалась. Проверьте, что сервер работает.";
  } else if (/[а-яё]/i.test(raw) && !/^\s*(HTTP\b|[[{])/.test(raw)) {
    // Ни один случай выше не подошёл, а текст уже по-русски. В этом проекте
    // сообщения сервера пишутся ДЛЯ секретаря — «СНИЛС не сходится по
    // контрольному числу», «Машина уже в рейсе». Заменять их общим «не
    // получилось» и прятать вниз мелким шрифтом значит менять точный ответ на
    // бессодержательный: человек видел «попробуйте ещё раз» и пробовал ещё
    // раз, хотя надо было исправить цифру.
    human = raw;
  }
  // Техническую строку не повторяем, когда она и есть сообщение: одна и та же
  // фраза дважды выглядит как сбой самой программы.
  const tech = human === raw ? "" : `<span class="tech">${escapeHtml(raw.slice(0, 400))}</span>`;
  box.innerHTML = escapeHtml(human) + tech;
  box.style.display = "block";
}

// --- Универсальная submit-логика ---

async function submitForm({ endpoint, buildRequest, resultContainer, progressContainer, renderResult }) {
  // Блокируются ВСЕ кнопки формы, а не только #submit. На проверке документа
  // кнопки две («быстрая» и «глубокая»), и при жёстком id вторая оставалась
  // живой: второй клик ставил вторую задачу на те же файлы и занимал очередь,
  // общую на тридцать человек.
  const buttons = [...document.querySelectorAll(".section .actions button")];
  const errBox = document.getElementById("error");
  errBox.style.display = "none";
  errBox.textContent = "";
  resultContainer.innerHTML = "";
  progressContainer.style.display = "block";
  renderProgress(progressContainer, "Отправка запроса…", 0);
  buttons.forEach(b => { b.disabled = true; });

  try {
    const { url, body, headers } = buildRequest();
    const r = await fetch(url, { method: "POST", body, headers });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t || `HTTP ${r.status}`);
    }
    const { task_id } = await r.json();
    // Идентификатор задачи в подписи не показывается: для секретаря это шум,
    // а место он занимал. Разработчику он виден в истории задач.
    renderProgress(progressContainer, "Задача поставлена в очередь…", 0, task_id);

    let taskKind = "";
    const result = await pollTask(task_id, (t) => {
      taskKind = t.kind || taskKind;
      renderProgress(progressContainer, queueLabel(t), t.percent, task_id);
    });

    progressContainer.style.display = "none";
    // Третьим аргументом — id задачи: он нужен там, где результат можно
    // пересобрать на сервере (проверка текста с частью принятых правок).
    // Остальные страницы его просто не принимают.
    renderResult(result, resultContainer, task_id);
    renderFeedbackBlock(resultContainer, taskKind, task_id);
  } catch (e) {
    progressContainer.style.display = "none";
    if (e && e.cancelled) {
      // Отмена — не отказ программы. Красная плашка здесь напугала бы зря.
      resultContainer.innerHTML = '<p class="empty">Задача отменена.</p>';
    } else {
      showError(errBox, e);
    }
  } finally {
    buttons.forEach(b => { b.disabled = false; });
  }
}
