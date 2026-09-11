---
name: debug
description: Diagnose and fix bugs in code based on error messages or unexpected behavior.
---

# debug

Diagnose and fix bugs in code based on error messages or unexpected behavior.

## Parameters

- **file_path** (string, required): Path to the file containing the bug.
- **error_message** (string, optional): The error message or description of the unexpected behavior.
- **reproduce_steps** (string, optional): Steps to reproduce the issue.

## Instructions

Diagnose and resolve the reported issue:
1. Analyze the error message and stack trace (if provided)
2. Trace the code execution path to identify root cause
3. Check for common issues: null/undefined references, off-by-one errors, incorrect assumptions
4. Verify input validation and type handling
5. Propose and implement a fix
6. Add a regression test to prevent the issue from recurring
7. Explain the root cause and the fix applied

Prefer minimal, targeted fixes over large rewrites.
