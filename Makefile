.PHONY: install test lint example figures paper clean

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check src tests scripts

example:
	temporal-invoice-clearing run \
		--input examples/synthetic_atomic_records.csv \
		--output build/example \
		--start-date 2025-01-01 \
		--end-date 2025-01-12 \
		--regime both --method both --write-log

figures:
	python scripts/make_paper_figures.py \
		--annual results/annual_series_2012_2023.csv \
		--output build/figures \
		--curve 2012=results/daily_curves/annual_2012_daily_curves.csv \
		--curve 2020=results/daily_curves/annual_2020_daily_curves.csv \
		--curve 2022=results/daily_curves/harmonized_2022_daily_curves.csv \
		--curve 2023=results/daily_curves/carryover_2023_attributed_daily_curves.csv \
		--bridge 2023=2024-01-01

paper:
	cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex

clean:
	rm -rf build .pytest_cache .ruff_cache
	cd paper && latexmk -C || true
