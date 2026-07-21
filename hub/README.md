# Sandcastle Community Hub

The Community Hub is the open registry of workflow templates for [Sandcastle](https://sandcastle-ai.eu/hub) - the workflow orchestrator for autonomous agents.

Browse, discover, and share reusable workflows at **[sandcastle-ai.eu/hub](https://sandcastle-ai.eu/hub)**.

## What is this?

This directory contains:

- **registry.json** - The master index of all available templates, collections, and community metadata
- **schema/** - JSON Schema for validating template metadata
- **community/** - Community-submitted workflow templates (via pull requests)

## Features

### Curated Collections
Pre-built workflow stacks for common use cases - install an entire pipeline with one command:
- **Complete Sales Stack** - Lead gen, scoring, CRM enrichment, pipeline management
- **Content Machine** - SEO research, writing, social distribution
- **DevOps Essentials** - Sprint tracking, standups, releases, documentation
- **Support Suite** - Ticket triage, SLA monitoring, FAQ generation
- **HR Toolkit** - Hiring, onboarding, compliance, contracts

### Cost Calculator
Every workflow shows an estimated cost per run based on models used:
- Token-based pricing per step (haiku ~$0.0004, sonnet ~$0.004, opus ~$0.02)
- Transparent cost breakdown in detail view

### Remix Chain
Fork any workflow to create your own variant. The hub tracks lineage:
- See which workflows were remixed from others
- Track how many remixes a popular template has generated
- Discover community improvements on built-in templates

### Live Playground
Try any workflow in the browser before installing - fill sample inputs and see a simulated preview of the execution flow.

## How to submit a template

1. **Fork** this repository
2. Create a new YAML workflow file in `community/<your-username>/` (e.g. `community/johndoe/my-workflow.yaml`)
3. Include the required comment headers at the top of your YAML file:
   ```yaml
   # name: My Awesome Workflow
   # description: A short description of what this workflow does (max 500 chars)
   # tags: [Category1, Category2]
   # category: general_ai
   ```
4. Make sure your workflow follows the Sandcastle template format (see existing templates in `src/sandcastle/templates/` for reference)
5. Open a **Pull Request** with your template

### Remixing an existing workflow

To submit a remix (fork) of an existing workflow:
1. Follow the steps above
2. Add a `# forked_from: original-author/template-slug` comment header
3. Describe what you changed/improved in the PR description

## Template format requirements

Every template YAML file must include:

- **Comment headers**: `# name:`, `# description:`, `# tags:`, `# category:`
- **name**: Unique workflow name (kebab-case)
- **description**: What the workflow does
- **input_schema**: With `required` and `properties` for dashboard form rendering
- **steps**: At least one step with `id` and `prompt`

### Valid categories

| Category ID    | Label            |
|----------------|------------------|
| sales_crm      | Sales & CRM      |
| marketing      | Marketing        |
| support        | Support          |
| engineering    | Engineering      |
| hr_legal       | HR & Legal       |
| general_ai     | General AI       |

### Metadata schema

See [schema/template.schema.json](schema/template.schema.json) for the full JSON Schema used to validate template entries in the registry.

## CLI commands

```bash
# Browse
sandcastle hub list                              # List all workflows
sandcastle hub search "lead scoring"             # Search by keyword
sandcastle hub collections                       # View curated collections

# Install
sandcastle hub install gizmax/lead-scoring       # Install a single workflow
sandcastle hub install-collection sales-automation-stack  # Install entire collection

# Share
sandcastle hub publish my-workflow.yaml          # Validate and start PR flow
```

## Registry format

The `registry.json` file is the single source of truth for the hub frontend. It includes:

- All template entries with metadata, cost estimates, download counts, and remix lineage
- Curated collections with template references
- Category counts and aggregate statistics

Built-in templates are seeded from `src/sandcastle/templates/`. Community templates are added via PR review.

## License

All built-in templates are MIT licensed. Community submissions must specify a license in their metadata (defaults to MIT).

---

Created by Tomas Pflanzer [@gizmax](https://github.com/gizmax)
