"""
Call sequences: what runs when one function runs.

Calls are matched by name, which is the weak point and therefore what
most of these tests are about. Being certain is easy; the value is in
what happens when the parse is NOT certain, because an arrow drawn on a
guess is worse than an arrow left out and counted.
"""
import unittest

from app.application.use_cases.trace_call_sequence import (
    SequenceNotTraceableError,
    TraceCallSequenceUseCase,
)
from app.domain.entities import ParsedRepoIndex, ParsedSymbol

REPO, SHA = "acme/widgets", "abc123"


def sym(name, file, start=1, sites=()):
    return ParsedSymbol(
        name=name, kind="function", file_path=file,
        start_line=start, end_line=start + 4,
        calls=[n for n, _ in sites], call_sites=list(sites),
    )


class FakeCache:
    def __init__(self, index):
        self._index = index

    async def get_parsed_index(self, *a):
        return self._index

    async def get(self, *a):
        return None


def trace(symbols, edges=(), *, entry=("api/views.py", "handler"), **kwargs):
    import asyncio

    index = ParsedRepoIndex(
        repo_full_name=REPO, commit_sha=SHA,
        symbols=list(symbols), module_edges=list(edges),
    )
    uc = TraceCallSequenceUseCase(cache=FakeCache(index), **kwargs)
    return asyncio.run(
        uc.execute(
            repo_full_name=REPO, commit_sha=SHA,
            entry_file=entry[0], entry_symbol=entry[1],
        )
    )


