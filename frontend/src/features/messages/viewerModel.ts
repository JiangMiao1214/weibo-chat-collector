import type { Filters } from '../../shared/contracts';

export type ViewerSelection = {members: number[]; hours: number[]; media: string; hide_noise: boolean; hide_join_notices: boolean};
export type Facets = {members: {id: number; display_name: string; avatar_url: string | null; count: number}[];
  hours: number[]; hidden_noise: number};
export const emptyViewer: ViewerSelection = {members: [], hours: [], media: '', hide_noise: false, hide_join_notices: false};
export function selectionOf(filters: Filters): ViewerSelection {
  return filters.viewer ? {...emptyViewer, ...JSON.parse(filters.viewer)} : emptyViewer;
}
export function hasDateScope(filters: Filters) {
  return !!(filters.dateFrom || filters.dateTo);
}
export function normalizeDateHours(filters: Filters): Filters {
  if (hasDateScope(filters) || !filters.viewer) return filters;
  return {...filters, viewer: JSON.stringify({...selectionOf(filters), hours: []})};
}
export function toggle(values: number[], value: number) {
  return values.includes(value) ? values.filter(item => item !== value) : [...values, value];
}
