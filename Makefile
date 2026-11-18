.PHONY: install test lint study pdf

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check src tests

study:
	python -m marketmodel.pipeline --out outputs

pdf: study
	cd outputs && soffice --headless --convert-to pdf ERCOT_Market_Consultant_Report.docx
