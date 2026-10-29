/**
 * Client of the FastAPI backend.  Failed requests throw an ``Error`` whose
 * message is the server's ``detail`` (in Spanish) and whose ``status`` is the
 * HTTP status.
 */

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function errorMessage(response) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      return payload.detail.map((item) => `${item.loc?.slice(1).join(".") ?? "campo"}: ${item.msg}`).join(" · ");
    }
  } catch {
    /* keep the status-based message */
  }
  return `Error ${response.status}`;
}

// ``as: "blob"`` returns the body as a file (the .ics calendar).
async function request(path, { method = "GET", body, as = "json" } = {}) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers = JSON_HEADERS;
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const error = new Error(await errorMessage(response));
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return as === "blob" ? response.blob() : response.json();
}

const post = (path, payload, extra = {}) => request(path, { method: "POST", body: payload, ...extra });

export const api = {
  health: () => request("/api/health"),
  degrees: () => request("/api/degrees"),
  year: (yearId) => request(`/api/years/${encodeURIComponent(yearId)}`),
  plan: (subjectIds) => request(`/api/plan?subject_ids=${subjectIds.map(encodeURIComponent).join(",")}`),
  planForYear: (yearId) => request(`/api/plan?year_id=${encodeURIComponent(yearId)}`),
  validate: (payload) => post("/api/schedules/validate", payload),
  generate: (payload) => post("/api/schedules/generate", payload),
  optimize: (payload) => post("/api/schedules/optimize", payload),
  freeSlots: (payload) => post("/api/free-slots", payload),
  share: (payload) => post("/api/schedules/share", payload),
  shared: (shareId) => request(`/api/shared/${encodeURIComponent(shareId)}`),
  ics: (payload) => post("/api/schedules/export.ics", payload, { as: "blob" }),
};
