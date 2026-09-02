# The whole thing, in plain English — plus the audit that changed the answer

**2026-08-27.** Written to be readable without a technical background. Section 1 is the
plain-English explanation. Section 2 is the honest answer about the AOI. Section 3 lists the
five real errors found auditing my own work, because they moved the conclusion.

---

## 1. What we are actually doing, in plain English

### The business idea, and why it needed testing
Find solar panels from aerial photos. Work out which ones are dirty enough that cleaning
them would earn the homeowner more than the cleaning costs. Tell them.

Step one works very well. Step two is where everything has been happening.

### Why dirt is harder than it sounds
Think of a solar panel like a window. Dirt on it blocks light and you lose electricity.
Simple. But there are two completely different kinds of dirt and they behave nothing alike.

**Dust** blows on, and rain washes it off. Santa Cruz gets about 26 proper downpours a year,
so a panel is rinsed clean roughly every fortnight through winter. If you pay someone to
wash dust off, the rain would have done it for free a few weeks later. **You are buying about
two weeks of cleanliness for $150.** That is why the dust business does not work, and we
measured it: a homeowner would need to be losing more than 100% of their electricity to dust
before a wash paid for itself. That is impossible, so it is a definitive no.

**Moss, lichen and algae** are alive. They grow on the panel, and rain does not remove them —
rain *feeds* them. Wash them off and they take one to four years to come back. So the same
$150 buys years instead of weeks. That is a genuinely different business, and it is the one
worth investigating.

### The one piece of physics that makes moss interesting
Solar panels are wired in chains, like old Christmas tree lights. Shade one part and the
whole chain suffers, far more than the shaded area alone would suggest.

Concretely: a thin line of moss covering **half a percent** of a panel can cost **nine
percent** of its output. That is roughly an eighteen-fold amplification, and it is measured,
not theoretical (Gostein 2015). Our own physics model, built independently, gives 9.10% for
the equivalent case. Two different routes to the same number is the strongest evidence we
have for anything.

And moss grows exactly where it does the most damage: along the **bottom edge** of the panel,
because the aluminium frame sits a couple of millimetres proud of the glass and traps water
there. That is the worst possible place, because the bottom edge is the part of the chain
that cuts out the whole section.

### Why we cannot just look at photos
The difference between "harmless" and "expensive" is a moss line about **30 millimetres**
thick. Our best aerial imagery sees about **78 millimetres per pixel.** The thing that
decides the answer is smaller than one pixel. No amount of clever processing fixes that.
Somebody has to stand in the street with a camera. That is why Craig's photos matter.

### How we predict where moss should be, using only free data
Moss needs the panel to stay wet. So we count how many hours a year the air is damp enough
for things to grow — a standard measure used for corrosion and building mould — and then
adjust it per roof using free laser scans of the county that tell us how much tree cover
hangs over each array, how steeply it is tilted, and which way it faces.

Santa Cruz scores **2,444 damp-growing-hours a year. Berkeley scores 2,468.** Berkeley is
where scientists confirmed living biofilm growing on solar panels. For growing purposes the
two towns are the same place. Our climate genuinely supports this.

---

## 2. So do any Santa Cruz panels actually clear the bar? **No.**

We ran all 2,494 measured arrays through the full chain: tree cover, tilt, direction, local
dampness, moss growth, the chain-reaction physics, the real cost of a wash, and the real
value of electricity.

**Zero arrays clear.** The best case in the entire AOI still loses about $30.

Three reasons, in order of importance.

**1. A safety code decided it for us.** Since January 2019, electrical code (NEC 690.12) has
required rooftop solar to shut down at the individual panel, which in practice means every
system uses microinverters or optimisers. Those devices are specifically designed to stop one
shaded panel dragging down its neighbours. **They defuse most of the chain-reaction effect
that made moss expensive in the first place.** The same moss line costs about 21% on old
wiring and about 8% on modern wiring. Essentially every California system built since 2019
has the modern wiring. This is a legal requirement, not a preference, so we cannot wish it
away.

**2. The $150 minimum charge.** A typical Santa Cruz array here is about 3.5 kW — small. But
a cleaner charges a $150 minimum regardless of size. That fixed cost swamps the small amount
of electricity a small roof loses. The minimum service charge, not the dirt, decides most
residential cases. This was already flagged in the repo and it survives every refinement.

**3. The favourable roofs are not the mossy ones.** Only **36** of the 3,362 detected arrays
match a permit showing both old-style wiring *and* the valuable old electricity tariff, and
**31** of those 36 have the laser-scan geometry needed to score them. They turn out to be
**smaller than average** (top-decile 5.5 kW against 7.9 kW across the AOI), so even where the
physics is favourable the arithmetic is not.

One caveat that cuts both ways: the permit file only starts in **2016**, so every system
installed before then is invisible to this match and sits in the 2,058 "unknown" arrays.
California residential solar boomed from 2010 to 2015, so the true old-wiring population is
larger than 36. Against that, "pre-2017" does not guarantee old wiring — microinverters were
already common here well before 2017. The layer narrows the question; it does not settle it.

