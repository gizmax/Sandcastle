/** Shared InputSchema types used by workflow and template components. */

export interface InputSchemaProperty {
  type: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  format?: string;
  /** For type: "file" - accepted file extensions (e.g. ".pdf,.csv,.xlsx") */
  accept?: string;
}

export interface InputSchema {
  properties: Record<string, InputSchemaProperty>;
  required?: string[];
}
