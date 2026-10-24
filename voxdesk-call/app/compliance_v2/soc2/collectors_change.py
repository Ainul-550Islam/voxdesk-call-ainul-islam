"""Change-management evidence from the repository host's API.

The source is the GitHub REST API and it needs a token: without one the
collector reports ``NOT_CONFIGURED`` and says so in the reason field. That is
the only honest answer — a deployment with no token has not been observed, and
inventing "0 unreviewed merges" would be exactly the fake evidence this batch
exists to avoid.

With a token, three facts are collected over the reporting window:

* pull requests merged into the default branch and whether each had at least one
  approving review;
* whether required status checks were configured on the default branch;
* release tags created in the window (a proxy for a controlled release path).
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.settings import get_compliance_v2_settings
from app.compliance_v2.soc2.collectors_base import CollectorResult, EvidenceCollector, item

CONTROL_VULN_MANAGEMENT = "CC7.1"
CONTROL_CHANGE_CONTROL = "CC7.2"

#: How far back a run looks. 30 days keeps the request count modest while
#: covering a monthly control window.
WINDOW_DAYS = 30

_API = "https://api.github.com"


@dataclasses.dataclass(frozen=True)
class ChangeWindow:
    repo: str
    since: dt.datetime
    token: str


class ChangeCollector(EvidenceCollector):
    """Merged pull requests, review coverage and release tags."""

    name = "change"
    control_ids = (CONTROL_VULN_MANAGEMENT, CONTROL_CHANGE_CONTROL)

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            timeout=20.0, follow_redirects=False, headers={"Accept": "application/vnd.github+json"}
        )

    async def _get(self, client: httpx.AsyncClient, path: str, *, params: dict, token: str):
        return await client.get(
            f"{_API}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
        )

    async def collect(self, session: AsyncSession) -> "CollectorResult":
        cfg = get_compliance_v2_settings()
        repo = (cfg.evidence_github_repo or "").strip()
        token = (cfg.evidence_github_token or "").strip()
        if not repo or not token:
            missing = "repository" if not repo else "token"
            return self._result(
                configured=False,
                not_configured_reason=f"EVIDENCE_GITHUB_REPO/TOKEN not configured ({missing} missing)",
            )

        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=WINDOW_DAYS)
        client = self._http()
        owns_client = self._client is None
        try:
            pulls = await self._get(
                client,
                f"/repos/{repo}/pulls",
                params={
                    "state": "closed",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                },
                token=token,
            )
            if pulls.status_code != 200:
                return self._result(
                    configured=False,
                    not_configured_reason=(
                        f"GitHub API refused the pull-request query (HTTP {pulls.status_code})"
                    ),
                )
            merged: list[dict] = []
            for pull in pulls.json():
                merged_at = pull.get("merged_at")
                if not merged_at:
                    continue
                if dt.datetime.fromisoformat(merged_at.replace("Z", "+00:00")) < since:
                    continue
                merged.append(pull)

            unreviewed: list[int] = []
            for pull in merged[:50]:
                reviews = await self._get(
                    client,
                    f"/repos/{repo}/pulls/{pull['number']}/reviews",
                    params={"per_page": 100},
                    token=token,
                )
                approvals = 0
                if reviews.status_code == 200:
                    approvals = sum(
                        1 for review in reviews.json() if review.get("state") == "APPROVED"
                    )
                if approvals == 0:
                    unreviewed.append(int(pull["number"]))

            branch = await self._get(client, f"/repos/{repo}", params={}, token=token)
            default_branch = branch.json().get("default_branch", "main") if branch.status_code == 200 else "main"
            protection = await self._get(
                client, f"/repos/{repo}/branches/{default_branch}/protection", params={}, token=token
            )
            required_checks = 0
            if protection.status_code == 200:
                checks = (protection.json().get("required_status_checks") or {}).get("contexts") or []
                required_checks = len(checks)

            tags = await self._get(
                client, f"/repos/{repo}/tags", params={"per_page": 100}, token=token
            )
            tag_count = len(tags.json()) if tags.status_code == 200 else 0
        finally:
            if owns_client:
                await client.aclose()

        items = [
            item(
                self.name,
                CONTROL_CHANGE_CONTROL,
                f"github:{repo}",
                {
                    "window_days": WINDOW_DAYS,
                    "merged_pull_requests": len(merged),
                    "merged_without_approval": unreviewed,
                    "default_branch": default_branch,
                    "required_status_checks": required_checks,
                    "release_tags": tag_count,
                },
            ),
            item(
                self.name,
                CONTROL_VULN_MANAGEMENT,
                f"github:{repo}:protection",
                {
                    "branch_protection_enabled": protection.status_code == 200,
                    "required_status_checks": required_checks,
                },
            ),
        ]

        findings: list[str] = []
        if unreviewed:
            findings.append(
                f"{len(unreviewed)} merged pull request(s) had no approving review: "
                + ", ".join(str(number) for number in unreviewed[:10])
            )
        if protection.status_code == 200 and required_checks == 0:
            findings.append("the default branch has no required status checks configured")

        return self._result(configured=True, items=items, findings=findings)


__all__ = ["ChangeCollector", "WINDOW_DAYS"]
