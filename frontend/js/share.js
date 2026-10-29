/**
 * Shared links ("Compartir") and exports (JSON, .ics calendar).
 */

import { api } from "./api.js";
import { yearSubjectIds } from "./catalog.js";
import { SEMESTER_NAMES } from "./constants.js";
import { showLanding } from "./landing.js";
import { timetablePdf } from "./pdf-export.js";
import { openPlan, planChipLabel, planLabel } from "./plan.js";
import { renderEverything, weekEvents } from "./render.js";
import { allSelectedGroupIds, selectedGroupIds } from "./selection.js";
import { els, state } from "./state.js";
import { downloadBlob, toast } from "./ui.js";
import { capitalize, slugify } from "./utils.js";

const shareUrl = (shareId) => {
  const url = new URL(window.location.href);
  url.search = `?s=${encodeURIComponent(shareId)}`;
  return url;
};

// Subjects of a shared timetable; old links only stored a year or groups.
function sharedSubjectIds(share) {
  if (share.subject_ids?.length) return share.subject_ids;
  if (share.year_id) return yearSubjectIds(share.year_id) ?? [];
  return [...new Set((share.selections || []).map((selection) => selection.subject_id))];
}

export async function loadShared(shareId) {
  try {
    const { share } = await api.shared(shareId);
    const subjectIds = sharedSubjectIds(share);
    if (!subjectIds.length) throw new Error("El enlace no contiene una matrícula válida");
    await openPlan(subjectIds, {
      groupIds: share.group_ids,
      customEvents: share.custom_events ?? [],
      pinnedGroupIds: share.pinned_group_ids ?? [],
    });
    state.shareId = share.id;
    renderEverything();
    window.history.replaceState({}, "", shareUrl(share.id));
    toast("Horario compartido cargado.");
  } catch (error) {
    toast(`No se pudo abrir el horario compartido: ${error.message}`, { type: "error", duration: 6000 });
    showLanding();
  }
}

export async function shareSchedule() {
  const ids = allSelectedGroupIds();
  if (!ids.length) {
    toast("Elige algún grupo antes de compartir.", { type: "warn" });
    return;
  }
  els.shareBtn.disabled = true;
  try {
    const response = await api.share({
      subject_ids: state.planSubjectIds,
      title: planLabel(),
      group_ids: ids,
      custom_events: state.customEvents,
      pinned_group_ids: [...state.pinnedGroupIds],
    });
    els.shareUrl.value = shareUrl(response.share.id).toString();
    els.shareNote.textContent = `Copia guardada con id ${response.share.id}. Cualquiera con el enlace verá esta combinación.`;
    els.shareDialog.showModal();
    setTimeout(() => els.shareUrl.select(), 80);
  } catch (error) {
    toast(`No se pudo compartir: ${error.message}`, { type: "error" });
  } finally {
    els.shareBtn.disabled = false;
  }
}

const exportName = (extension) => `horario-${slugify(planChipLabel() || "usc")}.${extension}`;

function selectionExport(subject) {
  const selection = state.selected[subject.id] || {};
  return {
    subject_id: subject.id,
    subject_name: subject.name,
    semester: subject.semester ?? 1,
    groups: Object.entries(selection).flatMap(([type, groupIds]) =>
      groupIds.map((groupId) => {
        const group = state.groupIndex.get(groupId);
        return {
          group_id: groupId,
          code: group?.code,
          type,
          type_label: group?.type_label,
          sessions: group?.sessions.map((session) => ({
            id: session.id,
            day: session.day,
            start: session.start,
            end: session.end,
            room_id: session.room_id,
            room_name: session.room_name,
            building_id: session.building_id,
            building_name: session.building_name,
          })),
        };
      }),
    ),
  };
}

export function downloadJson() {
  const data = state.yearData;
  const payload = {
    schema_version: "2.0",
    exported_at: new Date().toISOString(),
    term: data?.term,
    plan: {
      label: planLabel(),
      subject_ids: state.planSubjectIds,
      years: data?.years,
      degrees: data?.degrees,
    },
    degree: data?.degrees?.length === 1 ? data.degrees[0] : null,
    year: data?.years?.length === 1 ? data.years[0] : null,
    group_ids: allSelectedGroupIds(),
    pinned_group_ids: [...state.pinnedGroupIds],
    custom_events: state.customEvents,
    selections: state.subjects.map(selectionExport).filter((subject) => subject.groups.length),
    stats: state.validation?.stats ?? null,
  };
  downloadBlob(JSON.stringify(payload, null, 2), exportName("json"), "application/json");
  toast("JSON descargado.");
}

// The week on screen as a PDF, built in the browser (no print dialog).
export function downloadPdf() {
  const { sessions } = state.localSchedule;
  const week = els.weekPicker.classList.contains("hidden")
    ? ""
    : (els.weekSelect.selectedOptions[0]?.textContent ?? "").replace("(esta semana)", "").trim();
  const subtitle = [planLabel(), capitalize(SEMESTER_NAMES[state.semester] ?? ""), week].filter(Boolean).join(" · ");
  try {
    const blob = timetablePdf({ title: "Mi horario", subtitle, sessions, events: weekEvents(), term: state.term });
    downloadBlob(blob, exportName("pdf"), "application/pdf");
    toast("PDF descargado.");
  } catch (error) {
    toast(`No se pudo crear el PDF: ${error.message}`, { type: "error" });
  }
}

// The .ics covers the term on screen; the server builds it.
export async function downloadIcs() {
  const ids = selectedGroupIds();
  if (!ids.length) return;
  try {
    const blob = await api.ics({
      group_ids: ids,
      name: `Horario ${planChipLabel() || "USC"}`,
      custom_events: state.customEvents,
    });
    downloadBlob(blob, exportName("ics"), "text/calendar");
    toast("Calendario .ics descargado.");
  } catch (error) {
    toast(`No se pudo exportar el calendario: ${error.message}`, { type: "error" });
  }
}
