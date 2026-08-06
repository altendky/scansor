# Ceres Python and Rust Bindings

## Status

**Research finding, snapshot dated 2026-07-24.** This page records the binding
comparison. The separate [provisional decision][prototype-decision] selects
Python and SciPy for the first evidence-generating prototype. Neither the
finding nor that provisional decision selects a production language or solver.
No reusable Scansor solver implementation exists. The separate internal CLI
fixture performs bounded local inspection and replay only; it does not implement
or select a solver.

## Evidence Labels

- **Source-confirmed** means the cited project documentation or source directly
  exposes or states the behavior.
- **Observed** means a bounded research probe produced the result in the stated
  environment; it is not a general claim.
- **Inferred risk** means the source supports a concrete concern that has not
  been demonstrated as a failure or vulnerability.
- **Open evidence** means Scansor still needs a controlled experiment.

No official Google-maintained general Python or Rust binding was found in the
[Ceres documentation][ceres-docs] or [official repository][ceres-repo]. This is
a search result, not proof that no relevant project exists. Official Ceres is a
C++ nonlinear least-squares library with bounds, robust losses, automatic and
numeric differentiation, manifolds, sparse solvers, evaluation, callbacks, and
covariance facilities ([modeling][ceres-modeling], [solving][ceres-solving],
[covariance][ceres-covariance]). Direct C++ therefore remains the reference
surface against which bindings should be measured. It is a native CMake build;
available sparse and dense backends depend on build configuration and installed
dependencies ([installation][ceres-installation]).

## Comparison

### SciPy baseline

**Source-confirmed.** Python's
[`scipy.optimize.least_squares`][scipy-least-squares] accepts vector residuals,
box bounds, callable or numerically estimated dense/sparse Jacobians, Jacobian
sparsity, robust losses, and `trf`/`dogbox` iteration callbacks. Callbacks were
added in SciPy 1.16; the cited API snapshot is 1.18. Its result includes
residuals, a modified final Jacobian, gradient, optimality, active-bound
indicators, evaluation counts, and termination status and message. `trf`
supports bounded sparse problems and optional regularization for rank-deficient
Jacobians; `dogbox` is explicitly discouraged for rank-deficient problems.

**Gaps and open evidence.** SciPy does not directly provide Ceres-style
manifolds or covariance, and its success flag only records numerical
termination. It does not establish identifiability, physical validity, or
held-out performance. Its `active_mask` can be tolerance-dependent for `trf`,
and the returned Jacobian under robust loss must not automatically be treated as
the raw model Jacobian. These are fixture and diagnostic-design questions, not
reasons to reject it as a baseline.

### pyceres

The comparison covers released [v2.6][pyceres-v2.6], used by the preliminary
research probe, and unreleased current `main` source pinned at
[`7339d790f364accf3e8f2204d2342a621516a359`][pyceres-commit].

**Source-confirmed v2.6 capabilities.** The released binding is much broader
than the Rust binding. It wraps Python-defined analytic cost callbacks;
parameter blocks, constants, bounds, and residual blocks; solver options and
iteration callbacks; built-in Euclidean, subset, quaternion, Eigen-quaternion,
and sphere manifolds; problem residual and CRS-Jacobian evaluation; detailed but
partial structured solver summaries; and ambient/tangent covariance operations
([v2.6 cost source][pyceres-v26-cost], [problem source][pyceres-v26-problem],
[solver source][pyceres-v26-solver], [manifold source][pyceres-v26-manifold],
[covariance source][pyceres-v26-covariance]). It does not expose Ceres automatic
differentiation for Python callbacks. Python-defined custom manifold operations
are not implemented. Problem evaluation omits some native outputs, including
cost and gradient, and the wrappers discard native evaluation's success/failure
return. The structured summary omits some native fields, including
per-iteration records, although text reports remain available.

**Source-confirmed current main, unreleased.** The pinned current-main
[manifold source][pyceres-manifold] adds `ProductManifold`, composed from two or
more exposed manifolds. `ProductManifold` is absent from v2.6 and must not be
treated as a released capability. The pinned current-main [cost][pyceres-cost],
[problem][pyceres-problem], [solver][pyceres-solver], and
[covariance][pyceres-covariance] sources otherwise support checking this
comparison against ongoing changes; current-main behavior still requires a
release or commit-pinned build to use.

