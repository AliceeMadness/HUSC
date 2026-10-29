/**
 * The single mutable state of the app and the DOM elements it renders into.
 *
 * Every module reads and writes ``state`` directly; after changing the
 * selection call ``onSelectionChanged()`` (validation.js) or
 * ``renderEverything()`` (render.js) and ``saveLast()`` (storage.js).
 */

export const state = {
  meta: null,
  degrees: [],
  term: null,
  yearData: null,
  yearId: null,
  planSubjectIds: [],
  planDraft: new Set(),
  customEvents: [],
  pinnedGroupIds: new Set(),
  editingEventId: null,
  shareId: null,
  subjects: [],
  groupIndex: new Map(),
  sessionById: new Map(),
  // { subjectId: { type: [groupId, …] } }, in the order groups were picked.
  selected: {},
  semester: 1,
  // ISO week on screen (``2025-W43``).  While ``weekAuto`` is true it follows
  // the device date; picking a week in the menu turns it off.
  weekId: null,
  weekAuto: true,
  localSchedule: { sessions: [], conflicts: [], eventConflicts: [] },
  // Last server validation; only its stats are used (JSON export).
  validation: null,
  // Result of the old automatic generator, still read from saved plans.
  autoResult: null,
  // "Resolver solapes" is running (its button lives in the overlap alert).
  resolving: false,
  // The timetable before the last "Resolver solapes", for "Revertir cambios".
  // ``null`` once reverted, dismissed or after any manual change.
  lastResolve: null,
  validationTimer: null,
  validationToken: 0,
};

const ELEMENT_IDS = {
  landing: "landing",
  workspace: "workspace",
  degreeGrid: "degree-grid",
  yearChip: "year-chip",
  themeToggle: "theme-toggle",
  semesterToggle: "semester-toggle",
  semesterLabel: "semester-label",
  shareBtn: "share-btn",
  brandLink: "brand-link",
  changeYear: "change-year",
  customPlan: "custom-plan",
  configurePlan: "configure-plan",
  configurePlanLabel: "configure-plan-label",
  degreeSearch: "degree-search",
  editPlan: "edit-plan",
  addEventBtn: "add-event-btn",
  weekPicker: "week-picker",
  weekSelect: "week-select",
  eventList: "event-list",
  eventDialog: "event-dialog",
  eventDialogTitle: "event-dialog-title",
  eventForm: "event-form",
  eventTitle: "event-title",
  eventDay: "event-day",
  eventStart: "event-start",
  eventEnd: "event-end",
  eventLocation: "event-location",
  eventDelete: "event-delete",
  eventWeeksField: "event-weeks-field",
  eventWeeks: "event-weeks",
  yearTitle: "year-title",
  upcoming: "upcoming",
  yearSubtitle: "year-subtitle",
  subjects: "subjects",
  selectionCount: "selection-count",
  conflictPill: "conflict-pill",
  hoursStat: "hours-stat",
  daysStat: "days-stat",
  alerts: "alerts",
  timetable: "timetable",
  exportBtn: "export-btn",
  exportJson: "export-json",
  exportIcs: "export-ics",
  exportPdf: "export-pdf",
  printBtn: "print-btn",
  shareDialog: "share-dialog",
  shareUrl: "share-url",
  shareNote: "share-note",
  copyShare: "copy-share",
  exportDialog: "export-dialog",
  planDialog: "plan-dialog",
  planSearch: "plan-search",
  planOptions: "plan-options",
  planCount: "plan-count",
  planClear: "plan-clear",
  planApply: "plan-apply",
  toast: "toast",
};

// Filled once by ``collectElements()``; the object itself never changes, so
// modules can import it before the DOM is ready.
export const els = {};

export function collectElements() {
  for (const [key, id] of Object.entries(ELEMENT_IDS)) {
    els[key] = document.getElementById(id);
  }
}