### What would have to be true for the answer to change
- Moss coverage would need to reach roughly **50%** of the bottom cell row, well above the
  30% we assumed and five times the level at which the project's written kill-rule fires.
- **And** the roof would need old-style string wiring, which code has effectively banned
  since 2019.
- **And** the household would need the legacy NEM 2.0 tariff, worth 2.8x the current one.

All three together. That is a small and shrinking population.

### The honest bottom line
**The panel-cleaning business does not work in Santa Cruz, for dust or for moss.** We have
now tested the most favourable remaining mechanism using real per-roof data and it still
fails. That is a solid, defensible negative result and it is worth publishing.

What remains genuinely valuable is the *measurement* work: nobody has ever measured what
biological growth costs solar panels in a Mediterranean climate, and we now have the tools
and the data to do it. That is a research contribution. It is not a route to selling
cleanings.

---

## 3. Audit: five real errors in my own work, all found and fixed

These are listed because three of them changed the answer, and because the pattern is worth
knowing: **every one made the result look better than it was.**

### 3a. A cap that deleted the effect being modelled
`band_loss_pct` capped losses at the affected panels' share of the array (20% for 4 of 20
panels). But the entire point of the chain-reaction physics is that loss **exceeds** the
shaded area's share — the string result was 21%, and it was being silently truncated to 20%.
Fixed: the cap now applies only to modern per-panel wiring, where it is physically correct.

### 3b. Zero dirt cost 1.85% of output
The loss table started at 10% coverage. The interpolation could not extrapolate below that,
so **any** coverage under 10%, including exactly zero, returned the 10% figure. A clean roof
and a lightly soiled roof scored identically, which drove the calculated benefit of washing
to exactly zero. Fixed by anchoring every table at zero coverage costing zero.

### 3c. Every roof ended up equally mossy
I let tree cover affect only how *fast* moss grows, not how much a roof can sustain. Over ten
years all 2,494 arrays saturated at the same value and the per-roof differences vanished —
which is also obviously false, since plenty of roofs have no moss after fifteen years. Sun
and drying do not slow moss, they kill it. Fixed so the roof's environment sets the
equilibrium, with a floor below which biofilm does not persist at all.

### 3d. A stale constant, 1.6x optimistic
The AOI script hardcoded a moss-recovery figure of 0.61 that had been computed before fix
3c. The corrected value is 0.39. Fixed by deriving it at runtime so the two scripts cannot
drift apart again.

### 3e. Calibrating one thing against a different thing
I anchored the moss growth rate to a Brazilian study's "53% coverage at 12 months". But that
study measured a *thin film spread over the whole panel*, losing power by simply blocking
light. Our number is a *band along the bottom edge*, losing power by the chain reaction.
Different quantities, different physics; one cannot calibrate the other. Retracted, and the
constant is now labelled as the guess it is.

### Where the literature genuinely does not transfer
- **The bottom-edge band studies are about mineral dust in China**, at 3-4 degree tilts. We
  tested whether their mechanism applies here and it does not: it depends on light rain
  slowly building a deposit, and Santa Cruz's frequent downpours flush it. Two independent
  checks agree. So we rely on the *biological* version, which no paper has measured.
- **The Berkeley study confirms moss exists on panels here but never measured any power
  loss.** It reaches for the Brazilian figure for want of a local one. That is exactly the
  gap we would be filling, and it means we cannot cite it as evidence of cost.
- **The tilt curve comes from desert dust in Arizona**, so we deliberately did not reuse it
  for the biological channel.
- **The 9% figure is quoted inside another paper**, not read at source. Verify before it goes
  in a publication.
- **Moss is patchy, not a clean stripe.** Practitioner reports describe growth at the bottom
  frame but also scattered elsewhere. Our model assumes a continuous band, which flatters the
  landscape-mounted cases.

### One test thrown away entirely
A fleet-wide check of whether damper climates show faster panel degradation returned a
statistically significant result in exactly the direction the moss theory predicts. **I
rejected it.** The underlying degradation rates were physically impossible (five times the
known rate, with two panels apparently *improving*), and dampness was tangled up with
geography, so it could not tell moss apart from data quality. A guard is now in the code so
the number cannot be quoted by accident.

---

## 4. What is worth doing next

1. **Craig's photographs.** Still the only thing that measures the one unknown. The written
   kill-rule stands: if the thickest moss line in town is under ~16 mm, the theory is dead.
   Given section 2, even a thick line probably does not rescue the economics, so this is now
   about closing the question honestly rather than finding a business.
2. **Publish the negative.** Two clean, well-evidenced negatives (dust and moss) plus a
   validated measurement method is a genuine contribution.
3. **Keep the label work, drop the product framing.** The ability to measure per-roof soiling
   from public data is real and validated. Its value is scientific.

## Reproduce

```bash
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/array_install_era.py --partner-id santa-cruz-w2-21cm
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/aoi_cleaning_threshold.py
PYTHONPATH=. conda run -n solar-soiling python scripts/analyze/band_channel_economics.py
```
