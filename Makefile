PYTHON := .venv/Scripts/python.exe

.PHONY: lint type test ci up logs

lint:
	$(PYTHON) -m ruff check trading_bot tests
	$(PYTHON) -m black --check trading_bot tests

type:
	$(PYTHON) -m mypy trading_bot

test:
	$(PYTHON) -m pytest

ci: lint type
	$(PYTHON) -m pytest -m "not red"

up:
	docker compose up -d

logs:
	docker compose logs -f