class SequenceTest(unittest.TestCase):
    def test_calls_are_ordered_the_way_they_are_written(self):
        # A sequence diagram read top to bottom must match the source read
        # top to bottom, or it is telling a different story than the file.
        seq = trace([
            sym("handler", "api/views.py", 10, [("second", 20), ("first", 12)]),
            sym("first", "api/a.py"),
            sym("second", "api/b.py"),
        ])
        self.assertEqual([s.to_symbol for s in seq.steps], ["first", "second"])
        self.assertEqual([s.line for s in seq.steps], [12, 20])

    def test_nesting_is_recorded_as_depth(self):
        seq = trace([
            sym("handler", "api/views.py", 1, [("outer", 2)]),
            sym("outer", "api/a.py", 1, [("inner", 3)]),
            sym("inner", "api/b.py"),
        ])
        self.assertEqual([(s.to_symbol, s.depth) for s in seq.steps],
                         [("outer", 0), ("inner", 1)])

    def test_a_call_that_leaves_the_repo_is_counted_not_drawn(self):
        seq = trace([sym("handler", "api/views.py", 1, [("print", 2), ("json", 3)])])
        self.assertEqual(seq.steps, [])
        self.assertEqual(seq.unresolved_calls, {"json": 1, "print": 1})
        self.assertEqual(seq.unresolved_total, 2)

    def test_the_callers_own_file_wins_a_name_clash(self):
        seq = trace([
            sym("handler", "api/views.py", 1, [("save", 2)]),
            sym("save", "api/views.py", 30),
            sym("save", "core/db.py", 5),
        ])
        self.assertEqual([s.to_file for s in seq.steps], ["api/views.py"])

    def test_an_imported_file_resolves_a_name_clash(self):
        seq = trace(
            [
                sym("handler", "api/views.py", 1, [("save", 2)]),
                sym("save", "core/db.py", 5),
                sym("save", "legacy/db.py", 9),
            ],
            edges=[("api/views.py", "core/db.py")],
        )
        self.assertEqual([s.to_file for s in seq.steps], ["core/db.py"])

    def test_an_unresolvable_clash_is_reported_rather_than_guessed(self):
        # Neither candidate is in the caller's file, and the caller
        # imports both. Picking one would draw a call that may not happen.
        seq = trace(
            [
                sym("handler", "api/views.py", 1, [("save", 2)]),
                sym("save", "core/db.py", 5),
                sym("save", "legacy/db.py", 9),
            ],
            edges=[("api/views.py", "core/db.py"), ("api/views.py", "legacy/db.py")],
        )
        self.assertEqual(seq.steps, [])
        self.assertEqual(seq.ambiguous_calls, {"save": 1})
        self.assertEqual(seq.unresolved_calls, {})

    def test_ambiguous_and_external_are_counted_separately(self):
        # They mean different things: one is a call we could not place,
        # the other a call that is simply not ours.
        seq = trace(
            [
                sym("handler", "api/views.py", 1, [("save", 2), ("print", 3)]),
                sym("save", "core/db.py", 5),
                sym("save", "legacy/db.py", 9),
            ],
            edges=[("api/views.py", "core/db.py"), ("api/views.py", "legacy/db.py")],
        )
        self.assertEqual(seq.ambiguous_calls, {"save": 1})
        self.assertEqual(seq.unresolved_calls, {"print": 1})

    def test_recursion_is_drawn_once_and_not_followed(self):
        seq = trace([sym("handler", "api/views.py", 1, [("handler", 3)])])
        self.assertEqual(len(seq.steps), 1)
        self.assertTrue(seq.steps[0].is_recursive)

    def test_mutual_recursion_terminates(self):
        seq = trace([
            sym("handler", "api/views.py", 1, [("pong", 2)]),
            sym("pong", "api/a.py", 1, [("handler", 2)]),
        ])
        self.assertLessEqual(len(seq.steps), 4)
        self.assertTrue(any(s.is_recursive for s in seq.steps))

    def test_depth_is_capped_and_the_page_is_told(self):
        symbols = [sym("handler", "api/views.py", 1, [("f0", 2)])]
        for i in range(8):
            symbols.append(sym(f"f{i}", f"api/f{i}.py", 1, [(f"f{i + 1}", 2)]))
        symbols.append(sym("f8", "api/f8.py"))
        seq = trace(symbols, max_depth=3)
        self.assertEqual(len(seq.steps), 3)
        self.assertTrue(seq.truncated)

    def test_the_step_cap_also_reports_truncation(self):
        symbols = [sym("handler", "api/views.py", 1,
                       [(f"f{i}", i + 2) for i in range(20)])]
        symbols += [sym(f"f{i}", f"api/f{i}.py") for i in range(20)]
        seq = trace(symbols, max_steps=5)
        self.assertEqual(len(seq.steps), 5)
        self.assertTrue(seq.truncated)

    def test_participants_are_ordered_by_first_appearance(self):
        seq = trace([
            sym("handler", "api/views.py", 1, [("b", 2), ("a", 3)]),
            sym("a", "z/a.py"),
            sym("b", "m/b.py"),
        ])
        # b is called first (line 2), so its file comes before a's.
        self.assertEqual(seq.participants, ["api/views.py", "m/b.py", "z/a.py"])

    def test_a_missing_entry_symbol_is_refused(self):
        with self.assertRaises(SequenceNotTraceableError):
            trace([sym("other", "api/views.py")])

    def test_a_repo_with_no_parse_is_refused(self):
        import asyncio

        uc = TraceCallSequenceUseCase(cache=FakeCache(None))
        with self.assertRaises(SequenceNotTraceableError):
            asyncio.run(uc.execute(repo_full_name=REPO, commit_sha=SHA,
                                   entry_file="a.py", entry_symbol="f"))

    def test_an_entry_that_calls_nothing_internal_is_empty_not_an_error(self):
        seq = trace([sym("handler", "api/views.py", 1, [("print", 2)])])
        self.assertTrue(seq.is_empty)
        self.assertEqual(seq.entry_symbol, "handler")


class SuggestedEntriesTest(unittest.TestCase):
    def suggest(self, symbols, edges=()):
        import asyncio

        index = ParsedRepoIndex(
            repo_full_name=REPO, commit_sha=SHA,
            symbols=list(symbols), module_edges=list(edges),
        )
        uc = TraceCallSequenceUseCase(cache=FakeCache(index))
        return asyncio.run(uc.suggest_entries(repo_full_name=REPO, commit_sha=SHA))

    def test_symbols_calling_nothing_internal_are_not_offered(self):
        # Offering them would mostly offer empty diagrams.
        entries = self.suggest([
            sym("useful", "a.py", 1, [("helper", 2)]),
            sym("helper", "b.py"),
            sym("lonely", "c.py", 1, [("print", 2)]),
        ])
        self.assertEqual([e["symbol"] for e in entries], ["useful"])

    def test_ranked_by_how_much_sequence_there_is_to_show(self):
        entries = self.suggest([
            sym("small", "a.py", 1, [("helper", 2)]),
            sym("big", "b.py", 1, [("helper", 2), ("other", 3)]),
            sym("helper", "h.py"),
            sym("other", "o.py"),
        ])
        self.assertEqual([e["symbol"] for e in entries], ["big", "small"])
        self.assertEqual(entries[0]["internal_calls"], 2)

    def test_an_unparsed_repo_is_refused(self):
        import asyncio

        uc = TraceCallSequenceUseCase(cache=FakeCache(None))
        with self.assertRaises(SequenceNotTraceableError):
            asyncio.run(uc.suggest_entries(repo_full_name=REPO, commit_sha=SHA))


