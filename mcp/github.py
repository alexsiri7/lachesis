"""GitHub API client — module-level async functions using server-side PAT."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any, Literal, TypedDict

import httpx

from derive import ActivityFailure, BlockerFacts, CheckFacts, IssueFacts, PullFacts
from settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"
_ERROR_BODY_CHARS = 500


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }


async def list_files(repo: str, path: str, *, branch: str | None = None) -> list[dict[str, Any]]:
    """List files at *path* in *repo*. Returns ``[]`` on 404.

    Pass *branch* to target a specific branch; defaults to the repo's default branch.
    If *path* resolves to a single file rather than a directory, returns a
    one-element list (GitHub Contents API returns a dict in that case).
    """
    params = {"ref": branch} if branch else {}
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            params=params,
        )
    if resp.status_code == 404:
        logger.warning("github.list_files 404 repo=%s path=%s", repo, path)
        return []
    if not resp.is_success:
        logger.warning(
            "github.list_files failed repo=%s path=%s status=%s",
            repo,
            path,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


async def get_file(repo: str, path: str, *, branch: str | None = None) -> tuple[str, str]:
    """Return ``(decoded_content, sha)`` for *path*. Raises :class:`FileNotFoundError` on 404.

    Pass *branch* to target a specific branch; defaults to the repo's default branch.
    """
    params = {"ref": branch} if branch else {}
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            params=params,
        )
    if resp.status_code == 404:
        raise FileNotFoundError(f"{path!r} not found in {repo!r}")
    if not resp.is_success:
        logger.warning(
            "github.get_file failed repo=%s path=%s status=%s",
            repo,
            path,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        raise ValueError(f"{path!r} is a directory in {repo!r}, not a file")
    raw = data.get("content")
    if raw is None:
        raise ValueError(
            f"{path!r} in {repo!r} has no inline content "
            "(may be a submodule, symlink, or file >1 MB)"
        )
    content = base64.b64decode(raw).decode("utf-8")
    return content, data["sha"]


async def create_file(
    repo: str, path: str, content: str, message: str, *, branch: str | None = None
) -> dict[str, Any]:
    """Create a new file at *path* in *repo*. Returns the raw GitHub Contents API response.

    Pass *branch* to target a specific branch; defaults to the repo's default branch.
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {"message": message, "content": encoded}
    if branch:
        payload["branch"] = branch
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.put(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json=payload,
        )
    # 409 is handled by the caller (branch-protection fallback); skip the warning
    if not resp.is_success and resp.status_code != 409:
        logger.warning(
            "github.create_file failed repo=%s path=%s status=%s",
            repo,
            path,
            resp.status_code,
        )
    resp.raise_for_status()  # propagates 409 to caller as HTTPStatusError
    return resp.json()  # type: ignore[no-any-return]


async def get_branch_sha(repo: str, branch: str) -> str:
    """Return the HEAD SHA of *branch*. Raises :class:`FileNotFoundError` on 404."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/git/ref/heads/{branch}",
            headers=_headers(),
        )
    if resp.status_code == 404:
        raise FileNotFoundError(f"Branch {branch!r} not found in {repo!r}")
    if not resp.is_success:
        logger.warning(
            "github.get_branch_sha failed repo=%s branch=%s status=%s",
            repo,
            branch,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return data["object"]["sha"]  # type: ignore[no-any-return]


async def create_branch(repo: str, branch_name: str, sha: str) -> None:
    """Create a new branch pointing at *sha*."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            f"{_BASE_URL}/repos/{repo}/git/refs",
            headers=_headers(),
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
    if not resp.is_success:
        logger.warning(
            "github.create_branch failed repo=%s branch=%s status=%s",
            repo,
            branch_name,
            resp.status_code,
        )
    resp.raise_for_status()


