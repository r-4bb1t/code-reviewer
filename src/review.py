import subprocess
import os
import json
import re
import openai
import requests
from typing import Any


def run(cmd: str) -> str:
    result = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


def get_pr_number() -> str:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        raise RuntimeError("GITHUB_EVENT_PATH not found")

    with open(event_path) as f:
        event = json.load(f)
    return str(event["pull_request"]["number"])


def get_diff(exclude: str = "") -> str:
    run("git fetch origin main")
    exclude_args = ""
    if exclude:
        exclude_patterns = [
            pattern.strip() for pattern in exclude.split(",") if pattern.strip()
        ]
        exclude_args = " ".join(
            [f'":(exclude){pattern}"' for pattern in exclude_patterns]
        )

    cmd = 'git diff origin/main...HEAD -- . ":(exclude)dist/**"'
    if exclude_args:
        cmd += f" {exclude_args}"
    return run(cmd)


def get_changed_files(exclude: str = "") -> list[str]:
    run("git fetch origin main")
    exclude_args = ""
    if exclude:
        exclude_patterns = [
            pattern.strip() for pattern in exclude.split(",") if pattern.strip()
        ]
        exclude_args = " ".join(
            [f'":(exclude){pattern}"' for pattern in exclude_patterns]
        )

    cmd = 'git diff --name-only origin/main...HEAD -- . ":(exclude)dist/**"'
    if exclude_args:
        cmd += f" {exclude_args}"
    files = run(cmd).strip().split("\n")
    return [f for f in files if f.strip()]


def search_code_in_repo(
    pattern: str, file_extensions: list[str] | None = None
) -> dict[str, list[str]]:
    results = {}

    if file_extensions is None:
        file_extensions = [
            "*.py",
            "*.js",
            "*.ts",
            "*.jsx",
            "*.tsx",
            "*.java",
            "*.cpp",
            "*.c",
            "*.h",
            "*.cs",
            "*.go",
            "*.rs",
            "*.rb",
            "*.php",
        ]

    for ext in file_extensions:
        try:
            cmd = f'find . -name "{ext}" -type f | head -50 | xargs grep -l "{pattern}" 2>/dev/null || true'
            matching_files = run(cmd).strip()
            if matching_files:
                for file_path in matching_files.split("\n"):
                    if file_path.strip():
                        try:
                            grep_cmd = f'grep -n "{pattern}" "{file_path}" | head -10'
                            matches = run(grep_cmd).strip()
                            if matches:
                                results[file_path] = matches.split("\n")
                        except Exception as e:
                            print(f"Error processing {file_path}: {e}")
                            continue
        except Exception as e:
            print(f"Error searching for {pattern}: {e}")
            continue

    return results


def get_file_context(
    file_path: str, line_numbers: list[int] | None = None, context_lines: int = 5
) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not line_numbers:
            return "".join(lines[:50])

        context = []
        for line_num in line_numbers:
            start = max(0, line_num - context_lines - 1)
            end = min(len(lines), line_num + context_lines)
            context.append(f"\n--- Around line {line_num} in {file_path} ---")
            for i in range(start, end):
                marker = ">>> " if i == line_num - 1 else "    "
                context.append(f"{marker}{i+1}: {lines[i].rstrip()}")

        return "\n".join(context)
    except Exception as e:
        return f"Error reading {file_path}: {str(e)}"


def create_initial_prompt(diff: str) -> str:
    return f"""당신은 코드 리뷰 AI입니다.

다음 diff를 분석하고 필요한 경우 추가 컨텍스트를 요청해주세요.

응답은 반드시 JSON 형식으로 해주세요:

{{
  "needs_context": true/false,
  "context_requests": [
    {{
      "pattern": "검색할 패턴 (함수명, 클래스명, 변수명 등)",
      "reason": "왜 이 정보가 필요한지"
    }}
  ],
  "review": "만약 추가 컨텍스트가 불필요하다면 여기에 바로 코드 리뷰 작성"
}}

```diff
{diff}
```"""


def create_context_prompt(
    diff: str, context_data: dict[str, Any], iteration: int
) -> str:
    context_text = ""
    for pattern, data in context_data.items():
        context_text += f"\n=== 패턴 '{pattern}'에 대한 검색 결과 ===\n"
        if isinstance(data, dict):
            for file_path, matches in data.items():
                context_text += f"\n파일: {file_path}\n"
                if isinstance(matches, list):
                    for match in matches:
                        context_text += f"  {match}\n"
                else:
                    context_text += f"  {matches}\n"
        else:
            context_text += f"{data}\n"

    return f"""이전 요청에 대한 추가 컨텍스트를 제공합니다 (반복 {iteration}).

{context_text}

이 정보를 바탕으로, 더 필요한 컨텍스트가 있다면 요청하거나, 충분하다면 최종 코드 리뷰를 제공해주세요.

응답은 반드시 JSON 형식으로 해주세요:

{{
  "needs_context": true/false,
  "context_requests": [
    {{
      "pattern": "추가 검색 패턴",
      "reason": "이유"
    }}
  ],
  "review": "최종 코드 리뷰 (needs_context가 false일 때)"
}}

원본 diff:
```diff
{diff}
```"""


