# Progressive fitting experiment and conditional delivery

**Proposed optimization; experiment first.** This plan does not establish a
Scansor speedup or change fitting admission. The full selected observations,
original area weights, geometry and authored relationships remain the problem.
Only preliminary numerical work may use subsets. Final refinement and acceptance
must use the complete problem and the existing numerical tolerances.

The user authorized creating agent issues, running a useful-speedup experiment
first, and continuing implementation only if that experiment passes. Current-head
CI remains required before authorized admin override merging.

## Why measure complete fitting

The current selected cone and cylinder paths try multiple starts and assess them
on all data. They call the numerical solver directly, rather than exclusively
using the shared geometric adapter. Coordinate construction, start generation,
geometry predicates and full-problem assessments may consume much of the time.
An improvement to a single residual or solver kernel cannot establish an equal
improvement to complete recovery or graph evaluation.

The literature provides motivation, not a local measurement:
[Progressive Batching for Efficient Non-linear Least Squares](https://arxiv.org/abs/2010.10968).

## Delivery order

1. [Experiment #344](https://github.com/altendky/scansor/issues/344): compare the
   unmodified selected cone/cylinder recovery with an isolated opt-in staged
   prototype. Preregister the cases, schedule, correctness checks and decision
   rule before comparison. Keep production defaults unchanged. Report negative
   results and stop downstream implementation if no family passes.
2. [Standalone integration #345](https://github.com/altendky/scansor/issues/345):
   implement a shared bounded driver and only the families/size ranges supported
   by a positive experiment. Rerun the benchmark against the actual integration;
   preserve the ordinary path and small-case bypass.
3. [Joint fitting #346](https://github.com/altendky/scansor/issues/346): separately
   measure constrained components before enabling them. Preserve each surface's
   original relative weight mass, shared parameters and all exact equalities.
   A standalone speedup does not establish a joint-component gain.
4. [Runtime verification and rollout #347](https://github.com/altendky/scansor/issues/347):
   verify native and packaged browser execution, semantic parity, cancellation,
   recovery, source replacement and complete final results. Roll out only the
   measured family/size ranges. Joint components may remain on the ordinary path
   while a validated standalone family ships.

## Initial experiment

Start from main `73b098cd32eed663f8fedeb876753667fc36f454`, with its input-derived
starts and complete recovery behavior. Initial nested stage sizes are 256,
1,024 and all observations, using a deterministic non-prefix sample order.
Each stage starts a fresh fixed-row solve; the last solve uses the original
full-data callback and final tolerances. Full-data coordinates, proposals,
physical scales, geometry guards and support remain unchanged. Subset evaluation
must avoid computing full residual/Jacobian arrays first.

Use 512, 4,096 and 16,384 observations, two deterministic realizations, reasonable
partial noisy cones/cylinders and unequal weights. Add order/density controls and
existing ambiguous or invalid cases. Small sphere and cone/plane or shared-axis
workloads provide overhead and workflow controls. Do not reuse the historical
large S6 mesh artifacts for this experiment.

Count sampling, initialization, all attempts, preliminary stages, geometry
guards, final refinement/assessment and result construction in fit wall time.
Record phase/work diagnostics and memory separately. Use pinned thread counts,
warmups, at least three sequential paired/counterbalanced repetitions, input and
source identities, and measured variation. Keep bulk artifacts outside Git;
commit the compact harness, commands and result summary.

## Continuation rule

Evaluate each named family independently across all its preregistered large
cases: at least 4,096 rows and at least 50 ms baseline fit time. Require at least
2x median complete-fit speedup across those cases, no individual case slower by
more than 10% plus 10 ms, and unchanged correctness and public acceptance or
rejection behavior. Repeat borderline measurements more thoroughly. Do not
remove failed cases or relax the threshold after observing results.

Preserve existing full-data geometry, objective, gradient, rank, support,
constraint and ambiguity checks. Compare against fixture truth where available.
An acceptable full-data RMS alone is insufficient. Preliminary sample failure
falls back safely within the original cumulative work and cancellation policy.
The small-case bypass must stay within 10% plus 2 ms of the ordinary path.

## Performance expectation

Before comparative measurements, 2–5x complete fitting speedup is a hypothesis
for large iterative workloads, not a commitment. Small or well-initialized fits
may gain little or regress. User-visible improvement additionally depends on
how much time fitting consumes relative to import, graph work and rendering.
Replace this expectation with the measured report; report solver, complete-fit
and workflow speedups separately.
