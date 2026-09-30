from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Protocol

from .models import Extraction, Symbol


class Extractor(Protocol):
    name: str

    def supports(self, path: Path) -> bool: ...
    def extract(self, path: Path, root: Path) -> Extraction: ...


def module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    params = getattr(node, "type_params", [])
    generics = "[" + ", ".join(ast.unparse(p) for p in params) + "]" if params else ""
    returns = " -> " + ast.unparse(node.returns) if node.returns else ""
    return f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}{generics}({args}){returns}"


def normalized(node: ast.AST) -> str:
    return ast.dump(node, annotate_fields=True, include_attributes=False)


class PythonExtractor:
    name = "python"

    def supports(self, path: Path) -> bool:
        return path.suffix in {".py", ".pyi"}

    def extract(self, path: Path, root: Path) -> Extraction:
        relative = path.relative_to(root)
        result = Extraction(relative.as_posix(), "python", module_name(relative))
        try:
            tree = ast.parse(path.read_bytes(), filename=str(relative))
        except (SyntaxError, UnicodeError, ValueError) as exc:
            result.error = f"{relative.as_posix()}: Python parse failed: {exc}"
            return result
        structure: list[Any] = []
        import_structure: set[str] = set()
        imports: set[str] = set()
        package = result.module if relative.name == "__init__.py" else result.module.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
                import_structure.add(normalized(node))
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pieces = package.split(".") if package else []
                    pieces = pieces[:max(0, len(pieces) - node.level + 1)]
                    base = ".".join([*pieces, *([base] if base else [])])
                imports.add(base)
                imports.update(f"{base}.{a.name}".strip(".") for a in node.names if a.name != "*")
                import_structure.add(normalized(node))

        def visit(nodes: list[ast.stmt], prefix: str = "") -> None:
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("_") and node.name != "__init__":
                        continue
                    text = signature(node)
                    name = prefix + node.name
                    result.symbols.append(Symbol(name, prefix + text, "method" if prefix else "function"))
                    structure.append(("function", name, normalized(node.args), normalized(node.returns) if node.returns else None,
                                      [normalized(d) for d in node.decorator_list],
                                      [normalized(p) for p in getattr(node, "type_params", [])],
                                      isinstance(node, ast.AsyncFunctionDef)))
                elif isinstance(node, ast.ClassDef):
                    if node.name.startswith("_"):
                        continue
                    name = prefix + node.name
                    bases = [ast.unparse(base) for base in node.bases]
                    text = f"class {name}" + ("(" + ", ".join(bases) + ")" if bases else "")
                    result.symbols.append(Symbol(name, text, "class"))
                    structure.append(("class", name, [normalized(b) for b in node.bases],
                                      [normalized(k) for k in node.keywords], [normalized(d) for d in node.decorator_list],
                                      [normalized(p) for p in getattr(node, "type_params", [])]))
                    visit(node.body, name + ".")
                elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith)):
                    # Definitions guarded by TYPE_CHECKING/platform conditions remain architectural.
                    visit(node.body, prefix)
                    visit(getattr(node, "orelse", []), prefix)
                    visit(getattr(node, "finalbody", []), prefix)
                    for handler in getattr(node, "handlers", []):
                        visit(handler.body, prefix)

        visit(tree.body)
        result.imports = sorted(imports)
        result.structure = {"imports": sorted(import_structure), "symbols": structure,
                            "entrypoint": any(isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                                              and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__"
                                              for n in tree.body)}
        return result


class GenericExtractor:
    name = "generic"

    def supports(self, path: Path) -> bool:
        return True

    def extract(self, path: Path, root: Path) -> Extraction:
        relative = path.relative_to(root).as_posix()
        return Extraction(relative, path.suffix.lstrip(".") or "text", "",
                          structure=hashlib.sha256(path.read_bytes()).hexdigest())


# Specific extractors precede generic; each file is claimed exactly once.
REGISTRY: dict[str, Extractor] = {"python": PythonExtractor(), "generic": GenericExtractor()}


def extract_file(path: Path, root: Path, enabled: str | list[str]) -> Extraction | None:
    names = list(REGISTRY) if enabled == "auto" else list(enabled)
    names.sort(key=lambda name: name == "generic")
    for name in names:
        extractor = REGISTRY[name]
        if extractor.supports(path):
            return extractor.extract(path, root)
    return None
