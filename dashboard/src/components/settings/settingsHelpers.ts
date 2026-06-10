import type { EditableFields } from "./settingsContext";

/** Return only keys whose values differ between two objects. */
export function diffFields(
  current: Partial<EditableFields>,
  original: Partial<EditableFields>,
): Partial<EditableFields> {
  const changed: Record<string, unknown> = {};
  for (const key of Object.keys(current) as (keyof EditableFields)[]) {
    if (current[key] !== original[key]) {
      changed[key] = current[key];
    }
  }
  return changed as Partial<EditableFields>;
}

export interface BackendOption {
  id: string;
  label: string;
  desc: string;
  envHint: string;
}

export const LOG_LEVELS = ["debug", "info", "warning", "error"] as const;
