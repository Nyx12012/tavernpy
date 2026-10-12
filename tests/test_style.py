import ast
import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHES = (chr(0x2014), chr(0x2013))
SKIP = {".venv", "__pycache__", ".pytest_cache", "build", "dist"}
TEXT = (".py", ".md", ".txt", ".toml", ".cfg", ".json", ".gitignore", "LICENSE")
CREDIT = ("claude", "anthropic", "co-authored")


def files(*suffixes):
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP.intersection(path.relative_to(ROOT).parts):
            continue
        if path.suffix in suffixes or path.name in suffixes:
            yield path


def test_no_comments_in_code():
    found = []
    for path in files(".py"):
        tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
        found += [f"{path.name}:{t.start[0]}" for t in tokens if t.type == tokenize.COMMENT]
    assert found == []


def test_no_docstrings():
    found = []
    for path in files(".py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    found.append(f"{path.name}:{first.lineno}")
    assert found == []


def test_no_long_dashes_anywhere():
    found = [p.name for p in files(*TEXT) if any(d in p.read_text(encoding="utf-8") for d in DASHES)]
    assert found == []


def test_no_assistant_credit_anywhere():
    found = []
    for path in files(*TEXT):
        if path.name == "test_style.py":
            continue
        text = path.read_text(encoding="utf-8").lower()
        found += [f"{path.name}:{word}" for word in CREDIT if word in text]
    assert found == []
