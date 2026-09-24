# The scope split

Done 2026-09-22. The draft was three papers in fifteen pages, which was the single
largest thing standing between it and a journal. It is now two, and this records what
moved, what is finished, and what is not.

## Why

A reviewer reads scope before they read results. A paper carrying a detection gate, a
lidar geometry method, a soiling-transfer analysis and a cleaning-economics verdict asks
them to referee four literatures, and the usual outcome is a reject that names none of
them. Splitting costs nothing scientifically: the two halves share a pipeline but not an
argument.

## What each paper is

**`paper.tex` — the one to submit.** *Station Labels Cannot Rank Roofs: Why Public-Data
Soiling Models Do Not Transfer, and What Cleaning Is Actually Worth.* 12 pages, down from
15. One thesis with a positive control and a decision consequence:

- station-trained soiling models do not transfer to roofs, and our own published folds
  leaked: 0.710 becomes **0.622** under a jointly out-of-site-and-year fold, and latitude
  and longitude alone match all 40 features
- the limit is the **label unit**, not the model class: on per-system labels, per-array
  features rank systems that share weather at **0.573** within-cell concordance against
  the **0.5** a station-label model cannot exceed by construction
- cleaning does not clear, and not because the climate is wet: on 149 measured rooftops
  the median needs **\$2.35/kWh** and the entire annual recoverable value is **\$10.54**

Detection and roof geometry are compressed into one Methods subsection
(`sec:methods-inputs`) and one Results subsection (`sec:results-inputs`), about a page
and a half combined, carrying only what the soiling argument depends on.

**`paper_detection.tex` — the companion.** *Permissively Licensed Rooftop Array Detection
at 21 cm, with an Executable Gate.* 5 pages of moved material. Its contribution is the
gate stated as an executable definition rather than a number, the prompt-box result (the
roof-grab is a box problem, not a mask problem), the independent permit-recall probe, and
the web-Mercator projection trap that inflates lidar tilt by 18.9\%.

## State

| | `paper.tex` | `paper_detection.tex` |
|---|---|---|
| compiles | yes, 0 undefined refs | yes |
| pages | 12 | 5 |
| title, abstract | rewritten for the narrowed scope | **placeholder** |
| introduction | inherited, needs a pass | **missing** |
| related work | inherited, still carries detection-architecture depth that now belongs next door | **missing** |
| discussion, limitations | inherited, needs a pass | **missing** |
| figures | all present | shares `build/`, no new builds needed |

## Journal readiness, as of 2026-09-22

The technical gaps that were open when the split happened have mostly closed. What did:

| gap | state |
|---|---|
| headline 0.622 unverified against seeds/estimator | **closed.** 0.6201 ± 0.0047 over 7 seeds; logistic regression gives 0.571; summary-row weighting spans 0.594–0.622 |
| regional result rested on one fold geometry | **closed.** 0.638–0.664 for k = 5…12; at k=12, 7 regions hold ≥20 rows |
| no external baseline | **closed.** Kimber (2006) unfitted scores 0.610 [0.566, 0.652] against our 0.622; SOMOSclean 0.544 |
| economics credited every site 5.5 peak-sun-hours | **closed.** Measured per cell, median 5.09; the constant was too high for 22 of 29 and had been making the cleaning case optimistic |
| the per-system ranking was reported as a result | **closed, downward.** It does not survive the cleaning assumption (ρ = 0.126); now reported as label instability, which is the sturdier finding |
| numbers could drift between artifact and paper | **closed.** `verify_numbers.py`, 18 of 18, wired into `make paper` |
| paper depended on an unpublished companion | **closed.** Zero deferrals; every number stated inline |
| **economics measured on California only** | **open.** All 149 metered rooftops are Californian. The national extension is running: 157 irradiance cells warming, then intervals, recovery and economics across 12 Köppen classes |

The last row is the only substantive technical limit left, and it is a data-collection
run rather than a design question.

## What is left


1. **`paper_detection.tex` needs an introduction, related work and discussion.** The
   methods and results are real; the framing is not written. It is a draft, not a
   submission.
2. **`paper.tex` related work still over-covers detection architectures.** Trim toward
   soiling measurement, modelling and uncertainty.
3. **Forward references.** The main paper says "the companion paper" in four places. Once
   the companion has a title and a venue, cite it properly.
4. **The introduction of `paper.tex`** still opens on the pipeline rather than on the
   transfer question. It reads like the old scope for its first two paragraphs.

## What deliberately did not move

The tilt validation lives in the companion, because its honest form is a distributional
claim about two instruments and that is a geometry result. The main paper keeps one
sentence, because tilt exclusion is one of three reasons the risk model cannot rank, and
a reader needs to know the exclusion was measured rather than convenient.


## The workshop cut, if one is wanted

A 4-page version (CCAI-style) is a subtraction, not a rewrite, and it should be one thesis:
**station-trained soiling models do not transfer to roofs, and here is what the labels
would have to be instead.** The economics are the strongest single result but they need
the machinery of §3.3 to be credible in four pages; the transfer story does not.

**Keep** (roughly 3 pages of text, 3 figures):

| keep | why |
|---|---|
| Figure 1, the argument chain | it is the paper; a workshop reader may read nothing else |
| §4.2, the leak, 0.622, and the training-size control | the contribution |
| Figure 3, the two intervals | one picture, the whole claim |
| §4.3 label unit + Figure `fig_label_unit` | the portable idea: the 0.5 ceiling is a proof, not a measurement |
| the physics baselines, 3 sentences | Kimber unfitted at 0.610 is what makes the ablation unarguable |
| Table 1, cut to the five surviving claims | the rigor signal, and it is cheap in space |

**Cut:** all of §3.1 and §4.1 (detection and geometry are already one paragraph each and
can become one sentence), §4.4 and §4.5 in full, the break-even surface, the deployment
figure, §5.2, §5.3, §5.5, §5.6, and the coverage figure. Replace the economics with two
sentences and a pointer to the full paper: *the entire annual recoverable value on the
median metered roof is \$29.22 at retail against a \$150 service, so the ranking question
is moot in this market; see [full paper] for the boundary where it is not.*

**Do not cut** the "what we got wrong" paragraph. At four pages it is a larger share of the
contribution, not a smaller one: a workshop audience remembers a group that published its
own leak more readily than one that published an AUC.
