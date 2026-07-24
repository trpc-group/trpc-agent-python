"""Tests for trpc_agent_sdk.tools.safety._facts."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety._facts import (
    ConcurrencyFact,
    DependencyInstallFact,
    DynamicExecFact,
    Fact,
    FileDeleteFact,
    FileReadFact,
    FileWriteFact,
    ForkBombFact,
    LargeWriteFact,
    Loc,
    LongSleepFact,
    NetworkFact,
    ParseErrorFact,
    PrivilegeFact,
    ProcessFact,
    ScriptFacts,
    SecretFlowFact,
    ShellOperatorFact,
    UnboundedLoopFact,
)
from trpc_agent_sdk.tools.safety._models import ScriptLanguage


class TestLoc:

    def test_defaults(self):
        loc = Loc()
        assert loc.label() == ""

    def test_line_only_label(self):
        assert Loc(line=10).label() == "L10"

    def test_line_and_column_label(self):
        assert Loc(line=10, column=5).label() == "L10:C5"

    def test_zero_line_no_label(self):
        assert Loc(line=0, column=5).label() == ""


class TestFactDataclasses:

    def test_fact_base_fields(self):
        f = Fact(snippet="x", loc=Loc(line=1, column=2))
        assert f.snippet == "x"
        assert f.loc.line == 1

    def test_fact_is_hashable(self):
        # Frozen dataclasses are hashable; verify a few.
        hash(FileDeleteFact(target="/tmp"))
        hash(FileWriteFact(target="/a"))
        hash(FileReadFact(target="/a", kind="credential"))
        hash(NetworkFact(target="api.example.com"))
        hash(ProcessFact(command="ls"))

    def test_file_delete_defaults(self):
        f = FileDeleteFact()
        assert f.recursive is False
        assert f.explicit is True
        assert f.target == ""

    def test_file_read_kind_choices(self):
        FileReadFact(kind="credential")
        FileReadFact(kind="dotenv")
        FileReadFact(kind="regular")

    def test_secret_flow_sink_kinds(self):
        for kind in ("output", "file", "network", "subprocess", "unknown"):
            SecretFlowFact(sink_kind=kind)


class TestScriptFacts:

    def test_defaults(self):
        sf = ScriptFacts()
        assert sf.language == ScriptLanguage.UNKNOWN
        assert sf.has_any() is False

    def test_has_any_with_finding(self):
        sf = ScriptFacts(file_deletes=(FileDeleteFact(target="/x"), ))
        assert sf.has_any() is True

    def test_has_any_with_parse_error(self):
        sf = ScriptFacts(parse_errors=(ParseErrorFact(message="e"), ))
        assert sf.has_any() is True

    def test_merge_combines_lists(self):
        a = ScriptFacts(
            file_deletes=(FileDeleteFact(target="/a"), ),
            network_calls=(NetworkFact(target="a.com"), ),
        )
        b = ScriptFacts(
            file_deletes=(FileDeleteFact(target="/b"), ),
            network_calls=(NetworkFact(target="b.com"), ),
            privilege_commands=(PrivilegeFact(command="sudo"), ),
        )
        merged = a.merge(b)
        assert len(merged.file_deletes) == 2
        assert len(merged.network_calls) == 2
        assert len(merged.privilege_commands) == 1

    def test_merge_language_fallback(self):
        a = ScriptFacts(language=ScriptLanguage.PYTHON)
        b = ScriptFacts(language=ScriptLanguage.UNKNOWN)
        assert a.merge(b).language == ScriptLanguage.PYTHON

    def test_merge_language_other_wins_when_self_unknown(self):
        a = ScriptFacts(language=ScriptLanguage.UNKNOWN)
        b = ScriptFacts(language=ScriptLanguage.BASH)
        assert a.merge(b).language == ScriptLanguage.BASH

    def test_full_fact_bag_round_trips(self):
        sf = ScriptFacts(
            language=ScriptLanguage.PYTHON,
            file_deletes=(FileDeleteFact(target="/x", recursive=True), ),
            file_writes=(FileWriteFact(target="/y"), ),
            file_reads=(FileReadFact(target="/z", kind="credential"), ),
            network_calls=(NetworkFact(target="api.example.com"), ),
            process_calls=(ProcessFact(command="ls"), ),
            shell_operators=(ShellOperatorFact(operator="&&"), ),
            privilege_commands=(PrivilegeFact(command="sudo"), ),
            dependency_installs=(DependencyInstallFact(manager="pip", command="pip install x"), ),
            unbounded_loops=(UnboundedLoopFact(kind="while-True"), ),
            fork_bombs=(ForkBombFact(pattern="classic"), ),
            long_sleeps=(LongSleepFact(duration_seconds=10.0, raw="10"), ),
            concurrency=(ConcurrencyFact(count=2, raw="threading.Thread"), ),
            large_writes=(LargeWriteFact(size=10, target="/f"), ),
            secret_flows=(SecretFlowFact(source="s", sink="print", sink_kind="output"), ),
            dynamic_execs=(DynamicExecFact(kind="eval"), ),
            parse_errors=(ParseErrorFact(message="bad"), ),
        )
        assert sf.has_any() is True
