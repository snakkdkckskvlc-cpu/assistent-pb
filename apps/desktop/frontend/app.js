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
  book: '<path d="M3.5 3.5h5.5a2 2 0 0 1 2 2v11a1.6 1.6 0 0 0-1.6-1.6H3.5z"/><path d="M16.5 3.5H11a2 2 0 0 0-2 2v11a1.6 1.6 0 0 1 1.6-1.6h5.9z"/>',
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
  html += `<div class="bottom"><a href="/history.html"${here === "/history.html" ? ' aria-current="page"' : ""}>` +
    `${icon("clock")}<span class="nav-text">История</span></a></div>`;
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
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}

function showError(e) {
  const box = document.getElementById("error");
  if (!box) return;
  box.style.display = "block";
  box.textContent = e.message || String(e);
}

function fmtWhen(iso) {
  if (!iso) return "—";
  // SQLite отдаёт CURRENT_TIMESTAMP как «2026-08-06 09:14:22» без зоны;
  // без замены пробела на «T» Safari и Firefox дают Invalid Date.
  const d = new Date(iso.replace(" ", "T") + (iso.endsWith("Z") ? "" : "Z"));
  return isNaN(d) ? escapeHtml(iso) : d.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

const num = (v, suffix) => (v === null || v === undefined ? "—" : `${v} ${suffix}`);

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
function showError(box, error) {
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
  }
  box.innerHTML = `${escapeHtml(human)}<span class="tech">${escapeHtml(raw.slice(0, 400))}</span>`;
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
