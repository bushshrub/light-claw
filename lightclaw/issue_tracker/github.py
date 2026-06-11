from __future__ import annotations

import asyncio

from github import Github, GithubException

from .base import IssueTracker


class GitHubIssueTracker(IssueTracker):
    def __init__(self, token: str, repo: str) -> None:
        self._token = token
        self._repo = repo  # "owner/repo"

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    @property
    def tracker_name(self) -> str:
        return "GitHub"

    async def file_issue(self, title: str, body: str) -> str:
        def _create() -> str:
            g = Github(self._token)
            repo = g.get_repo(self._repo)
            issue = repo.create_issue(title=title, body=body)
            return issue.html_url

        return await asyncio.get_event_loop().run_in_executor(None, _create)
