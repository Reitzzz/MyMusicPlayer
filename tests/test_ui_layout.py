"""Regression checks for the static webview layout and theme contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = PROJECT_ROOT / "ui" / "app.css"
HTML_PATH = PROJECT_ROOT / "ui" / "index.html"


def css_rule(source: str, selector: str) -> str:
    """Return the first declaration block for a simple selector."""
    match = re.search(
        rf"(?m)^{re.escape(selector)}\s*\{{(?P<body>.*?)\n\}}",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"Missing CSS rule: {selector}")
    return match.group("body")


class WebviewLayoutRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_scheme_b_light_palette_is_pinned(self):
        expected = {
            "--bg": "#eeeef7",
            "--surface": "#fafaff",
            "--surface-2": "#e2e3f2",
            "--text": "#17182a",
            "--muted": "#636580",
            "--line": "#d0d2e6",
            "--primary": "#5744c7",
            "--on-primary": "#ffffff",
            "--soft": "#e2ddff",
            "--danger": "#b8344b",
        }
        root = css_rule(self.css, ":root")
        for name, value in expected.items():
            self.assertRegex(root, rf"{re.escape(name)}\s*:\s*{re.escape(value)}\s*;")

        self.assertNotIn("#b8acff", self.css)
        self.assertNotIn("#55598a", self.css)
        self.assertIn('<meta name="color-scheme" content="light">', self.html)

    def test_now_panel_keeps_full_clock_clear_of_next_copy(self):
        panel = css_rule(self.css, ".now-panel")
        self.assertRegex(
            panel,
            r"grid-template-columns\s*:\s*minmax\(max-content,\s*\.7fr\)\s+minmax\(0,\s*1\.3fr\)\s*;",
        )
        self.assertRegex(panel, r"gap\s*:\s*\d+px\s*;")

        clock = css_rule(self.css, ".live-clock")
        self.assertRegex(clock, r"white-space\s*:\s*nowrap\s*;")
        self.assertRegex(self.css, r"\.now-panel\s*>\s*div\s*\{[^}]*min-width\s*:\s*0\s*;")

    def test_narrow_now_panel_stacks_before_clock_can_overflow(self):
        self.assertRegex(
            self.css,
            re.compile(
                r"@media\s*\(max-width:\s*420px\)[^{]*\{.*?\.now-panel\s*\{[^}]*grid-template-columns\s*:\s*1fr\s*;",
                re.DOTALL,
            ),
        )


if __name__ == "__main__":
    unittest.main()
