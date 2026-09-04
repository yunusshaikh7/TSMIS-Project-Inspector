"""Packaging inventory guard: every flat scripts/*.py module is declared in
app.spec's APP_MODULES (PyInstaller hidden imports — several are imported
lazily) AND in self_test.APP_MODULES (what the frozen exe proves it can
import), with no strays or duplicates.

    python build\\check_app_modules.py
"""
import ast
import sys

from _checklib import ROOT, Checker, scripts_path

scripts_path()


def _spec_modules():
    tree = ast.parse((ROOT / "build" / "app.spec").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "APP_MODULES" for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError("APP_MODULES not found in build/app.spec")


def main():
    c = Checker()
    inventory = {p.stem for p in (ROOT / "scripts").glob("*.py")} | {"version"}
    spec = _spec_modules()
    import self_test
    c.check("app.spec APP_MODULES has no duplicates", len(spec) == len(set(spec)))
    c.check("every flat scripts/ module is in app.spec", set(spec) >= inventory, sorted(inventory - set(spec)))
    c.check("no stray app.spec entry", set(spec) <= inventory, sorted(set(spec) - inventory))
    c.check("self_test.APP_MODULES matches the inventory", set(self_test.APP_MODULES) == inventory,
            sorted(set(self_test.APP_MODULES) ^ inventory))
    raise SystemExit(c.summary())


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
