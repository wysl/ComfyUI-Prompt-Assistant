"""Safe filesystem helpers for user-managed multimedia reference prompts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple


LIBRARY_DIR_NAME = "multimedia_reference"
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 256 * 1024


class ReferencePromptError(ValueError):
    pass


def get_reference_prompt_root() -> Path:
    from ..config_manager import config_manager

    root = Path(config_manager.rules_dir) / LIBRARY_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def resolve_library_path(
    relative_path: str,
    *,
    root: Optional[Path] = None,
    expect_directory: Optional[bool] = None,
) -> Path:
    library_root = (root or get_reference_prompt_root()).resolve()
    raw = str(relative_path or "").strip().replace("\\", "/")

    if raw in ("", "."):
        candidate = library_root
    else:
        pure = PurePosixPath(raw)
        if pure.is_absolute() or any(part in ("", ".", "..") or ":" in part for part in pure.parts):
            raise ReferencePromptError(f"无效的参考提示词路径: {relative_path}")
        candidate = (library_root / Path(*pure.parts)).resolve()

    if not _is_within_root(candidate, library_root):
        raise ReferencePromptError(f"参考提示词路径超出允许目录: {relative_path}")
    if expect_directory is True and (not candidate.exists() or not candidate.is_dir()):
        raise ReferencePromptError(f"参考提示词目录不存在: {relative_path or '/'}")
    if expect_directory is False and (not candidate.exists() or not candidate.is_file()):
        raise ReferencePromptError(f"参考提示词文件不存在: {relative_path}")
    return candidate


def list_reference_directory(relative_dir: str = "", *, root: Optional[Path] = None) -> dict:
    library_root = (root or get_reference_prompt_root()).resolve()
    library_root.mkdir(parents=True, exist_ok=True)
    directory = resolve_library_path(
        relative_dir,
        root=library_root,
        expect_directory=True,
    )

    directories = []
    files = []
    for child in directory.iterdir():
        if child.name.startswith("."):
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if not _is_within_root(resolved, library_root):
            continue

        relative = resolved.relative_to(library_root).as_posix()
        if resolved.is_dir():
            directories.append({"name": child.name, "path": relative})
        elif resolved.is_file() and resolved.suffix.lower() == ".txt":
            stat = resolved.stat()
            files.append(
                {
                    "name": child.name,
                    "path": relative,
                    "size": stat.st_size,
                    "modified": stat.st_mtime_ns,
                }
            )

    key = lambda item: item["name"].casefold()
    directories.sort(key=key)
    files.sort(key=key)
    current = directory.relative_to(library_root).as_posix()
    return {
        "path": "" if current == "." else current,
        "directories": directories,
        "files": files,
    }


def parse_selected_files(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [line.strip() for line in text.splitlines() if line.strip()]
        items = parsed if isinstance(parsed, list) else []

    selected = []
    seen = set()
    for item in items:
        path = str(item or "").strip().replace("\\", "/")
        if path and path not in seen:
            selected.append(path)
            seen.add(path)
    return selected


def _decode_prompt(data: bytes, relative_path: str) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ReferencePromptError(f"无法识别文本编码: {relative_path}")


def compose_reference_prompts(
    selected_files,
    *,
    root: Optional[Path] = None,
) -> Tuple[str, str]:
    library_root = (root or get_reference_prompt_root()).resolve()
    selected = parse_selected_files(selected_files)
    total_bytes = 0
    sections = []

    for index, relative_path in enumerate(selected, start=1):
        path = resolve_library_path(relative_path, root=library_root, expect_directory=False)
        if path.suffix.lower() != ".txt":
            raise ReferencePromptError(f"只允许读取 TXT 文件: {relative_path}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ReferencePromptError(
                f"参考提示词文件超过 {MAX_FILE_BYTES // 1024} KB: {relative_path}"
            )
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ReferencePromptError(
                f"所选参考提示词总大小超过 {MAX_TOTAL_BYTES // 1024} KB"
            )
        content = _decode_prompt(path.read_bytes(), relative_path).strip()
        sections.append(f"[Reference file {index}: {relative_path}]\n{content}")

    manifest = json.dumps(selected, ensure_ascii=False, indent=2)
    return "\n\n".join(sections), manifest


def selection_content_digest(selected_files, *, root: Optional[Path] = None) -> str:
    library_root = (root or get_reference_prompt_root()).resolve()
    digest = hashlib.sha256()
    for relative_path in parse_selected_files(selected_files):
        digest.update(relative_path.encode("utf-8"))
        try:
            path = resolve_library_path(relative_path, root=library_root, expect_directory=False)
            digest.update(path.read_bytes())
        except (OSError, ReferencePromptError) as error:
            digest.update(f"missing:{type(error).__name__}".encode("ascii"))
    return digest.hexdigest()
