"""Local, dependency-free DOCX export and deliberately simple Word templates.

Only ordinary text, tables, styles and page settings are accepted. Never execute
template code, retain metadata, or copy facts into the candidate profile.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from io import BytesIO
from pathlib import Path
import re
import uuid
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
ET.register_namespace("w", W)
ET.register_namespace("r", R)
BUILTINS = {"clean": "Clean · Arial", "classic": "Classic · Georgia"}
REQUIRED = {
    "cv": {"NAME", "CONTACT", "HEADLINE", "CV_CONTENT"},
    "cover_letter": {"NAME", "CONTACT", "RECIPIENT", "SUBJECT", "LETTER_CONTENT"},
}
ALLOWED = {
    "cv": REQUIRED["cv"],
    "cover_letter": REQUIRED["cover_letter"] | {"HEADLINE", "DATE"},
}
MAX_SIZE = 8 * 1024 * 1024
MAX_EXPANDED = 24 * 1024 * 1024


def w(name):
    return f"{{{W}}}{name}"


def element(name, **attrs):
    return ET.Element(w(name), {w(key): str(value) for key, value in attrs.items()})


def xml(node):
    return ET.tostring(node, encoding="utf-8", xml_declaration=True)


def paragraph(text, style="Normal", *, prototype=None):
    p = element("p")
    properties = prototype.find(w("pPr")) if prototype is not None else None
    if properties is not None:
        p.append(deepcopy(properties))
    else:
        props = ET.SubElement(p, w("pPr"))
        props.append(element("pStyle", val=style))
    run = ET.SubElement(p, w("r"))
    if prototype is not None:
        rp = prototype.find(f".//{w('rPr')}")
        if rp is not None:
            run.append(deepcopy(rp))
    for i, line in enumerate(str(text).split("\n")):
        if i:
            ET.SubElement(run, w("br"))
        node = ET.SubElement(run, w("t"), {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
        node.text = line
    return p


def _styles(layout):
    styles = element("styles")
    font = "Arial" if layout == "clean" else "Georgia"
    for name, size, bold, before, after in (
        ("Normal", 21, False, 0, 100), ("Title", 38, True, 0, 80),
        ("Subtitle", 21, False, 0, 80), ("JRContact", 18, False, 0, 180),
        ("JRSection", 23, True, 180, 80), ("JRRole", 21, True, 100, 50),
        ("JRBullet", 21, False, 0, 60), ("JRSubject", 23, True, 220, 180),
        ("JRLetter", 22, False, 0, 150),
    ):
        style = element("style", type="paragraph", styleId=name)
        style.append(element("name", val=name))
        if name != "Normal":
            style.append(element("basedOn", val="Normal"))
        props = element("pPr")
        props.append(element("spacing", before=before, after=after, line=264, lineRule="auto"))
        props.append(element("widowControl"))
        if name in {"Title", "Subtitle", "JRSection", "JRRole", "JRSubject"}:
            props.append(element("keepNext"))
        if name == "JRBullet":
            props.append(element("ind", left=210, hanging=210))
        style.append(props)
        run = element("rPr")
        run.append(element("rFonts", ascii=font, hAnsi=font, cs=font))
        run.append(element("sz", val=size))
        run.append(element("color", val="000000"))
        if bold:
            run.append(element("b"))
        style.append(run)
        styles.append(style)
    return xml(styles)


def _package(parts):
    """Rebuild package relationships and content types, excluding all metadata."""
    contents = ET.Element(f"{{{CT}}}Types")
    ET.SubElement(contents, f"{{{CT}}}Default", Extension="rels",
                  ContentType="application/vnd.openxmlformats-package.relationships+xml")
    ET.SubElement(contents, f"{{{CT}}}Default", Extension="xml", ContentType="application/xml")
    types = {"document": "document.main", "styles": "styles", "numbering": "numbering",
             "settings": "settings", "fontTable": "fontTable"}
    rels = ET.Element(f"{{{REL}}}Relationships")
    for path in parts:
        stem = Path(path).stem
        if path == "word/theme/theme1.xml":
            kind, subtype, target = "theme", "application/vnd.openxmlformats-officedocument.theme+xml", "theme/theme1.xml"
        else:
            kind = stem
            subtype = f"application/vnd.openxmlformats-officedocument.wordprocessingml.{types[stem]}+xml"
            target = Path(path).name
        ET.SubElement(contents, f"{{{CT}}}Override", PartName="/" + path, ContentType=subtype)
        if path != "word/document.xml":
            ET.SubElement(rels, f"{{{REL}}}Relationship", Id="r" + kind, Type=R + "/" + kind, Target=target)
    root_rels = ET.Element(f"{{{REL}}}Relationships")
    ET.SubElement(root_rels, f"{{{REL}}}Relationship", Id="rDoc", Type=R + "/officeDocument", Target="word/document.xml")
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for path, content in {**parts, "[Content_Types].xml": xml(contents),
                              "_rels/.rels": xml(root_rels), "word/_rels/document.xml.rels": xml(rels)}.items():
            archive.writestr(path, content)
    return out.getvalue()


def starter_template(kind, layout="clean"):
    if kind not in REQUIRED or layout not in BUILTINS:
        raise ValueError("Choose a CV or cover-letter layout.")
    doc = element("document")
    body = ET.SubElement(doc, w("body"))
    fields = [("NAME", "Title"), ("HEADLINE", "Subtitle"), ("CONTACT", "JRContact")]
    fields += ([("CV_CONTENT", "Normal")] if kind == "cv" else
               [("DATE", "Normal"), ("RECIPIENT", "Normal"), ("SUBJECT", "JRSubject"), ("LETTER_CONTENT", "JRLetter")])
    for field, style in fields:
        body.append(paragraph("{{" + field + "}}", style))
    section = ET.SubElement(body, w("sectPr"))
    section.append(element("pgSz", w=11906, h=16838))
    section.append(element("pgMar", top=907, right=1021, bottom=907, left=1021, header=360, footer=360, gutter=0))
    return _package({"word/document.xml": xml(doc), "word/styles.xml": _styles(layout)})


def _parse(content):
    # Reject DTD/entities before parsing, including UTF-16/32 spellings.
    if b"<!doctype" in content.replace(b"\x00", b"").lower() or b"<!entity" in content.replace(b"\x00", b"").lower():
        raise ValueError("Templates cannot contain XML entities or document types.")
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError("This Word template contains invalid XML.") from exc


def read_template(data, kind):
    """Validate and sanitize a bounded, ordinary DOCX; return reusable parts."""
    if kind not in REQUIRED:
        raise ValueError("Choose CV or cover letter.")
    if len(data) > MAX_SIZE:
        raise ValueError("Word templates must be smaller than 8 MB.")
    keep = {"word/document.xml", "word/styles.xml", "word/numbering.xml",
            "word/settings.xml", "word/fontTable.xml", "word/theme/theme1.xml"}
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 100 or len(names) != len(set(names)) or sum(item.file_size for item in entries) > MAX_EXPANDED:
                raise ValueError("This Word template is too large or has duplicate parts.")
            if "word/document.xml" not in names or "word/styles.xml" not in names:
                raise ValueError("Choose a Word DOCX template, preferably made from a Job Radar starter.")
            parts = {}
            for item in entries:
                name = item.filename
                if name in {"word/webSettings.xml", "word/stylesWithEffects.xml"}:
                    # Word adds these on save. The normal styles carry the layout;
                    # browser settings and duplicate effect styles are not exported.
                    continue
                if name.startswith("word/") and name not in keep and not name.endswith(".rels"):
                    raise ValueError("This template has unsupported content (such as images, headers, comments or embedded files). Use a Job Radar starter with text, tables and styles.")
                if name.endswith(".rels"):
                    relationships = _parse(archive.read(name))
                    if any(rel.get("TargetMode", "").lower() == "external" for rel in relationships):
                        raise ValueError("Remove external links or linked templates before importing this layout.")
                if name in keep:
                    root = _parse(archive.read(name))
                    forbidden = {"fldSimple", "instrText", "fldChar", "altChunk", "object", "drawing", "pict",
                                 "ins", "del", "moveFrom", "moveTo", "sdt", "hyperlink", "vanish", "webHidden"}
                    if any(node.tag.split("}")[-1] in forbidden or
                           any(attr.startswith("{" + R + "}") for attr in node.attrib) for node in root.iter()):
                        raise ValueError("Remove fields, links, hidden text, tracked changes or embedded objects from the template.")
                    # Strip revision identifiers, author IDs and historical paragraph IDs.
                    for node in root.iter():
                        for attr in list(node.attrib):
                            if attr.split("}")[-1].startswith("rsid") or attr.split("}")[-1] in {"paraId", "textId", "author", "date"}:
                                del node.attrib[attr]
                    if name == "word/settings.xml":
                        # Only retain rendering settings, never template paths/doc variables.
                        for node in list(root):
                            if node.tag not in {w("compat"), w("defaultTabStop"), w("evenAndOddHeaders")}:
                                root.remove(node)
                    parts[name] = xml(root)
    except (BadZipFile, RuntimeError, KeyError) as exc:
        raise ValueError("This file is not a readable, unencrypted Word DOCX template.") from exc
    doc = _parse(parts["word/document.xml"])
    body = doc.find(w("body"))
    if body is None:
        raise ValueError("The template has no document body.")
    found = []
    for p in doc.iter(w("p")):
        text = "".join(node.text or "" for node in p.iter(w("t")))
        if "{{" in text or "}}" in text:
            match = re.fullmatch(r"\{\{([A-Z_]+)\}\}", text.strip())
            if not match or match[1] not in ALLOWED[kind]:
                raise ValueError("Each placeholder must be alone in its paragraph. Use the placeholders from the matching starter template.")
            found.append(match[1])
    missing = REQUIRED[kind] - set(found)
    if missing or len(found) != len(set(found)):
        raise ValueError("Template needs each required placeholder exactly once: " + ", ".join(sorted(REQUIRED[kind])))
    return parts


def template_path(library, template_id, kind):
    if not re.fullmatch(r"[0-9a-f]{16}", template_id):
        raise ValueError("Choose a saved Word layout.")
    path = Path(library) / f"{kind}_{template_id}.docx"
    if path.is_symlink() or not path.is_file():
        raise ValueError("This saved layout is unavailable.")
    return path


def import_template(source, library, kind):
    source, library = Path(source), Path(library)
    if source.suffix.lower() != ".docx" or source.stat().st_size > MAX_SIZE:
        raise ValueError("Choose a .docx template smaller than 8 MB.")
    sanitized = _package(read_template(source.read_bytes(), kind))
    identity = hashlib.sha256(sanitized).hexdigest()[:16]
    library.mkdir(parents=True, exist_ok=True)
    destination = library / f"{kind}_{identity}.docx"
    if not destination.exists():
        with destination.open("xb") as stream:
            stream.write(sanitized)
    label_path = destination.with_suffix(".json")
    if not label_path.exists():
        with label_path.open("x", encoding="utf-8") as stream:
            json.dump({"label": source.stem[:80]}, stream, ensure_ascii=False)
    return identity


def layouts(library, kind):
    items = [(key, label) for key, label in BUILTINS.items()]
    for path in sorted(Path(library).glob(f"{kind}_*.docx")):
        identity = path.stem.removeprefix(kind + "_")
        if re.fullmatch(r"[0-9a-f]{16}", identity) and not path.is_symlink():
            label = "Imported layout"
            metadata = path.with_suffix(".json")
            if metadata.is_file() and not metadata.is_symlink() and metadata.stat().st_size < 4096:
                try:
                    value = json.loads(metadata.read_text(encoding="utf-8")).get("label")
                    if isinstance(value, str) and value.strip():
                        label = " ".join(value.split())[:80]
                except (ValueError, OSError, AttributeError):
                    pass
            items.append((identity, label + " · " + identity[:8]))
    return items


def _cv_blocks(context):
    blocks = [("Profile", "JRSection"), (context["profile_text"], "Normal")]
    for title, key in (("Experience", "experiences"), ("Selected publications", "publications"),
                       ("Core competencies", "skills"), ("Languages", "languages"),
                       ("Education", "education"), ("Additional experience", "additional_experience"),
                       ("Interests", "interests")):
        values = context.get(key, [])
        if not values:
            continue
        blocks.append((title, "JRSection"))
        for item in values:
            if not isinstance(item, dict):
                blocks.append((str(item), "Normal"))
            elif key in {"experiences", "additional_experience"}:
                heading = " | ".join(str(item[field]) for field in ("role", "location", "dates") if item.get(field))
                if heading:
                    blocks.append((heading, "JRRole"))
                if item.get("subtitle"):
                    blocks.append((item["subtitle"], "Normal"))
                blocks.extend(("• " + bullet["text"], "JRBullet") for bullet in item.get("bullets", []))
                if item.get("text"):
                    blocks.append((item["text"], "Normal"))
            elif key == "publications":
                blocks.append((item.get("citation", ""), "Normal"))
                if item.get("url"):
                    blocks.append((item["url"], "Normal"))
            elif key == "skills":
                blocks.append((f"{item.get('label', '')}: {item.get('text', '')}", "Normal"))
            elif key == "education":
                blocks.append((" | ".join(str(item[field]) for field in ("institution", "degree", "dates") if item.get(field)), "JRRole"))
                if item.get("detail"):
                    blocks.append((item["detail"], "Normal"))
    return blocks


def render_word(parts, kind, context, letter=None, company="", date_line=""):
    contact = context["contact"]
    details = [str(contact[key]) for key in ("location", "phone", "email") if contact.get(key)]
    link = contact.get("linkedin_url") or contact.get("linkedin")
    if link:
        details.append(str(link))
    values = {"NAME": contact.get("name", ""), "CONTACT": "\n".join(details),
              "HEADLINE": context["headline"], "DATE": date_line}
    if kind == "cover_letter":
        values["RECIPIENT"] = "\n".join(str(value) for value in (company, letter.recipient_name, letter.recipient_role) if value) if letter.show_recipient_block else ""
        values["SUBJECT"] = letter.subject
    doc = _parse(parts["word/document.xml"])
    parents = {child: parent for parent in doc.iter() for child in parent}
    for p in list(doc.iter(w("p"))):
        text = "".join(node.text or "" for node in p.iter(w("t"))).strip()
        if not text.startswith("{{"):
            continue
        field = text[2:-2]
        parent = parents[p]
        index = list(parent).index(p)
        if field == "CV_CONTENT":
            nodes = [paragraph(text, style) for text, style in _cv_blocks(context)]
        elif field == "LETTER_CONTENT":
            texts = [letter.salutation, *letter.paragraphs, letter.closing, contact.get("name", "")]
            nodes = [paragraph(text, prototype=p) for text in texts if text]
            # Keep sign-off and name together, but allow body paragraphs to flow.
            if len(nodes) >= 2:
                props = nodes[-2].find(w("pPr"))
                if props is not None:
                    props.append(element("keepNext"))
        else:
            nodes = [paragraph(values.get(field, ""), prototype=p)] if values.get(field) else []
        parent.remove(p)
        for offset, node in enumerate(nodes):
            parent.insert(index + offset, node)
        if parent.tag == w("tc") and not parent.findall(w("p")):
            parent.append(paragraph(""))
    # Fill any missing content styles from the built-in layout.
    styles = _parse(parts["word/styles.xml"])
    existing = {node.get(w("styleId")) for node in styles}
    for style in _parse(_styles("clean")):
        if style.get(w("styleId")) not in existing:
            styles.append(style)
    return _package({**parts, "word/document.xml": xml(doc), "word/styles.xml": xml(styles)})


def export_documents(directory, library, selections, context, letter, company, date_line):
    """Build all selected documents first, then save a uniquely named export set."""
    if not selections or set(selections) - set(REQUIRED):
        raise ValueError("Choose at least one document to export.")
    payloads = {}
    for kind, layout in selections.items():
        if layout in BUILTINS:
            data = starter_template(kind, layout)
        else:
            path = template_path(library, layout, kind)
            if path.stat().st_size > MAX_SIZE:
                raise ValueError("The saved Word template is too large.")
            data = path.read_bytes()
        parts = read_template(data, kind)
        payloads[kind] = render_word(parts, kind, context, letter, company, date_line)
    # Stay within the existing application's desktop-friendly filename budget.
    stamp = datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
    names = {"cv": "cv", "cover_letter": "letter"}
    destinations = {kind: Path(directory) / f"{names[kind]}_{stamp}.docx" for kind in payloads}
    if any(len(str(path.resolve()).encode("utf-16-le")) // 2 > 240 for path in destinations.values()):
        raise ValueError("This older application's path is too long to open Word files reliably. Move the application to a shorter folder inside applications, then refresh the list.")
    created = []
    try:
        for kind, payload in payloads.items():
            path = destinations[kind]
            with path.open("xb") as stream:
                created.append(path)
                stream.write(payload)
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return created
