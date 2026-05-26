from pathlib import Path
import unittest


MOJIBAKE_PATTERNS = (
    "\u00c4",
    "\u00c6",
    "\u00f0\u0178",
    "\u00ef\u00b8",
    "\u00e1\u00ba",
    "\u00e1\u00bb",
    "\u00e2\u0161",
    "\u00e2\u0153",
    "\u00e2\u017e",
    "\u00e2\u2020",
    "\u00e2\u008f",
)


class SourceEncodingTests(unittest.TestCase):
    def test_runtime_sources_do_not_contain_vietnamese_mojibake(self):
        root = Path(__file__).resolve().parent
        if not (root / "bot.py").exists():
            root = root.parent
        offenders = []

        runtime_sources = [
            path
            for path in root.glob("*.py")
            if path.name not in {"seed.py"} and not path.name.startswith("test_")
        ]

        for path in runtime_sources:
            source = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(source.splitlines(), start=1):
                if any(pattern in line for pattern in MOJIBAKE_PATTERNS):
                    offenders.append(f"{path.name}:{line_no}: {line[:140]}")

        self.assertEqual([], offenders[:80])
