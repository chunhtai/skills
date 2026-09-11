---
name: write_tests
description: Generate unit tests for a given piece of code or function.
---

# write_tests

Generate unit tests for a given piece of code or function.

## Parameters

- **file_path** (string, required): Path to the source file to write tests for.
- **test_framework** (string, optional): The testing framework to use (e.g., pytest, jest, junit). Inferred from project if not specified.
- **coverage_target** (number, optional): Target code coverage percentage (0-100). Defaults to 80.

## Instructions

Generate comprehensive unit tests for the specified code:
1. Identify all public functions, methods, and classes
2. Create test cases covering normal usage (happy path)
3. Create test cases for edge cases and boundary conditions
4. Create test cases for error conditions and invalid inputs
5. Use mocks and stubs where appropriate for external dependencies
6. Follow the naming conventions and patterns used in existing tests

Place tests in the appropriate test directory following project conventions.
