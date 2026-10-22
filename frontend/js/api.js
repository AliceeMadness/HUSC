const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" };

async function request(path, { method = "GET", body } = {}) {
  const options = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    options.headers = JSON_HEADERS;
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Error ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => `${item.loc?.slice(1).join(".") ?? "campo"}: ${item.msg}`)
          .join(" · ");
      }
    } catch {
      /* keep the status-based message */
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  health: () => request("/api/health"),
  degrees: () => request("/api/degrees"),
  year: (yearId) => request(`/api/years/${encodeURIComponent(yearId)}`),
  plan: (subjectIds) =>
    request(`/api/plan?subject_ids=${subjectIds.map(encodeURIComponent).join(",")}`),
  planForYear: (yearId) => request(`/api/plan?year_id=${encodeURIComponent(yearId)}`),
  validate: (payload) => request("/api/schedules/validate", { method: "POST", body: payload }),
  generate: (payload) => request("/api/schedules/generate", { method: "POST", body: payload }),
  optimize: (payload) => request("/api/schedules/optimize", { method: "POST", body: payload }),
  freeSlots: (payload) => request("/api/free-slots", { method: "POST", body: payload }),
  share: (payload) => request("/api/schedules/share", { method: "POST", body: payload }),
  shared: (shareId) => request(`/api/shared/${encodeURIComponent(shareId)}`),
};