def parse_context_requests(response: str) -> tuple[list[dict[str, str]], str]:
    try:
        response_json = json.loads(response)

        needs_context = response_json.get("needs_context", False)
        context_requests = response_json.get("context_requests", [])
        review = response_json.get("review", "")

        if not needs_context:
            return [], review

        return context_requests, ""

    except json.JSONDecodeError as e:
        print(f"⚠️ JSON 파싱 실패, 텍스트 파싱으로 대체: {e}")

        requests = []
        lines = response.split("\n")

        current_request = {}
        in_context_request = False

        for line in lines:
            line = line.strip()
            if line == "CONTEXT_REQUEST:":
                if current_request and "pattern" in current_request:
                    requests.append(current_request)
                current_request = {}
                in_context_request = True
            elif in_context_request and line.startswith("- pattern:"):
                current_request["pattern"] = (
                    line.replace("- pattern:", "").strip().strip('"')
                )
            elif in_context_request and line.startswith("- reason:"):
                current_request["reason"] = (
                    line.replace("- reason:", "").strip().strip('"')
                )
            elif line and not line.startswith("-") and in_context_request:
                in_context_request = False

        if current_request and "pattern" in current_request:
            requests.append(current_request)

        return requests, ""


def create_final_prompt(diff: str, all_context: dict[str, Any]) -> str:
    return f"""모든 컨텍스트 수집이 완료되었습니다. 이제 최종 코드 리뷰를 작성해주세요.

다음 사항들을 포함해주세요:
- 코드 품질 및 베스트 프랙티스
- 잠재적 버그나 보안 이슈
- 성능 개선 사항
- 코드 가독성 및 유지보수성

마크다운 형식으로 응답해주세요.

```diff
{diff}
```

컨텍스트 정보:
{json.dumps(all_context, ensure_ascii=False, indent=2)}"""


def post_comment(github_token: str, body: str, pr_number: str):
    repo = os.environ["GITHUB_REPOSITORY"]
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
    response = requests.post(url, json={"body": body}, headers=headers)
    if response.status_code >= 300:
        raise RuntimeError(f"Failed to post comment: {response.text}")


def call_openai(
    messages: list[dict[str, str]], model: str, api_key: str, force_json: bool = True
) -> str:
    client = openai.OpenAI(api_key=api_key)

    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    if force_json:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def review_pr(
    github_token: str,
    openai_api_key: str,
    model: str = "gpt-4o",
    exclude: str = "",
    max_recursion: int = 3,
):
    print("📥 diff 가져오는 중...")
    diff = get_diff(exclude)
    if not diff.strip():
        print("✅ diff가 없어서 리뷰를 건너뜁니다.")
        return

    print("🧠 OpenAI로 초기 분석 전송 중...")

    messages = [
        {
            "role": "system",
            "content": "당신은 전문 소프트웨어 엔지니어로서 pull request를 리뷰하고 있습니다.",
        },
        {"role": "user", "content": create_initial_prompt(diff)},
    ]

    all_context = {}
    iteration = 0

    while iteration < max_recursion:
        response = call_openai(messages, model, openai_api_key, force_json=True)
        context_requests, review_content = parse_context_requests(response)

        if not context_requests:
            print(f"✅ 컨텍스트 수집 완료 (반복 {iteration}). 최종 리뷰 작성 중...")

            if review_content:
                final_review = review_content
            else:
                final_prompt = create_final_prompt(diff, all_context)
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": final_prompt})
                final_review = call_openai(
                    messages, model, openai_api_key, force_json=False
                )

            break

        print(f"🔍 컨텍스트 요청 처리 중 (반복 {iteration + 1})...")
        current_context = {}

        for req in context_requests:
            pattern = req.get("pattern", "")
            reason = req.get("reason", "")
            print(f"  - 패턴 검색: '{pattern}' (이유: {reason})")

            search_results = search_code_in_repo(pattern)
            current_context[pattern] = search_results

        all_context.update(current_context)

        context_prompt = create_context_prompt(diff, current_context, iteration + 1)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": context_prompt})

        iteration += 1

    if iteration >= max_recursion:
        print(f"⚠️ 최대 반복 횟수({max_recursion})에 도달했습니다.")
        final_prompt = create_final_prompt(diff, all_context)
        messages.append({"role": "user", "content": final_prompt})
        final_review = call_openai(messages, model, openai_api_key, force_json=False)

    print("📤 리뷰 완료. 댓글 작성 중...")

    pr_number = get_pr_number()
    comment_body = f"### 🤖 AI 코드 리뷰 (모델: {model})\n\n{final_review}"

    if iteration > 0:
        comment_body += (
            f"\n\n---\n*이 리뷰는 {iteration}번의 컨텍스트 수집을 통해 작성되었습니다.*"
        )

    post_comment(github_token, comment_body, pr_number)
    print("✅ 리뷰 댓글이 게시되었습니다.")
