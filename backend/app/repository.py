"""公开 GitHub 仓库的目录检查、选择性只读导入与社区代码阅读。"""
import asyncio
import shutil
from functools import wraps
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import auth, community, db
from .config import REPOSITORIES_DIR

router = APIRouter(prefix="/api/repositories")
MAX_IMPORT_FILES = 100
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024
MAX_TREE_FILES = 2000
TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml",
    ".html", ".css", ".scss", ".sql", ".sh", ".bash", ".ps1", ".toml", ".ini", ".cfg",
    ".csv", ".xml", ".svg", ".java", ".c", ".h", ".cpp", ".cs", ".go", ".rs", ".rb",
    ".php", ".vue", ".svelte", ".gitignore", ".dockerfile", ".example",
}
EXCLUDED_PARTS = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".pytest_cache", ".next", "coverage"}
LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".html": "html", ".css": "css",
    ".md": "markdown", ".sql": "sql", ".sh": "bash", ".xml": "xml", ".svg": "svg",
}


def safe_path(value: str) -> str:
    value = value.replace("\\", "/").strip().strip("/")
    path = PurePosixPath(value)
    if not value or value.startswith("/") or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("仓库文件路径无效")
    return "/".join(path.parts)


def parse_github_url(value: str) -> tuple[str, str, str]:
    parsed = urlsplit(value.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or len(parts) < 2:
        raise ValueError("目前仅支持 https://github.com/所有者/仓库 的公开仓库链接")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not owner or not repo or any(x in owner + repo for x in ("..", "%", " ")):
        raise ValueError("GitHub 仓库地址无效")
    return owner, repo, f"https://github.com/{owner}/{repo}"


def _sensitive_path(path: str) -> bool:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if name.startswith(".env") or name in {"id_rsa", "id_ed25519", "credentials", "secrets", "secret.json"}:
        return True
    return name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")) or "private_key" in name


def _allowed_file(item: dict) -> bool:
    path = item.get("path", "")
    try:
        path = safe_path(path)
    except ValueError:
        return False
    parts = set(path.split("/"))
    if parts & EXCLUDED_PARTS or _sensitive_path(path):
        return False
    if int(item.get("size") or 0) > MAX_FILE_BYTES:
        return False
    suffix = Path(path).suffix.lower()
    name = path.rsplit("/", 1)[-1].lower()
    return suffix in TEXT_EXTENSIONS or name in {"readme", "license", "dockerfile", "makefile"} or "." not in name


def _language(path: str) -> str:
    return LANGUAGE_BY_EXTENSION.get(Path(path).suffix.lower(), "text")


def _recommended(files: list[dict]) -> list[str]:
    prefixes = ("readme", "docs/", "src/", "tests/", "backend/app/", "frontend/src/")
    selected = [item["path"] for item in files if item["path"].lower().startswith(prefixes)]
    return selected[:MAX_IMPORT_FILES]


class GitHubClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=25, follow_redirects=False, transport=self.transport,
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "JOJO-Director-Community"})

    async def _json(self, client: httpx.AsyncClient, url: str) -> dict:
        response = await client.get(url)
        if response.status_code == 403:
            raise ValueError("GitHub 请求频率已达上限，请稍后再试")
        if response.status_code == 404:
            raise ValueError("仓库或分支不存在；私有仓库不能使用公开导入")
        if response.status_code != 200:
            raise ValueError(f"GitHub 读取失败（HTTP {response.status_code}）")
        return response.json()

    async def inspect(self, source_url: str, branch: str = "") -> dict:
        owner, repo, normalized_url = parse_github_url(source_url)
        async with self._client() as client:
            metadata = await self._json(client, f"https://api.github.com/repos/{owner}/{repo}")
            if metadata.get("private"):
                raise ValueError("该仓库为私有仓库，请后续使用私有授权导入")
            selected_branch = branch.strip() or metadata["default_branch"]
            branch_data = await self._json(client,
                f"https://api.github.com/repos/{owner}/{repo}/branches/{quote(selected_branch, safe='')}")
            commit_sha = branch_data["commit"]["sha"]
            tree = await self._json(client,
                f"https://api.github.com/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1")
        files = [{"path": item["path"], "size": int(item.get("size") or 0),
                  "sha": item.get("sha", ""), "language": _language(item["path"])}
                 for item in tree.get("tree", []) if item.get("type") == "blob" and _allowed_file(item)]
        files.sort(key=lambda item: item["path"])
        return {"sourceUrl": normalized_url, "owner": owner, "repo": repo,
                "fullName": f"{owner}/{repo}", "defaultBranch": metadata["default_branch"],
                "branch": selected_branch, "commitSha": commit_sha,
                "description": metadata.get("description") or "",
                "files": files[:MAX_TREE_FILES], "fileCount": len(files),
                "truncated": bool(tree.get("truncated")) or len(files) > MAX_TREE_FILES,
                "recommendedPaths": _recommended(files)}

    async def download(self, client: httpx.AsyncClient, owner: str, repo: str,
                       commit_sha: str, path: str) -> tuple[bytes, str]:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha}/{quote(path, safe='/')}"
        chunks = []
        size = 0
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ValueError(f"下载仓库文件失败：{path}（HTTP {response.status_code}）")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError(f"文件超过单文件上限：{path}")
                chunks.append(chunk)
        content = b"".join(chunks)
        if b"\x00" in content:
            raise ValueError(f"二进制文件不支持代码预览：{path}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"文件不是 UTF-8 文本：{path}") from exc
        return content, text


github_client = GitHubClient()


def _repository_view(row: dict) -> dict:
    return {**row, "fullName": f"{row['owner']}/{row['repo']}"}


def repository_for_contribution(contribution_id: str) -> dict | None:
    rows = db.query("code_repositories", "contribution_id=?", (contribution_id,))
    return _repository_view(rows[0]) if rows else None


async def import_repository(payload: dict, session: dict) -> dict:
    community_id = str(payload.get("community_id") or "")
    community._member(community_id, session)
    selected_paths = list(dict.fromkeys(safe_path(path) for path in payload.get("paths") or []))
    if not selected_paths or len(selected_paths) > MAX_IMPORT_FILES:
        raise ValueError(f"请选择 1–{MAX_IMPORT_FILES} 个文件")
    inspection = await github_client.inspect(str(payload.get("source_url") or ""),
                                             str(payload.get("branch") or ""))
    allowed = {item["path"]: item for item in inspection["files"]}
    unknown = [path for path in selected_paths if path not in allowed]
    if unknown:
        raise ValueError(f"所选文件不在可导入清单中：{unknown[0]}")
    total_bytes = sum(allowed[path]["size"] for path in selected_paths)
    if total_bytes > MAX_IMPORT_BYTES:
        raise ValueError("所选文件总大小超过 10MB，请减少导入范围")
    owner, repo = inspection["owner"], inspection["repo"]
    commit_sha = inspection["commitSha"]
    downloaded = []
    async with github_client._client() as client:
        semaphore = asyncio.Semaphore(4)
        async def fetch(path: str):
            async with semaphore:
                content, text = await github_client.download(client, owner, repo, commit_sha, path)
                downloaded.append({"path": path, "content": content, "text": text,
                                   "sha": allowed[path]["sha"], "language": allowed[path]["language"]})
        await asyncio.gather(*(fetch(path) for path in selected_paths))
    snapshot_id = db.new_id("repo")
    root = (REPOSITORIES_DIR / snapshot_id).resolve()
    root.mkdir(parents=True, exist_ok=False)
    try:
        for item in downloaded:
            target = (root / safe_path(item["path"])).resolve()
            if root not in target.parents:
                raise ValueError("仓库文件保存路径无效")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item["content"])
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    contribution = community.create_text_contribution(community_id, {
        "kind": "repository",
        "title": f"{owner}/{repo} · {inspection['branch']}",
        "body": f"公开仓库：{inspection['sourceUrl']}\n分支：{inspection['branch']}\nCommit：{commit_sha}\n导入选定文件 {len(downloaded)} 个，只读快照，不执行仓库代码。",
    }, session)
    actor_id, author_name = community._actor(session)
    db.insert("code_repositories", {
        "id": snapshot_id, "community_id": community_id, "contribution_id": contribution["id"],
        "source_url": inspection["sourceUrl"], "owner": owner, "repo": repo,
        "branch": inspection["branch"], "default_branch": inspection["defaultBranch"],
        "commit_sha": commit_sha, "file_count": len(downloaded),
        "total_bytes": sum(len(item["content"]) for item in downloaded),
        "author_id": actor_id, "author_name": author_name, "created_at": db.now(),
    })
    for item in downloaded:
        db.insert("code_repository_files", {
            "id": db.new_id("repofile"), "repository_id": snapshot_id, "path": item["path"],
            "size": len(item["content"]), "lines": len(item["text"].splitlines()),
            "language": item["language"], "sha": item["sha"],
        })
    return {"repository": _repository_view(db.get("code_repositories", snapshot_id)),
            "contribution": contribution, "inspection": inspection}


