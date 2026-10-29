/**
 * The HUSC landing: degree search, hero button and catalogue figures.
 */

import { showWorkspaceControls } from "./header.js";
import { els, state } from "./state.js";
import { readSaved } from "./storage.js";
import { escapeHtml, normalizeSearch, plural } from "./utils.js";

export function updateHeroCounts() {
  const counts = state.meta?.counts;
  const meta = els.landing?.querySelector(".hero-meta");
  if (!counts || !meta) return;
  meta.innerHTML = `
    <span><strong>${counts.degrees}</strong> grados</span>
    <span><strong>${counts.years}</strong> cursos</span>
    <span><strong>${counts.subjects}</strong> asignaturas</span>`;
}

// Every typed word must start a word of the degree, so "ia" finds
// Inteligencia Artificial ("IA") but not "IngenierÍA".
function matchingDegrees(rawQuery) {
  const terms = normalizeSearch(rawQuery).split(/[^a-z0-9]+/).filter(Boolean);
  return state.degrees.filter((degree) => {
    const words = normalizeSearch(`${degree.name} ${degree.short_name}`).split(/[^a-z0-9]+/);
    return terms.every((term) => words.some((word) => word.startsWith(term)));
  });
}

function degreeCard(degree) {
  const subjects = degree.years.reduce((sum, year) => sum + year.subject_count, 0);
  const years = degree.years
    .map(
      (year) => `
      <button class="year-btn" type="button" data-year="${escapeHtml(year.id)}">
        <span class="year-num">${year.number}º</span>
        <span class="year-label">${escapeHtml(year.name)}</span>
        <span class="year-count">${plural(year.subject_count, "asignatura", "asignaturas")}</span>
        <span class="year-arrow" aria-hidden="true">↗</span>
      </button>`,
    )
    .join("");
  return `
    <article class="degree-card">
      <h3 class="degree-name">${escapeHtml(degree.name)}</h3>
      <p class="degree-meta">${plural(degree.years.length, "curso", "cursos")} · ${plural(subjects, "asignatura", "asignaturas")}</p>
      <div class="year-grid">${years}</div>
    </article>`;
}

export function renderLanding() {
  const rawQuery = els.degreeSearch.value.trim();
  const degrees = matchingDegrees(rawQuery);
  els.degreeGrid.innerHTML = degrees.length
    ? degrees.map(degreeCard).join("")
    : `<p class="degree-empty">Ningún grado coincide con <strong>«${escapeHtml(rawQuery)}»</strong>.
       Prueba con otra palabra o usa «Nueva matrícula» para buscar asignaturas sueltas.</p>`;
}

export function renderFatal(error) {
  els.landing.classList.remove("hidden");
  els.workspace.classList.add("hidden");
  showWorkspaceControls(false);
  els.degreeGrid.innerHTML = `
    <article class="degree-card">
      <h3 class="degree-name">No se pudo cargar el catálogo</h3>
      <p class="degree-meta" style="margin-top:10px">${escapeHtml(error?.message ?? "Error desconocido")}</p>
      <p class="degree-meta" style="margin-top:6px">Comprueba que el backend esté arrancado y que <code>data/catalog.json</code> exista.</p>
    </article>`;
}

export function showLanding() {
  renderConfigureButton();
  els.landing.classList.remove("hidden");
  els.workspace.classList.add("hidden");
  showWorkspaceControls(false);
  const url = new URL(window.location.href);
  url.search = "";
  window.history.replaceState({}, "", url);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function hasPlan() {
  return Boolean(state.planSubjectIds.length || readSaved());
}

// The hero button goes back to the timetable when there is one, or else
// starts a plan in "Editar matrícula".
export function renderConfigureButton() {
  els.configurePlanLabel.textContent = hasPlan() ? "Ir a mi horario" : "Configurar mi horario";
}
