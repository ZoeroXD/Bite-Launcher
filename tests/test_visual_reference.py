"""Guard the two drawing functions against changes from the user's original ZIP."""
import ast
import hashlib
from pathlib import Path
import unittest


class VisualReferenceTests(unittest.TestCase):
    def test_original_controller_body_is_unchanged(self):
        """Only the cable accent may change, not the original deck/platters."""
        source = Path(__file__).resolve().parents[1] / "bitedj_launcher.py"
        node = next(n for n in ast.walk(ast.parse(source.read_text()))
                    if isinstance(n, ast.FunctionDef) and n.name == "paint_controller_icon")
        body = []
        for statement in node.body:
            if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "a"
                    for target in statement.targets):
                break  # The wire animation/color begins here.
            body.append(statement)
        actual = hashlib.sha256(ast.dump(ast.Module(body=body, type_ignores=[]),
                                         include_attributes=False).encode()).hexdigest()
        self.assertEqual(actual, "2d40449cfced52f6ae0e5a93c3fe8109f497bcdc6a1c6368db5c6edb6fc8f73d")

    def test_original_fin_functions_preserved(self):
        expected = {
            "fin_path": "8e60dbe9777c6ea438e8b88fedd19a768eb1472e8d7a56075f1fd10693dccb29",
            "render_fin": "3d8cc310b67923e5a0cd034a08ddb9f1a81b08b13897754dd1aadb003a5261e5",
        }
        source = Path(__file__).resolve().parents[1] / "bitedj_launcher.py"
        tree = ast.parse(source.read_text())
        for name, digest in expected.items():
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            actual = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
            self.assertEqual(actual, digest, f"{name} changed from the attached reference ZIP")
