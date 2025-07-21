from src import review
import os

review.review_pr(
    github_token=os.environ["GITHUB_TOKEN"],
    openai_api_key=os.environ["OPENAI_API_KEY"],
    model=os.environ.get("OPENAI_API_MODEL", "gpt-4o"),
    language=os.environ.get("LANGUAGE", "English"),
    exclude=os.environ.get("EXCLUDE", ""),
    max_recursion=int(os.environ.get("MAX_RECURSION", "3")),
)
