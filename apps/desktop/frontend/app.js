// Общая утилита: опрос статуса задачи + рендер результата.

async function updateHealth() {
  const el = document.getElementById("health");
  if (!el) return;
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    if (data.ok) {
      const rag = data.rag_ready ? " · нормативная база подключена" : " · нормативная база не подключена";
      const lt = data.languagetool_ready ? " · LanguageTool подключен" : "";
      el.textContent = `● ${data.ollama.model} готова${rag}${lt}`;
      el.className = "status ok";
    } else {
      el.textContent = "⚠ " + (data.ollama.warning || "Ollama недоступна");
      el.className = "status err";
    }
  } catch (e) {
    el.textContent = "⚠ Backend недоступен";
    el.className = "status err";
  }
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function pollTask(taskId, onProgress) {
  while (true) {
    const r = await fetch(`/api/tasks/${taskId}`);
    if (!r.ok) throw new Error("Ошибка опроса задачи");
    const data = await r.json();
    if (onProgress) onProgress(data);
    if (data.status === "done") return data.result;
    if (data.status === "error") throw new Error(data.error || "Ошибка");
    await new Promise(res => setTimeout(res, 1500));
  }
}

// --- Фидбек 👍/👎 (общий блок, дописывается под результатом любой из трёх функций) ---

function renderFeedbackBlock(container, functionName, taskId) {
  const box = document.createElement("div");
  box.className = "feedback-box";
  box.innerHTML = `
    <span class="feedback-label">Результат полезен?</span>
    <button type="button" class="feedback-vote" data-rating="up" title="Полезно">👍</button>
    <button type="button" class="feedback-vote" data-rating="down" title="Неполезно">👎</button>
    <input type="text" class="feedback-comment" placeholder="Комментарий (необязательно)" maxlength="1000">
    <button type="button" class="btn secondary feedback-send">Отправить</button>
    <span class="feedback-status"></span>
  `;
  container.appendChild(box);

  let rating = null;
  const upBtn = box.querySelector('[data-rating="up"]');
  const downBtn = box.querySelector('[data-rating="down"]');
  const commentInput = box.querySelector(".feedback-comment");
  const sendBtn = box.querySelector(".feedback-send");
  const statusEl = box.querySelector(".feedback-status");

  function selectRating(value) {
    rating = value;
    upBtn.classList.toggle("active", value === "up");
    downBtn.classList.toggle("active", value === "down");
    statusEl.textContent = "";
  }
  upBtn.addEventListener("click", () => selectRating("up"));
  downBtn.addEventListener("click", () => selectRating("down"));

  sendBtn.addEventListener("click", async () => {
    if (!rating) {
      statusEl.textContent = "Сначала выберите 👍 или 👎";
      return;
    }
    sendBtn.disabled = true;
    try {
      const r = await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          function: functionName || "unknown",
          task_id: taskId,
          rating,
          comment: commentInput.value,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      statusEl.textContent = "Спасибо, записано!";
      upBtn.disabled = true;
      downBtn.disabled = true;
      commentInput.disabled = true;
    } catch (e) {
      statusEl.textContent = "Не удалось отправить, попробуйте ещё раз";
      sendBtn.disabled = false;
    }
  });
}

// --- Универсальная submit-логика ---

async function submitForm({ endpoint, buildRequest, resultContainer, progressContainer, renderResult }) {
  const btn = document.getElementById("submit");
  const errBox = document.getElementById("error");
  errBox.style.display = "none";
  errBox.textContent = "";
  resultContainer.innerHTML = "";
  progressContainer.style.display = "block";
  progressContainer.innerHTML = '<span class="spinner"></span> Отправка запроса…';
  btn.disabled = true;

  try {
    const { url, body, headers } = buildRequest();
    const r = await fetch(url, { method: "POST", body, headers });
    if (!r.ok) {
      const t = await r.text();
      throw new Error(t || `HTTP ${r.status}`);
    }
    const { task_id } = await r.json();
    progressContainer.innerHTML = `<span class="spinner"></span> Задача поставлена в очередь (id: ${task_id})…`;

    let taskKind = "";
    const result = await pollTask(task_id, (t) => {
      taskKind = t.kind || taskKind;
      const label = t.status === "queued" ? "В очереди" : (t.progress || "Обработка");
      const tokenSuffix = t.tokens > 0 ? ` (…${t.tokens} токенов)` : "";
      progressContainer.innerHTML = `<span class="spinner"></span> ${escapeHtml(label)}${tokenSuffix}`;
    });

    progressContainer.style.display = "none";
    renderResult(result, resultContainer);
    renderFeedbackBlock(resultContainer, taskKind, task_id);
  } catch (e) {
    progressContainer.style.display = "none";
    errBox.style.display = "block";
    errBox.textContent = e.message || String(e);
  } finally {
    btn.disabled = false;
  }
}
