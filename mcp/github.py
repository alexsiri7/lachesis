"""GitHub API client — module-level async functions using server-side PAT."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"


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
    """Create a pull request and return ``{number, url}``."""
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
    return {"number": data["number"], "url": data["html_url"], "node_id": data["node_id"]}


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


async def find_pr_branch(repo: str, head_prefix: str) -> str | None:
    """Return the head branch of the first open PR whose ref starts with *head_prefix*.

    Returns ``None`` if no matching PR is found.
    """
    try:
        for pr in await _list_open_prs(repo):
            if pr["head"]["ref"].startswith(head_prefix):
                return pr["head"]["ref"]  # type: ignore[no-any-return]
    except (KeyError, TypeError) as exc:
        logger.warning(
            "github.find_pr_branch unexpected response shape repo=%s error=%r", repo, exc
        )
    return None


async def list_lachesis_prs(repo: str) -> list[dict[str, Any]]:
    """Return all open PRs whose head branch starts with 'lachesis/'.

    Returns list of {"branch": str, "pr_number": int, "pr_url": str}.
    """
    result: list[dict[str, Any]] = []
    try:
        for pr in await _list_open_prs(repo):
            ref = pr["head"]["ref"]
            if ref.startswith("lachesis/"):
                result.append({"branch": ref, "pr_url": pr["html_url"], "pr_number": pr["number"]})
    except (KeyError, TypeError) as exc:
        logger.warning(
            "github.list_lachesis_prs unexpected response shape repo=%s error=%r", repo, exc
        )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "github.list_lachesis_prs HTTP error repo=%s status=%s; returning []",
            repo,
            exc.response.status_code,
        )
    return result


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
            "github.update_file failed repo=%s path=%s status=%s",
            repo,
            path,
            resp.status_code,
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
            "github.delete_file failed repo=%s path=%s status=%s",
            repo,
            path,
            resp.status_code,
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
