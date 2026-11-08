/**
 * In-memory client-side catalog store.
 * Replaces backend/store.py completely in static hosting environments like GitHub Pages.
 */

const DAY_ORDER = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4 };
const DAY_LABELS = { mon: "Lunes", tue: "Martes", wed: "Miércoles", thu: "Jueves", fri: "Viernes" };
const GROUP_TYPE_LABELS = {
  expositive: "Expositiva",
  laboratory: "Laboratorio",
  seminar: "Seminario",
  other: "Otro",
};

let catalog = null;
let campusById = new Map();
let buildingById = new Map();
let roomById = new Map();
let degreeById = new Map();
let yearById = new Map();
let subjectById = new Map();
let groupById = new Map();
let sessionsByGroup = new Map();
let loadPromise = null;

function initCatalog(data) {
  catalog = data;
  campusById = new Map(catalog.campuses.map((item) => [item.id, item]));
  buildingById = new Map(catalog.buildings.map((item) => [item.id, item]));
  roomById = new Map(catalog.rooms.map((item) => [item.id, item]));
  degreeById = new Map(catalog.degrees.map((item) => [item.id, item]));
  yearById = new Map(catalog.years.map((item) => [item.id, item]));
  subjectById = new Map(catalog.subjects.map((item) => [item.id, item]));
  groupById = new Map(catalog.groups.map((item) => [item.id, item]));

  sessionsByGroup = new Map(catalog.groups.map((group) => [group.id, []]));
  for (const session of catalog.class_sessions) {
    const list = sessionsByGroup.get(session.group_id);
    if (list) list.push(session);
  }
  for (const sessions of sessionsByGroup.values()) {
    sessions.sort((a, b) => (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || a.start.localeCompare(b.start));
  }
}

export async function ensureCatalog() {
  if (catalog) return catalog;
  if (!loadPromise) {
    loadPromise = (async () => {
      // Support Node.js testing environment
      if (typeof process !== "undefined" && process.versions?.node) {
        try {
          const fs = await import("fs/promises");
          const path = await import("path");
          const { fileURLToPath } = await import("url");
          const currentDir = path.dirname(fileURLToPath(import.meta.url));
          const fileCandidates = [
            path.resolve(currentDir, "../data/catalog.json"),
            path.resolve(currentDir, "../../data/catalog.json"),
            path.resolve(process.cwd(), "data/catalog.json"),
          ];
          for (const filePath of fileCandidates) {
            try {
              const raw = await fs.readFile(filePath, "utf-8");
              initCatalog(JSON.parse(raw));
              return catalog;
            } catch {
              // try next
            }
          }
        } catch {
          // fallback to web fetch
        }
      }

      // Browser environment (relative to module or base)
      const candidates = [
        new URL("../data/catalog.json", import.meta.url).href,
        new URL("../../data/catalog.json", import.meta.url).href,
      ];
      let lastErr = null;
      for (const url of candidates) {
        try {
          const res = await fetch(url);
          if (res.ok) {
            const data = await res.json();
            initCatalog(data);
            return catalog;
          }
        } catch (err) {
          lastErr = err;
        }
      }
      throw new Error(`No se pudo cargar el catálogo de horarios: ${lastErr?.message || "404"}`);
    })();
  }
  return loadPromise;
}

export function sessionView(session) {
  const room = roomById.get(session.room_id) || { name: session.room_id, kind: "room" };
  const building = buildingById.get(session.building_id) || { name: session.building_id, short_name: session.building_id };
  const subject = subjectById.get(session.subject_id) || { name: session.subject_id, slug: session.subject_id };
  const group = groupById.get(session.group_id) || { code: session.group_id, type: "other" };
  const campus = building.campus_id ? campusById.get(building.campus_id) : null;

  return {
    id: session.id,
    subject_id: session.subject_id,
    subject_name: subject.name,
    subject_slug: subject.slug,
    group_id: session.group_id,
    group_code: group.code,
    group_type: group.type,
    group_type_label: GROUP_TYPE_LABELS[group.type] || group.type,
    day: session.day,
    day_label: DAY_LABELS[session.day] || session.day,
    semester: session.semester,
    start: session.start,
    end: session.end,
    duration_minutes: session.duration_minutes,
    room_id: session.room_id,
    room_name: room.name,
    room_kind: room.kind,
    building_id: session.building_id,
    building_name: building.name,
    building_short_name: building.short_name || building.name,
    campus_name: campus ? campus.name : null,
    instructors: session.instructors ? [...session.instructors] : [],
    weeks: session.weeks ? [...session.weeks] : null,
    note: session.note || null,
    legacy_slot: session.legacy?.slot ?? null,
  };
}

export function groupView(group) {
  const sessions = sessionsByGroup.get(group.id) || [];
  return {
    id: group.id,
    subject_id: group.subject_id,
    code: group.code,
    type: group.type,
    type_label: GROUP_TYPE_LABELS[group.type] || group.type,
    session_count: sessions.length,
    weekly_minutes: sessions.reduce((sum, s) => sum + s.duration_minutes, 0),
    sessions: sessions.map(sessionView),
  };
}

export function subjectView(subject) {
  const groups = (subject.group_ids || []).map((id) => groupById.get(id)).filter(Boolean);
  return {
    id: subject.id,
    year_id: subject.year_id,
    name: subject.name,
    slug: subject.slug,
    semester: subject.semester,
    groups: groups.map(groupView),
  };
}

export function buildingsView() {
  return catalog.buildings.map((b) => ({
    ...b,
    campus_name: campusById.get(b.campus_id)?.name ?? null,
  }));
}

export function yearSummary(year) {
  const degree = degreeById.get(year.degree_id) || { name: year.degree_id, short_name: year.degree_id };
  return {
    id: year.id,
    degree_id: year.degree_id,
    degree_name: degree.name,
    degree_short_name: degree.short_name,
    number: year.number,
    name: year.name,
    subject_count: year.subject_ids.length,
  };
}

export async function getHealth() {
  await ensureCatalog();
  return {
    status: "ok",
    version: "2.0.0-pages",
    schema_version: catalog.schema_version,
    generated_at: catalog.generated_at,
    counts: {
      degrees: catalog.degrees.length,
      years: catalog.years.length,
      subjects: catalog.subjects.length,
      groups: catalog.groups.length,
      class_sessions: catalog.class_sessions.length,
      rooms: catalog.rooms.length,
    },
  };
}

export async function getDegreesResponse() {
  await ensureCatalog();
  return {
    term: { ...catalog.term },
    degrees: await getDegreeSummaries(),
  };
}

export async function getDegreeSummaries() {
  await ensureCatalog();
  return catalog.degrees.map((degree) => {
    const years = catalog.years.filter((y) => y.degree_id === degree.id);
    return {
      ...degree,
      years: years.map((year) => {
        const subjects = year.subject_ids
          .map((id) => subjectById.get(id))
          .filter(Boolean)
          .map((sub) => {
            const groupCount = sub.group_ids.length;
            const sessionCount = sub.group_ids.reduce((sum, gid) => sum + (sessionsByGroup.get(gid)?.length || 0), 0);
            return {
              id: sub.id,
              name: sub.name,
              slug: sub.slug,
              semester: sub.semester,
              group_count: groupCount,
              session_count: sessionCount,
            };
          });
        const yearSessionCount = subjects.reduce((sum, s) => sum + s.session_count, 0);
        return {
          ...yearSummary(year),
          subjects,
          session_count: yearSessionCount,
        };
      }),
    };
  });
}

export async function getPlanView(subjectIds) {
  await ensureCatalog();
  const orderedIds = [...new Set(subjectIds)];
  if (!orderedIds.length) throw new Error("La matrícula no puede estar vacía");
  const unknown = orderedIds.filter((id) => !subjectById.has(id));
  if (unknown.length) throw new Error(`Asignaturas desconocidas: ${unknown.join(", ")}`);

  const orderMap = new Map(catalog.subjects.map((sub, idx) => [sub.id, idx]));
  orderedIds.sort((a, b) => (orderMap.get(a) ?? 0) - (orderMap.get(b) ?? 0));

  const subjects = [];
  const yearIds = [];
  const degreeIds = [];
  for (const subjectId of orderedIds) {
    const subject = subjectById.get(subjectId);
    const year = yearById.get(subject.year_id);
    const degree = degreeById.get(year.degree_id);
    const view = subjectView(subject);
    view.year = yearSummary(year);
    view.degree = { ...degree };
    view.origin_label = `${degree.short_name} · ${year.number}º`;
    subjects.push(view);
    if (!yearIds.includes(year.id)) yearIds.push(year.id);
    if (!degreeIds.includes(degree.id)) degreeIds.push(degree.id);
  }

  return {
    term: { ...catalog.term },
    subject_ids: orderedIds,
    subjects,
    years: yearIds.map((id) => yearSummary(yearById.get(id))),
    degrees: degreeIds.map((id) => ({ ...degreeById.get(id) })),
    buildings: buildingsView(),
  };
}

export async function getYearView(yearId) {
  await ensureCatalog();
  const year = yearById.get(yearId);
  if (!year) throw new Error(`Curso no encontrado: ${yearId}`);
  return getPlanView(year.subject_ids);
}

export function getRawCatalog() {
  return catalog;
}

export function getGroupById(groupId) {
  return groupById.get(groupId);
}

export function getSubjectById(subjectId) {
  return subjectById.get(subjectId);
}

export function getSessionsByGroupId(groupId) {
  return sessionsByGroup.get(groupId) || [];
}
