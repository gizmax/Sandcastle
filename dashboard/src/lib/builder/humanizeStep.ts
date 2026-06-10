/**
 * humanizeStep.ts — turn a step config into one plain-English sentence.
 *
 * `humanizeStep` reads the parts of a step that matter for a human-readable
 * description and produces a single sentence, e.g.
 *
 *   standard { id: "summary", prompt: "summarize {input.text}" }
 *     → 'Asks the agent to "summarize {input.text}" and saves the result as "summary".'
 *
 * Unknown or under-specified steps fall back to a generic sentence so the
 * function never throws and always returns something useful. Pure, no React.
 */

import type { StepLike } from "@/lib/builder/stepValidation";

/** Collapse whitespace and trim a free-text field for inline display. */
function clean(text: string | undefined, max = 120): string {
  if (!text) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** ` and saves the result as "id".` suffix, when the step has an id. */
function savedAs(id: string | undefined): string {
  return id ? ` and saves the result as "${id}"` : "";
}

/**
 * humanizeStep — one-sentence plain-English description of a step.
 *
 * @param step a full StepConfig or any partial/legacy shape.
 * @returns a single sentence ending in a period.
 */
export function humanizeStep(step: StepLike): string {
  const id = typeof step?.id === "string" ? step.id : undefined;
  const type = (step?.stepType as string) ?? "standard";

  switch (type) {
    case "standard":
    case "managed-agent": {
      const prompt = clean(step.prompt);
      if (prompt) {
        return `Asks the agent to "${prompt}"${savedAs(id)}.`;
      }
      return `Runs an agent step${savedAs(id)}.`;
    }

    case "llm": {
      const prompt = clean(step.prompt);
      if (prompt) {
        return `Sends the prompt "${prompt}" to the model${savedAs(id)}.`;
      }
      return `Makes a single LLM call${savedAs(id)}.`;
    }

    case "http": {
      const url = clean(step.httpConfig?.url, 80);
      return url
        ? `Calls ${url}${savedAs(id)}.`
        : `Makes an HTTP request${savedAs(id)}.`;
    }

    case "code":
      return `Runs Python code${savedAs(id)}.`;

    case "condition": {
      const expr = clean(step.conditionConfig?.expression, 80);
      return expr
        ? `Branches on whether ${expr}.`
        : `Branches the workflow on a condition.`;
    }

    case "classify": {
      const cats = step.classifyConfig?.categories;
      if (cats && cats.length > 0) {
        return `Classifies the input into ${cats
          .map((c) => `"${c}"`)
          .join(", ")} and routes accordingly.`;
      }
      return `Classifies the input and routes to a branch.`;
    }

    case "loop": {
      const over = clean(step.loopConfig?.over, 60);
      return over
        ? `Loops over ${over}, running its steps for each item.`
        : `Loops over a list, running its steps for each item.`;
    }

    case "race":
      return `Runs branches in parallel and takes the first valid result${savedAs(
        id,
      )}.`;

    case "gate": {
      const strat = step.gateConfig?.strategies?.[0]?.type;
      return strat
        ? `Waits for approval via the "${strat}" strategy before continuing.`
        : `Waits for approval before continuing.`;
    }

    case "approval":
      return `Pauses for human approval before continuing.`;

    case "sensor": {
      const url = clean(step.sensorConfig?.url, 60);
      return url
        ? `Polls ${url} until its condition is met.`
        : `Polls a URL until a condition is met.`;
    }

    case "transform":
      return `Transforms data with a template${savedAs(id)}.`;

    case "notify": {
      const svc = step.notifyConfig?.service;
      return svc
        ? `Sends a notification via ${svc}.`
        : `Sends a notification.`;
    }

    case "delegate":
    case "sub_workflow": {
      const wf = clean(step.delegateConfig?.workflow, 60);
      return wf
        ? `Delegates to the "${wf}" workflow${savedAs(id)}.`
        : `Delegates to another workflow${savedAs(id)}.`;
    }

    case "browser": {
      const url = clean(step.browserConfig?.startUrl, 60);
      return url
        ? `Automates a browser starting at ${url}.`
        : `Automates a browser session.`;
    }

    case "parse":
      return `Extracts text from a document${savedAs(id)}.`;

    case "report":
      return `Compiles the results into a report${savedAs(id)}.`;

    case "agent":
      return `Runs an agent${savedAs(id)}.`;

    default:
      return `Runs a ${type} step${id ? ` called "${id}"` : ""}.`;
  }
}
