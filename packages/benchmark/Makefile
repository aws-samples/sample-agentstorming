# Agent Storming benchmark Makefile
# Single-command reproducibility: `make all` runs the entire pipeline.
# `make paper` additionally rebuilds the PDF + DOCX from the latest results.

PYTHON ?= python3
VENV := .venv
ACT := . $(VENV)/bin/activate &&
PAPER_DIR := ../../dev/paper
NEURIPS_DIR := $(PAPER_DIR)/neurips

.PHONY: help
help: ## Show this help.
	@awk 'BEGIN{FS=":.*## "; printf "Targets:\n"} /^[a-zA-Z0-9_-]+:.*## /{printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------- environment ----------

$(VENV)/pyvenv.cfg:
	$(PYTHON) -m venv $(VENV)
# pip and wheel are pinned for the same reason every other tool here is: an
# unpinned bootstrap resolves to whatever is current, so two runs of the same
# commit can build in different environments.
	$(ACT) pip install pip==26.2.1 wheel==0.48.0

.PHONY: setup
setup: $(VENV)/pyvenv.cfg ## Create venv and install deps (editable).
	$(ACT) pip install -e ../client-py -e .
	$(ACT) python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"

# ---------- datasets ----------

.PHONY: data
data: setup ## Download and cache all benchmark datasets.
	$(ACT) agentstorming-bench data download

# ---------- run tiers ----------

.PHONY: smoke
smoke: data ## 1 question across all 7 configs (~1 USD).
	$(ACT) agentstorming-bench run --tier smoke

.PHONY: pilot
pilot: data ## 30 questions x 7 configs x 3 seeds (~35 USD, ~90 min).
	$(ACT) agentstorming-bench run --tier pilot

.PHONY: full
full: data ## 200 questions x 7 configs x 3 seeds (~200 USD, ~5-10 hours).
	$(ACT) agentstorming-bench run --tier full

.PHONY: stretch
stretch: data ## Optional: GPQA Diamond 198 x top 4 configs x 3 seeds (needs HF_TOKEN).
	$(ACT) agentstorming-bench run --tier stretch

# ---------- analysis ----------

.PHONY: analyse
analyse: ## Rebuild stats, tables, and plots from existing raw results.
	$(ACT) agentstorming-bench analyse --include-metrics

.PHONY: analyse-fast
analyse-fast: ## Rebuild accuracy tables and plots without running LLM-judge / CAR on transcripts.
	$(ACT) agentstorming-bench analyse --no-include-metrics

.PHONY: paper-tables
paper-tables: analyse ## Emit LaTeX tables + plots into dev/paper/.
	$(ACT) agentstorming-bench emit-paper-assets --out $(PAPER_DIR)

# ---------- paper ----------

.PHONY: pdf
pdf: ## Rebuild the NeurIPS-style PDF from the current tables.
	cd $(NEURIPS_DIR) && tectonic -X compile agentstorming.tex

.PHONY: docx
docx: ## Rebuild the DOCX from the current LaTeX.
	cd $(NEURIPS_DIR) && pandoc -s agentstorming.tex \
	  --bibliography=../references.bib --bibliography=../references-extra.bib --citeproc \
	  -o ../agentstorming-v2.docx

.PHONY: paper
paper: paper-tables pdf docx ## Full paper rebuild: tables + plots + PDF + DOCX.

# ---------- literature ----------

.PHONY: lit-review
lit-review: ## Harvest 5k+ arXiv papers + consolidate into bib/md.
	$(ACT) python ../../dev/research/lit-review/search_arxiv.py
	$(ACT) python ../../dev/research/lit-review/consolidate.py
	$(ACT) python ../../dev/research/lit-review/curate_refs.py

# ---------- clean ----------

.PHONY: clean
clean: ## Remove venv and results (keeps raw transcripts).
	rm -rf $(VENV) build dist *.egg-info

.PHONY: clean-all
clean-all: clean ## Also wipe results/.
	rm -rf results

# ---------- one shot ----------

.PHONY: all
all: setup data smoke pilot full analyse paper ## Full pipeline end-to-end.

.PHONY: quick
quick: setup data smoke pilot analyse paper ## Skip the full run (pilot only).
