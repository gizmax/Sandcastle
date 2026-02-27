import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { X, Wand2, Loader2, CheckCircle, AlertTriangle, Send, ChevronDown, ChevronRight, MessageSquare, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

interface GenerateResult {
  yaml_content: string;
  name: string;
  description?: string;
  steps_count: number;
  validation_errors: string[];
  input_schema: Record<string, unknown> | null;
}

interface GenerateChatModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (template: { name: string; content: string; step_count: number }) => void;
  existingYaml?: string;
}

type ChatMessage =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; yaml?: GenerateResult };

interface ChatResponse {
  mode: "questions" | "yaml";
  message: string;
  yaml_content?: string;
  name?: string;
  description?: string;
  steps_count?: number;
  validation_errors?: string[];
  input_schema?: Record<string, unknown> | null;
}

export function GenerateChatModal({ open, onClose, onSelect, existingYaml }: GenerateChatModalProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [latestResult, setLatestResult] = useState<GenerateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previousYaml, setPreviousYaml] = useState<string | null>(null);
  const [currentYaml, setCurrentYaml] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const prevOpenRef = useRef(false);
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // Reset state when modal opens
  useEffect(() => {
    if (open && !prevOpenRef.current) {
      setMessages([]);
      setInput("");
      setLoading(false);
      setLatestResult(null);
      setError(null);
      setPreviousYaml(existingYaml || null);
      setCurrentYaml(existingYaml || null);
    }
    prevOpenRef.current = open;
  }, [open, existingYaml]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  // Focus input when modal opens
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: ChatMessage = { role: "user", content: text };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setLoading(true);
    setError(null);

    // Build API messages (only role + content for the API)
    const apiMessages = newMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    const res = await api.post<ChatResponse>(
      "/generate/chat",
      {
        messages: apiMessages,
        existing_yaml: currentYaml || null,
      },
      90_000
    );

    setLoading(false);

    if (res.error) {
      setError(res.error.message);
      return;
    }

    if (res.data) {
      const data = res.data;
      if (data.mode === "yaml" && data.yaml_content) {
        const yamlResult: GenerateResult = {
          yaml_content: data.yaml_content,
          name: data.name || "",
          description: data.description || "",
          steps_count: data.steps_count || 0,
          validation_errors: data.validation_errors || [],
          input_schema: data.input_schema || null,
        };
        setLatestResult(yamlResult);
        // Track YAML versions for diff
        setPreviousYaml(currentYaml);
        setCurrentYaml(data.yaml_content);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: data.message, yaml: yamlResult },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: data.message },
        ]);
      }
    }
  }, [input, messages, loading, currentYaml]);

  const handleUse = useCallback(() => {
    if (!latestResult) return;
    onSelect({
      name: latestResult.name,
      content: latestResult.yaml_content,
      step_count: latestResult.steps_count,
    });
    setMessages([]);
    setInput("");
    setLatestResult(null);
    setError(null);
    setPreviousYaml(null);
    setCurrentYaml(null);
  }, [latestResult, onSelect]);

  const handleClose = useCallback(() => {
    setMessages([]);
    setInput("");
    setLatestResult(null);
    setError(null);
    setPreviousYaml(null);
    setCurrentYaml(null);
    onClose();
  }, [onClose]);

  // Send a suggestion chip - insert text and auto-send
  const sendSuggestion = useCallback((text: string) => {
    if (loading) return;
    setInput(text);
    // Use a microtask to allow state to update before sending
    setTimeout(() => {
      const userMsg: ChatMessage = { role: "user", content: text };
      const currentMessages = messagesRef.current;
      const newMessages = [...currentMessages, userMsg];
      setMessages(newMessages);
      setInput("");
      setLoading(true);
      setError(null);

      const apiMessages = newMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      api.post<ChatResponse>(
        "/generate/chat",
        {
          messages: apiMessages,
          existing_yaml: currentYaml || null,
        },
        90_000
      ).then((res) => {
        setLoading(false);
        if (res.error) {
          setError(res.error.message);
          return;
        }
        if (res.data) {
          const data = res.data;
          if (data.mode === "yaml" && data.yaml_content) {
            const yamlResult: GenerateResult = {
              yaml_content: data.yaml_content,
              name: data.name || "",
              description: data.description || "",
              steps_count: data.steps_count || 0,
              validation_errors: data.validation_errors || [],
              input_schema: data.input_schema || null,
            };
            setLatestResult(yamlResult);
            setPreviousYaml(currentYaml);
            setCurrentYaml(data.yaml_content);
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: data.message, yaml: yamlResult },
            ]);
          } else {
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: data.message },
            ]);
          }
        }
      });
    }, 0);
  }, [loading, currentYaml]);

  if (!open) return null;

  const isEditing = !!existingYaml;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/40" onClick={handleClose} />

      {/* Modal */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-2xl rounded-xl border border-border bg-surface shadow-xl flex flex-col max-h-[85vh]">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-6 py-4">
            <div className="flex items-center gap-2.5">
              <MessageSquare className="h-5 w-5 text-accent" />
              <h2 className="text-lg font-semibold text-foreground">
                {isEditing ? "Edit Workflow with AI" : "AI Workflow Assistant"}
              </h2>
            </div>
            <button
              onClick={handleClose}
              className="rounded-lg p-1.5 text-muted hover:text-foreground hover:bg-muted/10 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Messages area */}
          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4 min-h-[200px]">
            {/* Welcome message */}
            {messages.length === 0 && !loading && (
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10">
                  <Wand2 className="h-3.5 w-3.5 text-accent" />
                </div>
                <div className="rounded-lg bg-background/50 border border-border px-4 py-3 text-sm text-foreground/80 leading-relaxed max-w-[85%]">
                  {isEditing ? (
                    <>
                      I can help you modify your workflow. Tell me what you'd like to change - for example:
                      <ul className="mt-2 space-y-1 text-muted text-xs">
                        <li>"Add a notification step at the end"</li>
                        <li>"Change all models to haiku"</li>
                        <li>"Add an approval gate before the final step"</li>
                      </ul>
                    </>
                  ) : (
                    <>
                      Describe the workflow you want to create. I'll ask a few questions to understand your needs, then generate it.
                      <span className="block mt-2 text-xs text-muted">
                        Tip: Say "just generate" to skip questions and get a workflow immediately.
                      </span>
                    </>
                  )}
                </div>
              </div>
            )}

            {/* Chat messages */}
            {messages.map((msg, i) => (
              <div
                key={i}
                className={cn(
                  "flex items-start gap-3",
                  msg.role === "user" && "flex-row-reverse"
                )}
              >
                {/* Avatar */}
                <div
                  className={cn(
                    "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
                    msg.role === "user"
                      ? "bg-accent/15 text-accent"
                      : "bg-accent/10 text-accent"
                  )}
                >
                  {msg.role === "user" ? (
                    <span className="text-xs font-semibold">U</span>
                  ) : (
                    <Wand2 className="h-3.5 w-3.5" />
                  )}
                </div>

                {/* Message bubble */}
                <div
                  className={cn(
                    "rounded-lg px-4 py-3 text-sm leading-relaxed max-w-[85%]",
                    msg.role === "user"
                      ? "bg-accent text-accent-foreground"
                      : "bg-background/50 border border-border text-foreground/80"
                  )}
                >
                  {/* Message text */}
                  <div className="whitespace-pre-wrap">{msg.content}</div>

                  {/* YAML preview (for assistant messages with yaml) */}
                  {msg.role === "assistant" && msg.yaml && (
                    <>
                      <YamlChatPreview
                        result={msg.yaml}
                        previousYaml={previousYaml}
                      />
                      {/* Suggested actions - only on the latest YAML result */}
                      {msg.yaml === latestResult && !loading && (
                        <SuggestedActions
                          yamlContent={msg.yaml.yaml_content}
                          onSend={sendSuggestion}
                        />
                      )}
                    </>
                  )}
                </div>
              </div>
            ))}

            {/* Loading indicator */}
            {loading && (
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/10">
                  <Wand2 className="h-3.5 w-3.5 text-accent" />
                </div>
                <div className="rounded-lg bg-background/50 border border-border px-4 py-3">
                  <div className="flex items-center gap-1.5">
                    <div className="h-1.5 w-1.5 rounded-full bg-accent/40 animate-bounce [animation-delay:0ms]" />
                    <div className="h-1.5 w-1.5 rounded-full bg-accent/40 animate-bounce [animation-delay:150ms]" />
                    <div className="h-1.5 w-1.5 rounded-full bg-accent/40 animate-bounce [animation-delay:300ms]" />
                  </div>
                </div>
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="rounded-lg border border-error/30 bg-error/5 px-4 py-3 text-sm text-error">
                {error}
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Input area */}
          <div className="border-t border-border px-6 py-3">
            <div className="flex gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  isEditing
                    ? "Describe what to change..."
                    : messages.length === 0
                      ? "Describe your workflow..."
                      : "Type a message..."
                }
                rows={1}
                className={cn(
                  "flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground",
                  "placeholder:text-muted/50 resize-none",
                  "focus:border-accent/50 focus:outline-none focus:ring-1 focus:ring-ring/30",
                  "min-h-[38px] max-h-[100px]"
                )}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    sendMessage();
                  }
                }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = "auto";
                  target.style.height = Math.min(target.scrollHeight, 100) + "px";
                }}
              />
              <button
                onClick={sendMessage}
                disabled={loading || !input.trim()}
                className={cn(
                  "flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-lg transition-all",
                  loading || !input.trim()
                    ? "bg-muted/20 text-muted cursor-not-allowed"
                    : "bg-accent text-accent-foreground hover:bg-accent-hover shadow-sm"
                )}
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </button>
            </div>
            <p className="mt-1.5 text-[10px] text-muted/60">
              {navigator.platform.includes("Mac") ? "Cmd" : "Ctrl"}+Enter to send
            </p>
          </div>

          {/* Footer with Use button */}
          {latestResult && (
            <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
              <button
                onClick={handleClose}
                className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUse}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-xs font-medium text-accent-foreground",
                  "hover:bg-accent-hover transition-all shadow-sm"
                )}
              >
                <Wand2 className="h-3.5 w-3.5" />
                Use This Workflow
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/** Compute a simple line-by-line diff between two YAML strings */
function computeLineDiff(
  oldText: string,
  newText: string
): Array<{ type: "added" | "removed" | "unchanged"; line: string }> {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  const oldSet = new Set(oldLines);
  const newSet = new Set(newLines);
  const result: Array<{ type: "added" | "removed" | "unchanged"; line: string }> = [];

  // Build a simple LCS-inspired diff using line matching
  let oi = 0;
  let ni = 0;

  while (oi < oldLines.length && ni < newLines.length) {
    if (oldLines[oi] === newLines[ni]) {
      result.push({ type: "unchanged", line: oldLines[oi] });
      oi++;
      ni++;
    } else if (!newSet.has(oldLines[oi])) {
      // Old line not present in new text - removed
      result.push({ type: "removed", line: oldLines[oi] });
      oi++;
    } else if (!oldSet.has(newLines[ni])) {
      // New line not present in old text - added
      result.push({ type: "added", line: newLines[ni] });
      ni++;
    } else {
      // Both lines exist elsewhere - treat old as removed, new as added
      result.push({ type: "removed", line: oldLines[oi] });
      oi++;
    }
  }

  // Remaining old lines are removed
  while (oi < oldLines.length) {
    result.push({ type: "removed", line: oldLines[oi] });
    oi++;
  }

  // Remaining new lines are added
  while (ni < newLines.length) {
    result.push({ type: "added", line: newLines[ni] });
    ni++;
  }

  return result;
}