async def create_pull_request(
    repo: str, title: str, body: str, head: str, base: str
) -> dict[str, Any]:
    """Create a pull request and return ``{number, url, node_id, created_at}``."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            f"{_BASE_URL}/repos/{repo}/pulls",
            headers=_headers(),
            json={"title": title, "body": body, "head": head, "base": base},
        )
    if not resp.is_success:
        logger.warning(
            "github.create_pull_request failed repo=%s head=%s status=%s",
            repo,
            head,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {
        "number": data["number"],
        "url": data["html_url"],
        "node_id": data["node_id"],
        "created_at": data.get("created_at"),
    }


async def get_pull_request(repo: str, pr_number: int) -> dict[str, Any] | None:
    """Return pull request fields for *pr_number*, or ``None`` on 404.

    Returned keys: ``number``, ``title``, ``url``, ``state`` (``"open"``/``"closed"``),
    ``merged``, ``merged_at``, ``mergeable``, ``mergeable_state``, ``head_ref``,
    ``base_ref``, ``draft``.

    ``mergeable`` and ``mergeable_state`` are null until GitHub finishes computing
    mergeability in the background, so a null there means "not known yet", not "blocked".
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/pulls/{pr_number}",
            headers=_headers(),
        )
    if resp.status_code == 404:
        logger.debug("github.get_pull_request 404 repo=%s pr=%s", repo, pr_number)
        return None
    if not resp.is_success:
        logger.warning(
            "github.get_pull_request failed repo=%s pr=%s status=%s",
            repo,
            pr_number,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {
        "number": data["number"],
        "title": data["title"],
        "url": data["html_url"],
        "state": data["state"],  # "open" | "closed"
        "merged": data["merged"],
        "merged_at": data.get("merged_at"),
        "mergeable": data.get("mergeable"),
        "mergeable_state": data.get("mergeable_state"),
        "head_ref": data["head"]["ref"],
        "base_ref": data["base"]["ref"],
        "draft": data["draft"],
    }


async def _list_open_prs(repo: str) -> list[dict[str, Any]]:
    """Return raw PR objects for all open PRs in *repo* (up to 100)."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/pulls",
            headers=_headers(),
            params={"state": "open", "per_page": 100},
        )
    if not resp.is_success:
        logger.warning("github._list_open_prs failed repo=%s status=%s", repo, resp.status_code)
        resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def list_open_issues(repo: str) -> list[dict[str, Any]]:
    """Return ``[{"number": int, "title": str, "labels": [str]}, ...]``, ascending by number.

    The /issues endpoint also returns pull requests; those are filtered out.
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/issues",
            headers=_headers(),
            params={"state": "open", "per_page": 100},
        )
    if not resp.is_success:
        logger.warning("github.list_open_issues failed repo=%s status=%s", repo, resp.status_code)
        resp.raise_for_status()
    issues = [item for item in resp.json() if "pull_request" not in item]
    return sorted(
        (
            {
                "number": i["number"],
                "title": i["title"],
                "labels": [lb["name"] for lb in i.get("labels", [])],
            }
            for i in issues
        ),
        key=lambda i: int(i["number"]),
    )


async def find_open_pr(repo: str, head_prefix: str) -> dict[str, Any] | None:
    """Return the first open PR whose head branch starts with *head_prefix*.

    Returns ``{"branch": str, "pr_number": int, "pr_url": str, "created_at": str | None}``,
    or ``None`` when no open PR matches.  An HTTP failure propagates rather than reading
    as ``None``: a lookup that could not run is not the same fact as a requirement that
    has no PR.
    """
    try:
        for pr in await _list_open_prs(repo):
            if pr["head"]["ref"].startswith(head_prefix):
                return {
                    "branch": pr["head"]["ref"],
                    "pr_url": pr["html_url"],
                    "pr_number": pr["number"],
                    "created_at": pr.get("created_at"),
                }
    except (KeyError, TypeError) as exc:
        logger.warning("github.find_open_pr unexpected response shape repo=%s error=%r", repo, exc)
    return None


