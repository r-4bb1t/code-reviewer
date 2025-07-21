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
            match = re.search(r"diff --git a/(.*?) b/(.*?)$", line)
            if match:
                current_file = match.group(2)
                file_changes[current_file] = []

        elif line.startswith("@@"):
            match = re.search(r"@@ -(\d+),?\d* \+(\d+),?\d* @@", line)
            if match and current_file:
                current_hunk = {
                    "old_start": int(match.group(1)),
                    "new_start": int(match.group(2)),
                    "lines": [],
                }
                file_changes[current_file].append(current_hunk)

        elif current_hunk is not None and current_file:
            if line.startswith("+") and not line.startswith("+++"):
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


def get_valid_diff_lines(diff: str) -> dict[str, set[int]]:
    """
    diff에서 실제로 변경된 줄 번호들을 추출
    GitHub API는 주로 추가된 줄(+)에만 줄별 댓글을 허용
    """
    valid_lines = {}
    file_changes = parse_diff_with_line_numbers(diff)

    for file_path, hunks in file_changes.items():
        valid_lines[file_path] = set()
        for hunk in hunks:
            for line in hunk["lines"]:
                # 추가된 줄(+)에만 댓글 허용
                if line["type"] == "+" and line["line_number"] is not None:
                    valid_lines[file_path].add(line["line_number"])

    return valid_lines


