/**
 * Lookups in the degree directory (``/api/degrees``) kept in ``state.degrees``.
 */

import { state } from "./state.js";

export function findYear(yearId) {
  for (const degree of state.degrees) {
    const year = degree.years.find((item) => item.id === yearId);
    if (year) return { degree, year };
  }
  return null;
}

export function yearSubjectsById(yearId) {
  return findYear(yearId)?.year.subjects ?? [];
}

export function yearSubjectIds(yearId) {
  const found = findYear(yearId);
  return found?.year.subjects?.map((subject) => subject.id) ?? null;
}

// Every double-degree subject is also offered, with the same groups and
// hours, by one of the single degrees.
export function isDoubleDegree(degree) {
  return /^doble grado/i.test(degree.name);
}