async def list_lachesis_prs(repo: str) -> list[dict[str, Any]]:
    """Return all open PRs whose head branch starts with 'lachesis/'.

    Returns list of {"branch": str, "pr_number": int, "pr_url": str, "created_at": str | None}.

    ``created_at`` is read leniently: a payload that omits it still reports its PR,
    since the surrounding ``except KeyError`` would otherwise discard every PR in the
    repo over one missing timestamp.  An HTTP failure, by contrast, propagates rather
    than reading as an empty list: a lookup that could not run is not the same fact as
    a repo with no Lachesis pull requests.
    """
    result: list[dict[str, Any]] = []
    try:
        for pr in await _list_open_prs(repo):
            ref = pr["head"]["ref"]
            if ref.startswith("lachesis/"):
                result.append(
                    {
                        "branch": ref,
                        "pr_url": pr["html_url"],
                        "pr_number": pr["number"],
                        "created_at": pr.get("created_at"),
                    }
                )
    except (KeyError, TypeError) as exc:
        logger.warning(
            "github.list_lachesis_prs unexpected response shape repo=%s error=%r", repo, exc
        )
    return result


async def list_labelled_prs(repo: str, label: str) -> list[dict[str, Any]]:
    """Return ``[{"number": int, "title": str, "url": str}, ...]`` for open PRs labelled *label*."""
    return [
        {"number": pr["number"], "title": pr["title"], "url": pr["html_url"]}
        for pr in await _list_open_prs(repo)
        if any(lb["name"] == label for lb in pr.get("labels", []))
    ]


async def list_pull_request_files(repo: str, pr_number: int) -> list[str]:
    """Return the paths of the files changed by *pr_number* (up to 100).

    A renamed file contributes both its old and new path.
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/pulls/{pr_number}/files",
            headers=_headers(),
            params={"per_page": 100},
        )
    if not resp.is_success:
        logger.warning(
            "github.list_pull_request_files failed repo=%s pr=%s status=%s",
            repo,
            pr_number,
            resp.status_code,
        )
        resp.raise_for_status()
    paths: list[str] = []
    for f in resp.json():
        paths.append(f["filename"])
        if "previous_filename" in f:
            paths.append(f["previous_filename"])
    return paths


async def update_file(
    repo: str, path: str, content: str, sha: str, message: str, *, branch: str | None = None
) -> dict[str, Any]:
    """Update an existing file at *path* (requires current *sha*).

    Pass *branch* to target a specific branch; defaults to the repo's default branch.
    Returns the raw GitHub Contents API response.
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    payload: dict[str, Any] = {"message": message, "content": encoded, "sha": sha}
    if branch:
        payload["branch"] = branch
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.put(
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json=payload,
        )
    if not resp.is_success:
        logger.warning(
            "github.update_file failed repo=%s path=%s status=%s body=%s",
            repo,
            path,
            resp.status_code,
            resp.text[:_ERROR_BODY_CHARS],
        )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def delete_file(
    repo: str, path: str, sha: str, message: str, *, branch: str | None = None
) -> None:
    """Delete the file at *path* (requires current *sha*).

    Pass *branch* to target a specific branch; defaults to the repo's default branch.
    """
    payload: dict[str, Any] = {"message": message, "sha": sha}
    if branch:
        payload["branch"] = branch
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.request(
            "DELETE",
            f"{_BASE_URL}/repos/{repo}/contents/{path}",
            headers=_headers(),
            json=payload,
        )
    if not resp.is_success:
        logger.warning(
            "github.delete_file failed repo=%s path=%s status=%s body=%s",
            repo,
            path,
            resp.status_code,
            resp.text[:_ERROR_BODY_CHARS],
        )
    resp.raise_for_status()


