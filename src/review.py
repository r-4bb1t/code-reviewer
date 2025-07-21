import subprocess
import os
import json
import re
import openai
import requests
from typing import Any
from .prompts import create_initial_prompt, create_context_prompt, create_final_prompt


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


def get_pr_head_sha() -> str:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        raise RuntimeError("GITHUB_EVENT_PATH not found")

    with open(event_path) as f:
        event = json.load(f)
    return event["pull_request"]["head"]["sha"]


def get_base_branch() -> str:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        raise RuntimeError("GITHUB_EVENT_PATH not found")

    with open(event_path) as f:
        event = json.load(f)
    return event["pull_request"]["base"]["ref"]


def get_diff(exclude: str = "") -> str:
    base_branch = get_base_branch()
    try:
        run(
            f"git fetch --unshallow origin {base_branch} 2>/dev/null || git fetch origin {base_branch}"
        )
    except Exception:
        pass

    exclude_args = ""
    if exclude:
        exclude_patterns = [
            pattern.strip() for pattern in exclude.split(",") if pattern.strip()
        ]
        exclude_args = " ".join(
            [f'":(exclude){pattern}"' for pattern in exclude_patterns]
        )

    try:
        cmd = f'git diff origin/{base_branch}...HEAD -- . ":(exclude)dist/**"'
        if exclude_args:
            cmd += f" {exclude_args}"
        return run(cmd)
    except RuntimeError:
        try:
            cmd = f'git diff origin/{base_branch} HEAD -- . ":(exclude)dist/**"'
            if exclude_args:
                cmd += f" {exclude_args}"
            return run(cmd)
        except RuntimeError:
            try:
                merge_base = run(f"git merge-base origin/{base_branch} HEAD").strip()
                cmd = f'git diff {merge_base}..HEAD -- . ":(exclude)dist/**"'
                if exclude_args:
                    cmd += f" {exclude_args}"
                return run(cmd)
            except RuntimeError:
                cmd = 'git diff HEAD~1 HEAD -- . ":(exclude)dist/**"'
                if exclude_args:
                    cmd += f" {exclude_args}"
                return run(cmd)


def get_changed_files(exclude: str = "") -> list[str]:
    base_branch = get_base_branch()
    try:
        run(
            f"git fetch --unshallow origin {base_branch} 2>/dev/null || git fetch origin {base_branch}"
        )
    except Exception:
        pass

    exclude_args = ""
    if exclude:
        exclude_patterns = [
            pattern.strip() for pattern in exclude.split(",") if pattern.strip()
        ]
        exclude_args = " ".join(
            [f'":(exclude){pattern}"' for pattern in exclude_patterns]
        )

    try:
        cmd = (
            f'git diff --name-only origin/{base_branch}...HEAD -- . ":(exclude)dist/**"'
        )
        if exclude_args:
            cmd += f" {exclude_args}"
        files = run(cmd).strip().split("\n")
        return [f for f in files if f.strip()]
    except RuntimeError:
        try:
            cmd = f'git diff --name-only origin/{base_branch} HEAD -- . ":(exclude)dist/**"'
            if exclude_args:
                cmd += f" {exclude_args}"
            files = run(cmd).strip().split("\n")
            return [f for f in files if f.strip()]
        except RuntimeError:
            try:
                merge_base = run(f"git merge-base origin/{base_branch} HEAD").strip()
                cmd = (
                    f'git diff --name-only {merge_base}..HEAD -- . ":(exclude)dist/**"'
                )
                if exclude_args:
                    cmd += f" {exclude_args}"
                files = run(cmd).strip().split("\n")
                return [f for f in files if f.strip()]
            except RuntimeError:
                cmd = 'git diff --name-only HEAD~1 HEAD -- . ":(exclude)dist/**"'
                if exclude_args:
                    cmd += f" {exclude_args}"
                files = run(cmd).strip().split("\n")
                return [f for f in files if f.strip()]


