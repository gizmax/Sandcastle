import { useState, useCallback, useRef, useEffect } from "react";
import { X, Wand2, Loader2, CheckCircle, AlertTriangle, Send, ChevronDown, ChevronRight, MessageSquare } from "lucide-react";
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
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const prevOpenRef = useRef(false);

  // Reset state when modal opens
  useEffect(() => {
    if (open && !prevOpenRef.current) {
      setMessages([]);
      setInput("");
      setLoading(false);
      setLatestResult(null);
      setError(null);
    }
    prevOpenRef.current = open;
  }, [open]);

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
        existing_yaml: existingYaml || null,
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
  }, [input, messages, loading, existingYaml]);

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
  }, [latestResult, onSelect]);

  const handleClose = useCallback(() => {
    setMessages([]);
    setInput("");
    setLatestResult(null);
    setError(null);
    onClose();
  }, [onClose]);

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
                    <YamlChatPreview result={msg.yaml} />
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

/** Collapsible YAML preview inside a chat message */
function YamlChatPreview({ result }: { result: GenerateResult }) {
  const [expanded, setExpanded] = useState(true);
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

      {/* Collapsible YAML */}
      <div className="rounded-lg border border-border bg-black/20 overflow-hidden">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted hover:text-foreground transition-colors"
        >
          <span className="font-medium">Generated YAML</span>
          <span className="flex items-center gap-1.5">
            <span className="text-muted/60">{result.steps_count} steps</span>
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </span>
        </button>
        {expanded && (
          <pre className="max-h-52 overflow-auto p-3 text-[11px] text-foreground/80 font-mono leading-relaxed">
            {result.yaml_content}
          </pre>
        )}
      </div>
    </div>
  );
}