async def create_issue(
    repo: str, title: str, body: str, labels: list[str] | None = None
) -> dict[str, Any]:
    """Create an issue and return ``{number, title, url}``."""
    payload: dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            f"{_BASE_URL}/repos/{repo}/issues",
            headers=_headers(),
            json=payload,
        )
    if not resp.is_success:
        logger.warning(
            "github.create_issue failed repo=%s status=%s",
            repo,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {
        "number": data["number"],
        "id": data["id"],
        "title": data["title"],
        "url": data["html_url"],
        "node_id": data["node_id"],
    }


async def get_issue(repo: str, issue_number: int) -> dict[str, Any] | None:
    """Return issue fields for *issue_number*, or ``None`` on 404.

    Returned keys: ``number``, ``id``, ``title``, ``state`` (``"open"``/``"closed"``),
    ``body``, ``assignees`` (list of logins), ``labels`` (list of names),
    ``linked_pr`` (html_url of the linked PR or ``None``), ``url``.
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/issues/{issue_number}",
            headers=_headers(),
        )
    if resp.status_code == 404:
        return None
    if not resp.is_success:
        logger.warning(
            "github.get_issue failed repo=%s issue=%s status=%s",
            repo,
            issue_number,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {
        "number": data["number"],
        "id": data["id"],
        "title": data["title"],
        "state": data["state"],  # "open" | "closed"
        "body": data.get("body") or "",
        "assignees": [a["login"] for a in data.get("assignees", [])],
        "labels": [lb["name"] for lb in data.get("labels", [])],
        "linked_pr": data.get("pull_request", {}).get("html_url"),
        "url": data["html_url"],
    }


async def update_issue(
    repo: str, issue_number: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    """PATCH an issue's title, body, and/or state. Returns None on 404.

    Accepted keys in *fields*: ``title``, ``body``, ``state`` (``"open"`` or ``"closed"``).
    Returned keys: ``number``, ``title``, ``url``.
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.patch(
            f"{_BASE_URL}/repos/{repo}/issues/{issue_number}",
            headers=_headers(),
            json=fields,
        )
    if resp.status_code == 404:
        return None
    if not resp.is_success:
        logger.warning(
            "github.update_issue failed repo=%s issue=%s status=%s",
            repo,
            issue_number,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {
        "number": data["number"],
        "title": data["title"],
        "url": data["html_url"],
    }


async def add_to_project(project_id: str, issue_node_id: str) -> None:
    """Add an issue to a GitHub Projects v2 board."""
    query = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item { id }
      }
    }
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            "https://api.github.com/graphql",
            headers=_headers(),
            json={
                "query": query,
                "variables": {"projectId": project_id, "contentId": issue_node_id},
            },
        )
    if not resp.is_success:
        logger.warning(
            "github.add_to_project failed project=%s status=%s",
            project_id,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        logger.warning("github.add_to_project graphql errors: %s", data["errors"])


async def add_issue_dependency(repo: str, issue_number: int, blocked_by_id: int) -> None:
    """Mark issue_number as blocked by the issue with database id blocked_by_id."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            f"{_BASE_URL}/repos/{repo}/issues/{issue_number}/dependencies/blocked_by",
            headers=_headers(),
            json={"issue_id": blocked_by_id},
        )
    if not resp.is_success:
        logger.warning(
            "github.add_issue_dependency failed repo=%s issue=%s status=%s",
            repo,
            issue_number,
            resp.status_code,
        )
    resp.raise_for_status()


async def get_issue_dependencies(repo: str, issue_number: int) -> dict[str, Any] | None:
    """Return blocked_by and blocking issue numbers for *issue_number*, or ``None`` on 404.

    Returned keys: ``blocked_by`` (list of ints), ``blocking`` (list of ints).
    """
    base = f"{_BASE_URL}/repos/{repo}/issues/{issue_number}/dependencies"
    async with httpx.AsyncClient(timeout=10.0) as http:
        blocked_by_resp, blocking_resp = await asyncio.gather(
            http.get(f"{base}/blocked_by", headers=_headers()),
            http.get(f"{base}/blocking", headers=_headers()),
        )
    for resp in (blocked_by_resp, blocking_resp):
        if resp.status_code == 404:
            logger.debug(
                "github.get_issue_dependencies repo=%s issue=%s: 404 (not found)",
                repo,
                issue_number,
            )
            return None
        if not resp.is_success:
            logger.warning(
                "github.get_issue_dependencies failed repo=%s issue=%s status=%s",
                repo,
                issue_number,
                resp.status_code,
            )
        resp.raise_for_status()
    try:
        return {
            "blocked_by": [item["number"] for item in blocked_by_resp.json()],
            "blocking": [item["number"] for item in blocking_resp.json()],
        }
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "github.get_issue_dependencies unexpected response shape repo=%s issue=%s error=%r",
            repo,
            issue_number,
            exc,
        )
        raise


async def list_blocked_by_numbers(repo: str, issue_number: int) -> list[int] | None:
    """Return the issue numbers blocking *issue_number*, or ``None`` on 404.

    Half the requests of :func:`get_issue_dependencies` for callers that only need
    the blocking direction.
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}/issues/{issue_number}/dependencies/blocked_by",
            headers=_headers(),
        )
    if resp.status_code == 404:
        return None
    if not resp.is_success:
        logger.warning(
            "github.list_blocked_by_numbers failed repo=%s issue=%s status=%s",
            repo,
            issue_number,
            resp.status_code,
        )
    resp.raise_for_status()
    return [item["number"] for item in resp.json()]