def parse_diff_with_line_numbers(diff: str) -> dict[str, list[dict]]:
    """
    diff를 파싱하여 파일별로 변경된 라인 정보를 반환
    """
    file_changes = {}
    current_file = None
    current_hunk = None

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            # 새 파일 시작
            match = re.search(r"diff --git a/(.*?) b/(.*?)$", line)
            if match:
                current_file = match.group(2)
                file_changes[current_file] = []

        elif line.startswith("@@"):
            # 새 hunk 시작
            match = re.search(r"@@ -(\d+),?\d* \+(\d+),?\d* @@", line)
            if match and current_file:
                current_hunk = {
                    "old_start": int(match.group(1)),
                    "new_start": int(match.group(2)),
                    "lines": [],
                }
                file_changes[current_file].append(current_hunk)

        elif current_hunk is not None and current_file:
            # 라인 내용
            if line.startswith("+") and not line.startswith("+++"):
                # 추가된 라인
                new_line_num = current_hunk["new_start"] + len(
                    [l for l in current_hunk["lines"] if l["type"] in ["+", " "]]
                )
                current_hunk["lines"].append(
                    {"type": "+", "content": line[1:], "line_number": new_line_num}
                )
            elif line.startswith("-") and not line.startswith("---"):
                # 삭제된 라인
                current_hunk["lines"].append(
                    {"type": "-", "content": line[1:], "line_number": None}
                )
            elif line.startswith(" "):
                # 변경되지 않은 라인
                new_line_num = current_hunk["new_start"] + len(
                    [l for l in current_hunk["lines"] if l["type"] in ["+", " "]]
                )
                current_hunk["lines"].append(
                    {"type": " ", "content": line[1:], "line_number": new_line_num}
                )

    return file_changes


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


def parse_context_requests(
    response: str,
) -> tuple[list[dict[str, str]], str, list[dict]]:
    try:
        response_json = json.loads(response)

        needs_context = response_json.get("needs_context", False)
        context_requests = response_json.get("context_requests", [])
        review = response_json.get("review", "")
        line_comments = response_json.get("line_comments", [])

        if not needs_context:
            return [], review, line_comments

        return context_requests, "", line_comments

    except json.JSONDecodeError as e:
        print(f"⚠️ JSON parsing failed, falling back to text parsing: {e}")

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

        return requests, "", []


def post_comment(github_token: str, body: str, pr_number: str):
    repo = os.environ["GITHUB_REPOSITORY"]
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.post(url, json={"body": body}, headers=headers)
    if response.status_code >= 300:
        raise RuntimeError(f"Failed to post comment: {response.text}")


def post_review_comments(
    github_token: str, pr_number: str, head_sha: str, line_comments: list[dict]
):
    """
    특정 줄에 리뷰 댓글을 답니다
    """
    if not line_comments:
        return

    repo = os.environ["GITHUB_REPOSITORY"]

    comments = []
    for comment in line_comments:
        if "file" in comment and "line" in comment and "comment" in comment:
            comments.append(
                {
                    "path": comment["file"],
                    "line": comment["line"],
                    "body": comment["comment"],
                }
            )

    if not comments:
        return

    review_data = {
        "commit_id": head_sha,
        "body": "🤖 AI 코드 리뷰",
        "event": "COMMENT",
        "comments": comments,
    }

    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    response = requests.post(url, json=review_data, headers=headers)
    if response.status_code >= 300:
        print(f"⚠️ Failed to post line comments: {response.text}")
        # 줄별 댓글 실패 시 일반 댓글로 폴백
        fallback_body = "🤖 AI 코드 리뷰 (줄별 댓글)\n\n"
        for comment in line_comments:
            fallback_body += f"**{comment.get('file', 'Unknown file')}:{comment.get('line', 'Unknown line')}**\n"
            fallback_body += f"{comment.get('comment', '')}\n\n"
        post_comment(github_token, fallback_body, pr_number)
    else:
        print(f"✅ {len(comments)} line comments posted successfully.")


def call_openai(
    messages: list[dict[str, str]], model: str, api_key: str, force_json: bool = True
) -> str:
    client = openai.OpenAI(api_key=api_key)

    kwargs = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": 5000,
    }

    if force_json:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def extract_line_comments_from_text(text: str) -> list[dict]:
    """
    텍스트에서 JSON 형식의 line_comments를 추출
    """
    try:
        # JSON 블록 찾기
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            json_data = json.loads(json_match.group(1))
            return json_data.get("line_comments", [])
    except Exception as e:
        print(f"⚠️ Failed to extract line_comments from text: {e}")

    return []


