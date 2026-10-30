"""Small, explainable term prioritization. Original code; no AI or fit inference."""
import json
from pathlib import Path
import re
import unicodedata


def parse_terms(expression: str) -> list[list[str]]:
    if not isinstance(expression, str) or len(expression) > 2000:
        raise ValueError('Priority terms must be text shorter than 2,000 characters.')
    groups, seen = [], set()
    for part in expression.split(';'):
        if not part.strip():
            continue
        alternatives = []
        for term in part.split('|'):
            term = unicodedata.normalize('NFKC', term).strip().casefold()
            if not term or len(term) > 100:
                raise ValueError('Use non-empty terms of up to 100 characters; separate alternatives with |.')
            if term not in alternatives:
                alternatives.append(term)
        key = tuple(sorted(alternatives))
        if key not in seen:
            groups.append(alternatives)
            seen.add(key)
    if len(groups) > 30:
        raise ValueError('Use up to 30 priority groups.')
    return groups


def term_breakdown(candidate: dict, groups: list[list[str]]) -> dict:
    text = unicodedata.normalize('NFKC', ' '.join(str(candidate.get(key) or '')
           for key in ('company', 'title', 'location', 'description'))).casefold()
    matched, missing = [], []
    for alternatives in groups:
        hits = [term for term in alternatives if re.search(r'(?<!\w)' +
                r'\s+'.join(re.escape(word) for word in term.split()) + r'(?!\w)', text)]
        label = ' | '.join(alternatives)
        if hits:
            matched.append({'group': label, 'terms': hits})
        else:
            missing.append(label)
    return {'matched': matched, 'missing': missing, 'count': len(matched), 'total': len(groups)}


def load_terms(root: Path) -> str:
    path = Path(root) / 'data/discovery_preferences.json'
    if not path.exists():
        return ''
    if path.stat().st_size > 16_384:
        raise ValueError('The saved priority terms file is too large.')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('The saved priority terms have an unsupported format.')
    terms = value.get('terms')
    parse_terms(terms)
    return terms


def save_terms(root: Path, terms: str) -> list[list[str]]:
    groups = parse_terms(terms)
    path = Path(root) / 'data/discovery_preferences.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # One GUI action writes a small complete preference file; no user facts.
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'version': 1, 'terms': terms}, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return groups