/** Visual diff display component */
function YamlDiffPreview({ oldYaml, newYaml }: { oldYaml: string; newYaml: string }) {
  const diffLines = useMemo(() => computeLineDiff(oldYaml, newYaml), [oldYaml, newYaml]);

  return (
    <pre className="max-h-52 overflow-auto p-3 text-[11px] font-mono leading-relaxed">
      {diffLines.map((line, i) => (
        <div
          key={i}
          className={cn(
            "px-1 -mx-1 rounded-sm",
            line.type === "removed" && "bg-red-500/10 text-red-400",
            line.type === "added" && "bg-green-500/10 text-green-400",
            line.type === "unchanged" && "text-foreground/60"
          )}
        >
          <span className="select-none inline-block w-4 text-right mr-2 opacity-50">
            {line.type === "removed" ? "-" : line.type === "added" ? "+" : " "}
          </span>
          {line.line || " "}
        </div>
      ))}
    </pre>
  );
}

/** Collapsible YAML preview inside a chat message with optional diff tab */
function YamlChatPreview({
  result,
  previousYaml,
}: {
  result: GenerateResult;
  previousYaml: string | null;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasDiff = !!previousYaml;
  const [activeTab, setActiveTab] = useState<"yaml" | "diff">("yaml");
  const hasErrors = result.validation_errors.length > 0;

  return (
    <div className="mt-3 space-y-2">
      {/* Status */}
      <div className="flex items-center gap-2 text-xs">
        {hasErrors ? (
          <>
            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
            <span className="text-warning">
              {result.validation_errors.length} issue(s)
            </span>
          </>
        ) : (
          <>
            <CheckCircle className="h-3.5 w-3.5 text-success" />
            <span className="text-success">
              {result.name ? `"${result.name}" - ` : ""}
              {result.steps_count} steps
            </span>
          </>
        )}
      </div>

      {/* Validation errors */}
      {hasErrors && (
        <div className="space-y-0.5">
          {result.validation_errors.map((err, i) => (
            <div key={i} className="text-[11px] text-warning pl-5">
              - {err}
            </div>
          ))}
        </div>
      )}

      {/* Collapsible YAML with optional diff tab */}
      <div className="rounded-lg border border-border bg-black/20 overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
          <div className="flex items-center gap-0">
            {/* Tab buttons */}
            <button
              type="button"
              onClick={() => setActiveTab("yaml")}
              className={cn(
                "px-2 py-0.5 text-xs font-medium rounded-md transition-colors",
                activeTab === "yaml"
                  ? "text-foreground bg-muted/15"
                  : "text-muted hover:text-foreground"
              )}
            >
              YAML
            </button>
            {hasDiff && (
              <button
                type="button"
                onClick={() => setActiveTab("diff")}
                className={cn(
                  "px-2 py-0.5 text-xs font-medium rounded-md transition-colors",
                  activeTab === "diff"
                    ? "text-foreground bg-muted/15"
                    : "text-muted hover:text-foreground"
                )}
              >
                Diff
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
          >
            <span className="text-muted/60">{result.steps_count} steps</span>
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        </div>
        {expanded && (
          activeTab === "diff" && hasDiff ? (
            <YamlDiffPreview oldYaml={previousYaml} newYaml={result.yaml_content} />
          ) : (
            <pre className="max-h-52 overflow-auto p-3 text-[11px] text-foreground/80 font-mono leading-relaxed">
              {result.yaml_content}
            </pre>
          )
        )}
      </div>
    </div>
  );
}

/** Contextual suggestion chips shown after YAML generation */
function SuggestedActions({
  yamlContent,
  onSend,
}: {
  yamlContent: string;
  onSend: (text: string) => void;
}) {
  const suggestions = useMemo(() => {
    const chips: Array<{ label: string; message: string }> = [];
    const lc = yamlContent.toLowerCase();

    if (!lc.includes("type: \"approval\"") && !lc.includes("type: approval") && !lc.includes("type: \"gate\"") && !lc.includes("type: gate")) {
      chips.push({
        label: "Add approval gate",
        message: "Add an approval gate step before the final step so a human can review before execution",
      });
    }

    if (!lc.includes("type: \"notify\"") && !lc.includes("type: notify")) {
      chips.push({
        label: "Add notifications",
        message: "Add a notification step at the end to alert when the workflow completes",
      });
    }

    // Check if all steps use sonnet
    const modelMatches = lc.match(/model:\s*["']?(\w+)/g) || [];
    const allSonnet = modelMatches.length > 0 && modelMatches.every((m) => m.includes("sonnet"));
    if (allSonnet && modelMatches.length > 1) {
      chips.push({
        label: "Optimize models",
        message: "Optimize model usage - use haiku for simpler steps to reduce cost and latency",
      });
    }

    // Check for parallelization opportunity - no shared depends_on
    const dependsMatches = lc.match(/depends_on:/g) || [];
    if (dependsMatches.length <= 1) {
      chips.push({
        label: "Parallelize steps",
        message: "Identify independent steps and run them in parallel using depends_on to speed up execution",
      });
    }

    // Always available suggestions
    chips.push({
      label: "Add error handling",
      message: "Add error handling and retry logic to steps that might fail",
    });

    chips.push({
      label: "Add input validation",
      message: "Add input validation to verify all required inputs before the workflow starts",
    });

    // Return max 4 suggestions
    return chips.slice(0, 4);
  }, [yamlContent]);

  if (suggestions.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {suggestions.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => onSend(s.message)}
          className={cn(
            "inline-flex items-center gap-1 rounded-full",
            "border border-accent/30 text-accent text-xs px-2.5 py-1",
            "hover:bg-accent/10 transition-colors cursor-pointer"
          )}
        >
          <Plus className="h-3 w-3" />
          {s.label}
        </button>
      ))}
    </div>
  );
}
