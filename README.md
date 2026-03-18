# skills
Agent skills: a collection of reusable skill definitions for AI coding agents.

## Overview

This repository contains YAML-formatted skill definition files that describe capabilities an AI agent can perform. Each skill specifies its name, description, input parameters, and step-by-step instructions for the agent to follow.

## Skills

| Skill | Description |
|-------|-------------|
| [code_review](skills/code_review.yaml) | Review code for quality, correctness, security, and style issues |
| [write_tests](skills/write_tests.yaml) | Generate unit tests for a given piece of code or function |
| [refactor](skills/refactor.yaml) | Refactor code to improve readability and maintainability |
| [debug](skills/debug.yaml) | Diagnose and fix bugs based on error messages or unexpected behavior |
| [write_documentation](skills/write_documentation.yaml) | Generate or improve documentation for code and APIs |
| [implement_feature](skills/implement_feature.yaml) | Implement a new feature based on a description or specification |

## Skill File Format

Each skill is defined in a YAML file with the following structure:

```yaml
name: skill_name
description: Short description of what the skill does.
parameters:
  - name: param_name
    type: string
    description: What this parameter is for.
    required: true
instructions: |
  Step-by-step instructions the agent follows to execute this skill.
```

### Fields

- **name**: Unique identifier for the skill.
- **description**: A brief summary of the skill's purpose.
- **parameters**: List of input parameters the skill accepts.
  - **name**: Parameter name.
  - **type**: Data type (`string`, `number`, `boolean`, `array`).
  - **description**: What the parameter represents.
  - **required**: Whether the parameter must be provided.
- **instructions**: Detailed instructions the agent follows when executing the skill.

## Adding a New Skill

1. Create a new `.yaml` file in the `skills/` directory.
2. Follow the skill file format described above.
3. Add an entry to the table in this README.

