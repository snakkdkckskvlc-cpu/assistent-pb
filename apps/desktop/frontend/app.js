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

    const result = await pollTask(task_id, (t) => {
      const label = t.status === "queued" ? "В очереди" : (t.progress || "Обработка");
      progressContainer.innerHTML = `<span class="spinner"></span> ${escapeHtml(label)}`;
    });

    progressContainer.style.display = "none";
    renderResult(result, resultContainer);
  } catch (e) {
    progressContainer.style.display = "none";
    errBox.style.display = "block";
    errBox.textContent = e.message || String(e);
  } finally {
    btn.disabled = false;
  }
}