def list_files(repository_id: str, session: dict) -> list[dict]:
    row = db.get("code_repositories", repository_id)
    if not row:
        raise ValueError("代码仓库快照不存在")
    community._member(row["community_id"], session)
    return db.query("code_repository_files", "repository_id=? ORDER BY path", (repository_id,))


def read_file(repository_id: str, path: str, session: dict) -> dict:
    row = db.get("code_repositories", repository_id)
    if not row:
        raise ValueError("代码仓库快照不存在")
    community._member(row["community_id"], session)
    path = safe_path(path)
    metadata = db.query("code_repository_files", "repository_id=? AND path=?", (repository_id, path))
    if not metadata:
        raise ValueError("该文件不在只读快照中")
    root = (REPOSITORIES_DIR / repository_id).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise ValueError("仓库文件不存在")
    text = target.read_text(encoding="utf-8-sig")
    return {"repositoryId": repository_id, "path": path, "text": text,
            "language": metadata[0]["language"], "lines": metadata[0]["lines"],
            "commitSha": row["commit_sha"], "readOnly": True}


def errors(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(400, "连接 GitHub 失败，请检查网络后重试") from exc
    return wrapper


class InspectIn(BaseModel):
    source_url: str
    branch: str = ""


class ImportIn(InspectIn):
    community_id: str
    paths: list[str]


@router.post("/inspect")
@errors
async def inspect_route(payload: InspectIn, request: Request):
    auth.require(request)
    return await github_client.inspect(payload.source_url, payload.branch)


@router.post("/import")
@errors
async def import_route(payload: ImportIn, request: Request):
    return await import_repository(payload.model_dump(), auth.require(request))


@router.get("/{repository_id}/files")
@errors
async def files_route(repository_id: str, request: Request):
    return list_files(repository_id, auth.require(request))


@router.get("/{repository_id}/file")
@errors
async def file_route(repository_id: str, path: str, request: Request):
    return read_file(repository_id, path, auth.require(request))
