# Paper bundle

Two papers, one pipeline. The split happened 2026-09-22 because the draft was three
papers in fifteen pages; `SPLIT_PLAN.md` records what moved and why.

## Which paper is which

**`paper.tex` — the submission.** *Station Labels Cannot Rank Roofs.* 16 pages, one thesis
with a positive control and a decision consequence. Target: *Solar Energy*, which is
rolling submission, takes systems-and-economics work, and published Mejia and Kleissl.
Self-contained: no claim in it depends on reading the other paper.

**`paper_detection.tex` — not a journal submission.** The detection gate, the prompt-box
result, the permit-recall probe and the web-Mercator projection trap. These are engineering
findings rather than research novelty, so the honest venue is PVSC or an arXiv technical
note, not a companion journal paper. It currently has methods and results but no
introduction, related work or discussion, and its abstract says so.

## How the submission is meant to be read

The paper is built to be understood in roughly this order, and the front matter is designed
for a reader who will not finish it:

| if you read only... | you get |
|---|---|
| Figure 1 | the entire argument: four questions, what each returned, and which one settles the others |
| the abstract | the same thing in 240 words |
| Table 1 (Discussion) | every claim, its number, and the strongest objection we know of |
| Figure 3 (`fig_label_unit`) | why station labels cannot rank roofs, shown on 103 metered ones |
| Figure 7 (`fig_cleaning_value`) | why none of the modelling matters: $10.54 of value, $150 of service |

Limits are collected in Table 1 rather than distributed through the text, so a reader can
audit the paper's confidence in one place instead of eight.

## Files

```
paper/
  paper.tex                  the submission
  paper_detection.tex        the detection half, draft, not submission-ready
  verify_numbers.py          fails if any headline number drifts from its artifact
  refs.bib
  SPLIT_PLAN.md              what moved in the split, and what is still open
  FACT_TABLE.md              superseded by verify_numbers.py; kept as narrative history
  MISSING_INFORMATION.md     open items, predates the validation audit
  figures/                   one script per figure, all reading artifacts directly
  build/                     generated PDFs + PNG previews (gitignored)
```

## The numbers check

`make paper` runs `verify_numbers.py` first and fails the build if any headline value in
`paper.tex` no longer matches the JSON that produced it. It passes 18 of 18. This exists
because three results in this paper changed after being written down as findings, and none
of the three was caught by review.

## Build order

Figures must exist before LaTeX runs; `paper.tex` sets `\graphicspath{{build/}}`.

```bash
make            # figures, then paper -> build/paper.pdf
make figures    # figures only
make paper      # paper only (assumes figures exist)
make clean
```

Or by hand:

```bash
cd figures && for f in figure_*.py; do PYTHONPATH=../.. python3 "$f"; done
cd .. && conda run -n tex tectonic -X compile paper.tex --outdir build
```

### Toolchain

There is no system LaTeX on this machine and `sudo` needs a password, so TeX Live via
`apt` is not an option. The bundle builds with **Tectonic** instead, installed with conda
(no root required):

```bash
mamba create -y -n tex -c conda-forge tectonic
mamba install -y -n tex -c conda-forge poppler   # optional: pdftoppm, for page previews
```

Tectonic is a single-binary XeTeX engine that downloads exactly the packages the document
needs on first run and caches them (~200 MB under `~/.cache/Tectonic`), rather than
installing a multi-GB distribution. It runs the bibtex and cross-reference passes itself,
so there is no `pdflatex`/`bibtex`/`pdflatex`/`pdflatex` dance.

`pdflatex` also works if you have TeX Live, with no source changes:

```bash
pdflatex paper && bibtex paper && pdflatex paper && pdflatex paper
```

**Current build status:** compiles clean. 13 pages, zero errors, zero overfull boxes, zero
unresolved references or citations, all 8 figures and 2 tables placed in order near their
first reference.

