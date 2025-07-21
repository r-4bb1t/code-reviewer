from typing import Any
import json


def create_initial_prompt(diff: str, language: str) -> str:
    lang_instruction = f"Answer in {language}." if language.lower() != "english" else ""

    return f"""You are a code review AI. {lang_instruction}

Analyze the following diff and request additional context if needed.

When providing code review feedback, include specific code examples to illustrate your suggestions if necessary. 
For example, if suggesting an improvement, show both the problematic code and the improved version.
Consider suggesting multiple improvements for the same code.

Follow proper markdown syntax:
- Use backticks (`) around keywords, function names, variable names, and inline code
- Use triple backticks (```) for code blocks and always specify the language (e.g., ```python, ```javascript)
- Use proper markdown formatting for emphasis and structure

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
      "comment": "specific review comment for that line with code examples if applicable"
    }}
  ]
}}

```diff
{diff}
```"""


def create_context_prompt(
    diff: str, context_data: dict[str, Any], iteration: int, language: str
) -> str:
    lang_instruction = f"Answer in {language}." if language.lower() != "english" else ""

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

When providing code review feedback, always include specific code examples to illustrate your suggestions.
For example, if suggesting an improvement, show both the problematic code and the improved version.

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
      "comment": "specific review comment for that line with code examples if applicable"
    }}
  ]
}}

Original diff:
```diff
{diff}
```"""


def create_final_prompt(diff: str, all_context: dict[str, Any], language: str) -> str:
    lang_instruction = f"Answer in {language}." if language.lower() != "english" else ""

    return f"""All context gathering is complete. Please write the final code review now. {lang_instruction}

Please include the following:
- Code quality and best practices
- Potential bugs or security issues
- Performance improvements
- Code readability and maintainability

IMPORTANT: For each suggestion or issue you identify, provide concrete code examples showing:
1. The current problematic code (if applicable)
2. The improved version of the code
3. Brief explanation of why the change is beneficial

Use markdown code blocks with appropriate language syntax highlighting.

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

```diff
{diff}
```

Context information:
{json.dumps(all_context, ensure_ascii=False, indent=2)}"""
