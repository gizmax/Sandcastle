/**
 * Anthropic managed-agent SSE event shapes surfaced to the dashboard.
 *
 * These mirror the events emitted by an Anthropic agent session:
 *   - agent.message              assistant text turn
 *   - agent.thinking             reasoning trace
 *   - agent.tool_use             tool invocation with structured input
 *   - agent.tool_result          tool output (success or error)
 *   - agent.thread_message_received   multiagent thread fan-in
 *   - session.status_running     session became active
 *   - session.status_idle        session paused, optionally with a stop reason
 *   - session.error              fatal session error
 */
export type AgentMessageEvent = {
  type: "agent.message";
  threadId?: string;
  text: string;
  ts: number;
};

export type AgentThinkingEvent = {
  type: "agent.thinking";
  threadId?: string;
  text: string;
  ts: number;
};

export type AgentToolUseEvent = {
  type: "agent.tool_use";
  threadId?: string;
  toolName: string;
  input: Record<string, unknown>;
  toolUseId: string;
  ts: number;
};

export type AgentToolResultEvent = {
  type: "agent.tool_result";
  toolUseId: string;
  output: string;
  isError: boolean;
  ts: number;
};

export type AgentThreadMessageEvent = {
  type: "agent.thread_message_received";
  threadId: string;
  fromAgentId: string;
  preview: string;
  ts: number;
};

export type SessionStatusEvent = {
  type: "session.status_running" | "session.status_idle";
  ts: number;
  stopReason?: string;
};

export type SessionErrorEvent = {
  type: "session.error";
  error: string;
  ts: number;
};

export type AgentEvent =
  | AgentMessageEvent
  | AgentThinkingEvent
  | AgentToolUseEvent
  | AgentToolResultEvent
  | AgentThreadMessageEvent
  | SessionStatusEvent
  | SessionErrorEvent;

export type AgentStreamStatus =
  | "idle"
  | "connecting"
  | "running"
  | "error"
  | "unavailable";
