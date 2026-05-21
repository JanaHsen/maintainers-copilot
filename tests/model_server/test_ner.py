"""Unit tests for the deterministic regex code-entity extractor."""

from __future__ import annotations

from model_server.ner import CodeEntity, extract


def _types(entities: list[CodeEntity]) -> list[tuple[str, str]]:
    return [(e.text, e.type) for e in entities]


def test_extracts_exception_class() -> None:
    out = extract("This raises IndexError on empty inputs.")
    assert ("IndexError", "exception_class") in _types(out)


def test_extracts_userwarning_and_runtimeerror() -> None:
    out = extract("UserWarning was raised and then a RuntimeError surfaced.")
    types = _types(out)
    assert ("UserWarning", "exception_class") in types
    assert ("RuntimeError", "exception_class") in types


def test_extracts_plain_function_call() -> None:
    out = extract("Calling len(df) returns the row count.")
    assert ("len", "function_call") in _types(out)


def test_extracts_dotted_method_call() -> None:
    out = extract("Use df.groupby(['a','b']).agg('sum').")
    types = _types(out)
    assert ("df.groupby", "function_call") in types
    assert ("agg", "function_call") in types


def test_extracts_module_path() -> None:
    out = extract("Documented under pandas.api.types.is_numeric_dtype.")
    assert ("pandas.api.types.is_numeric_dtype", "module_path") in _types(out)


def test_exception_overlap_wins_over_function_call() -> None:
    # KeyError("oops") would be both an exception class AND a function-shaped
    # call (the constructor). exception_class has priority so we get one entity.
    out = extract("raise KeyError('oops') here")
    types = _types(out)
    assert types.count(("KeyError", "exception_class")) == 1
    assert ("KeyError", "function_call") not in types


def test_module_path_not_emitted_when_followed_by_paren() -> None:
    # `pd.read_csv(` is a function_call, not a two-segment module_path.
    out = extract("pd.read_csv(file)")
    types = _types(out)
    assert ("pd.read_csv", "function_call") in types
    assert ("pd.read_csv", "module_path") not in types


def test_empty_text_returns_empty() -> None:
    assert extract("") == []


def test_plain_prose_emits_nothing() -> None:
    assert extract("This is a sentence with no code-shaped tokens.") == []


def test_entities_are_sorted_by_offset() -> None:
    out = extract("First call: pd.merge(a, b). Then ValueError might appear.")
    starts = [e.start for e in out]
    assert starts == sorted(starts)
