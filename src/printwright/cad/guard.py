"""Static checks on model scripts before they run.

Model scripts are written by an LLM and executed on your machine. This guard is defence in depth, not a
sandbox: it rejects scripts that import anything beyond geometry/maths libraries, call dynamic-execution
built-ins, touch dunder attributes, or write files. The runner additionally executes scripts in a separate
process with a timeout and without API keys in its environment. Review scripts before running them if you
use printwright with untrusted prompts. See SECURITY.md.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

ALLOWED_IMPORTS = {
    "cadquery",
    "OCP",
    "math",
    "cmath",
    "numpy",
    "typing",
    "dataclasses",
    "itertools",
    "functools",
    "collections",
    "enum",
    "fractions",
    "decimal",
    "statistics",
    "random",
    "copy",
    "trimesh",
    "manifold3d",
    "printwright.cad.organic",
    "__future__",
}
BANNED_CALLS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
}
BANNED_ATTRS = {
    "exporters",
    "export",
    "save",
    "system",
    "popen",
    "remove",
    "unlink",
    "rmtree",
    "write_text",
    "write_bytes",
    "tofile",
    "savetxt",
    "load_library",
}


@dataclass
class GuardIssue:
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


def _module_allowed(name: str) -> bool:
    return any(name == a or name.startswith(a + ".") for a in ALLOWED_IMPORTS)


def check_python(source: str) -> list[GuardIssue]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [GuardIssue(e.lineno or 0, f"syntax error: {e.msg}")]
    issues: list[GuardIssue] = []
    has_build = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not _module_allowed(a.name):
                    issues.append(GuardIssue(node.lineno, f"import of '{a.name}' is not allowed"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level or not _module_allowed(mod):
                issues.append(GuardIssue(node.lineno, f"import from '{mod}' is not allowed"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BANNED_CALLS:
            issues.append(GuardIssue(node.lineno, f"call to '{node.func.id}()' is not allowed"))
        elif isinstance(node, ast.Attribute):
            if (
                node.attr.startswith("__")
                and node.attr.endswith("__")
                and node.attr not in {"__init__", "__name__"}
            ):
                issues.append(GuardIssue(node.lineno, f"dunder attribute '{node.attr}' is not allowed"))
            elif node.attr in BANNED_ATTRS:
                issues.append(
                    GuardIssue(node.lineno, f"'.{node.attr}' is not allowed (the runner exports files)")
                )
        elif isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__spec__"}:
            issues.append(GuardIssue(node.lineno, f"'{node.id}' is not allowed"))
        elif isinstance(node, ast.FunctionDef) and node.name == "build":
            has_build = True
    if not has_build:
        issues.append(GuardIssue(1, "the script must define build(p) returning the parts"))
    return issues


def check_openscad(source: str) -> list[GuardIssue]:
    """OpenSCAD cannot run programs, but import()/include can read arbitrary files."""
    issues = []
    for i, line in enumerate(source.splitlines(), 1):
        s = line.strip()
        if s.startswith(("include", "use")) and "<" in s and ("/" in s.split("<", 1)[1][:2] or ".." in s):
            issues.append(GuardIssue(i, "include/use of absolute or parent paths is not allowed"))
        if "import(" in s and ("/" in s.split("import(", 1)[1][:3] or ".." in s):
            issues.append(GuardIssue(i, "import() of absolute or parent paths is not allowed"))
    return issues
