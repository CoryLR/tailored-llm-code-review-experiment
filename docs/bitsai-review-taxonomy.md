
# BitsAI-CR Code Review Taxonomy Notes

Extracted from Table 1 of Sun et al., "BitsAI-CR: Automated Code Review via LLM in Practice," FSE Companion '25 (ByteDance). DOI: https://doi.org/10.1145/3696630.3728552

The taxonomy has three tiers: 4 review dimensions, 41 review categories, and 219 individual review rules. The paper only publishes the first two tiers (dimensions and categories). The 219 individual rules are internal to ByteDance and not enumerated in the paper.

## 1. Security Vulnerability

1. SQL Injection
2. Insecure Deserialization
3. Insecure Object Reference *
4. Memory Leak / Long-term Reference Holding
5. Improper Password Handling
6. Type and Non-null Assertion
7. XSS (Cross-Site Scripting)
8. CSRF (Cross-Site Request Forgery)

## 2. Code Defect

1. Function Parameter Passing *
2. Loop Logic Errors *
3. Database Access Error
4. Conditional Logic Error / Omission / Duplication *
5. Null Pointer Exception
6. Algorithm / Business Logic Error *
7. Index / Boundary Condition Error *
8. Syntax Issue
9. Resource Not Released / Resource Leak
10. Error and Exception Handling Issue *
11. Incorrect Concurrency Control *
12. Data Format / Conversion / Comparison Error
13. Incorrect Sequence Dependency *

## 3. Maintainability and Readability

1. Unclear Naming *
2. Code Testability Issues *
3. Code Readability *
4. Code Formatting Errors / Inconsistencies
5. Redundant / Complex Conditional Logic
6. Variable Naming Conventions *
7. Complex Code *
8. Spelling Error
9. Unused Definition / Redundant Code *
10. Missing or Inappropriate Code Comments *
11. Overly Long Functions or Methods
12. Code Duplication
13. Unclear Error Handling *
14. Magic Numbers / Strings

## 4. Performance Issue (6 categories)

1. Inappropriate Data Structures *
2. Unoptimized Loops *
3. Data Format Conversion Performance
4. Excessive or Improper Lock Usage *
5. Excessive I/O Operations
6. Repeated Calculations

\* = No existing non-LLM tool exists to fully automate

## Areas LLMs May Help With

20 of 41 categories (49%) are marked with an asterisk (\*) where correctness requires understanding intent, design, or project conventions, beyond what rule-based or traditional ML tools can automate.

The unmarked categories are well-served by existing tools:
- SAST (SQL injection, XSS, CSRF, deserialization, password handling)
- Type checkers (null pointer, type assertions)
- Linters (syntax, formatting, naming conventions, unreachable code, magic numbers, function length, complexity metrics)
- Spell checkers
- Duplication detectors (SonarQube, PMD)
- Resource leak analyzers
- Profilers (memory leaks, resource leaks, repeated calculations, I/O patterns, data conversion)

The marked categories share a common trait: correctness depends on understanding what the code is supposed to do, not just what it does syntactically. Specific rationale by category:

- **Insecure Object Reference**: Whether access control is appropriate depends on the authorization model. SAST can flag missing auth decorators but cannot judge whether a specific endpoint should restrict access.
- **Function Parameter Passing**: Type checkers catch type mismatches, but passing the wrong argument of the correct type (e.g., swapping `width` and `height` when both are `int`) requires understanding intent.
- **Loop Logic Errors**: Static analysis detects infinite loops, but wrong bounds, step size, or termination conditions require understanding what the loop should accomplish.
- **Conditional Logic Error / Omission / Duplication**: Whether branching logic is correct or a branch is missing requires knowing the requirements.
- **Algorithm / Business Logic Error**: No tool can verify that business logic matches its specification. This is the clearest case for human/LLM review.
- **Index / Boundary Condition Error**: Off-by-one errors and edge case handling depend on the intended behavior, not just array bounds.
- **Error and Exception Handling Issue**: Linters flag missing error handling, but whether the handling is appropriate (correct recovery, useful error message, right granularity) requires judgment.
- **Incorrect Concurrency Control**: Race detectors (ThreadSanitizer) catch data races at runtime, but design-level questions (lock scope, synchronization primitive choice, atomicity requirements) require understanding the concurrency model.
- **Incorrect Sequence Dependency**: Operation ordering correctness requires domain knowledge about what must happen before what.
- **Unclear Naming**: Naming convention linters enforce patterns (camelCase, snake_case), but whether a name communicates its purpose requires understanding the domain.
- **Variable Naming Conventions**: Linters enforce syntactic patterns, but project-specific conventions (e.g., prefixing reactive variables, domain-specific terminology, abbreviation standards) vary across codebases and are not captured by generic lint rules.
- **Code Testability Issues**: Design judgment about coupling, dependency injection, and test surface area.
- **Code Readability**: Subjective and context-dependent; no metric captures whether code communicates its intent clearly.
- **Unused Definition/Redundant Code**: Linters detect syntactically unused code (unreferenced variables, unused imports), but semantically unnecessary code that still executes requires understanding context and project conventions.
- **Complex Code**: Cyclomatic complexity metrics flag thresholds, but whether the complexity is justified or avoidable requires understanding the problem being solved.
- **Missing or Inappropriate Code Comments**: Whether a comment is needed, accurate, or misleading requires understanding the code's intent.
- **Unclear Error Handling**: Whether error messages help users and whether recovery logic is correct for the failure mode.
- **Inappropriate Data Structures**: Requires understanding access patterns, expected data volume, and algorithmic needs to judge whether a data structure choice is suitable.
- **Unoptimized Loops**: Recognizing algorithmic inefficiency (e.g., linear search where a hash lookup would work) requires understanding the data and access patterns, not just profiling hot spots.
- **Excessive or Improper Lock Usage**: Whether locking granularity is appropriate requires understanding contention patterns and the concurrency design.
