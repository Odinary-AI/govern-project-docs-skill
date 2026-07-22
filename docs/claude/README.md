# Claude Code Installation Notes

This repository contains a canonical Agent Skill in `skills/govern-project-docs/`.

## User-scoped install

Copy the skill folder into your Claude Code user skills directory if your setup supports user-scoped skills:

```bash
mkdir -p ~/.claude/skills/govern-project-docs
cp -R skills/govern-project-docs/. ~/.claude/skills/govern-project-docs/
```

## Project-scoped install

Copy the skill folder into a project-level skills directory used by your local Claude Code setup:

```bash
mkdir -p .claude/skills/govern-project-docs
cp -R skills/govern-project-docs/. .claude/skills/govern-project-docs/
```

## Usage prompt

Use a short trigger phrase in development tasks:

```text
Enable documentation governance. I am starting <task>.
```

Before finishing:

```text
Enable documentation governance. Close out this task.
```

The agent should run Impact before work and Closeout before claiming completion.
