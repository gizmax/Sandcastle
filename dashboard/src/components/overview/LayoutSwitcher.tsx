import { cn } from "@/lib/utils";

interface LayoutSwitcherProps {
  layout: string;
  setLayout: (l: string) => void;
}

export function LayoutSwitcher({ layout, setLayout }: LayoutSwitcherProps) {
  const options = [
    { id: "bento", title: "Bento Grid", icon: (
      <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
        <rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor" />
        <rect x="8" y="1" width="5" height="5" rx="1" fill="currentColor" opacity="0.5" />
        <rect x="1" y="8" width="5" height="5" rx="1" fill="currentColor" opacity="0.5" />
        <rect x="8" y="8" width="5" height="5" rx="1" fill="currentColor" opacity="0.5" />
      </svg>
    )},
    { id: "focus", title: "Focus Mode", icon: (
      <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
        <rect x="2" y="3" width="10" height="1.5" rx="0.75" fill="currentColor" />
        <rect x="4" y="6.25" width="6" height="1.5" rx="0.75" fill="currentColor" opacity="0.7" />
        <rect x="5" y="9.5" width="4" height="1.5" rx="0.75" fill="currentColor" opacity="0.4" />
      </svg>
    )},
    { id: "default", title: "Classic", icon: (
      <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
        <rect x="1" y="1" width="12" height="3" rx="1" fill="currentColor" />
        <rect x="1" y="5.5" width="12" height="3" rx="1" fill="currentColor" opacity="0.6" />
        <rect x="1" y="10" width="12" height="3" rx="1" fill="currentColor" opacity="0.35" />
      </svg>
    )},
  ];

  return (
    <div className="flex items-center gap-0.5 rounded-xl border border-border bg-surface shadow-sm p-0.5">
      {options.map((opt) => (
        <button
          key={opt.id}
          title={opt.title}
          onClick={() => {
            setLayout(opt.id);
            localStorage.setItem("sandcastle_overview_layout", opt.id);
          }}
          className={cn(
            "flex items-center justify-center rounded-lg w-7 h-7 transition-settle",
            layout === opt.id
              ? "bg-accent text-accent-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {opt.icon}
        </button>
      ))}
    </div>
  );
}
