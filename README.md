# Governed Analytics Agent

A CLI prototype for internal self-service analytics using synthetic SQLite data for customers, checkout sessions, orders, and payments.

The planned design uses LangGraph for orchestration and the OpenAI API for intent understanding. A lightweight metric registry and knowledge retrieval inform structured query plans. Deterministic role-based access control governs execution through approved parameterised SQL templates, and structured audit logs record decisions and execution outcomes.

This initial phase provides the Python package, test directory, and local setup files.

## Local setup

Use Python 3.11 or newer. Check the interpreter version before creating the virtual environment.

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import langgraph.graph, openai, pytest, sqlite3"
```

If `python3` points to an older interpreter, substitute the path to a supported Python executable. Dependency checks require no API key. `.env.example` documents the environment variable for the future OpenAI integration.

## Project layout

| Path | Purpose |
| --- | --- |
| `governed_analytics/` | Application package |
| `tests/` | Pytest test package |
| `requirements.txt` | Runtime and test dependencies |
| `.env.example` | Environment variable template |
| `.gitignore` | Rules for local credentials, environments, and generated files |