Page 1 is a standalone title page (title, authors, abstract, full width, unnumbered); the
body runs two-column from page 2. The source switches with `\onecolumn` ... `\clearpage`
`\twocolumn` rather than the `titlepage` class option, so the float and footnote machinery
never straddles the column-count change. Note that `\maketitle` issues its own
`\thispagestyle{plain}`, so the `\thispagestyle{empty}` that suppresses the page-1 number
has to come *after* it.

## Uploading to Overleaf

`make overleaf` writes `build/overleaf.zip` (paper.tex, refs.bib, paper.bbl, and the eight
figure PDFs under `build/`). Import that zip as a new Overleaf project and it compiles as
is: verified 2026-09-22 at 13 pages with zero unresolved citations.

**If every citation renders as `[?]`, `refs.bib` is not in the project.** That is what
natbib prints for a citation with no bibliography entry behind it, and all-`[?]`
means the database is absent rather than one key being wrong. Check the Overleaf file tree
for `refs.bib` sitting next to `paper.tex`, then recompile. If it is present and the
problem persists, clear Overleaf's cache: Recompile dropdown -> *Recompile from scratch*,
which forces the BibTeX pass to re-run.

The bundled `paper.bbl` is a partial safety net only. It rescues the case where Overleaf
skips BibTeX entirely, because `\bibliography{refs}` inputs `paper.bbl` when one exists.
It does **not** rescue a missing `refs.bib`: measured here, BibTeX still runs, finds no
database, and overwrites the shipped `.bbl` with an empty one, giving a silent empty
References section and a 12-page document. Upload both files.

Source-side the bibliography is sound and can be re-checked in one line: 29 keys cited, 29
entries defined, exact match.

```bash
diff <(grep -oE '\\cite[a-z]*\{[^}]*\}' paper.tex | sed 's/.*{//;s/}//' \
        | tr ',' '\n' | tr -d ' ' | sort -u) \
     <(grep -oE '^@[a-zA-Z]+\{[^,]+,' refs.bib | sed 's/^@[a-zA-Z]*{//;s/,$//' | sort -u)
```

## Required assets

Every figure reads a committed artifact directly. No figure contains a hand-entered number.

| Figure | Script | Reads |
|---|---|---|
| 1 pipeline | `figure_pipeline.py` | none (schematic; constants inline, sourced in FACT_TABLE) |
| 2 threshold sweep | `figure_threshold_sweep.py` | `outputs/eval/rfdetr_w2/threshold_sweep.csv` |
| 3 SAM2 prompt box | `figure_sam_promptbox.py` | `outputs/eval/sam_containment_ab.json` |
| 4 tilt validation | `figure_tilt_validation.py` | `outputs/aoi/santa-cruz-w2-21cm/roof_planes.csv`, `data/external/dgstats/santa_cruz_interconnected_pv.csv` |
| 5 risk validation | `figure_risk_validation.py` | `runs/soiling/run_optionb/holdout_ci.json` |
| 6 regional holdout | `figure_regional_holdout.py` | `outputs/soiling/regional_holdout.json` |
| 7 variance decomposition | `figure_variance_decomposition.py` | `outputs/aoi/santa-cruz-w2-21cm/site_economics.csv` |
| Table 2 (shortfall) | `figure_shortfall.py` | `site_economics.csv` + `src.risk.economics.professional_cost` |

`figure_shortfall.py` and `figure_variance_decomposition.py` import from `src/`, so they
need `PYTHONPATH` set to the repo root. The rest are pure file reads.

Two of these inputs are **gitignored and must be pulled** before the figures build on a
fresh clone (see `DATA.md`): `data/external/dgstats/` and
`outputs/soiling/training_matrix.parquet`. The eval JSONs and `site_economics.csv` are
committed.

## Style contract

`figstyle.py` owns the whole visual system: Type-42 embedded fonts (Type 3 is rejected by
most CS venues), serif family matching the body text, colour-blind-safe categorical
palette that survives grayscale printing, no top/right spines, vector PDF plus a PNG
preview per figure. Add a figure by importing `apply_style()` and `save()`; do not set
rcParams in an individual script.
