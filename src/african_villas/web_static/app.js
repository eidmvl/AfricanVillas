const state = { config: null, projects: [], project: null, tab: "block1", jobTimer: null };
const app = document.querySelector("#app");
const dialog = document.querySelector("#project-dialog");
const form = document.querySelector("#project-form");
const toast = document.querySelector("#toast");
const connection = document.querySelector("#connection");

const escapeHtml = (value = "") => String(value)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

const number = (value, digits = 0) => new Intl.NumberFormat("ru-RU", {
  maximumFractionDigits: digits,
}).format(Number(value || 0));

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Ошибка HTTP ${response.status}`);
  return payload;
}

function notify(message, error = false) {
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3500);
}

function setLoading(message = "Загружаем данные…") {
  app.innerHTML = `<section class="loading-card"><span class="spinner"></span>${escapeHtml(message)}</section>`;
}

function optionList(items, selected, valueKey = null, labelKey = null) {
  return items.map((item) => {
    const value = valueKey ? item[valueKey] : item;
    const label = labelKey ? item[labelKey] : item;
    return `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}

async function loadProjects() {
  clearInterval(state.jobTimer);
  state.project = null;
  state.projects = await api("/api/projects");
  renderProjects();
}

function renderProjects() {
  const count = state.projects.length;
  const cards = state.projects.map((project, index) => {
    const total = project.progress.total;
    const ready = project.progress.ready;
    const progress = total ? Math.round(ready / total * 100) : 0;
    return `<article class="project-card" data-action="open-project" data-project-id="${project.id}">
      <div class="card-top"><div class="project-index">${String(index + 1).padStart(2, "0")}</div><span class="status">${ready}/${total}</span></div>
      <h3>${escapeHtml(project.name)}</h3>
      <p>${escapeHtml(project.description || project.client_name || "Проект пока без описания")}</p>
      <div class="progress"><span style="width:${progress}%"></span></div>
      <span class="progress-label">Готовность юридического блока — ${progress}%</span>
      <span class="open-link">Открыть проект →</span>
    </article>`;
  }).join("");
  app.innerHTML = `<section class="hero">
      <div><span class="eyebrow">Облачное рабочее пространство</span><h1>Девелоперские проекты в одном контуре</h1>
      <p class="lead">Юрисдикции, градостроительные сценарии и предварительная смета — с проверяемыми источниками и единой историей проекта.</p></div>
      <div class="metric"><strong>${count}</strong><span>активных проектов</span></div>
    </section>
    ${count ? `<section class="project-grid">${cards}</section>` : `<section class="empty-state"><span class="eyebrow">Первый шаг</span><h2>Создайте первый проект</h2><p>Добавьте страну, регион и цель — остальное рабочее пространство соберётся автоматически.</p><button class="button primary" data-action="new-project">Новый проект</button></section>`}`;
}

async function openProject(id, keepTab = false) {
  setLoading("Открываем проект…");
  state.project = await api(`/api/projects/${id}`);
  if (!keepTab) state.tab = "block1";
  renderProject();
}

function renderProject() {
  const data = state.project;
  if (!data) return;
  const project = data.project;
  const tabs = [
    ["block1", "01 · Юрисдикции"], ["block2", "02 · Сценарии"], ["block3", "03 · Сметы и PDF"],
  ].map(([key, label]) => `<button class="tab ${state.tab === key ? "active" : ""}" data-action="tab" data-tab="${key}">${label}</button>`).join("");
  const content = state.tab === "block1" ? renderBlock1(data) : state.tab === "block2" ? renderBlock2(data) : renderBlock3(data);
  app.innerHTML = `<section class="project-heading">
      <div><button class="back" data-action="home">← Все проекты</button><h1>${escapeHtml(project.name)}</h1>
      <div class="project-meta">${escapeHtml(project.client_name || "Без заказчика")} · обновлён ${escapeHtml(project.updated_at)}</div></div>
      <span class="status">${data.progress.ready} из ${data.progress.total} юрисдикций готовы</span>
    </section><nav class="tabs">${tabs}</nav>${content}`;
}

function renderSources(finding) {
  const sources = finding?.sources || [];
  if (!sources.length) return "";
  return `<ul class="source-list">${sources.map((source) => `<li><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.title)}</a></li>`).join("")}</ul>`;
}

