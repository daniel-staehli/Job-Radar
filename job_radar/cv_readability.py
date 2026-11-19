"""Compare locally extracted export text with selected facts from a saved CV."""
import re
import unicodedata


def _normalise(text):
    text = unicodedata.normalize("NFKC", str(text)).casefold().replace("\u00ad", "")
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    return " ".join(text.split())


def _phrase(value):
    # A wrapped line, or spaces around a date dash, need not change the value.
    literal = re.escape(_normalise(value)).replace(r"\-", r"\s*-\s*")
    return rf"(?<!\w){literal}(?!\w)"


def review_export(text, context):
    """Report concrete differences. Preserve original text for reading-order review."""
    body = _normalise(text)
    headings = {_normalise(line) for line in text.splitlines()}
    findings = []

    def check(label, value):
        if value and not re.search(_phrase(value), body):
            findings.append(f"{label} not found as written: {value}")
            return False
        return True

    for key in ("name", "email", "phone"):
        check(key.capitalize(), context["contact"].get(key))

    # Compare entries as well as individual values. Two swapped date ranges
    # would otherwise pass a check for the presence of the dates alone.
    for heading, items, title_key, detail_key in (
        ("Experience", context["experiences"], "role", "location"),
        ("Education", context["education"], "institution", "degree"),
        ("Additional experience", context["additional_experience"], "role", "location"),
    ):
        if items and heading.casefold() not in headings:
            findings.append(f"Check the section heading: {heading}")
        for item in items:
            title = item.get(title_key)
            detail = item.get(detail_key)
            dates = item.get("dates")
            title_found = check(title_key.capitalize(), title)
            detail_found = check(detail_key.capitalize(), detail)
            dates_found = check(f"Dates for {title or heading}", dates)
            if title and dates and title_found and dates_found and detail_found:
                header = r"[\s|,;:·.()/-]*".join(_phrase(v) for v in (title, detail, dates) if v)
                if not re.search(header, body):
                    findings.append(f"Check that the dates appear with the right entry: {title} ({dates})")

    summary = ("Review these differences from the saved CV:\n" +
               "\n".join(f"• {line}" for line in findings)) if findings else (
        "The checked details from the saved CV were found in the extracted text."
    )
    return (summary + "\n\nRead the extracted text below in order. Differences can also come "
            "from intentional edits or a custom layout. Each employer’s system may read the file differently."
            "\n\nExtracted text\n\n" + text)
