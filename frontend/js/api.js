/**
 * In-memory client-side provider replacing the FastAPI backend.
 * Provides full feature parity for static hosting (e.g. GitHub Pages).
 */

import {
  ensureCatalog,
  getDegreeSummaries,
  getDegreesResponse,
  getHealth,
  getPlanView,
  getRawCatalog,
  getSessionsByGroupId,
  getYearView,
  sessionView,
} from "./client-store.js";
import { buildCalendarIcs } from "./ics-export.js";
import { optimizeSchedule } from "./repair.js";
import { mergeLocalSessions } from "./schedule.js";

export const api = {
  health: async () => getHealth(),

  degrees: async () => getDegreesResponse(),

  year: async (yearId) => getYearView(yearId),

  plan: async (subjectIds) => getPlanView(subjectIds),

  planForYear: async (yearId) => getYearView(yearId),

  validate: async (payload) => {
    // Client already calculates overlaps locally via buildLocalSchedule()
    return {
      status: "ok",
      is_valid: true,
      conflicts: [],
      stats: {
        total_sessions: payload.group_ids?.length ?? 0,
      },
    };
  },

  generate: async () => {
    throw new Error("Generación automática no disponible en modo estático");
  },

  optimize: async (payload) => {
    await ensureCatalog();
    return optimizeSchedule({
      groupIds: payload.group_ids || [],
      subjectIds: payload.subject_ids || [],
      customEvents: payload.custom_events || [],
      pinnedGroupIds: payload.pinned_group_ids || [],
    });
  },

  freeSlots: async () => [],

  share: async () => {
    throw new Error("El guardado en servidor no está disponible en GitHub Pages; usa enlaces URL");
  },

  shared: async () => {
    throw new Error("Los enlaces de id no están disponibles; usa enlaces URL directos");
  },

  ics: async (payload) => {
    await ensureCatalog();
    const catalog = getRawCatalog();
    const term = catalog?.term;
    const groupIds = payload.group_ids || [];

    // Collect and merge raw sessions for all chosen groups across the term
    const rawSessions = [];
    for (const gid of groupIds) {
      const sessions = getSessionsByGroupId(gid);
      for (const s of sessions) {
        rawSessions.push(sessionView(s));
      }
    }
    const blocks = mergeLocalSessions(rawSessions);

    const icsContent = buildCalendarIcs({
      blocks,
      customEvents: payload.custom_events || [],
      term,
      name: payload.name || "Horario USC",
    });

    return new Blob([icsContent], { type: "text/calendar;charset=utf-8" });
  },
};
