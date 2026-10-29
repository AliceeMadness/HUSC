/**
 * "Editar matrícula": the dialog where subjects are added to or removed from
 * the plan.  Changes stay in ``state.planDraft`` until "Aplicar".
 */

import { isDoubleDegree, yearSubjectsById } from "./catalog.js";
import { openPlan } from "./plan.js";
import { els, state } from "./state.js";
import { toast } from "./ui.js";
import { escapeHtml, normalizeSearch, plural } from "./utils.js";

export function openPlanDialog() {
  state.planDraft = new Set(state.planSubjectIds);
  els.planSearch.value = "";
  renderPlanOptions();
  els.planDialog.showModal();
  window.setTimeout(() => els.planSearch.focus(), 60);
}

function planSubjectRow(subject, year) {
  return `
    <label class="plan-subject" data-plan-year-scope="${escapeHtml(year.id)}">
      <input type="checkbox" value="${escapeHtml(subject.id)}" data-plan-subject
        data-plan-year-scope="${escapeHtml(year.id)}"
        ${state.planDraft.has(subject.id) ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(subject.name)}</strong>
        <small>${subject.semester ?? 1}C · ${plural(subject.group_count ?? 0, "grupo", "grupos")} · ${plural(subject.session_count ?? 0, "sesión", "sesiones")}</small>
      </span>
    </label>`;
}

function planYearBlock(year, subjects, { title = `${year.number}º · ${year.name}`, toggle = true } = {}) {
  const allSelected = subjects.every((subject) => state.planDraft.has(subject.id));
  return `
    <div class="plan-year">
      <div class="plan-year-head">
        <strong>${escapeHtml(title)}</strong>
        <small>${plural(subjects.length, "materia", "materias")}</small>
        ${toggle ? `<button class="text-btn" type="button" data-plan-year="${escapeHtml(year.id)}">${allSelected ? "Quitar" : "Todo"}</button>` : ""}
      </div>
      <div class="plan-subject-grid">${subjects.map((subject) => planSubjectRow(subject, year)).join("")}</div>
    </div>`;
}

function degreeSection(name, blocks, extraClass = "") {
  return `
    <section class="plan-degree${extraClass}">
      <h3 class="plan-degree-name">${escapeHtml(name)}</h3>
      ${blocks.join("")}
    </section>`;
}

export function renderPlanOptions() {
  const query = normalizeSearch(els.planSearch.value);
  const matches = (subject, degree, year) =>
    !query ||
    normalizeSearch(`${subject.name} ${degree.name} ${degree.short_name} ${year.name} ${year.number}`).includes(query);

  // "Tus asignaturas": the subjects of the plan as it was when the dialog was
  // opened, grouped by the degree and year they come from, so they can be
  // unticked without searching.  Unticking keeps them listed until applied.
  const planIds = new Set(state.planSubjectIds);
  const mineBlocks = [];
  for (const degree of state.degrees) {
    for (const year of degree.years) {
      const subjects = (year.subjects ?? []).filter(
        (subject) => planIds.has(subject.id) && matches(subject, degree, year),
      );
      if (subjects.length) {
        mineBlocks.push(planYearBlock(year, subjects, { title: `${year.number}º · ${degree.name}`, toggle: false }));
      }
    }
  }

  // The catalogue below leaves double degrees out so a search does not list
  // the same subject three times.
  const degreeBlocks = [];
  for (const degree of state.degrees) {
    if (isDoubleDegree(degree)) continue;
    const yearBlocks = [];
    for (const year of degree.years) {
      const subjects = (year.subjects ?? []).filter((subject) => matches(subject, degree, year));
      if (subjects.length) yearBlocks.push(planYearBlock(year, subjects));
    }
    if (yearBlocks.length) degreeBlocks.push(degreeSection(degree.name, yearBlocks));
  }

  const mine = mineBlocks.length ? degreeSection("Tus asignaturas", mineBlocks, " plan-mine") : "";
  els.planOptions.innerHTML =
    mine || degreeBlocks.length
      ? mine + degreeBlocks.join("")
      : '<p class="plan-empty">No hay asignaturas que coincidan con la búsqueda.</p>';
  updatePlanCount();
  refreshPlanYearButtons();
}

function updatePlanCount() {
  els.planCount.textContent = plural(state.planDraft.size, "materia", "materias");
}

function refreshPlanYearButtons() {
  for (const button of els.planOptions.querySelectorAll("[data-plan-year]")) {
    const ids = yearSubjectsById(button.dataset.planYear).map((subject) => subject.id);
    const allSelected = ids.length > 0 && ids.every((id) => state.planDraft.has(id));
    button.textContent = allSelected ? "Quitar" : "Todo";
  }
}

function clearDraft() {
  state.planDraft.clear();
  for (const input of els.planOptions.querySelectorAll("[data-plan-subject]")) input.checked = false;
  updatePlanCount();
  refreshPlanYearButtons();
}

function toggleDraftSubject(input) {
  if (input.checked) state.planDraft.add(input.value);
  else state.planDraft.delete(input.value);
  // A subject of the plan is listed twice: in "Tu matrícula" and in its degree.
  for (const twin of els.planOptions.querySelectorAll("[data-plan-subject]")) {
    if (twin.value === input.value) twin.checked = input.checked;
  }
  updatePlanCount();
  refreshPlanYearButtons();
}

// "Todo" / "Quitar" of a year.
function toggleDraftYear(yearId) {
  const ids = yearSubjectsById(yearId).map((subject) => subject.id);
  const allSelected = ids.length > 0 && ids.every((id) => state.planDraft.has(id));
  for (const id of ids) {
    if (allSelected) state.planDraft.delete(id);
    else state.planDraft.add(id);
  }
  const scrollTop = els.planOptions.scrollTop;
  renderPlanOptions();
  els.planOptions.scrollTop = scrollTop;
}

async function applyPlanDraft() {
  const ids = [...state.planDraft];
  if (!ids.length) {
    toast("Selecciona al menos una materia.", { type: "warn" });
    return;
  }
  els.planDialog.close();
  try {
    await openPlan(ids, { preserveSelection: true });
    toast("Matrícula actualizada.");
  } catch (error) {
    toast(`No se pudo actualizar la matrícula: ${error.message}`, { type: "error" });
  }
}

export function bindPlanDialog() {
  els.planSearch.addEventListener("input", renderPlanOptions);
  els.planClear.addEventListener("click", clearDraft);
  els.planOptions.addEventListener("change", (event) => {
    const input = event.target.closest("[data-plan-subject]");
    if (input) toggleDraftSubject(input);
  });
  els.planOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-plan-year]");
    if (button) toggleDraftYear(button.dataset.planYear);
  });
  els.planApply.addEventListener("click", applyPlanDraft);
}
