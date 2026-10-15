# Bounded native/browser numerical parity

The selected verification contract compares published results and their intent
while retaining numerical traces from both runtimes. It does not promise
identical floating-point execution histories. This decision addresses the
measured cross-runtime differences tracked in
[#266](https://github.com/altendky/scansor/issues/266).

The [shared comparator](../../../../experiments/browser_runtime/semantic_parity.py)
uses strict comparison by default. Source/recipe identity, original membership
and weights, authored constraints, published geometry and residuals, selected
acceptance certificates, and public failure/ambiguity classifications remain
gates. Existing fixture length normalization and numerical tolerances remain;
this is not a change to fitting admission or physical accuracy requirements.

Recognized bounded recovery records additionally validate each runtime's finite
proposal, attempt, iteration and callback limits. Realized counts and discarded
numerical paths may differ within the same policy. Screened intent, selected
candidate identity and acceptance-affecting classifications remain compared.
Unrecognized numerical adapters retain the strict comparison. Reported counts
cannot independently prove unreported trial/projection counts; producer limit
tests and harness deadlines remain necessary.

All raw records remain available alongside a versioned, source-hashed receipt
and a ledger of allowed diagnostic differences. Browser graph harnesses invoke
the same reviewed Python policy. The artifact verifier checks input identities
and independently repeats that comparison using its trusted policy.

Registration reports the best and runner-up scores, normal scale and existing
denominator floor. Each ratio must agree with its own scores; cross-runtime
comparison uses the underlying scores and selected placement. A nearly zero
best score can amplify roundoff in the quotient. The current ratio is diagnostic
and has no ambiguity rejection threshold; this decision introduces none.

The rejected cylinder gradients behind the original failure were about 1.96–4.63
and differed by roughly 10–14 parts per billion. Both runtimes rejected those
rank-deficient starts, and their selected fit agreed. Different Python/NumPy
runtimes and poorly constrained chart directions support floating-point
sensitivity as an explanation; no particular BLAS implementation was isolated
as the cause. Normalization must use a meaningful problem scale, rather than
dividing by an almost-zero result.

Mutation tests guard geometry, constraints, identity, selected certificates and
budget policy changes. Complete current-head CI remains required for each
dependent branch; earlier failed strict reports remain diagnostic evidence.