**Source-confirmed packaging and runtime facts.** The current-main
[README][pyceres-repo] advertises pip wheels for Python 3.9 through 3.14 on
Linux, macOS, and Windows and requires an installed Ceres for source builds.
The v2.6 release notes record wheel-infrastructure changes and removal of macOS
x86_64 wheels. Both the v2.6 and pinned current-main sources show that solving
releases the GIL, but every Python cost evaluation reacquires it. Callback
parameters, residuals, and flattened Jacobians are no-copy NumPy views over
native memory; parameter blocks are also registered by raw NumPy data pointer,
making dtype, contiguity, mutation, and object lifetime part of correct use.

**Observed, preliminary and bounded.** On CPython 3.14/Linux with pyceres 2.6,
a research probe could solve a Python-defined scalar cost and invoke its
callback, but `Problem.evaluate_residuals()` segfaulted for that problem. No
reproducer or crash trace was retained. The observation must be reproduced
before it contributes decision evidence; it does not independently justify the
Python/SciPy provisional decision and is not a general pyceres conclusion.
Separately, open [issue 70][pyceres-issue-70] reports incorrectly populated
residuals and Jacobians for a Python-defined vector cost; it remains an
unconfirmed user report, not a demonstrated general limitation.

**Inferred risk.** Python callbacks serialize through the GIL and cross the
Python/C++ boundary for each evaluation, so callback granularity may dominate
performance. No-copy views and pointer-based parameter identity increase the
consequence of ownership or layout mistakes. The preliminary observation and
issue 70 raise the priority of correctness probes, but do not show that built-in
C++ costs or all Python costs are unsafe.

**Open evidence.** Reproduce the crash across supported Python versions and
current `main`; test scalar and vector residual/Jacobian extraction, covariance,
thread counts, callback overhead, wheel installation, and source builds.

### `ceres-solver-rs`

This comparison pins the `ceres-solver` 0.5.1 source at
[`42121ef651756787d48103e7733f5f60a4689375`][rust-commit]. Published components
are [`ceres-solver` 0.5.1][crate-safe], [`ceres-solver-sys` 0.5.3][crate-sys],
and [`ceres-solver-src` 0.5.1 with native-version metadata][crate-src].

**Source-confirmed capabilities and gaps.** The project's
[support matrix][rust-matrix] covers analytic Rust callbacks with dynamic
parameter and residual blocks, constants, bounds, custom and built-in losses,
thread count, broad solver options, validation, and brief/full reports. It marks
autodiff and numeric-diff costs, manifolds, covariance, and solver callbacks as
unsupported. The wrapper also lacks problem evaluation/Jacobian extraction and
exposes only a small structured subset of the native summary. These omissions
make it narrower, not universally unusable.

**Source-confirmed packaging/build facts.** The default `system` feature finds
Ceres through `pkg-config`; the optional `source` feature builds bundled Ceres
2.2, Eigen 3.4, and glog 0.7.1. That
[bundled build][rust-bundled-build] disables CUDA, LAPACK, SuiteSparse,
AccelerateSparse, EigenMetis, and Schur specializations while retaining
EigenSparse. This eases a self-contained build at the cost of native backend
choice; system Ceres remains a separate path.

**Inferred risk, high confidence.** The cost [callback type][rust-cost] and
custom [loss callback][rust-loss] are `Fn` without `Send` or `Sync`, while
[`num_threads` is forwarded][rust-solver] to Ceres. Native concurrent callback
invocation could therefore violate Rust's thread-safety assumptions. This
source-level mismatch is not a demonstrated exploit, CVE, or observed failure;
multithreaded callback use should be treated as unsafe until isolated and
resolved upstream.

**Open evidence.** Test single- and multi-thread callback correctness under
sanitizers, analytic Jacobian checking, failure propagation, system and bundled
builds on target platforms, and the performance effect of disabled bundled
backends.

## Research Conclusion

The comparison supports the [provisional decision][prototype-decision] to use
Python with `scipy.optimize.least_squares` for the first evidence-generating
prototype. It is the shortest path to testing Scansor's residual formulation,
reduced parameterization, bounds, robust losses, Jacobians, rank-deficient
cases, termination records, and held-out validation without first depending on
a Ceres binding. The unretained pyceres segfault observation is not needed to
reach this conclusion.

This selects neither the eventual production language nor the production
solver, and it does not decide whether production should use Ceres. If fixture
evidence indicates that Ceres-specific behavior or performance matters, compare
pyceres with direct C++ Ceres; the Rust wrapper can be reconsidered if its
surface and callback-threading contract change or a narrower use case fits.

## Controlled Experiments

1. Implement one small deterministic Python/SciPy fixture with analytic and
   finite-difference Jacobian checks, bounds, robust losses, deliberate rank
   deficiency, an optimum on a bound, explicit termination assertions, and
   held-out observations.
