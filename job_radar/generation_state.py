"""Keep interrupted new application folders out of the finished-draft library."""
from pathlib import Path
from .cancellation import current_token

IN_PROGRESS = "GENERATION_IN_PROGRESS.txt"
ABORTED = "GENERATION_ABORTED.txt"
FAILED = "GENERATION_FAILED.txt"
INCOMPLETE_MARKERS = (IN_PROGRESS, ABORTED, FAILED)


def track_new_output(directory: Path) -> None:
    """Register only newly allocated folders from a cancellable run."""
    token = current_token()
    if token is None:
        return
    directory = Path(directory)
    token.track_output(directory)
    (directory / IN_PROGRESS).write_text(
        "Generation is in progress. Its files are not a completed application.\n", encoding="utf-8")
    token.check()


def mark_incomplete(token, *, aborted: bool) -> None:
    name = ABORTED if aborted else FAILED
    text = "Generation was aborted by the user." if aborted else "Generation did not finish."
    for directory in token.outputs:
        path = Path(directory)
        (path / name).write_text(text + "\nPartial files are retained for inspection, not shown as finished drafts. "
                                "Start a new generation for a complete application.\n", encoding="utf-8")
        (path / IN_PROGRESS).unlink(missing_ok=True)


def finish_outputs(token) -> None:
    """The UI accepts completion before making the new drafts visible."""
    token.check()
    for directory in token.outputs:
        (Path(directory) / IN_PROGRESS).unlink(missing_ok=True)


def is_incomplete(directory: Path) -> bool:
    return any((Path(directory) / name).exists() for name in INCOMPLETE_MARKERS)
