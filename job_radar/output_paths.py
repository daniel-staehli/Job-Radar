"""Create readable, unique application folders within desktop path limits."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .normalize import slugify


_MAX_ARTIFACT_PATH_UNITS = 240
_LONGEST_ARTIFACT = "motivation_letter_compile.log"
_MAX_COMPANY_LENGTH = 24
_CREATION_ATTEMPTS = 5


def create_application_directory(output_root: Path, company: str) -> Path:
    """Reserve a fresh company/date/ID folder without reusing saved output."""
    output_root = Path(output_root)
    date = datetime.now().strftime("%Y-%m-%d")
    minimum_name = f"a_{date}_000000"
    minimum_path = str(output_root.resolve() / minimum_name / _LONGEST_ARTIFACT)
    remaining_units = _MAX_ARTIFACT_PATH_UNITS - len(minimum_path.encode("utf-16-le")) // 2
    if remaining_units < 0:
        raise ValueError(
            "The output folder path is too long. Choose a shorter output folder "
            "so the generated documents can be opened by desktop applications."
        )

    # Company slugs are ASCII; the parent may need two UTF-16 units per character.
    company_slug = slugify(company, max_length=_MAX_COMPANY_LENGTH) or "company"
    company_slug = company_slug[:min(_MAX_COMPANY_LENGTH, remaining_units + 1)].rstrip("-")
    for _ in range(_CREATION_ATTEMPTS):
        directory = output_root / f"{company_slug}_{date}_{uuid4().hex[:6]}"
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return directory
    raise ValueError(
        "Could not create a unique application folder after several attempts. "
        "Try generating the documents again."
    )