async def get_repo_settings(repo: str) -> dict[str, Any]:
    """Return merge/automerge settings for *repo*.

    Returned keys: ``allow_auto_merge`` (bool), ``allow_squash_merge`` (bool),
    ``allow_merge_commit`` (bool), ``allow_rebase_merge`` (bool),
    ``delete_branch_on_merge`` (bool).
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            f"{_BASE_URL}/repos/{repo}",
            headers=_headers(),
        )
    if not resp.is_success:
        logger.warning(
            "github.get_repo_settings failed repo=%s status=%s",
            repo,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    return {
        "allow_auto_merge": data.get("allow_auto_merge", False),
        "allow_squash_merge": data.get("allow_squash_merge", True),
        "allow_merge_commit": data.get("allow_merge_commit", True),
        "allow_rebase_merge": data.get("allow_rebase_merge", True),
        "delete_branch_on_merge": data.get("delete_branch_on_merge", False),
    }


async def enable_automerge(pr_node_id: str, merge_method: str = "SQUASH") -> None:
    """Enable automerge on a pull request via GraphQL.

    *merge_method* must be ``"SQUASH"``, ``"MERGE"``, or ``"REBASE"``.

    Raises :class:`httpx.HTTPStatusError` on HTTP-level failure (non-2xx response).
    Raises :class:`RuntimeError` on GraphQL-level errors (HTTP 200 but
    ``data["errors"]`` is present, e.g. insufficient permissions or ineligible PR).
    """
    query = """
    mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
      enablePullRequestAutoMerge(input: {
        pullRequestId: $pullRequestId,
        mergeMethod: $mergeMethod
      }) {
        pullRequest {
          autoMergeRequest {
            enabledAt
          }
        }
      }
    }
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            "https://api.github.com/graphql",
            headers=_headers(),
            json={
                "query": query,
                "variables": {
                    "pullRequestId": pr_node_id,
                    "mergeMethod": merge_method,
                },
            },
        )
    if not resp.is_success:
        logger.warning(
            "github.enable_automerge failed pr_node_id=%s status=%s",
            pr_node_id,
            resp.status_code,
        )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        logger.warning("github.enable_automerge graphql errors: %s", data["errors"])
        raise RuntimeError(f"GraphQL errors enabling automerge: {data['errors']}")


_ISSUE_ACTIVITY_CHUNK = 25

