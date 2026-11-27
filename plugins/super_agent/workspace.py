"""Choose the Git checkout named by a task and interpret its own changes."""
from pathlib import Path
import re

from agent.i18n import t


WORKTREE_DIRECTORY = ".super-agent-worktrees"


def task_repository(root: Path, task: str) -> Path:
    """Follow project names or relative paths in the task, within the configured root."""
    text = task.replace("\\", "/")
    folded = text.casefold()
    prefix = rf"(?:{re.escape(root.as_posix().rstrip('/'))}/|\./)?"
    pending = [root]
    matches = []
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            if child.name in {".git", WORKTREE_DIRECTORY} or child.name.casefold() not in folded:
                continue
            path = child.relative_to(root).as_posix()
            pattern = rf"(?<![\w./-]){prefix}{re.escape(path)}(?![\w-]|\.[\w-])"
            if not re.search(pattern, text, re.ASCII | re.IGNORECASE):
                continue
            if not child.is_dir() or child.resolve() != child:
                continue
            if (child / ".git").exists():
                matches.append(child)
            if path.casefold() + "/" in folded:
                pending.append(child)
    # An explicit path through a repository selects its most specific project.
    targets = [path for path in matches if not any(path in other.parents for other in matches)]
    if len(targets) > 1:
        names = ", ".join(sorted(path.relative_to(root).as_posix() for path in targets))
        raise ValueError(t("super_agent.ambiguous_repo", names=names))
    return targets[0] if targets else root


def has_checkout_changes(status: str) -> bool:
    for entry in status.split("\0"):
        # With -uall the only untracked directories Git leaves unexpanded are repositories.
        if not entry or (entry.startswith("? ") and entry.endswith("/")):
            continue
        # Porcelain v2: only the checked-out submodule commit differs from the parent's index.
        if not entry.startswith("1 .M SC.. "):
            return True
    return False