def post_review_comments(
    github_token: str,
    pr_number: str,
    head_sha: str,
    line_comments: list[dict],
    diff: str = "",
):
    """
    특정 줄에 리뷰 댓글을 답니다
    """
    if not line_comments:
        return

    repo = os.environ["GITHUB_REPOSITORY"]

    valid_diff_lines = {}
    if diff:
        valid_diff_lines = get_valid_diff_lines(diff)

    valid_comments = []
    invalid_comments = []

    for comment in line_comments:
        if "file" in comment and "line" in comment and "comment" in comment:
            file_path = comment["file"]
            line_number = comment["line"]
            comment_text = comment["comment"]

            if not validate_comment_quality(comment_text):
                print(f"⚠️ Filtered low-quality comment: {comment_text[:50]}...")
                invalid_comments.append(comment)
                continue

            if (
                file_path in valid_diff_lines
                and line_number in valid_diff_lines[file_path]
            ):
                valid_comments.append(
                    {
                        "path": file_path,
                        "line": line_number,
                        "body": comment_text,
                        "side": "RIGHT",
                    }
                )
            else:
                invalid_comments.append(comment)

    if valid_comments:
        review_data = {
            "commit_id": head_sha,
            "body": "⚡ Code review by AI",
            "event": "COMMENT",
            "comments": valid_comments,
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
        else:
            print(f"✅ {len(valid_comments)} line comments posted successfully.")

    if invalid_comments:
        print(f"⚠️ {len(invalid_comments)} comments ignored (not on valid diff lines)")


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
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            json_data = json.loads(json_match.group(1))
            return json_data.get("line_comments", [])
    except Exception as e:
        print(f"⚠️ Failed to extract line_comments from text: {e}")

    return []


def get_function_definition(
    function_name: str, file_extensions: list[str] | None = None
) -> dict[str, str]:
    """
    함수 정의를 정확하게 찾아서 반환
    """
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
            if ext == "*.py":
                # Python 함수 정의 검색
                cmd = f'find . -name "{ext}" -type f | head -50 | xargs grep -n "def {function_name}\\|class {function_name}" 2>/dev/null || true'
            elif ext in ["*.js", "*.ts", "*.jsx", "*.tsx"]:
                # JavaScript/TypeScript 함수 정의 검색
                cmd = f'find . -name "{ext}" -type f | head -50 | xargs grep -n "function {function_name}\\|const {function_name}\\|class {function_name}\\|{function_name} =" 2>/dev/null || true'
            else:
                # 기타 언어
                cmd = f'find . -name "{ext}" -type f | head -50 | xargs grep -n "{function_name}" 2>/dev/null || true'

            matching_lines = run(cmd).strip()
            if matching_lines:
                for line in matching_lines.split("\n"):
                    if ":" in line:
                        file_path, line_content = line.split(":", 1)
                        if file_path not in results:
                            results[file_path] = get_file_context(
                                file_path.strip(), None, 10
                            )[:1000]
        except Exception as e:
            print(f"Error searching function definition for {function_name}: {e}")
            continue

    return results


def enhanced_search_code_in_repo(
    pattern: str, search_type: str = "usage", file_extensions: list[str] | None = None
) -> dict[str, list[str]]:
    """
    향상된 코드 검색 - 사용법, 정의, 임포트 등을 구분하여 검색
    """
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

    # 검색 패턴을 타입별로 구분
    search_patterns = []

    if search_type == "definition":
        search_patterns = [
            f"def {pattern}",  # Python function
            f"class {pattern}",  # Python class
            f"function {pattern}",  # JavaScript function
            f"const {pattern}",  # JavaScript const function
            f"{pattern} =",  # Variable assignment
        ]
    elif search_type == "import":
        search_patterns = [
            f"from .* import.*{pattern}",
            f"import.*{pattern}",
            f"require.*{pattern}",
        ]
    else:  # usage
        search_patterns = [pattern]

    for ext in file_extensions:
        for search_pattern in search_patterns:
            try:
                cmd = f'find . -name "{ext}" -type f | head -50 | xargs grep -l "{search_pattern}" 2>/dev/null || true'
                matching_files = run(cmd).strip()
                if matching_files:
                    for file_path in matching_files.split("\n"):
                        if file_path.strip():
                            try:
                                grep_cmd = f'grep -n "{search_pattern}" "{file_path}" | head -10'
                                matches = run(grep_cmd).strip()
                                if matches:
                                    if file_path not in results:
                                        results[file_path] = []
                                    results[file_path].extend(matches.split("\n"))
                            except Exception as e:
                                print(f"Error processing {file_path}: {e}")
                                continue
            except Exception as e:
                print(f"Error searching for {search_pattern}: {e}")
                continue

    return results


def validate_comment_quality(comment: str, pattern: str = "") -> bool:
    """
    댓글의 품질을 검증하여 모호한 댓글을 필터링
    """
    # 모호한 표현들
    vague_phrases = [
        "확인이 필요합니다",
        "requires checking",
        "needs verification",
        "검토가 필요합니다",
        "should be verified",
        "might need",
        "확인해야 합니다",
        "should check",
        "consider checking",
        "명확하지 않음",
        "unclear",
        "not clear",
        "검증 필요",
        "verification needed",
        "needs review",
        "확인해 주세요",
        "please check",
        "please verify",
        "살펴봐야",
        "should examine",
        "should investigate",
    ]

    comment_lower = comment.lower()

    # 모호한 표현이 있으면 False
    for phrase in vague_phrases:
        if phrase.lower() in comment_lower:
            return False

    # 너무 짧거나 일반적인 댓글 필터링
    if len(comment.strip()) < 20:
        return False

    # STRICT 모드 (context가 부족할 때)
    if pattern == "STRICT":
        # 더 엄격한 기준 적용
        strict_requirements = [
            any(marker in comment for marker in ["```", "`"]),  # 코드 예제 필수
            any(
                word in comment_lower
                for word in [
                    "버그",
                    "bug",
                    "오류",
                    "error",
                    "보안",
                    "security",
                    "성능",
                    "performance",
                ]
            ),  # 구체적인 이슈 타입 언급 필수
            len(comment.strip()) > 50,  # 더 긴 설명 필수
        ]
        if sum(strict_requirements) < 2:  # 3개 중 최소 2개 충족
            return False

    # 패턴이 제공되었는데 구체적인 언급이 없으면 False
    if pattern and pattern != "STRICT" and pattern.lower() not in comment_lower:
        # 하지만 코드 예제나 구체적인 설명이 있으면 허용
        if not any(marker in comment for marker in ["```", "`", ":", "=", "(", ")"]):
            return False

    return True


def gather_comprehensive_context(
    context_requests: list[dict[str, str]],
) -> dict[str, Any]:
    """
    포괄적인 context 수집 - 정의, 사용법, 임포트를 모두 검색
    """
    comprehensive_context = {}

    for req in context_requests:
        pattern = req.get("pattern", "")
        reason = req.get("reason", "")
        print(f"  - Comprehensive search for: '{pattern}' (reason: {reason})")

        # 1. 함수/클래스 정의 검색
        definitions = get_function_definition(pattern)
        if definitions:
            comprehensive_context[f"{pattern}_definitions"] = definitions

        # 2. 사용법 검색
        usage = enhanced_search_code_in_repo(pattern, "usage")
        if usage:
            comprehensive_context[f"{pattern}_usage"] = usage

        # 3. 임포트 검색
        imports = enhanced_search_code_in_repo(pattern, "import")
        if imports:
            comprehensive_context[f"{pattern}_imports"] = imports

        # 기존 검색도 유지 (backward compatibility)
        search_results = search_code_in_repo(pattern)
        if search_results:
            comprehensive_context[pattern] = search_results

    return comprehensive_context


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
                    # 최종 리뷰에서도 품질 검증 적용
                    filtered_comments = []
                    for comment in additional_comments:
                        if validate_comment_quality(comment.get("comment", "")):
                            filtered_comments.append(comment)
                        else:
                            print(
                                f"⚠️ Filtered low-quality final comment: {comment.get('comment', '')[:50]}..."
                            )
                    final_line_comments.extend(filtered_comments)

            break

        print(f"🔍 Processing context requests (iteration {iteration + 1})...")

        # 포괄적인 context 수집 사용
        current_context = gather_comprehensive_context(context_requests)
        all_context.update(current_context)

        context_prompt = create_context_prompt(
            diff, current_context, iteration + 1, language
        )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": context_prompt})

        iteration += 1

    if iteration >= max_recursion:
        print(f"⚠️ Reached maximum iteration count ({max_recursion}).")
        if not all_context:
            print("⚠️ No context gathered. Applying strict filtering for final review.")

        final_prompt = create_final_prompt(diff, all_context, language)
        if not all_context or len(all_context) < 2:
            final_prompt += "\n\nWARNING: Limited context available. Only comment on issues that are immediately obvious from the diff itself. When in doubt, skip commenting."

        messages.append({"role": "user", "content": final_prompt})
        final_review = call_openai(messages, model, openai_api_key, force_json=False)
        additional_comments = extract_line_comments_from_text(final_review)
        if additional_comments:
            strict_filtering = not all_context or len(all_context) < 2
            filtered_comments = []
            for comment in additional_comments:
                if validate_comment_quality(
                    comment.get("comment", ""), "" if not strict_filtering else "STRICT"
                ):
                    filtered_comments.append(comment)
                else:
                    print(
                        f"⚠️ Filtered comment due to insufficient context: {comment.get('comment', '')[:50]}..."
                    )
            final_line_comments.extend(filtered_comments)

    print("📤 Review completed. Posting comments...")

    pr_number = get_pr_number()
    head_sha = get_pr_head_sha()

    if final_line_comments:
        print(f"📌 Posting {len(final_line_comments)} line comments...")
        post_review_comments(
            github_token, pr_number, head_sha, final_line_comments, diff
        )

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

        context_details = "\n<details>\n<summary>🔍 Context 상세 정보</summary>\n\n"
        for pattern, files in all_context.items():
            context_details += f"**패턴: `{pattern}`**\n"
            if not files:
                context_details += "  - 매치 없음\n\n"
                continue

            for file_path, matches in files.items():
                context_details += f"  - **{file_path}**\n"
                for match in matches[:3]:
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