if __name__ == "__main__":
    unittest.main()


class EntryKindTest(unittest.TestCase):
    """
    Only things that run can be a starting point.

    A class node contains its methods, so its call count is their sum.
    That put classes at the top of every ranking — the picker opened on
    _ProjectDetailScreenState with 349 calls, whose flowchart is empty
    because a class body has no single path through it, while followUser
    was nowhere to be seen.
    """

    def suggest(self, symbols, edges=()):
        import asyncio

        index = ParsedRepoIndex(
            repo_full_name=REPO, commit_sha=SHA,
            symbols=list(symbols), module_edges=list(edges),
        )
        uc = TraceCallSequenceUseCase(cache=FakeCache(index))
        return asyncio.run(uc.suggest_entries(repo_full_name=REPO, commit_sha=SHA))

    @staticmethod
    def sym_of(kind, name, file, sites):
        return ParsedSymbol(
            name=name, kind=kind, file_path=file, start_line=1, end_line=9,
            calls=[n for n, _ in sites], call_sites=list(sites),
        )

    def test_a_class_is_never_offered_even_with_the_most_calls(self):
        entries = self.suggest([
            self.sym_of("class", "Screen", "ui.dart", [("helper", 2), ("helper", 3), ("helper", 4)]),
            self.sym_of("method", "onTap", "ui.dart", [("helper", 5)]),
            self.sym_of("function", "helper", "util.dart", []),
        ])
        names = [e["symbol"] for e in entries]
        self.assertNotIn("Screen", names)
        self.assertIn("onTap", names)

    def test_other_non_callable_kinds_are_excluded_too(self):
        entries = self.suggest([
            self.sym_of("struct", "S", "a.rs", [("helper", 2)]),
            self.sym_of("interface", "I", "a.java", [("helper", 2)]),
            self.sym_of("module", "M", "a.rb", [("helper", 2)]),
            self.sym_of("function", "helper", "u.py", []),
            self.sym_of("function", "run", "m.py", [("helper", 2)]),
        ])
        self.assertEqual([e["symbol"] for e in entries], ["run"])

    def test_the_cap_keeps_the_best_of_both_orders(self):
        # The client re-sorts this list per diagram, so truncating by one
        # criterion hides the other's best candidates entirely. Ranked by
        # calls alone, every branchy function fell outside the cap and
        # re-sorting forty branchless methods got nowhere.
        symbols = [
            self.sym_of("function", f"caller{i}", "a.py", [("helper", 2)] * (20 - i))
            for i in range(8)
        ]
        branchy = ParsedSymbol(
            name="branchy", kind="function", file_path="b.py", start_line=1, end_line=9,
            calls=["helper"], call_sites=[("helper", 2)], branch_count=9,
        )
        symbols.append(branchy)
        symbols.append(self.sym_of("function", "helper", "u.py", []))

        import asyncio

        index = ParsedRepoIndex(repo_full_name=REPO, commit_sha=SHA, symbols=symbols)
        entries = asyncio.run(
            TraceCallSequenceUseCase(cache=FakeCache(index)).suggest_entries(
                repo_full_name=REPO, commit_sha=SHA, limit=4
            )
        )
        names = [e["symbol"] for e in entries]
        self.assertEqual(len(entries), 4)
        self.assertIn("branchy", names, "the branchiest must survive a cap ranked by calls")
        self.assertIn("caller0", names, "so must the one that calls the most")

    def test_symbols_the_parser_could_not_name_are_not_offered(self):
        entries = self.suggest([
            self.sym_of("method", "<anonymous>", "a.dart", [("helper", 2)]),
            self.sym_of("method", "real", "a.dart", [("helper", 2)]),
            self.sym_of("function", "helper", "u.dart", []),
        ])
        self.assertEqual([e["symbol"] for e in entries], ["real"])