def review_pr(
    github_token: str,
    openai_api_key: str,
    model: str = "gpt-4o",
    language: str = "Korean",
    exclude: str = "",
    max_recursion: int = 3,
):
    print("📥 Fetching diff...")
    diff = get_diff(exclude)
    if not diff.strip():
        print("✅ No diff found, skipping review.")
        return

    print("🧠 Sending initial analysis to OpenAI...")

    system_message = (
        f"You are a professional software engineer reviewing pull requests. Answer in {language}."
        if language.lower() != "english"
        else "You are a professional software engineer reviewing pull requests."
    )

    messages = [
        {
            "role": "system",
            "content": system_message,
        },
        {"role": "user", "content": create_initial_prompt(diff, language)},
    ]

    all_context = {}
    iteration = 0
    final_line_comments = []

    while iteration < max_recursion:
        response = call_openai(messages, model, openai_api_key, force_json=True)
        context_requests, review_content, line_comments = parse_context_requests(
            response
        )

        if line_comments:
            final_line_comments.extend(line_comments)

        if not context_requests:
            print(
                f"✅ Context collection completed (iteration {iteration}). Writing final review..."
            )

            if review_content:
                final_review = review_content
            else:
                final_prompt = create_final_prompt(diff, all_context, language)
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": final_prompt})
                final_review = call_openai(
                    messages, model, openai_api_key, force_json=False
                )
                additional_comments = extract_line_comments_from_text(final_review)
                if additional_comments:
                    final_line_comments.extend(additional_comments)

            break

        print(f"🔍 Processing context requests (iteration {iteration + 1})...")
        current_context = {}

        for req in context_requests:
            pattern = req.get("pattern", "")
            reason = req.get("reason", "")
            print(f"  - Searching pattern: '{pattern}' (reason: {reason})")

            search_results = search_code_in_repo(pattern)
            current_context[pattern] = search_results

        all_context.update(current_context)

        context_prompt = create_context_prompt(
            diff, current_context, iteration + 1, language
        )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": context_prompt})

        iteration += 1

    if iteration >= max_recursion:
        print(f"⚠️ Reached maximum iteration count ({max_recursion}).")
        final_prompt = create_final_prompt(diff, all_context, language)
        messages.append({"role": "user", "content": final_prompt})
        final_review = call_openai(messages, model, openai_api_key, force_json=False)
        # 최종 리뷰에서도 line_comments 추출 시도
        additional_comments = extract_line_comments_from_text(final_review)
        if additional_comments:
            final_line_comments.extend(additional_comments)

    print("📤 Review completed. Posting comments...")

    pr_number = get_pr_number()
    head_sha = get_pr_head_sha()

    if final_line_comments:
        print(f"📌 Posting {len(final_line_comments)} line comments...")
        post_review_comments(github_token, pr_number, head_sha, final_line_comments)

    context_summary = ""
    context_details = ""
    if all_context:
        total_patterns = len(all_context)
        total_files = sum(len(files) for files in all_context.values())
        total_matches = sum(
            len(matches) for files in all_context.values() for matches in files.values()
        )
        context_summary = (
            f"{total_patterns}개 패턴, {total_files}개 파일, {total_matches}개 매치"
        )

        # 자세한 context 정보 생성
        context_details = "\n<details>\n<summary>🔍 Context 상세 정보</summary>\n\n"
        for pattern, files in all_context.items():
            context_details += f"**패턴: `{pattern}`**\n"
            if not files:
                context_details += "  - 매치 없음\n\n"
                continue

            for file_path, matches in files.items():
                context_details += f"  - **{file_path}**\n"
                for match in matches[:3]:  # 처음 3개만 표시
                    context_details += f"    ```\n    {match}\n    ```\n"
                if len(matches) > 3:
                    context_details += f"    ... 및 {len(matches) - 3}개 추가 매치\n"
                context_details += "\n"
        context_details += "</details>\n"
    else:
        context_summary = "없음"

    comment_body = f"""### 🤖 AI Code Review

| Model | Language | Iterations | Context |
| --- | --- | --- | --- |
| {model} | {language} | {iteration} | {context_summary} |

{context_details}
{final_review}"""

    post_comment(github_token, comment_body, pr_number)
    print("✅ Review comment posted.")
