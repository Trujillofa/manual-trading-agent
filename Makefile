.PHONY: install test lint format scan dashboard health deploy clean

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

scan:
	python -m src.cli scan

dashboard:
	python -m src.cli dashboard --days 7

health:
	python -m src.cli healthcheck

news:
	python -m src.cli news --hours 48

analyze:
	python -m src.cli analyze $(PAIR)

backtest:
	python -m src.cli backtest-enhanced --pair $(PAIR) --start $(START) --end $(END)

deploy:
	./scripts/deploy.sh

clean:
	rm -rf .venv build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache __pycache__
