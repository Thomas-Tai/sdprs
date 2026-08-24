"""Poll the edge-release branch tip (GitHub API) so the dashboard can show
whether each node has an update available. Best-effort: any fetch failure
keeps the last-known tip (or None) and never raises into a request."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger("release_check")

_HTTP_TIMEOUT_S = 10.0
_service: Optional["ReleaseCheckService"] = None


def compute_update_available(node_version: Optional[str], tip_sha: Optional[str]) -> Optional[bool]:
    """None = unknown (either side missing); False = up to date; True = behind."""
    if not node_version or not tip_sha:
        return None
    return node_version != tip_sha


class ReleaseCheckService:
    def __init__(self, owner: str, repo: str, branch: str = "edge-release",
                 enabled: bool = True):
        self.owner, self.repo, self.branch = owner, repo, branch
        self.enabled = enabled
        self.tip_sha: Optional[str] = None

    @property
    def _url(self) -> str:
        return (f"https://api.github.com/repos/{self.owner}/{self.repo}"
                f"/git/refs/heads/{self.branch}")

    async def refresh(self) -> None:
        if not self.enabled:
            return
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(self._url, timeout=_HTTP_TIMEOUT_S,
                                     headers={"Accept": "application/vnd.github+json"})
                r.raise_for_status()
                sha = (r.json() or {}).get("object", {}).get("sha")
                if sha:
                    self.tip_sha = sha
                    logger.debug("edge-release tip: %s", sha)
        except Exception as e:  # network, rate-limit, malformed — keep last-known
            logger.warning("release-check refresh failed (keeping last-known): %s", e)


def init_release_check_service(settings) -> ReleaseCheckService:
    global _service
    owner, _, repo = getattr(settings, "UPDATE_RELEASE_REPO", "Thomas-Tai/sdprs").partition("/")
    _service = ReleaseCheckService(
        owner=owner, repo=repo,
        branch=getattr(settings, "UPDATE_RELEASE_BRANCH", "edge-release"),
        enabled=getattr(settings, "UPDATE_CHECK_ENABLED", True),
    )
    return _service


def get_release_check_service() -> Optional["ReleaseCheckService"]:
    return _service
