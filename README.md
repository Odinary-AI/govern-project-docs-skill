# govern-project-docs Skill

An Agent Skill for keeping project documentation aligned with development facts, plans, decisions, evidence, release claims, and historical material.

`govern-project-docs` helps an AI agent run a small documentation-governance loop around real development work: identify affected document authority, check impact before work, review semantic drift, and close out with evidence. It does not impose fixed filenames or directory layouts. Projects provide an adapter that maps their own documents to semantic roles.

## What is included

- Canonical Agent Skill: `skills/govern-project-docs/`
- Codex plugin wrapper: `plugins/govern-project-docs/`
- Adapter and result contract: `skills/govern-project-docs/references/adapter-schema.md`
- Deterministic helper script: `skills/govern-project-docs/scripts/govern_project_docs.py`
- Claude Code copy instructions: `docs/claude/README.md`

## What it does

- Resolves document authority by governed question and scope, not by filename.
- Runs Impact before work starts or when task meaning changes.
- Runs Closeout before declaring a task, batch, validation change, or release-stage transition complete.
- Separates mechanical checks, AI semantic review, and human approval boundaries.
- Supports Git, filesystem receipts, supplied inventories, and explicit path fallback.
- Keeps adapters as pointers and rules, not copied project facts.

## What it does not do

- It does not decide product meaning, architecture meaning, formal release/version claims, or irreversible archival choices.
- It does not make generated findings into project authority.
- It does not require projects to use fixed status, task-list, changelog, or document-map filenames.
- It does not modify protected, excluded, or non-document project files as part of documentation governance.

## Install in Codex

Copy the canonical skill into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills/govern-project-docs
cp -R skills/govern-project-docs/. ~/.codex/skills/govern-project-docs/
```

Or install the local Codex plugin wrapper if your Codex setup supports local plugin manifests.

## Use

In a project task, tell the agent:

```text
Enable documentation governance. I am starting <task>.
```

Before finishing:

```text
Enable documentation governance. Close out this task.
```

The agent should locate or create a project adapter, run Impact, check actual changed paths when possible, run semantic review when the task changes claims or decisions, and run Closeout before claiming completion.

## Adapter

A project adapter is JSON. It declares authority rules, entrypoints, boundaries, and human approval categories. It should store only pointers and rules, not project facts.

Read the full contract in:

```text
skills/govern-project-docs/references/adapter-schema.md
```

## Validation

Validate the skill:

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/govern-project-docs
```

Run a simple adapter check:

```bash
python3 skills/govern-project-docs/scripts/govern_project_docs.py validate-adapter path/to/adapter.json
```

## Author

Odinary-AI

## License

MIT
