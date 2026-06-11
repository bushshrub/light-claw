from .base import IssueTracker
from .github import GitHubIssueTracker

__all__ = ["IssueTracker", "GitHubIssueTracker"]


def get_default_tracker() -> IssueTracker | None:
    """Return the configured tracker from env/config, or None if unconfigured."""
    from lightclaw.config import get_config
    cfg = get_config()
    if cfg.github_token:
        return GitHubIssueTracker(token=cfg.github_token, repo=cfg.issue_repo)
    return None
