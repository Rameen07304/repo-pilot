"""
No source file may contain a raw control byte.

This exists because the same mistake happened twice. Writing a regex
like r"\\bword\\b" through a layer that unescapes once more turns the
two characters backslash-b into a single 0x08 BACKSPACE byte. The file
still imports, the tests that do not cover that branch still pass, and
the regex silently matches nothing at all -- the detection it guards
just quietly stops finding anything.

It is invisible in an editor and invisible in a diff. It is one line to
catch.
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Tab, newline and carriage return are ordinary whitespace in source.
ALLOWED = {0x09, 0x0A, 0x0D}

SEARCHED = ("app", "tests", "mcp_server.py")


def source_files():
    for target in SEARCHED:
        path = ROOT / target
        if path.is_file():
            yield path
            continue
        for pattern in ("*.py", "*.js", "*.css", "*.html"):
            for found in path.rglob(pattern):
                if "node_modules" not in found.parts and "__pycache__" not in found.parts:
                    yield found


class NoControlBytesTest(unittest.TestCase):
    def test_no_source_file_contains_a_raw_control_byte(self):
        offenders = []
        checked = 0
        for path in source_files():
            checked += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                for char in line:
                    code = ord(char)
                    if code < 0x20 and code not in ALLOWED:
                        offenders.append(
                            f"{path.relative_to(ROOT).as_posix()}:{number} "
                            f"contains 0x{code:02X} "
                            f"(did an escape like \\b survive as a literal byte?)"
                        )
                        break

        self.assertGreater(checked, 50, "the file sweep should have found the source tree")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
