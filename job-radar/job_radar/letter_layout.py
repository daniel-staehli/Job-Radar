"""Supported cover-letter layout controls and migration of saved templates."""
from __future__ import annotations

from jinja2 import Environment


# Match the complete known recipient minipage, never a company-name occurrence
# in the letter body, subject, or an unrelated custom template.
LEGACY_RECIPIENT_BLOCK = r"""\begin{minipage}[t]{\linewidth}
\textbf{((( company | tex )))}\par
((* if letter.recipient_name *))((( letter.recipient_name | tex )))\par
((* endif *))
((* if letter.recipient_role *))((( letter.recipient_role | tex )))\par
((* endif *))
\end{minipage}"""
RECIPIENT_BLOCK = LEGACY_RECIPIENT_BLOCK.replace(
    r"\textbf{((( company | tex )))}\par",
    "((* if letter.show_recipient_block | default(true) *))\n"
    + r"\textbf{((( company | tex )))}\par",
).replace(
    r"\end{minipage}",
    "((* else *))\n" + r"\mbox{}" + "\n((* endif *))\n" + r"\end{minipage}",
)
UNSUPPORTED_LAYOUT = (
    "This saved cover-letter template does not support the recipient-block layout control. "
    "No document was changed. Edit the LaTeX file manually, or use the standard "
    "Job Radar cover-letter template before requesting this formatting change."
)


def prepare_letter_template(source: str, *, require_support: bool = False) -> str:
    """Upgrade only the exact known block, retaining all surrounding layout."""
    normalized = source.replace("\r\n", "\n")
    if normalized.count(RECIPIENT_BLOCK) == 1:
        return source
    if normalized.count(LEGACY_RECIPIENT_BLOCK) == 1:
        newline = "\r\n" if "\r\n" in source else "\n"
        return normalized.replace(LEGACY_RECIPIENT_BLOCK, RECIPIENT_BLOCK, 1).replace("\n", newline)
    if require_support:
        raise ValueError(UNSUPPORTED_LAYOUT)
    return source


def change_recipient_block(source: str, *, template_source: str,
                           environment: Environment, original_context: dict,
                           revised_context: dict) -> str:
    """Change only this block in the saved TeX, preserving date and all wording."""
    prepare_letter_template(template_source, require_support=True)
    block = environment.from_string(RECIPIENT_BLOCK)
    previous = block.render(**original_context)
    replacement = block.render(**revised_context)
    if "\r\n" in source:
        previous = previous.replace("\n", "\r\n")
        replacement = replacement.replace("\n", "\r\n")
    if source.count(previous) != 1:
        raise ValueError(
            "The saved recipient block could not be located uniquely. No document was changed. "
            "Open the LaTeX file to make this formatting change manually."
        )
    return source.replace(previous, replacement, 1)
