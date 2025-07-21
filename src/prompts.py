from typing import Any
import json


def _get_language_instruction(language: str) -> str:
    return f"Answer in {language}." if language.lower() != "english" else ""


def _get_common_guidelines() -> str:
    return """Review Guidelines:
- Focus on SIGNIFICANT issues only (bugs, security, major performance problems)
- Keep it concise - highlight only the most important improvements
- Avoid obvious or minor style suggestions
- DO NOT write praise or positive comments - only mention actual issues that need fixing
- Skip commenting if there are no significant issues to report
- Only provide code examples for complex issues that need clarification

Follow proper markdown syntax:
- Use backticks (`) around keywords, function names, variable names, and inline code
- Use triple backticks (```) for code blocks and always specify the language (e.g., ```python, ```javascript)
- Use proper markdown formatting for emphasis and structure"""


def _get_markdown_guidelines() -> str:
    return """Follow proper markdown syntax:
- ALWAYS use backticks (`) around keywords, function names, variable names, and inline code
- Use triple backticks (```) for code blocks and always specify the language (e.g., ```python, ```javascript)
- Use proper markdown formatting for emphasis and structure"""


def create_initial_prompt(diff: str, language: str) -> str:
    lang_instruction = _get_language_instruction(language)

    return f"""You are a code review AI. {lang_instruction}

Analyze the following diff and request additional context if needed.

{_get_common_guidelines()}

Please respond in JSON format:

{{
  "needs_context": true/false,
  "context_requests": [
    {{
      "pattern": "pattern to search (function names, class names, variable names, etc.)",
      "reason": "why this information is needed"
    }}
  ],
  "review": "if no additional context is needed, write the code review directly here with code examples",
  "line_comments": [
    {{
      "file": "filename", 
      "line": line_number,
      "comment": "brief, specific comment for that line"
    }}
  ]

IMPORTANT for line_comments:
- Only comment on ADDED lines (marked with + in the diff)
- Keep comments brief and focused on significant issues only
- DO NOT add praise or positive comments - only actual issues
- Use exact line numbers from the diff output
- ALWAYS use backticks around function names, variables, and code elements
}}

```diff
{diff}
```"""


def create_context_prompt(
    diff: str, context_data: dict[str, Any], iteration: int, language: str
) -> str:
    lang_instruction = _get_language_instruction(language)

    context_text = ""
    for pattern, data in context_data.items():
        context_text += f"\n=== Search results for pattern '{pattern}' ===\n"
        if isinstance(data, dict):
            for file_path, matches in data.items():
                context_text += f"\nFile: {file_path}\n"
                if isinstance(matches, list):
                    for match in matches:
                        context_text += f"  {match}\n"
                else:
                    context_text += f"  {matches}\n"
        else:
            context_text += f"{data}\n"

    return f"""Additional context for previous requests is provided (iteration {iteration}). {lang_instruction}

{context_text}

Based on this information, please request more context if needed, or provide the final code review if sufficient.

{_get_common_guidelines()}

Please respond in JSON format:

{{
  "needs_context": true/false,
  "context_requests": [
    {{
      "pattern": "additional search pattern",
      "reason": "reason"
    }}
  ],
  "review": "final code review with code examples (when needs_context is false)",
  "line_comments": [
    {{
      "file": "filename",
      "line": line_number,
      "comment": "brief, specific comment for that line"
    }}
  ]

IMPORTANT for line_comments:
- Only comment on ADDED lines (marked with + in the diff)
- Keep comments brief and focused on significant issues only
- DO NOT add praise or positive comments - only actual issues
- Use exact line numbers from the diff output
- ALWAYS use backticks around function names, variables, and code elements
}}

Original diff:
```diff
{diff}
```"""


def create_final_prompt(diff: str, all_context: dict[str, Any], language: str) -> str:
    lang_instruction = _get_language_instruction(language)

    return f"""All context gathering is complete. Please write the final code review now. {lang_instruction}

Review Guidelines:
- Focus on SIGNIFICANT issues only (bugs, security, major performance problems)
- Keep it concise - highlight only the most important improvements
- Avoid obvious or minor style suggestions
- DO NOT write praise or positive comments - only mention actual issues that need fixing
- Skip commenting if there are no significant issues to report
- Only provide code examples for complex issues that need clarification

{_get_markdown_guidelines()}

Example format:
```python
# Current code (problematic)
result = [expensive_function(item) for item in items if expensive_function(item) > threshold]

# Improved code
result = [value for item in items if (value := expensive_function(item)) > threshold]
```

Please respond in markdown format.

If you have detailed comments for specific lines, also provide the following JSON format:

```json
{{
  "line_comments": [
    {{
      "file": "filename",
      "line": line_number,
      "comment": "specific review comment for that line with code examples if applicable"
    }}
  ]
}}
```

IMPORTANT for line_comments:
- Only comment on ADDED lines (marked with + in the diff)
- Keep comments brief and focused on significant issues only
- DO NOT add praise or positive comments - only actual issues
- Use exact line numbers from the diff output
- ALWAYS use backticks around function names, variables, and code elements

```diff
{diff}
```

Context information:
{json.dumps(all_context, ensure_ascii=False, indent=2)}"""