2. Record solution correctness, residual and Jacobian conventions, active
   bounds, conditioning/rank evidence, termination, diagnostics, runtime, and
   packaging effort. Treat solver success separately from fit acceptance.
3. If the baseline reveals a Ceres-relevant need, implement the same fixture in
   pyceres and direct C++ Ceres. Compare correctness and diagnostics first, then
   packaging, callback overhead, and representative performance.
4. Run the pyceres and Rust-specific open-evidence probes above before relying
   on either binding. Preserve versions, platforms, minimal reproducers, and
   crash/sanitizer output.

[ceres-docs]: https://ceres-solver.org/
[prototype-decision]: decisions.md#use-python-and-scipy-for-the-first-evidence-generating-prototype
[ceres-repo]: https://github.com/ceres-solver/ceres-solver
[ceres-installation]: https://ceres-solver.org/installation.html
[ceres-modeling]: https://ceres-solver.org/nnls_modeling.html
[ceres-solving]: https://ceres-solver.org/nnls_solving.html
[ceres-covariance]: https://ceres-solver.org/nnls_covariance.html
[scipy-least-squares]: https://docs.scipy.org/doc/scipy-1.18.0/reference/generated/scipy.optimize.least_squares.html
[pyceres-repo]: https://github.com/cvg/pyceres
[pyceres-v2.6]: https://github.com/cvg/pyceres/releases/tag/v2.6
[pyceres-v26-cost]: https://github.com/cvg/pyceres/blob/3cb95962b3d5f5fa8a5894c277feabe37c82bd8a/_pyceres/core/cost_functions.h
[pyceres-v26-problem]: https://github.com/cvg/pyceres/blob/3cb95962b3d5f5fa8a5894c277feabe37c82bd8a/_pyceres/core/problem.h
[pyceres-v26-solver]: https://github.com/cvg/pyceres/blob/3cb95962b3d5f5fa8a5894c277feabe37c82bd8a/_pyceres/core/solver.h
[pyceres-v26-manifold]: https://github.com/cvg/pyceres/blob/3cb95962b3d5f5fa8a5894c277feabe37c82bd8a/_pyceres/core/manifold.h
[pyceres-v26-covariance]: https://github.com/cvg/pyceres/blob/3cb95962b3d5f5fa8a5894c277feabe37c82bd8a/_pyceres/core/covariance.h
[pyceres-commit]: https://github.com/cvg/pyceres/tree/7339d790f364accf3e8f2204d2342a621516a359
[pyceres-cost]: https://github.com/cvg/pyceres/blob/7339d790f364accf3e8f2204d2342a621516a359/_pyceres/core/cost_functions.h
[pyceres-problem]: https://github.com/cvg/pyceres/blob/7339d790f364accf3e8f2204d2342a621516a359/_pyceres/core/problem.h
[pyceres-solver]: https://github.com/cvg/pyceres/blob/7339d790f364accf3e8f2204d2342a621516a359/_pyceres/core/solver.h
[pyceres-manifold]: https://github.com/cvg/pyceres/blob/7339d790f364accf3e8f2204d2342a621516a359/_pyceres/core/manifold.h
[pyceres-covariance]: https://github.com/cvg/pyceres/blob/7339d790f364accf3e8f2204d2342a621516a359/_pyceres/core/covariance.h
[pyceres-issue-70]: https://github.com/cvg/pyceres/issues/70
[rust-commit]: https://github.com/light-curve/ceres-solver-rs/tree/42121ef651756787d48103e7733f5f60a4689375
[rust-matrix]: https://github.com/light-curve/ceres-solver-rs/blob/42121ef651756787d48103e7733f5f60a4689375/README.md#status-of-the-binding-support
[rust-cost]: https://github.com/light-curve/ceres-solver-rs/blob/42121ef651756787d48103e7733f5f60a4689375/src/cost.rs
[rust-loss]: https://github.com/light-curve/ceres-solver-rs/blob/42121ef651756787d48103e7733f5f60a4689375/src/loss.rs
[rust-solver]: https://github.com/light-curve/ceres-solver-rs/blob/42121ef651756787d48103e7733f5f60a4689375/src/solver.rs
[rust-bundled-build]: https://github.com/light-curve/ceres-solver-rs/blob/42121ef651756787d48103e7733f5f60a4689375/ceres-solver-src/build.rs
[crate-safe]: https://crates.io/crates/ceres-solver/0.5.1
[crate-sys]: https://crates.io/crates/ceres-solver-sys/0.5.3
[crate-src]: https://crates.io/crates/ceres-solver-src/0.5.1%2Bceres2.2.0-eigen3.4.0-glog0.7.1