function renderAnalysis(analysis) {
  if (!analysis) return "";
  const findings = [
    ["Земля и нормы", analysis.land_rights], ["Форма компании", analysis.recommended_entity],
    ["Капитал", analysis.capital_requirements], ["Иностранный учредитель", analysis.foreign_company_rules],
  ];
  return `<details class="analysis"><summary>Показать результат и источники</summary><div class="finding-grid">
    ${findings.map(([title, finding]) => `<article class="finding"><small>${title}</small><h3>${escapeHtml(finding?.summary || "Нет вывода")}</h3><p>${escapeHtml(finding?.conclusion || "")}</p>${renderSources(finding)}</article>`).join("")}
  </div></details>`;
}

function renderBlock1(data) {
  const rows = data.rows.map((row, index) => `<article class="panel" data-row-id="${row.id}">
    <div class="row-card"><div class="row-number">${index + 1}</div>
      <label>Страна<select data-field="country"><option value="">Выберите страну</option>${optionList(state.config.countries, row.country)}</select></label>
      <label>Регион / город<input data-field="region" value="${escapeHtml(row.region)}" placeholder="Занзибар"></label>
      <label>Цель проекта<select data-field="goal_code"><option value="">Выберите цель</option>${optionList(state.config.goals, row.goal_code, "code", "label")}</select></label>
      <div class="actions"><button class="button secondary small" data-action="save-row">Сохранить</button><button class="button danger small" data-action="delete-row">Удалить</button></div>
    </div>
    <div class="row-foot"><span class="status ${escapeHtml(row.status)}">${escapeHtml(row.status_label)}</span>${row.map_url ? `<a href="${escapeHtml(row.map_url)}" target="_blank" rel="noopener noreferrer">Открыть карту ↗</a>` : ""}</div>
    ${row.error_message ? `<p class="compliance">${escapeHtml(row.error_message)}</p>` : ""}${renderAnalysis(row.analysis)}
  </article>`).join("");
  return `<section class="section-toolbar"><div><span class="eyebrow">Блок №1</span><h2>Юрисдикции и правовая среда</h2></div>
    <div class="actions"><button class="button ghost" data-action="add-row">+ Строка</button><button class="button secondary" data-action="analyze" data-mode="deep">Глубокая проверка</button><button class="button primary" data-action="analyze" data-mode="standard">Рассчитать</button></div></section>
    <div id="job-banner"></div>${rows}`;
}

function renderBlock2(data) {
  const cards = data.scenarios.map((scenario) => {
    const calc = scenario.calculation;
    const floors = scenario.floors.map((floor) => `<div class="floor-row"><span>Этаж ${floor.floor_number}</span><input type="number" min="0" step="1" data-floor="${floor.floor_number}" value="${floor.area_m2}"></div>`).join("");
    return `<article class="scenario-card" data-scenario-id="${scenario.id}"><div class="card-heading"><div><h3>${escapeHtml(scenario.name)}</h3><p>${escapeHtml(scenario.jurisdiction)}</p></div><button class="button danger small" data-action="delete-scenario">Удалить</button></div>
      <div class="form-grid">
        <label>Название<input data-field="name" value="${escapeHtml(scenario.name)}"></label>
        <label>Исходная земля, м²<input type="number" min="0" data-field="initial_land_m2" value="${scenario.initial_land_m2}"></label>
        <label>Земля на объект, м²<input type="number" min="0" data-field="object_land_m2" value="${scenario.object_land_m2}"></label>
        <label>Пятно здания, м²<input type="number" min="0" data-field="footprint_m2" value="${scenario.footprint_m2}"></label>
        <label>Инфраструктура, %<input type="number" min="0" max="100" data-field="infrastructure_pct" value="${scenario.infrastructure_pct}"></label>
        <label>Прочие потери, %<input type="number" min="0" max="100" data-field="other_losses_pct" value="${scenario.other_losses_pct}"></label>
        <label>Количество этажей<input type="number" min="0" max="50" data-field="floor_count" value="${scenario.floor_count}"></label>
        <label>Средняя площадь лота, м²<input type="number" min="0" data-field="average_unit_m2" value="${scenario.average_unit_m2}"></label>
      </div><div class="floors">${floors || "<span class='progress-label'>Для продажи земли этажи не требуются</span>"}</div>
      <div class="calc-grid"><div class="calc"><strong>${number(calc.usable_land_m2)}</strong><span>полезная земля, м²</span></div><div class="calc"><strong>${number(calc.building_count)}</strong><span>зданий</span></div><div class="calc"><strong>${number(calc.gross_floor_area_m2)}</strong><span>общая площадь, м²</span></div></div>
      <div class="compliance">${escapeHtml(calc.compliance_status)}</div><div class="actions"><button class="button primary" data-action="save-scenario">Пересчитать и сохранить</button></div>
    </article>`;
  }).join("");
  return `<section class="section-toolbar"><div><span class="eyebrow">Блок №2</span><h2>Сценарии застройки</h2></div></section>
    ${cards ? `<section class="scenario-grid">${cards}</section>` : `<section class="empty-state"><h2>Сначала заполните блок №1</h2><p>Для каждой юрисдикции автоматически появится базовый сценарий.</p></section>`}`;
}

