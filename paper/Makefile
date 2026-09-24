# Paper bundle. `make` builds figures then the PDF.
REPO    := $(shell cd .. && pwd)
TEXENV  := tex
FIGS    := $(wildcard figures/figure_*.py)
PDFS    := $(patsubst figures/figure_%.py,build/fig_%.pdf,$(FIGS))

.PHONY: all figures paper verify overleaf clean distclean

all: paper

figures: $(PDFS)

# Each script names its own output, so key the rule off the script and touch nothing else.
build/fig_%.pdf: figures/figure_%.py figures/figstyle.py
	cd figures && PYTHONPATH=$(REPO) python3 $(notdir $<)

# Fails if any headline number in paper.tex no longer matches the artifact that
# produced it. Cheap, and it is the check this repo has historically needed most.
verify:
	python3 verify_numbers.py

paper: figures verify
	conda run -n $(TEXENV) tectonic -X compile paper.tex --outdir build
	@echo "-> build/paper.pdf"

# Overleaf upload bundle. Ships a pre-built paper.bbl so the bibliography renders even
# if Overleaf's BibTeX pass does not run: \bibliography{refs} inputs paper.bbl when one
# exists. Figures go in build/ because paper.tex sets \graphicspath{{build/}}.
#
# The .bbl covers "BibTeX never ran". It does NOT cover "refs.bib is missing": measured
# 2026-09-22, a build with refs.bib deleted and paper.bbl present gives an EMPTY
# bibliography and 12 pages, because BibTeX does run, finds no database, and overwrites
# the shipped .bbl. Both files have to go up.
overleaf: figures
	rm -rf build/overleaf build/overleaf.zip
	mkdir -p build/overleaf/build
	conda run -n $(TEXENV) tectonic -X compile paper.tex \
	    --outdir build/overleaf --keep-intermediates >/dev/null
	rm -f build/overleaf/paper.aux build/overleaf/paper.out build/overleaf/paper.pdf
	cp paper.tex refs.bib build/overleaf/
	cp build/fig_*.pdf build/overleaf/build/
	cd build/overleaf && zip -qr ../overleaf.zip .
	@echo "-> build/overleaf.zip  ($$(unzip -l build/overleaf.zip | tail -1 | awk '{print $$2}') files)"

clean:
	rm -rf build/overleaf build/overleaf.zip
	rm -f build/paper.aux build/paper.bbl build/paper.blg build/paper.log build/paper.out

distclean:
	rm -rf build
