import io
from datetime import datetime, timezone
from pathlib import Path

from orchestre.audit.legacy import render_markdown, scan
from orchestre.cli.main import main

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
SECRET = "ABCDEFGHIJKLMNOPQRSTUVWX1234567890"


def project(tmp_path: Path, files: dict) -> Path:
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def kinds(report):
    return {f.kind for f in report.findings}


def test_hardcoded_credentials_are_critical_and_never_copied(tmp_path):
    root = project(tmp_path, {"main.py": f'from binance.client import Client\napi_key = "{SECRET}"\napi_secret = "{SECRET}Z"\nclient = Client(api_key, api_secret)\n'})
    report = scan(root)
    secrets = [f for f in report.findings if f.kind == "clé en clair"]
    assert len(secrets) == 2 and all(f.severity == "CRITIQUE" for f in secrets)
    md = render_markdown(report, NOW)
    assert SECRET not in md and all(SECRET not in f.message for f in report.findings)
    assert "api_key" in md                                    # le nom de la variable et la ligne suffisent


def test_positional_credentials_in_a_client_call_are_found(tmp_path):
    root = project(tmp_path, {"a.py": f'c = Client("{SECRET}", "{SECRET}9")\n'})
    assert "clé en clair" in kinds(scan(root))


def test_placeholders_are_not_reported_as_secrets(tmp_path):
    root = project(tmp_path, {"a.py": 'api_key = "YOUR_API_KEY_HERE_PLEASE"\napi_secret = os.environ["X"]\ntoken = "xxxxxxxxxxxxxxxxxxxxxxxx"\n'})
    assert "clé en clair" not in kinds(scan(root))


def test_direct_order_placement_is_flagged_for_the_risk_agent_rule(tmp_path):
    root = project(tmp_path, {"a.py": 'client.order_limit_buy(symbol="X", quantity=1, price=1)\nexchange.create_order("X","market","buy",1)\n'})
    report = scan(root)
    assert sum(f.kind == "ordre direct" for f in report.findings) == 2
    assert "pas de testnet" in " ".join(f.kind for f in report.findings) or "plateforme réelle" in " ".join(f.message for f in report.findings)


def test_withdrawals_are_critical(tmp_path):
    root = project(tmp_path, {"a.py": 'client.withdraw(asset="USDT", address="x", amount=1)\n'})
    f = [f for f in scan(root).findings if f.kind == "retrait"][0]
    assert f.severity == "CRITIQUE"


def test_missing_stop_tests_and_risk_management_are_reported(tmp_path):
    root = project(tmp_path, {"a.py": 'client.order_market_buy(symbol="X", quantity=1)\n'})
    assert {"aucun stop", "aucun test", "aucune gestion du risque"} <= kinds(scan(root))


def test_a_reasonable_project_has_fewer_findings(tmp_path):
    root = project(tmp_path, {
        "agent.py": 'import os\nclass RiskAgent:\n    max_loss = 0.1\nclass TraderAgent:\n    pass\nclient = Client(testnet=True)\nstop_loss = 1\n',
        "tests/test_agent.py": "def test_x():\n    pass\n",
    })
    report = scan(root)
    assert report.has_tests and report.uses_testnet and report.agent_classes == ["RiskAgent", "TraderAgent"]
    assert not {"aucun test", "aucune gestion du risque", "clé en clair"} & kinds(report)


def test_environment_directories_are_ignored(tmp_path):
    root = project(tmp_path, {"venv/lib/x.py": f'api_key = "{SECRET}"\n', "app.py": "x = 1\n"})
    assert scan(root).files == ["app.py"]


def test_a_tiny_project_is_flagged_as_not_being_the_multi_agent_system(tmp_path):
    root = project(tmp_path, {"main.py": "print('bonjour')\n"})
    md = render_markdown(scan(root), NOW)
    assert "ne contient pas" in md and "multi-agents" in md and "Q-2" in md


def test_report_has_the_sections_required_by_the_specification(tmp_path):
    md = render_markdown(scan(project(tmp_path, {"main.py": "x = 1\n"})), NOW)
    for title in ("Périmètre", "Constats", "Gestion des clés", "Tests", "À compléter", "Décision de réutilisation"):
        assert title in md


def test_cli_audit_writes_the_report_without_printing_secrets(tmp_path):
    root = project(tmp_path / "legacy", {"main.py": f'api_key = "{SECRET}"\n'})
    out_file = tmp_path / "docs" / "audit-existant.md"
    out, err = io.StringIO(), io.StringIO()
    code = main(["audit-legacy", "--path", str(root), "--output", str(out_file)], environ={}, out=out, err=err)
    assert code == 0 and out_file.exists()
    assert SECRET not in out.getvalue() + err.getvalue() + out_file.read_text(encoding="utf-8")
    assert "CRITIQUE" in out.getvalue()


def test_cli_audit_rejects_a_missing_path(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    code = main(["audit-legacy", "--path", str(tmp_path / "nope")], environ={}, out=out, err=err)
    assert code == 2 and "introuvable" in err.getvalue()