function renderBlock3(data) {
  const scenarioNames = new Map(data.scenarios.map((item) => [item.id, item.name]));
  const cards = data.estimates.map((estimate) => `<article class="estimate-card" data-estimate-id="${estimate.id}">
    <div class="card-heading"><div><h3>${escapeHtml(scenarioNames.get(estimate.scenario_id) || `Сценарий ${estimate.scenario_id}`)}</h3><p>Статус: ${escapeHtml(estimate.status)} · материалов ${estimate.material_count}</p></div><span class="status">${escapeHtml(estimate.currency)}</span></div>
    <div class="form-grid"><label>Валюта<input data-field="currency" value="${escapeHtml(estimate.currency)}"></label><label>Стадия<input data-field="estimate_stage" value="${escapeHtml(estimate.estimate_stage)}"></label>
      <label>Параметрическая ставка / м²<input type="number" min="0" data-field="parametric_rate_per_m2" value="${estimate.parametric_rate_per_m2}"></label><label>Срок, дней<input type="number" min="0" data-field="schedule_days" value="${estimate.schedule_days}"></label></div>
    <div class="document-list">${estimate.documents.length ? estimate.documents.map((doc) => `<div class="document"><strong>${escapeHtml(doc.original_name)}</strong><small>${doc.page_count} стр. · ${number(doc.size_bytes / 1024 / 1024, 1)} МБ · ${escapeHtml(doc.analysis_status)}</small></div>`).join("") : `<span class="progress-label">Проектные PDF ещё не загружены</span>`}</div>
    <div class="actions"><button class="button secondary" data-action="save-estimate">Сохранить параметры</button></div>
    <div class="upload"><label>Добавить проектный PDF<input type="file" accept="application/pdf" data-field="document"></label><button class="button primary" data-action="upload-document">Загрузить</button></div>
  </article>`).join("");
  return `<section class="section-toolbar"><div><span class="eyebrow">Блок №3</span><h2>Сметы и проектные документы</h2></div></section>
    ${cards ? `<section class="estimate-grid">${cards}</section>` : `<section class="empty-state"><h2>Нет сценариев для сметы</h2><p>Заполните блоки №1 и №2.</p></section>`}`;
}

async function saveRow(button) {
  const card = button.closest("[data-row-id]");
  const payload = Object.fromEntries([...card.querySelectorAll("[data-field]")].map((input) => [input.dataset.field, input.value]));
  payload.user_note = "";
  await api(`/api/rows/${card.dataset.rowId}`, { method: "PUT", body: JSON.stringify(payload) });
  notify("Строка сохранена");
  await openProject(state.project.project.id, true);
}

async function startAnalysis(mode) {
  const job = await api(`/api/projects/${state.project.project.id}/analyze`, { method: "POST", body: JSON.stringify({ mode, force: mode === "deep" }) });
  notify("Анализ запущен");
  watchJob(job.id);
}

