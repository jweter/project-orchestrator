from knowledge_engine.corpus.normalize import normalize_evidence_record


def test_normalize_evidence_record_example_1() -> None:
    assert normalize_evidence_record({'title': 'Example', 'authors': ['John Doe'], 'year': 2020}) == {'title': 'Example', 'authors': ['John Doe'], 'year': 2020}


def test_normalize_evidence_record_example_2() -> None:
    assert normalize_evidence_record({'title': 'Example', 'authors': [], 'year': 2020}) == {'title': 'Example', 'authors': [], 'year': 2020}


def test_normalize_evidence_record_example_3() -> None:
    assert normalize_evidence_record({'title': 'Example', 'authors': ['John Doe'], 'year': '2020'}) == {'title': 'Example', 'authors': ['John Doe'], 'year': 2020}


def test_normalize_evidence_record_example_4() -> None:
    assert normalize_evidence_record({'title': 'Example', 'authors': ['John Doe'], 'year': None}) == {'title': 'Example', 'authors': ['John Doe'], 'year': 2020}


def test_normalize_evidence_record_example_5() -> None:
    assert normalize_evidence_record({'title': 'Example', 'authors': ['John Doe'], 'year': 2020, 'source': 'PubMed'}) == {'title': 'Example', 'authors': ['John Doe'], 'year': 2020, 'source': 'PubMed'}