_ISSUE_ACTIVITY_FRAGMENT = """
fragment activity on Issue {
  number
  state
  updatedAt
  closedAt
  labels(first: 20) { nodes { name } }
  blockedBy(first: 10) {
    nodes { number state updatedAt repository { nameWithOwner } }
  }
  timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
    nodes { ... on ClosedEvent { closer { __typename } } }
  }
  closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
    nodes {
      number
      state
      merged
      updatedAt
      mergedAt
      mergeCommit { oid }
      isDraft
      commits(last: 1) {
        nodes {
          commit {
            statusCheckRollup {
              contexts(first: 20) {
                nodes {
                  __typename
                  ... on CheckRun { conclusion completedAt }
                  ... on StatusContext { state createdAt }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def _issue_activity_query(numbers: list[int]) -> str:
    aliases = "\n".join(f"    i{n}: issue(number: {n}) {{ ...activity }}" for n in numbers)
    return (
        "query($owner: String!, $name: String!) {\n"
        "  repository(owner: $owner, name: $name) {\n"
        f"{aliases}\n"
        "  }\n"
        "}\n" + _ISSUE_ACTIVITY_FRAGMENT
    )


def _normalize_checks(pull: dict[str, Any]) -> list[CheckFacts]:
    commits = (pull.get("commits") or {}).get("nodes") or []
    if not commits:
        return []
    rollup = (commits[0].get("commit") or {}).get("statusCheckRollup")
    if not rollup:
        return []
    checks: list[CheckFacts] = []
    for node in (rollup.get("contexts") or {}).get("nodes") or []:
        if node.get("__typename") == "CheckRun":
            checks.append({"conclusion": node.get("conclusion"), "at": node.get("completedAt")})
        elif node.get("__typename") == "StatusContext":
            checks.append({"conclusion": node.get("state"), "at": node.get("createdAt")})
    return checks


def _normalize_pull(pull: dict[str, Any]) -> PullFacts:
    return {
        "number": pull["number"],
        "state": pull["state"],
        "merged": bool(pull.get("merged")),
        "updated_at": pull.get("updatedAt"),
        "merged_at": pull.get("mergedAt"),
        "merge_commit": (pull.get("mergeCommit") or {}).get("oid"),
        "is_draft": bool(pull.get("isDraft")),
        "checks": _normalize_checks(pull),
    }


def _normalize_blockers(issue: dict[str, Any]) -> list[BlockerFacts]:
    """Return the still-open issues this one declares itself blocked by.

    Issue dependencies cross repositories, so a blocker is identified by its
    repo as well as its number.  A closed blocker constrains nothing and is
    dropped here rather than at every call site.
    """
    return [
        {
            "repo": (node.get("repository") or {}).get("nameWithOwner", ""),
            "number": node["number"],
            "last_activity": node.get("updatedAt"),
        }
        for node in (issue.get("blockedBy") or {}).get("nodes") or []
        if node.get("state") == "OPEN"
    ]


def _normalize_issue(issue: dict[str, Any]) -> IssueFacts:
    closed_events = (issue.get("timelineItems") or {}).get("nodes") or []
    closer = (closed_events[-1].get("closer") or {}) if closed_events else {}
    pulls = (issue.get("closedByPullRequestsReferences") or {}).get("nodes") or []
    return {
        "number": issue["number"],
        "state": issue["state"],
        "updated_at": issue.get("updatedAt"),
        "labels": [label["name"] for label in (issue.get("labels") or {}).get("nodes") or []],
        "closed_by_commit": closer.get("__typename") == "Commit",
        "closed_at": issue.get("closedAt"),
        "blocked_by": _normalize_blockers(issue),
        "pulls": [_normalize_pull(pull) for pull in pulls],
    }


_ISSUE_ALIAS = re.compile(r"i\d+")

_FAILURE_RANK: dict[ActivityFailure, int] = {"rate_limited": 0, "inaccessible": 1, "transient": 2}


class IssueActivity(TypedDict):
    """One repo's issue facts, and the worst thing that stopped the rest being read."""

    facts: dict[int, IssueFacts]
    failure: ActivityFailure | None


def _worse(current: ActivityFailure | None, candidate: ActivityFailure) -> ActivityFailure:
    """The failure worth reporting of two, so the answer does not depend on chunk order."""
    if current is None:
        return candidate
    return min(current, candidate, key=_FAILURE_RANK.__getitem__)


def _missing_issue(error: dict[str, Any]) -> bool:
    """True when an error means only "no such issue", which the null node already says.

    GitHub reports a number that is a pull request, or an issue since deleted or
    transferred, as a ``NOT_FOUND`` under that issue's own alias.  A repo it
    cannot see is a ``NOT_FOUND`` at ``["repository"]``, which this leaves alone.
    """
    path = error.get("path") or []
    return (
        error.get("type") == "NOT_FOUND"
        and len(path) == 2
        and path[0] == "repository"
        and bool(_ISSUE_ALIAS.fullmatch(str(path[1])))
    )


