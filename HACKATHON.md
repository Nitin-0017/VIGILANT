# HACKATHON.md

## Team name
Chakravyuh

## Team members
- Nitin Kumar
- Manjeet
- Kunal Kumar Mandal

## Problem statement
Problem 5: Automated Documentation Drift Detection

## Models we use and why

- **Qwen2.5-Coder-7B-Instruct:** Analyzes source code, understands code changes, and compares actual code behavior with existing documentation to identify inconsistencies. We selected it because it is specifically designed for programming-related tasks and code reasoning.

- **Qwen3-Embedding-0.6B:** Converts code descriptions and documentation into embeddings to retrieve relevant documentation based on semantic meaning rather than exact keyword matches. We selected it because it is specifically designed for semantic search and retrieval.

We use two specialized models because finding relevant documentation and reasoning about code-documentation inconsistencies are two different tasks. Together, they form the AI foundation of VIGILANT.