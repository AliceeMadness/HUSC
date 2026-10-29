/**
 * "Resolver solapes", its green summary box with "Revertir cambios", and
 * pinning groups so the repair leaves them alone.
 */

import { api } from "./api.js";
import { setSemester } from "./header.js";
import { updatePlanUrl } from "./plan.js";
import { renderAlerts, renderEverything, renderSchedule, renderStatus, renderSubjects } from "./render.js";
import { prunePins, selectedGroupIds, semesterSubjectIds, setGroupSelection } from "./selection.js";
import { state } from "./state.js";
import { saveLast } from "./storage.js";
import { toast } from "./ui.js";
import { escapeHtml, plural } from "./utils.js";
import { onSelectionChanged, scheduleValidation } from "./validation.js";

/* --------------------------------------------------------------------------
   Pinned groups
   -------------------------------------------------------------------------- */

// Clicking a class fixes its group: "Resolver solapes" will not change it.
export function togglePin(groupId) {
  if (!groupId) return;
  const group = state.groupIndex.get(groupId);
  const name = group ? `${group.code}` : "Grupo";
  if (state.pinnedGroupIds.has(groupId)) {
    state.pinnedGroupIds.delete(groupId);
    toast(`${name} desfijado.`);
  } else {
    state.pinnedGroupIds.add(groupId);
    toast(`${name} fijado: «Resolver solapes» no lo cambiará.`);
  }
  prunePins();
  renderSubjects();
  renderSchedule();
  renderStatus();
  saveLast();
}

/* --------------------------------------------------------------------------
   Pieces of the alerts
   -------------------------------------------------------------------------- */

// The button inside the red overlap alert.
export function resolveButton() {
  return `
    <button class="btn alert-action" type="button" data-action="resolve" ${state.resolving ? "disabled" : ""}>
      ${state.resolving ? '<span class="spinner" aria-hidden="true"></span> Buscando el arreglo más pequeño…' : "✦ Resolver solapes"}
    </button>
    <p class="alert-note">Cambia los menos grupos posibles entre los que ya has elegido. Los grupos fijados (⚑, clic en la clase) no se tocan.</p>`;
}

// The green box left after "Resolver solapes": what changed, a button to undo
// it and a cross to hide the box.
export function resolvedAlert() {
  const last = state.lastResolve;
  if (!last || last.semester !== state.semester) return "";
  const parts = [];
  if (last.changed) parts.push(plural(last.changed, "grupo cambiado", "grupos cambiados"));
  if (last.removed) parts.push(plural(last.removed, "grupo quitado", "grupos quitados"));
  const summary = parts.length ? parts.join(" y ") : "Tu horario ha cambiado";
  return `
    <div class="alert alert-success alert-resolved" role="status">
      <span class="alert-icon">✦</span>
      <div class="alert-body"><strong>Solapes resueltos.</strong> ${escapeHtml(summary)}${last.detail ? `: ${escapeHtml(last.detail)}` : ""}.
        <div><button class="btn alert-action" type="button" data-action="revert">↶ Revertir cambios</button></div>
      </div>
      <button class="alert-close" type="button" data-action="dismiss-resolve" aria-label="Cerrar aviso" title="Cerrar">
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
      </button>
    </div>`;
}

/* --------------------------------------------------------------------------
   Repair, revert, dismiss
   -------------------------------------------------------------------------- */

// "Revertir cambios": put back the groups chosen before "Resolver solapes".
export function revertResolve() {
  const last = state.lastResolve;
  if (!last) return;
  state.selected = last.selected;
  state.autoResult = last.autoResult;
  if (state.semester !== last.semester) setSemester(last.semester, { announce: false });
  onSelectionChanged();
  updatePlanUrl();
  toast("Cambios revertidos: vuelves a tener los grupos de antes.");
}

export function dismissResolve() {
  state.lastResolve = null;
  renderAlerts();
}

function describeChanges(changes) {
  const moved = changes.filter((change) => change.from_group_id && change.to_group_id);
  if (!moved.length) return "";
  const sample = moved
    .slice(0, 3)
    .map((change) => `${change.subject_name} (${change.from_group_code} → ${change.to_group_code})`)
    .join(", ");
  return moved.length > 3 ? `${sample} y ${moved.length - 3} más` : sample;
}

// Replace the groups of the visible term by the repaired ones; the other
// term keeps its groups.
function applyRepair(groupIds) {
  state.autoResult = null;
  for (const subjectId of semesterSubjectIds()) delete state.selected[subjectId];
  for (const groupId of groupIds) setGroupSelection(groupId);
  prunePins();
  state.shareId = null;
  state.validation = null;
  updatePlanUrl();
  renderEverything();
  saveLast();
  scheduleValidation();
}

// A full repair is explained by the green box; a partial one also by a toast.
function reportRepair(result, changes, changed, removed) {
  if (result.status === "partial") {
    const dropped = changes
      .filter((change) => change.from_group_id && !change.to_group_id)
      .map((change) => `${change.subject_name} (${change.type_label})`)
      .join(", ");
    toast(
      `No caben todos los grupos sin solapes: se han quitado ${plural(removed, "grupo", "grupos")} (${dropped})` +
        (changed ? ` y cambiado ${plural(changed, "grupo", "grupos")}.` : "."),
      { type: "warn", duration: 8000 },
    );
  } else if (result.status !== "optimized") {
    toast("No se pudo quitar los solapes sin mover los grupos fijados.", { type: "error" });
  }
}

export async function optimizeTimetable() {
  if (!state.planSubjectIds.length) return;
  // Only the groups already chosen are repaired; nothing new is added.
  if (!selectedGroupIds().length) {
    toast("Elige algún grupo antes de resolver solapes.", { type: "warn" });
    return;
  }
  if (state.resolving) return;
  state.resolving = true;
  renderAlerts();
  try {
    const termIds = new Set(selectedGroupIds());
    const result = await api.optimize({
      subject_ids: semesterSubjectIds(),
      group_ids: [...termIds],
      custom_events: state.customEvents,
      pinned_group_ids: [...state.pinnedGroupIds].filter((groupId) => termIds.has(groupId)),
    });
    if (result.status === "unchanged") {
      toast("Tu horario no tiene solapes: no hay nada que cambiar.");
      return;
    }
    const before = { selected: structuredClone(state.selected), autoResult: state.autoResult, semester: state.semester };
    applyRepair(result.group_ids || []);

    const changes = result.changes || [];
    const changed = result.solver?.changed ?? 0;
    const removed = result.solver?.removed ?? 0;
    if (result.status === "optimized" || result.status === "partial") {
      state.lastResolve = { ...before, status: result.status, changed, removed, detail: describeChanges(changes) };
    }
    reportRepair(result, changes, changed, removed);
  } catch (error) {
    toast(`No se pudo resolver los solapes: ${error.message}`, { type: "error" });
  } finally {
    state.resolving = false;
    renderAlerts();
  }
}
