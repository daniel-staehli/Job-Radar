"""Private contacts, employer aliases and explicit vacancy associations."""
from collections import defaultdict
from contextlib import contextmanager
import csv
from datetime import date
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import unquote, urlsplit, quote

from .profile import _data_dir, _linked

STRENGTHS = ("Unrated", "Acquaintance", "Colleague", "Close contact")
FIELDS = ("name", "company", "position", "url", "strength", "last_interaction", "notes")
SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, company TEXT NOT NULL,
 position TEXT NOT NULL, url TEXT NOT NULL, strength TEXT NOT NULL,
 last_interaction TEXT NOT NULL, notes TEXT NOT NULL, name_key TEXT NOT NULL, company_key TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS contact_url ON contacts(url) WHERE url != '';
CREATE INDEX IF NOT EXISTS contact_identity ON contacts(name_key, company_key);
CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, parent TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS links (
 job TEXT NOT NULL, contact INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
 PRIMARY KEY (job, contact)
);
"""


def _key(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", value))


def employer(value):
    key = _key(value)
    return re.sub(r"(?: (?:incorporated|inc|limited|ltd|llc|l l c|plc|corp|corporation|gmbh|ag|a g|sa|s a|sarl))+$", "", key)


def _url(value):
    if not value:
        return ""
    parts = urlsplit(value if "://" in value else "https://" + value)
    if (parts.scheme not in ("http", "https") or not re.fullmatch(r"(?:[a-z]{2,3}\.)?linkedin\.com", parts.hostname or "")
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError("Use a LinkedIn profile URL with the path /in/name.")
    slug = unquote(parts.path).strip("/").split("/")
    if len(slug) != 2 or slug[0] != "in" or not re.fullmatch(r"[\w.%~-]+", slug[1]) or slug[1] in {".", ".."}:
        raise ValueError("Use a LinkedIn profile URL with the path /in/name.")
    return "https://www.linkedin.com/in/" + quote(slug[1].casefold(), safe="-._~")


def _values(raw):
    values = {k: str(raw.get(k) or "").strip() for k in FIELDS}
    if not values["name"] or any(len(v) > (10000 if k == "notes" else 1024) for k, v in values.items()):
        raise ValueError("Enter a name and keep notes under 10,000 characters and other fields under 1,024.")
    values["url"] = _url(values["url"])
    values["strength"] = values["strength"] or STRENGTHS[0]
    if values["strength"] not in STRENGTHS:
        raise ValueError("Choose a relationship strength.")
    if values["last_interaction"]:
        parsed = date.fromisoformat(values["last_interaction"])
        if parsed.isoformat() != values["last_interaction"]:
            raise ValueError("Use YYYY-MM-DD for the last interaction.")
    return dict(values, name_key=_key(values["name"]), company_key=employer(values["company"]))


@contextmanager
def _db(root, *, write=False):
    data = _data_dir(root)
    path = data / "network.sqlite3"
    if _linked(path):
        raise ValueError("The network database cannot be a file link.")
    if not write and not path.exists():
        yield None
        return
    if write:
        data.mkdir(exist_ok=True, parents=True)
    con = sqlite3.connect(path.resolve().as_uri() + ("?mode=rwc" if write else "?mode=ro"), uri=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if write:
            con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        with con:
            yield con
    finally:
        con.close()


def _store(con, row, contact_id=None):
    if contact_id is None:
        return con.execute("INSERT INTO contacts (" + ",".join(row) + ") VALUES ("
                           + ",".join(":" + k for k in row) + ")", row).lastrowid
    updated = con.execute("UPDATE contacts SET " + ",".join(f"{k}=:{k}" for k in row) + " WHERE id=:id",
                          {**row, "id": contact_id})
    if not updated.rowcount:
        raise ValueError("The selected contact no longer exists.")
    return contact_id


def save_contact(root, raw, contact_id=None, *, expected=None):
    row = _values(raw)
    with _db(root, write=True) as con:
        if expected is not None:
            current = con.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
            if current is None or any(current[k] != expected[k] for k in FIELDS):
                raise ValueError("This contact changed since you opened it. Reopen the current record before editing.")
        try:
            return _store(con, row, contact_id)
        except sqlite3.IntegrityError:
            raise ValueError("A contact already has this LinkedIn profile URL.") from None


def delete_contact(root, contact_id):
    with _db(root, write=True) as con:
        con.execute("DELETE FROM contacts WHERE id=?", (contact_id,))


def import_connections(root, source):
    """Import one official English-header Connections.csv as a single transaction."""
    source = Path(source)
    if source.suffix.casefold() != ".csv" or source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Choose an extracted Connections.csv file under 32 MB.")
    counts = dict(added=0, updated=0, unchanged=0, skipped=0)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        for header in reader:
            columns = [cell.strip().casefold() for cell in header]
            if {"first name", "last name", "company", "position"} <= set(columns):
                break
        else:
            raise ValueError("Connections.csv needs First Name, Last Name, Company and Position headers.")
        with _db(root, write=True) as con:
            for cells in reader:
                if not any(cells):
                    continue
                raw = dict(zip(columns, cells))
                name = " ".join(raw.get(k, "").strip() for k in ("first name", "last name")).strip()
                try:
                    row = _values(dict(name=name, company=raw.get("company"), position=raw.get("position"),
                                       url=raw.get("url")))
                except ValueError:
                    counts["skipped"] += 1
                    continue
                match = con.execute("SELECT * FROM contacts WHERE url=? AND url<>''", (row["url"],)).fetchone()
                if match is None:
                    if not row["url"] and not row["company_key"]:
                        counts["skipped"] += 1
                        continue
                    matches = con.execute("SELECT * FROM contacts WHERE name_key=? AND company_key=? "
                                          "AND company_key<>'' AND (url='' OR ?='')",
                                          (row["name_key"], row["company_key"], row["url"])).fetchall()
                    if len(matches) > 1:
                        counts["skipped"] += 1
                        continue
                    match = matches[0] if matches else None
                if match is None:
                    _store(con, row)
                    counts["added"] += 1
                    continue
                # Only LinkedIn's nonblank identity and employment fields may change.
                # Personal annotations and the original contact id survive re-import.
                merged = dict(match)
                merged.update({k: row[k] for k in ("name", "company", "position", "url") if row[k]})
                changed = _values(merged)
                if any(match[k] != v for k, v in changed.items()):
                    _store(con, changed, match["id"])
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
    return counts


def _parent(key, aliases):
    visited = set()
    while key in aliases:
        if key in visited:
            raise ValueError("Employer aliases cannot form a loop.")
        visited.add(key)
        key = aliases[key]
    return key


def save_alias(root, alias, parent):
    alias, parent = employer(alias), employer(parent)
    if not alias or not parent or alias == parent:
        raise ValueError("Enter two distinct employer names.")
    with _db(root, write=True) as con:
        aliases = dict(con.execute("SELECT alias,parent FROM aliases"))
        _parent(alias, {**aliases, alias: parent})
        con.execute("INSERT INTO aliases VALUES (?,?) ON CONFLICT(alias) DO UPDATE SET parent=excluded.parent", (alias, parent))


def remove_alias(root, alias):
    with _db(root, write=True) as con:
        con.execute("DELETE FROM aliases WHERE alias=?", (employer(alias),))


def job_key(job):
    if job.get("job_id"):
        return job["job_id"]
    if str(job.get("id", "")).startswith("candidate_"):
        return "job_ext_" + job["id"].removeprefix("candidate_")
    return ""


def link(root, job, contact, *, remove=False):
    key = job_key(job)
    if not key:
        raise ValueError("Choose a saved job or an employer-feed vacancy.")
    with _db(root, write=True) as con:
        if remove:
            con.execute("DELETE FROM links WHERE job=? AND contact=?", (key, contact))
        else:
            con.execute("INSERT OR IGNORE INTO links VALUES (?,?)", (key, contact))


class Network:
    """Read one consistent snapshot; keep personal information outside job and AI records."""
    def __init__(self, root=None):
        self.contacts, self.aliases = {}, {}
        self.employers, self.links = defaultdict(set), defaultdict(set)
        if root is None:
            return
        with _db(root) as con:
            if con is None:
                return
            self.aliases = dict(con.execute("SELECT alias,parent FROM aliases ORDER BY alias"))
            for row in con.execute("SELECT * FROM contacts ORDER BY name_key,id"):
                self.contacts[row["id"]] = dict(row)
                if row["company_key"]:
                    self.employers[_parent(row["company_key"], self.aliases)].add(row["id"])
            for job, contact in con.execute("SELECT job,contact FROM links"):
                self.links[job].add(contact)

    def _ids(self, job):
        key = _parent(employer(job.get("company") or ""), self.aliases)
        return self.employers.get(key, set()) | self.links.get(job_key(job), set())

    def count(self, job):
        return len(self._ids(job))

    def matches(self, job):
        return sorted((self.contacts[i] for i in self._ids(job)),
                      key=lambda c: (-STRENGTHS.index(c["strength"]), c["name_key"], c["id"]))

    def priority(self, job):
        return max((1 + STRENGTHS.index(self.contacts[i]["strength"]) for i in self._ids(job)), default=0)

    def summary(self, job):
        contacts = self.matches(job)
        if not contacts:
            return "My Network: no linked contacts."
        return "My Network\n" + "\n".join(" · ".join(filter(None, (c["name"], c["position"], c["company"], c["strength"])))
                                          for c in contacts)
