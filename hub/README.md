# Sandcastle Community Hub

The Community Hub is the open registry of workflow templates for [Sandcastle](https://gizmax.cz/sandcastle) - the workflow orchestrator for autonomous agents.

Browse, discover, and share reusable workflows at **[gizmax.cz/sandcastle/hub](https://gizmax.cz/sandcastle/hub)**.

## What is this?

This directory contains:

- **registry.json** - The master index of all available templates (built-in and community-contributed)
- **schema/** - JSON Schema for validating template metadata
- **community/** - Community-submitted workflow templates (via pull requests)

## How to submit a template

1. **Fork** this repository
2. Create a new YAML workflow file in `community/` with a unique slug name (e.g. `community/my-awesome-workflow.yaml`)
3. Include the required comment headers at the top of your YAML file:
   ```yaml
   # name: My Awesome Workflow
   # description: A short description of what this workflow does (max 500 chars)
   # tags: [Category1, Category2]
   # category: general_ai
   ```
4. Make sure your workflow follows the Sandcastle template format (see existing templates in `src/sandcastle/templates/` for reference)
5. Open a **Pull Request** with your template

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
| support         | Support          |
| engineering    | Engineering      |
| hr_legal       | HR & Legal       |
| general_ai     | General AI       |

### Metadata schema

See [schema/template.schema.json](schema/template.schema.json) for the full JSON Schema used to validate template entries in the registry.

## Registry format

The `registry.json` file is the single source of truth for the hub frontend. It includes:

- All template entries with metadata, model/tool references, and download URLs
- Category counts
- Aggregate statistics

Built-in templates are seeded from `src/sandcastle/templates/`. Community templates are added via PR review.

## License

All built-in templates are MIT licensed. Community submissions must specify a license in their metadata (defaults to MIT).

---

Created by Tomas Pflanzer [@gizmax](https://github.com/gizmax)
