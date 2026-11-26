import json
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from governed_analytics import demo_ui
from governed_analytics.synthetic_data import generate_database


@pytest.fixture
def fixture_path(tmp_path, monkeypatch):
    db = tmp_path / "synthetic.sqlite3"
    generate_database(db)
    monkeypatch.setattr(demo_ui, "DEMO_DB", db)
    monkeypatch.setattr(demo_ui, "DEMO_AUDIT", tmp_path / "audit.jsonl")
    return db


def _app():
    app = AppTest.from_file(demo_ui.ROOT / "app.py", default_timeout=30).run()
    assert not app.exception
    return app


def _ask(app, question):
    app.text_area(key="question").set_value(question)
    app.button(key="run_analysis").click().run()
    assert not app.exception


def test_metric_catalogue_and_live_evaluation_use_tracked_sources(fixture_path, monkeypatch):
    setup = Mock(side_effect=AssertionError("Page load must not initialise OpenAI"))
    monkeypatch.setattr(demo_ui.OpenAIInterpreter, "from_environment", setup)
    app = _app()
    assert [tab.label for tab in app.tabs] == [
        "Analytics Assistant", "Metric Governance", "Evaluation Dashboard",
    ]
    catalogue = app.tabs[1].dataframe[0].value
    matrix = app.tabs[1].dataframe[1].value.set_index("Metric")
    assert len(catalogue) == len(matrix) == 8
    assert matrix.loc["Payment Failure Rate", "Payments"] == "Allowed"
    assert matrix.loc["Payment Failure Rate", "Product"] == "Denied"
    comparison = app.tabs[2].dataframe[0].value.set_index("Measure")
    supplemental = app.tabs[2].dataframe[1].value.set_index("Measure")
    assert comparison.loc["Status accuracy", "Baseline"] == "28/40"
    assert comparison.loc["Status accuracy", "Optimised"] == "40/40"
    assert comparison.loc["P95 latency (ms)", "Optimised"] == "1767.590"
    assert supplemental.loc["Status accuracy", "Result"] == "15/15"
    setup.assert_not_called()


def test_denial_shows_audit_evidence_without_calling_openai(fixture_path, monkeypatch):
    setup = Mock(side_effect=AssertionError("Denial must happen before OpenAI configuration"))
    monkeypatch.setattr(demo_ui.OpenAIInterpreter, "from_environment", setup)
    app = _app()
    app.selectbox(key="example").select("Unauthorised metric")
    app.button(key="use_example").click().run()
    assert "payment failure rate" in app.text_area(key="question").value
    app.button(key="run_analysis").click().run()
    assert not app.exception
    assistant = app.tabs[0]
    assert any(item.value == "Status · denied" for item in assistant.warning)
    assert any(item.value == "Authorisation decision · denied" for item in assistant.markdown)
    assert any(item.value == "SQL attempted · No" for item in assistant.markdown)
    assert not assistant.dataframe
    event = json.loads(demo_ui.DEMO_AUDIT.read_text())
    assert event["sql_attempted"] is False
    assert any(item.value == f"Audit request ID · {event['request_id']}" for item in assistant.caption)
    setup.assert_not_called()


def test_grouped_results_and_evidence_clear_on_role_change(fixture_path, monkeypatch):
    interpreter = Mock(return_value=json.dumps({
        "intent_status": "answerable", "metric_id": "average_order_value",
        "dimension": "customer_segment", "start_date": "2025-06-01", "end_date": "2025-07-01",
    }))
    monkeypatch.setattr(demo_ui.OpenAIInterpreter, "from_environment", lambda: interpreter)
    app = _app()
    app.selectbox(key="role").select("Marketing").run()
    _ask(app, "What was AOV by customer segment in June 2025?")
    assistant = app.tabs[0]
    assert any(item.value == "Status · answered" for item in assistant.success)
    rows = assistant.dataframe[0].value
    assert set(rows["Customer segment"]) == {"premium", "standard"}
    assert any("AUD 313.50" in item.value for item in assistant.text)
    assert any(item.value == "SQL succeeded · Yes" for item in assistant.markdown)
    assert assistant.get("vega_lite_chart") or assistant.get("arrow_vega_lite_chart")
    app.selectbox(key="role").select("Payments").run()
    assert not app.exception
    assistant = app.tabs[0]
    assert not assistant.success and not assistant.dataframe
    assert not any("Request ID" in item.value for item in assistant.caption)
    assert app.text_area(key="question").value == ""
    interpreter.assert_called_once()


def test_missing_openai_configuration_is_safe(fixture_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    app = _app()
    _ask(app, "What was GMV in June 2025?")
    assistant = app.tabs[0]
    assert any(item.value == "Status · error" for item in assistant.error)
    assert any("local environment" in item.value for item in assistant.text)
    assert any(item.value == "SQL attempted · No" for item in assistant.markdown)


def test_missing_fixture_disables_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_ui, "DEMO_DB", tmp_path / "missing.sqlite3")
    app = _app()
    app.text_area(key="question").set_value("What was GMV in June 2025?").run()
    assert app.button(key="run_analysis").proto.disabled
    assert any("synthetic database is missing" in item.value for item in app.warning)


def test_missing_audit_event_is_not_presented_as_verified(fixture_path, monkeypatch):
    monkeypatch.setattr("governed_analytics.agent.append_event", lambda path, event: None)
    app = _app()
    _ask(app, "What was the payment failure rate in June 2025?")
    assistant = app.tabs[0]
    assert any("Matching audit evidence is unavailable" in item.value for item in assistant.warning)
    assert not any("SQL attempted" in item.value for item in assistant.markdown)
