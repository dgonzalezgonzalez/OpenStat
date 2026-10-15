# Stata compatibility target inventory

This is a **feature-family target**, not a claim that OpenStat implements these features. Inspection date: 15 October 2026. Sources: selected installed StataNow 19 files; bounded REA resource evidence; the public documentation links below. Actual OpenStat support must be established by implementation review and executable tests.

Evidence classes: **O** = observed installed artifact/manual/menu inventory; **D** = public documented feature; **I** = inferred compatibility requirement; **U** = unverified semantics or coverage. REA IDs, artifact hashes, search boundaries and provider limits are in [inspection.md](inspection.md). No proprietary implementation or documentation is included here.

## Language, data and application behavior

| Family | Compatibility surface / representative probes | Evidence |
|---|---|---|
| Interactive commands and scripts | Command abbreviations, delimiters, comments, continuation, quoting, do/run/include, positional arguments, working directory. | O: Programming/User's Guide; I/U: full grammar. |
| Expressions and values | Numeric/string coercion, missing values including extended missings, precedence, subscripts, dates, Unicode, regex, format strings, scalar and variable functions. | O: Functions/Data Management manuals; U: exact semantics. |
| Macro expansion | Local/global macros, compound quotes, nested expansion, extended macro functions, scope, temporary names/files/variables. | O: Programming manual; U. |
| Control flow | `foreach`, `forvalues`, `while`, `if`/`else`, blocks, `continue`, `exit`, nested execution, error propagation. | O: Programming manual; U. |
| Program/ado system | Autoload/search paths, `program`, `syntax`, `args`, `marksample`, r/e/s-class results, version control, replay, estimation posting, debugging. | O: Programming manual and 3,954 installed `.ado` files; U. |
| Prefixes and qualifiers | `if`/`in`, `by`/`bysort`, weights, `quietly`, `noisily`, `capture`, `statsby`, resampling/survey/imputation/estimation prefixes. | O: User's Guide/Programming manuals; U. |
| Variable lists | Ranges, wildcards, factor variables, interactions, base levels, time-series operators, name limits and ambiguity. | O: User's Guide/Programming manuals; U. |
| Matrices and saved results | Stata matrix expressions, row/column names, stripes, constraints, coefficient vectors, covariance matrices, estimates store/replay/save. | O: Programming/Functions manuals; U. |
| Mata language | Interactive and block execution; functions, types, pointers, structures/classes, compiled code, `.mlib` libraries, views, Stata data/result API. | O: Mata manual, 421 `.mata` files and 30 `.mlib` files; [D](https://www.stata.com/features/matrix-programming-mata/); U. |
| Mata numerical library | Matrix algebra, complex/string matrices, linear solvers, decompositions, optimization, derivatives, integration, probability/RNG, Unicode/date/file functions. | O: Mata manual; [D](https://www.stata.com/features/matrix-programming-mata/); U. |
| Data storage and `.dta` | Storage types, labels/value labels, notes, characteristics, formats, sort metadata, encodings, long strings and binary strings, historical format versions and round trips. | O: Data Management manual and `dta_contents` help; U: every DTA revision. |
| Data inspection/editing | Describe, codebook, summaries, browse/edit, variable properties, data signatures, comparison, duplicates, observation/variable order. | O: Data menus/manual; U. |
| Data transformations | Generate/replace, egen, recode, rename, destring/tostring, encode/decode, labels, notes, sorting, grouping, dates, strings, ICD utilities. | O: Data menu/manual; U. |
| Combining/restructuring | Merge cardinalities and checks, append, reshape, join/cross, collapse, contract, expand, transpose, fill-in, preserve/restore. | O: Data menu/manual; U. |
| Multiple frames | Named datasets, frame switching/copying/linking, frame sets, cross-frame access and metadata. | O: Data Management manual; U. |
| File interoperability | Delimited/fixed-width text, Excel, SAS/SPSS and other supported formats, ODBC/JDBC/SQL, URLs, clipboard, import/export options and encodings. | O: Import/export menus; [D](https://www.stata.com/features/data-management/); U. |
| Package discovery/install | `ssc`, `search`, `findit`, `net`, package descriptors/file lists, PLUS/PERSONAL paths, updates, replacement, installed package tracking. | O: Base Reference manual; U: repository behavior and all third-party packages. |
| External code/ABI | Python/PyStata/SFI, Java and Java plugins, C/C++ plugins, H2O, process/file/Internet interfaces. | O: Programming/H2O manuals; [D](https://www.stata.com/features/programming-language/); U. |
| Graph language | `twoway` plot composition, bars/boxes/pies, histograms, densities, categorical/grouped plots, legends, axes, schemes, layouts and graph exports. | O: Graphics menu/manual; U. |
| Statistical graph families | Distribution, smoothing, diagnostics, time series, survival, ROC, multivariate, treatment-effect and quality-control graphs. | O: 109 graphics menu action occurrences; U. |
| Tables and collections | `table`, `dtable`, `etable`, collection dimensions/levels, formatting/styles, layouts, saved collections and export. | O: Tables manual and Statistics menu; U. |
| Documents and reports | Word/Excel/PDF/HTML creation, dynamic Markdown, embedded results/graphs, graph output, document conversions and rendering. | O: Reporting manual; U. |
| Reproducibility/session | Seeds/RNG streams, `version`, settings, logs/SMCL, return codes, tracing, timers, batch behavior, memory/limits and deterministic ordering. | O: User's Guide/Programming manuals; I/U. |
| Main application UI | Command/Results/History/Variables/Properties panes, menus/toolbars, docking, status, preferences, window focus, keyboard commands, help/search. | O: REA resources and installed menu files; [D](https://www.stata.com/features/overview/graphical-user-interface/); U: runtime parity. |
| Specialized editors/builders | Data Editor, Variables Manager, Do-file Editor, Graph Editor, SEM Builder, Tables Builder, Project Manager, Viewer/help/PDF integration. | O: Resource/dialog and manual families; [D](https://www.stata.com/features/overview/graphical-user-interface/); U. |
| Desktop/platform behavior | Printing, open/save dialogs, file associations, fonts, clipboard, accessibility, language/locales, platform-specific options and automation. | O: native resource/menu inventory; I/U. |

## Statistical families

The installed Statistics menu contains 1,183 quoted action occurrences, including repeated context entries. This table groups its independent targets; it does not treat one menu action as one estimator or claim all command options were inspected.

| Family | Representative target commands / methods | Installed evidence |
|---|---|---|
| Descriptive statistics | `summarize`, `mean`, `proportion`, `ratio`, `total`, `centile`, `ci`, `correlate`; weighted and grouped variants. | Statistics menu; Base Reference. |
| Tables and classical tests | Frequency/cross tables, means/variance/proportion tests, contingency and association tests. | Statistics menu; Base Reference. |
| Nonparametric/exact methods | Ranks, signed ranks, trend tests, distribution tests, exact regression/inference, nonparametric regression. | Statistics menu; Base Reference. |
| Linear/GLS models | `regress`, `areg`, robust/cluster VCE, constraints, high-dimensional effects, GLS, ANOVA/MANOVA. | Statistics menu; Base Reference. |
| Nonlinear and systems estimation | Nonlinear least squares, seemingly unrelated equations, GMM, ML, demand systems and cross-equation constraints. | Statistics menu; Base Reference/Programming. |
| IV/endogeneity/selection | IV/2SLS/3SLS, weak-instrument inference, control functions, sample selection, endogenous treatments and covariates. | Statistics menu; Base Reference/ERM. |
| Limited and censored outcomes | Binary/ordinal/multinomial/fractional outcomes, censored/truncated regression, count/zero-inflated models. | Statistics menu; Base Reference. |
| Discrete/rank choice | Choice setup, conditional/nested/mixed logit, multinomial probit, rank models, panel choices. | Choice Models manual; Statistics menu. |
| Longitudinal/panel models | Fixed/random/correlated effects, GEE, dynamic panels, panel IV, noncontinuous outcomes and diagnostics. | Panel Data manual; Statistics menu. |
| Multilevel mixed models | Continuous/discrete/survival outcomes, crossed/nested random effects, random slopes, covariance/residual structures and inference. | Multilevel Mixed-Effects manual; Statistics menu. |
| Finite mixtures/latent classes | Mixture prefixes, multiple response families, membership models, class probabilities and fit comparisons. | Finite Mixture manual; Statistics menu. |
| Time series | ARIMA/ARFIMA, volatility, smoothers/filters, stationarity/break tests, forecasts, state-space, Markov switching. | Time-Series manual; Statistics menu. |
| Multivariate time series | VAR/SVAR/VEC, IVSVAR, GARCH, local projections, panel VAR, IRFs, forecasts and diagnostics. | Time-Series/Panel manuals; Statistics menu. |
| DSGE | Algebraic model specification, linear/nonlinear solution, identification, estimation, IRFs, Bayesian models. | DSGE manual; Statistics menu. |
| Survival/event history | st setup, censoring/truncation, Kaplan-Meier, Cox, parametric/frailty models, competing risks, recurrent/multiple events. | Survival manual; Statistics menu. |
| Epidemiology/ROC | Rates, standardization, case-control/cohort/matched analyses, risk measures, pharmacokinetics, ROC. | Statistics menu; Base Reference. |
| Causal inference | Matching, IPW, regression adjustment, doubly robust methods, DID/heterogeneous DID, CATE, mediation, endogenous treatment. | Causal Inference manual; Statistics menu. |
| Structural equations | `sem`, `gsem`, path/latent-variable models, grouped models, fit indices, effects, predictions and graphical builder. | SEM manual; Statistics menu. |
| Multivariate statistics | PCA/factor, discrimination, clustering, MDS, correspondence, Procrustes, multivariate regression and related plots. | Multivariate manual; Statistics menu. |
| Item response theory | Binary/ordinal/categorical items, multiple-group models, information/characteristic curves and DIF. | IRT manual. |
| Survey statistics | Survey setup, strata/PSU/FPC, weighting/calibration, linearized/replicate VCE, subpopulations and supported estimators. | Survey manual; Statistics menu. |
| Multiple imputation | MI data management, chained/joint imputation, diagnostics, pooled estimation, tests and predictions. | Multiple-Imputation manual. |
| Spatial analysis | Spatial data/matrices/weights, cross-sectional/panel SAR models, spillovers, endogenous regressors and diagnostics. | Spatial manual. |
| Lasso/regularization | Lasso/elastic net, CV/adaptation, prediction and postselection inference, partialing/double selection and endogenous covariates. | Lasso manual; Statistics menu. |
| H2O machine learning | Random forests, gradient boosting, tuning/validation, predictions, model evaluation and explainability. | H2O Machine Learning manual. |
| Bayesian analysis | Priors, MCMC, diagnostics, posterior inference, predictions/model comparison, Bayesian prefix, VAR/DSGE and bootstrap. | Bayesian manual; Statistics menu. |
| Bayesian model averaging | Model-space priors/search, posterior inclusion/model probabilities, predictions and diagnostics. | Bayesian Model Averaging manual; Statistics menu. |
| Meta-analysis | Effect-size setup, synthesis, meta-regression, forest/funnel plots, bias/sensitivity, multivariate and multilevel models. | Meta-Analysis manual. |
| Power/sample-size/design | Power, precision/CI width, sample size, clustered designs and group sequential designs. | Power/Precision/Sample Size and Adaptive Designs manuals. |
| Resampling/simulation | Bootstrap, Bayesian bootstrap, jackknife, permutation, simulation, rolling/recursive estimation. | Statistics menu; Base Reference/Programming. |
| Postestimation | Predictions, residuals, margins, contrasts, pairwise comparisons, hypothesis tests, diagnostics, fit criteria and stored results. | Cross-cutting manual entries and menu groups. |
| Quality/reliability utilities | Quality-control charts, agreement/reliability measures, normality tests and related diagnostic statistics. | Statistics/Graphics menus; Base Reference. |

Numerical agreement also requires sample selection, weights, missingness, convergence, rank deficiency, degrees of freedom, VCE, likelihood definitions, finite-sample corrections, RNG and return metadata. Listing an estimator name does not verify these semantics.

## Full installed PDF family boundary

All 37 installed PDF stems were enumerated. This provides a reproducible family boundary, not a claim that every page was read:

| Stems | Documentation family |
|---|---|
| `gsw`, `gsm`, `gsu`, `u`, `ig` | Windows/Mac/Unix Getting Started, User's Guide, Installation Guide. |
| `r`, `d`, `fn`, `p`, `m`, `i`, `stoc` | Base, Data Management, Functions, Programming, Mata, Index, Subject Table of Contents. |
| `g`, `tables`, `rpt` | Graphics, Customizable Tables/Collections, Reporting. |
| `adapt`, `bayes`, `bma`, `causal`, `cm`, `dsge`, `erm`, `fmm` | Adaptive Designs, Bayesian, BMA, Causal, Choice, DSGE, Extended Regression, Finite Mixture. |
| `h2oml`, `irt`, `lasso`, `me`, `meta`, `mi`, `mv`, `pss` | H2O ML, IRT, Lasso, Multilevel, Meta, Multiple Imputation, Multivariate, Power/Precision/Sample Size. |
| `sem`, `sp`, `st`, `svy`, `ts`, `xt` | Structural Equations, Spatial, Survival, Survey, Time Series, Panel Data. |

The publisher's [documentation catalog](https://www.stata.com/features/documentation/index.html) is the public starting point for command entries, syntax, methods/formulas and worked examples. Feature-family labels alone are insufficient specifications.

## Post-snapshot/latest StataNow requirements

Public sources were checked on 15 October 2026. The [official update page](https://www.stata.com/support/updates/) reports revision 14 October 2026; the selected installed help snapshot stops in April 2025. The following later additions are therefore documented targets, **not locally observed/tested capabilities**:

- Parquet input; additional Python data-array interfaces; Mata quantiles.
- Financial statistics; business-calendar downloads; PDF text conversion; bar offsets.
- Psychometric meta-analysis; additional mediation/DID methods; proportional-odds tests; HDFE interactions.
- Discrete numerical derivatives; additional Bayesian priors; spatial residual structures for multilevel models.
- Frames in Data Editor; Do-file Editor monitoring/colorization; Word conversion; PDF append; table/collection improvements.

See the dated [StataNow update history](https://www.stata.com/news/statanow-updates/) and detailed [new features](https://www.stata.com/new-in-stata/features/) for scope. No global latest-version compatibility can be inferred from tests against the older installed executable.

## Remaining verification boundaries

Each implemented family needs short deterministic positive and negative tests, comparable return codes/results, data and metadata round trips, and numerical tolerances justified by the procedure. Third-party ado/Mata code can invoke any language feature or native integration; successful download/installation does not prove execution compatibility. A finite smoke suite cannot prove that every possible do-file runs.

The public [feature catalog](https://www.stata.com/features/) also distinguishes third-party/community features and embedded deployments. Accessibility, installation qualification, regulatory claims, performance scaling, byte-for-byte output and every platform's UI remain outside this bounded inspection. They must not be claimed as validated OpenStat features.