def _graphql_failure(errors: list[dict[str, Any]]) -> ActivityFailure | None:
    """Classify what is left of *errors* once the merely-missing issues are dropped.

    Dropping those first is load-bearing: classified the other way round, one
    linked number that is a pull request marks its whole repo unreadable.
    """
    remaining = [error for error in errors if not _missing_issue(error)]
    if not remaining:
        return None
    types = {error.get("type") for error in remaining}
    if "RATE_LIMITED" in types:
        return "rate_limited"
    if types & {"NOT_FOUND", "FORBIDDEN"}:
        return "inaccessible"
    return "transient"


def _response_failure(resp: httpx.Response) -> ActivityFailure:
    """Classify an unsuccessful response, separating spent quota from refusal."""
    if resp.status_code == 429 or (
        resp.status_code == 403
        and ("retry-after" in resp.headers or resp.headers.get("x-ratelimit-remaining") == "0")
    ):
        return "rate_limited"
    if resp.status_code in (401, 403, 404):
        return "inaccessible"
    return "transient"


def failure_from_exception(exc: BaseException) -> ActivityFailure:
    """Classify what stopped a read when the exception is all that is left to go on.

    Anything this cannot name is ``transient`` deliberately: an unrecognised
    failure must not read as a permanent verdict about the repo.  That covers
    :class:`FileNotFoundError`, which :func:`get_file` raises instead of an HTTP
    error when a file listed a moment earlier is already gone.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return _response_failure(exc.response)
    return "transient"


async def _read_issue_chunk(
    http: httpx.AsyncClient, repo: str, chunk: list[int]
) -> tuple[dict[int, IssueFacts], ActivityFailure | None]:
    """Read one aliased batch, returning the issues it yielded and what it could not."""
    owner, _, name = repo.partition("/")
    try:
        resp = await http.post(
            "https://api.github.com/graphql",
            headers=_headers(),
            json={
                "query": _issue_activity_query(chunk),
                "variables": {"owner": owner, "name": name},
            },
        )
    except httpx.RequestError as exc:
        logger.warning("github.get_issue_activity unreachable repo=%s error=%r", repo, exc)
        return {}, "transient"
    if not resp.is_success:
        refused = _response_failure(resp)
        logger.warning(
            "github.get_issue_activity failed repo=%s status=%s cause=%s",
            repo,
            resp.status_code,
            refused,
        )
        return {}, refused
    data = resp.json()
    errors = data.get("errors") or []
    failure = _graphql_failure(errors)
    if failure is not None:
        logger.warning(
            "github.get_issue_activity graphql errors repo=%s cause=%s: %s", repo, failure, errors
        )
    repository = (data.get("data") or {}).get("repository")
    if repository is None:
        logger.warning("github.get_issue_activity: no repository for repo=%s", repo)
        return {}, _worse(failure, "inaccessible")
    return {node["number"]: _normalize_issue(node) for node in repository.values() if node}, failure


async def get_issue_activity(repo: str, numbers: list[int]) -> IssueActivity:
    """Return activity facts for *numbers* keyed by issue number, and what failed.

    Issues are fetched in aliased batches, so a whole repo's requirements cost a
    request or two instead of one per requirement, and an empty *numbers* costs
    none.  Issues that no longer exist are absent from ``facts``.

    Unlike the rest of this module this never raises when GitHub cannot be read:
    a caller reporting derived status degrades to saying so rather than failing
    the listing it was really asked for.  So ``facts`` keeps whatever the
    readable batches yielded and ``failure`` names the worst cause seen — and a
    cause no retry can improve stops the remaining batches instead of spending
    quota to fail identically.
    """
    if not numbers:
        return {"facts": {}, "failure": None}
    facts: dict[int, IssueFacts] = {}
    failure: ActivityFailure | None = None
    async with httpx.AsyncClient(timeout=10.0) as http:
        for start in range(0, len(numbers), _ISSUE_ACTIVITY_CHUNK):
            chunk = numbers[start : start + _ISSUE_ACTIVITY_CHUNK]
            read, chunk_failure = await _read_issue_chunk(http, repo, chunk)
            facts.update(read)
            if chunk_failure is not None:
                failure = _worse(failure, chunk_failure)
                if chunk_failure != "transient":
                    break
    return {"facts": facts, "failure": failure}


_DEPLOYMENT_PAGE_SIZE = 100
_DEPLOYMENT_SCAN_LIMIT = 5
_STATUS_SCAN_LIMIT = 10
_TERMINAL_STATES = frozenset({"success", "failure", "error"})

DeploymentState = Literal["success", "failure", "pending"]


class DeploymentVerdict(TypedDict):
    """What the production deployments for one commit currently say."""

    state: DeploymentState
    url: str | None


async def get_production_deployment(repo: str, sha: str) -> DeploymentVerdict | None:
    """Return what production says about *sha*, or ``None`` when it says nothing yet.

    Only deployments GitHub marks ``production_environment`` and whose task is a
    deploy are considered, so a pull request preview is never announced as
    production.  A success anywhere among them wins over a sibling failure:
    burning a requirement's single notification on a deployment that was retried
    successfully is the worse mistake, and a state that is still moving reports
    ``pending`` so the next read can decide instead.

    ``inactive`` statuses are skipped rather than read as an outcome.  GitHub
    appends one to a deployment whenever a later deployment supersedes it, so
    the newest status of a deployment that succeeded hours ago is routinely
    ``inactive`` — reading it as "still moving" would leave a landed
    requirement owed a notification indefinitely.

    Like :func:`get_issue_activity` this degrades rather than raises: a caller
    here is deciding whether to make a claim, and an unreadable GitHub means it
    must make none.  That extends to a sibling deployment whose statuses could
    not be read: a ``failure`` computed without it could be contradicted by the
    retry it was hiding, so the whole answer is withheld instead.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                f"{_BASE_URL}/repos/{repo}/deployments",
                headers=_headers(),
                params={"sha": sha, "per_page": _DEPLOYMENT_PAGE_SIZE},
            )
            if not resp.is_success:
                logger.warning(
                    "github.get_production_deployment failed repo=%s sha=%s status=%s",
                    repo,
                    sha,
                    resp.status_code,
                )
                return None
            deployments = resp.json()
            production = [
                d
                for d in deployments
                if d.get("production_environment") and d.get("task") == "deploy"
            ]
            if not production:
                if deployments:
                    logger.info(
                        "github.get_production_deployment: repo=%s sha=%s has only "
                        "non-production deployments in environments %s",
                        repo,
                        sha,
                        sorted({str(d.get("environment")) for d in deployments}),
                    )
                return None

            verdict: DeploymentVerdict | None = None
            unreadable = False
            for deployment in production[:_DEPLOYMENT_SCAN_LIMIT]:
                status_resp = await http.get(
                    f"{_BASE_URL}/repos/{repo}/deployments/{deployment['id']}/statuses",
                    headers=_headers(),
                    params={"per_page": _STATUS_SCAN_LIMIT},
                )
                if not status_resp.is_success:
                    logger.warning(
                        "github.get_production_deployment statuses failed repo=%s id=%s status=%s",
                        repo,
                        deployment["id"],
                        status_resp.status_code,
                    )
                    unreadable = True
                    continue
                outcome = next(
                    (s for s in status_resp.json() if s.get("state") in _TERMINAL_STATES),
                    None,
                )
                if outcome is None:
                    if verdict is None:
                        verdict = {"state": "pending", "url": None}
                    continue
                url = outcome.get("log_url") or outcome.get("target_url")
                if outcome["state"] == "success":
                    return {"state": "success", "url": url}
                verdict = {"state": "failure", "url": url}
            if unreadable:
                return None
    except Exception:
        logger.warning(
            "github.get_production_deployment could not read repo=%s sha=%s",
            repo,
            sha,
            exc_info=True,
        )
        return None

    return verdict
