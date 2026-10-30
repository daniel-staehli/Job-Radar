import pytest
from job_radar.ranking import load_terms, parse_terms, save_terms, term_breakdown


def test_alternatives_and_duplicates_count_once():
    groups = parse_terms('R | Python; microscopy; Python | R; microscopy')
    result = term_breakdown({'description': 'Python and R microscopy'}, groups)
    assert result['count'] == result['total'] == 2
    assert result['matched'][0]['terms'] == ['r', 'python']


def test_single_letter_does_not_match_inside_research_and_prose_is_literal():
    groups = parse_terms('R; C++; machine learning; Lausanne')
    result = term_breakdown({'title': 'Research', 'location': 'Lausanne',
                             'description': 'C++ and machine\nlearning; no R experience required.'}, groups)
    assert result['count'] == 4  # Mentions include negation; this is not a fit claim.
    result = term_breakdown({'description': 'Research and C programming'}, groups)
    assert result['count'] == 0
    assert result['missing'] == ['r', 'c++', 'machine learning', 'lausanne']


def test_unicode_and_empty_unknown_data():
    groups = parse_terms('Français | French; Zürich')
    assert term_breakdown({'description': 'FRANÇAIS', 'location': 'Zürich'}, groups)['count'] == 2
    assert term_breakdown({}, groups)['count'] == 0
    assert term_breakdown({}, []) == {'matched': [], 'missing': [], 'count': 0, 'total': 0}


@pytest.mark.parametrize('expression', ['R |', '| Python', 'x' * 2001, ';'.join(str(i) for i in range(31)), None])
def test_invalid_preferences_rejected(expression):
    with pytest.raises(ValueError):
        parse_terms(expression)


def test_persistence_is_explicit_and_invalid_save_keeps_previous(tmp_path):
    assert load_terms(tmp_path) == ''
    assert list(tmp_path.iterdir()) == []
    save_terms(tmp_path, 'microscopy; R | Python')
    assert load_terms(tmp_path) == 'microscopy; R | Python'
    with pytest.raises(ValueError):
        save_terms(tmp_path, 'R |')
    assert load_terms(tmp_path) == 'microscopy; R | Python'