function watchJob(jobId) {
  clearInterval(state.jobTimer);
  const poll = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      const banner = document.querySelector("#job-banner");
      if (banner) banner.innerHTML = `<section class="panel"><span class="spinner"></span>${escapeHtml(job.message)} · готово ${job.completed}, ошибок ${job.failed}</section>`;
      if (!["queued", "running"].includes(job.status)) {
        clearInterval(state.jobTimer);
        notify(job.message, job.status === "error");
        await openProject(job.project_id, true);
      }
    } catch (error) { clearInterval(state.jobTimer); notify(error.message, true); }
  };
  poll();
  state.jobTimer = setInterval(poll, 2500);
}

async function saveScenario(button) {
  const card = button.closest("[data-scenario-id]");
  const payload = {};
  card.querySelectorAll("[data-field]").forEach((input) => {
    payload[input.dataset.field] = input.type === "number" ? Number(input.value || 0) : input.value;
  });
  payload.floors = [...card.querySelectorAll("[data-floor]")].map((input) => ({ floor_number: Number(input.dataset.floor), area_range: "", area_m2: Number(input.value || 0) }));
  await api(`/api/scenarios/${card.dataset.scenarioId}`, { method: "PUT", body: JSON.stringify(payload) });
  notify("Сценарий пересчитан");
  await openProject(state.project.project.id, true);
}

async function saveEstimate(button) {
  const card = button.closest("[data-estimate-id]");
  const payload = {};
  card.querySelectorAll("[data-field]:not([type=file])").forEach((input) => { payload[input.dataset.field] = input.type === "number" ? Number(input.value || 0) : input.value; });
  await api(`/api/estimates/${card.dataset.estimateId}`, { method: "PUT", body: JSON.stringify(payload) });
  notify("Параметры сметы сохранены");
  await openProject(state.project.project.id, true);
}

async function uploadDocument(button) {
  const card = button.closest("[data-estimate-id]");
  const input = card.querySelector("[data-field=document]");
  if (!input.files.length) throw new Error("Выберите PDF-файл");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>Загрузка';
  const body = new FormData(); body.append("upload", input.files[0]);
  await api(`/api/estimates/${card.dataset.estimateId}/documents`, { method: "POST", body });
  notify("PDF загружен");
  await openProject(state.project.project.id, true);
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  try {
    if (action === "home") await loadProjects();
    if (action === "logout") { await api("/api/logout", { method: "POST" }); window.location.assign("/login"); }
    if (action === "new-project") dialog.showModal();
    if (action === "close-dialog") dialog.close();
    if (action === "open-project") await openProject(Number(target.dataset.projectId));
    if (action === "tab") { state.tab = target.dataset.tab; renderProject(); }
    if (action === "add-row") { await api(`/api/projects/${state.project.project.id}/rows`, { method: "POST" }); await openProject(state.project.project.id, true); }
    if (action === "save-row") await saveRow(target);
    if (action === "delete-row") { const card = target.closest("[data-row-id]"); if (confirm("Удалить строку и связанные сценарии?")) { await api(`/api/rows/${card.dataset.rowId}`, { method: "DELETE" }); await openProject(state.project.project.id, true); } }
    if (action === "analyze") await startAnalysis(target.dataset.mode);
    if (action === "save-scenario") await saveScenario(target);
    if (action === "delete-scenario") { const card = target.closest("[data-scenario-id]"); if (confirm("Удалить сценарий?")) { await api(`/api/scenarios/${card.dataset.scenarioId}`, { method: "DELETE" }); await openProject(state.project.project.id, true); } }
    if (action === "save-estimate") await saveEstimate(target);
    if (action === "upload-document") await uploadDocument(target);
  } catch (error) { notify(error.message, true); target.disabled = false; }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = Object.fromEntries(new FormData(form).entries());
    const created = await api("/api/projects", { method: "POST", body: JSON.stringify(payload) });
    dialog.close(); form.reset(); notify("Проект создан"); await openProject(created.project.id);
  } catch (error) { notify(error.message, true); }
});

async function init() {
  try {
    state.config = await api("/api/config");
    connection.textContent = `Сервер · v${state.config.version}`;
    connection.classList.add("online");
    await loadProjects();
  } catch (error) {
    connection.textContent = "Нет соединения";
    app.innerHTML = `<section class="empty-state"><h2>Не удалось открыть приложение</h2><p>${escapeHtml(error.message)}</p></section>`;
  }
}

init();
