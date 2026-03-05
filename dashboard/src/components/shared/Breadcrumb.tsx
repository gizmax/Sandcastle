import { Link } from "react-router-dom";
import { ChevronRight, LayoutDashboard } from "lucide-react";

interface BreadcrumbItem {
  label: string;
  href?: string;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
}

export function Breadcrumb({ items }: BreadcrumbProps) {
  return (
    <nav className="flex items-center gap-1.5 text-sm">
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        const isFirst = i === 0;
        const showHomeIcon = isFirst && item.label === "Overview";

        return (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && (
              <ChevronRight className="h-3 w-3 text-muted/50" />
            )}
            {isLast ? (
              <span className="font-medium text-foreground">
                {showHomeIcon && <LayoutDashboard className="mr-1 inline h-3.5 w-3.5" />}
                {item.label}
              </span>
            ) : item.href ? (
              <Link
                to={item.href}
                className="text-muted transition-colors hover:text-accent"
              >
                {showHomeIcon && <LayoutDashboard className="mr-1 inline h-3.5 w-3.5" />}
                {item.label}
              </Link>
            ) : (
              <span className="text-muted">
                {showHomeIcon && <LayoutDashboard className="mr-1 inline h-3.5 w-3.5" />}
                {item.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
