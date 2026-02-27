#!/usr/bin/env python3
"""Add example values to input_schema properties in all YAML templates.

Uses heuristic rules based on field name, type, and description to generate
realistic example values. Safe to run multiple times - skips fields that
already have an example.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path("src/sandcastle/templates")

# Heuristic example values keyed by field name pattern (regex)
NAME_EXAMPLES: list[tuple[str, str | int | float]] = [
    # URLs
    (r"(landing_page|brand|website|site)_url$", "https://acme.com/product"),
    (r"url$", "https://example.com"),
    # Paths / directories
    (r"directory$", "~/Documents/reports"),
    (r"path$", "./data/input"),
    # Addresses
    (r"property_address$", "742 Evergreen Terrace, Austin, TX 78701"),
    (r"address$", "123 Main St, San Francisco, CA 94102"),
    # Pricing / money
    (r"listing_price$", 485000),
    (r"salary_range$", "$150K-$180K + equity"),
    (r"approval_threshold$", "10000"),
    (r"(price|cost|budget|amount)$", "5000"),
    # Time / duration
    (r"lookback_hours$", 24),
    (r"(timeout|duration|hours|minutes|seconds)$", 60),
    (r"forecast_horizon$", "next quarter"),
    # Channels (Slack)
    (r"(standup|summary|slack)_channel$", "#daily-standup"),
    (r"channel$", "#general"),
    # Languages
    (r"(target|source)_language$", "Spanish"),
    (r"language$", "English"),
    # Tone / voice / style
    (r"(brand_voice|tone|writing_style)$", "professional, clear, and approachable"),
    (r"documentation_standard$", "SOAP"),
    (r"output_format$", "executive-brief"),
    # Severity / priority
    (r"severity$", "P2"),
    (r"priority$", "high"),
    # Domain / specialty / category
    (r"specialty$", "general"),
    (r"domain$", "general"),
    (r"accounting_system$", "QuickBooks"),
    # People / team
    (r"team_members$", "Alice Chen, Bob Smith, Carol Davis"),
    (r"(target_buyer|buyer)_profile$", "Young professionals, ages 28-40, dual income, first-time homebuyers"),
    # Location
    (r"location$", "Remote (US)"),
    (r"geography$", "North America"),
    (r"region$", "US West"),
    # Industry / market
    (r"target_industry$", "FinTech"),
    (r"(product_)?category$", "project management tools"),
    # Roles
    (r"role_title$", "Senior Backend Engineer"),
    (r"department$", "Engineering"),
    # Service / system names
    (r"(service_name|service)$", "payments-api"),
    (r"alert_source$", "datadog"),
    (r"(repo|repository)_url$", "https://github.com/acme/platform"),
    (r"(repo|repository)$", "github.com/acme/platform"),
    # Email fields
    (r"manager_email$", "jane.manager@acme.com"),
    (r"(employee|buddy|user|admin)_email$", "john.doe@acme.com"),
    (r"email$", "user@example.com"),
    # Date fields
    (r"start_date$", "2026-03-15"),
    (r"end_date$", "2026-06-30"),
    (r"(date|deadline)$", "2026-04-01"),
    # Name fields
    (r"employee_name$", "John Doe"),
    (r"(client|customer|patient|user)_name$", "Jane Smith"),
    (r"^name$", "Project Alpha"),
    # Level / seniority
    (r"seniority_level$", "senior"),
    (r"^level$", "mid"),
    # Common short names
    (r"^topic$", "How to scale your engineering team from 10 to 50"),
    (r"^text$", "The quick brown fox jumps over the lazy dog. This is a sample text for processing."),
    (r"^query$", "What are the top 3 emerging trends in AI?"),
    (r"^keywords?$", "automation, productivity, developer tools"),
    (r"^sprint_goal$", "Ship v2.0 checkout flow by end of sprint"),
    # Names
    (r"company_name$", "Acme Corp"),
    (r"product_name$", "Sandcastle AI"),
    (r"(competitor_names?|competitors)$", "Zapier, Make, n8n"),
    # Content fields (longer text)
    (r"product_brief$", "Sandcastle AI - workflow orchestrator for dev teams. Target: CTOs at Series A-C startups. Key differentiators: pluggable sandboxes, community hub, 5-min setup."),
    (r"product_description$", "AI-powered workflow orchestrator that lets teams build, test, and deploy multi-step AI pipelines with pluggable sandbox backends"),
    (r"encounter_data$", "45yo male, presenting with chest pain onset 2 hours ago, history of hypertension, currently on lisinopril 10mg"),
    (r"patient_context$", "62yo female, type 2 diabetes (A1C 7.2), on metformin 1000mg BID, allergic to penicillin, presenting for routine follow-up"),
    (r"(invoice_source)$", "AP email inbox - batch of 15 vendor invoices from last week"),
    (r"requirements$", "5+ years Python/Go, distributed systems experience, strong communication skills"),
    (r"(key_features|features)$", "4 bed / 3 bath, 2,400 sqft, renovated kitchen, hardwood floors, 2-car garage"),
    (r"(focus_areas)$", "financial performance, risk factors, growth strategy"),
    (r"(segments?)$", "Enterprise, Mid-Market, SMB"),
    (r"(revenue_data)$", "Monthly ARR by segment from Jan 2024 to present, exported from Stripe"),
    (r"(property_type)$", "residential"),
    (r"target_audience$", "SaaS founders and engineering leaders"),
    # Generic content / description patterns
    (r"(brief|overview|summary|context|details|notes|info)$", "Provide relevant context and details for analysis"),
    (r"(description|desc)$", "A detailed description of the subject for analysis"),
    # Role field
    (r"^role$", "Senior Frontend Engineer"),
]

# Fallback examples by type
TYPE_FALLBACKS: dict[str, str | int | float] = {
    "string": "example input text",
    "number": 100,
    "integer": 10,
    "boolean": True,
}


def get_example_for_field(
    name: str,
    field_type: str,
    description: str,
    default: str | None,
) -> str | int | float:
    """Generate an example value for a field using heuristics."""
    # Try name-based patterns first
    for pattern, example in NAME_EXAMPLES:
        if re.search(pattern, name, re.IGNORECASE):
            return example

    # Try to extract example from description (e.g. "e.g. 'HealthTech', 'FinTech'")
    eg_match = re.search(r"e\.g\.?\s*['\"]([^'\"]+)['\"]", description)
    if eg_match:
        return eg_match.group(1)

    # Use default if available and meaningful
    if default and default not in ("", "general"):
        return default

    # Fallback by type
    return TYPE_FALLBACKS.get(field_type, "sample input")


def add_examples_to_yaml(path: Path) -> int:
    """Add example values to input_schema properties in a YAML file.

    Returns the number of examples added.
    """
    text = path.read_text()

    # Check if file has input_schema
    if "input_schema:" not in text:
        return 0

    lines = text.split("\n")
    new_lines: list[str] = []
    added = 0

    i = 0
    in_properties = False
    current_field: str | None = None
    current_indent = 0
    field_indent = 0
    field_type = "string"
    field_desc = ""
    field_default: str | None = None
    has_example = False

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Detect "properties:" under input_schema
        if stripped == "properties:" and in_properties is False:
            # Check if we're within input_schema context
            # Look back for input_schema
            for prev in new_lines[-5:]:
                if "input_schema:" in prev:
                    in_properties = True
                    current_indent = len(line) - len(stripped)
                    break

        if in_properties:
            indent = len(line) - len(stripped) if stripped else 999

            # End of properties block - back to lower indent or EOF
            if stripped and indent <= current_indent and stripped != "properties:":
                # Flush pending field - insert before trailing blank lines
                if current_field and not has_example:
                    example = get_example_for_field(
                        current_field, field_type, field_desc, field_default,
                    )
                    ex_line = " " * field_indent + "  example: "
                    if isinstance(example, (int, float)):
                        ex_line += str(example)
                    else:
                        ex_line += f'"{example}"'
                    # Insert before trailing blank lines
                    insert_pos = len(new_lines)
                    while insert_pos > 0 and new_lines[insert_pos - 1].strip() == "":
                        insert_pos -= 1
                    new_lines.insert(insert_pos, ex_line)
                    added += 1
                in_properties = False
                current_field = None

            # Detect a field name (direct child of properties)
            elif stripped and indent == current_indent + 2 and stripped.endswith(":"):
                # Flush previous field - insert before trailing blank lines
                if current_field and not has_example:
                    example = get_example_for_field(
                        current_field, field_type, field_desc, field_default,
                    )
                    ex_line = " " * field_indent + "  example: "
                    if isinstance(example, (int, float)):
                        ex_line += str(example)
                    else:
                        ex_line += f'"{example}"'
                    insert_pos = len(new_lines)
                    while insert_pos > 0 and new_lines[insert_pos - 1].strip() == "":
                        insert_pos -= 1
                    new_lines.insert(insert_pos, ex_line)
                    added += 1

                current_field = stripped.rstrip(":")
                field_indent = indent
                field_type = "string"
                field_desc = ""
                field_default = None
                has_example = False

            # Detect field attributes
            elif current_field and stripped:
                if stripped.startswith("type:"):
                    field_type = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("description:"):
                    field_desc = stripped.split(":", 1)[1].strip().strip('"\'')
                elif stripped.startswith("default:"):
                    field_default = stripped.split(":", 1)[1].strip().strip('"\'')
                elif stripped.startswith("example:"):
                    has_example = True

        new_lines.append(line)
        i += 1

    # Flush last field if still pending
    if in_properties and current_field and not has_example:
        example = get_example_for_field(
            current_field, field_type, field_desc, field_default,
        )
        ex_line = " " * field_indent + "  example: "
        if isinstance(example, (int, float)):
            ex_line += str(example)
        else:
            ex_line += f'"{example}"'
        new_lines.append(ex_line)
        added += 1

    if added > 0:
        path.write_text("\n".join(new_lines))

    return added


def main() -> None:
    templates = sorted(TEMPLATES_DIR.glob("*.yaml"))
    total_added = 0
    total_files = 0

    for path in templates:
        added = add_examples_to_yaml(path)
        if added > 0:
            total_files += 1
            total_added += added
            print(f"  + {path.name}: {added} examples added")
        else:
            print(f"  . {path.name}: skipped (no input_schema or already has examples)")

    print(f"\nDone. Added {total_added} examples across {total_files} files.")


if __name__ == "__main__":
    main()
