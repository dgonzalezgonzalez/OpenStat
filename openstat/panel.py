"""Panel-data commands: ``xtset``, ``xtsum`` and the absorbed estimators.

Stata's panel commands need the panel identifier and the time variable.
``xtset idvar timevar`` records them, and ``xtsum`` then reports the
overall, between and within variation of a variable.  The absorbed
estimators (``areg``, ``xtreg`` and ``reghdfe``) project the fixed effects
out of the data before the regression is estimated.

The engine owns ``xtset`` and ``xtsum``: its implementations are the newer,
Stata-shaped ones, so ``_panel_command`` leaves those two to the engine and
claims the time-series declaration, the panel description and the absorbed
estimators.  The absorbed estimators come from the older branch; they read
the panel declaration from either spelling (``panel_id``/``panel_time`` or
``_panel_variable``/``_time_variable``), so either producer works.

The module also evaluates the ``L``/``F``/``D``/``S`` time-series operators
in an assignment: the engine's expression evaluator does not know them, but
the panel's own machinery does.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .design import (build_design, independent_columns, model_tokens,
                     parse_weight, time_series)
from .errors import StataError
from .formats import format_value
from .linear import post_linear_result
from .parser import quoted_end, split_outside, tokens

#: A time-series operator term such as ``L.x`` or ``F2.y``.
TIME_SERIES = re.compile(r"(?<![\w.])([LFDS]\d*)\.([A-Za-z_]\w*)", re.I)


def _words(text: str) -> list[str]:
    """Split a command's argument list into its words."""
    return [word for word in tokens(text) if word]


def _rewrite_time_series(text: str, replace) -> str:
    """Rewrite the time-series operator terms of an expression.

    Only the terms outside a quoted string are rewritten, so a string
    literal that happens to contain ``L.x`` is left alone.
    """
    parts, index, start = [], 0, 0
    while index < len(text):
        end = quoted_end(text, index)
        if end is not None:
            parts.append(TIME_SERIES.sub(replace, text[start:index]))
            parts.append(text[index:end])
            index = end
            start = end
            continue
        index += 1
    parts.append(TIME_SERIES.sub(replace, text[start:]))
    return "".join(parts)


def _number(value, width: int) -> str:
    """Render a statistic in a fixed-width field, as Stata's ``xtsum`` does."""
    if value is None:
        return ".".rjust(width)
    number = float(value)
    if not np.isfinite(number):
        return ".".rjust(width)
    return format_value(number, "%10.0g").rjust(width)


class PanelCommands:
    """Mixin providing the ``xt`` family of commands."""

    # ------------------------------------------------------------------
    # Command routing
    # ------------------------------------------------------------------
    def _panel_command(self, command, arg):
        """Claim the panel and absorbed-model commands, or return ``False``.

        ``True`` means the command was handled here; ``False`` leaves it to
        the engine's own dispatch table.  ``xtset`` and ``xtsum`` belong to
        the engine, whose implementations are newer, so they are never
        claimed here.
        """
        if command in {"xtset", "xtsum"}:
            return False
        if command == "tsset":
            self._tsset(arg)
            return True
        if command == "xtdescribe":
            self._xtdescribe(arg)
            return True
        if command in {"xtreg", "areg", "reghdfe"}:
            return self._fixed_effects(command, arg)
        if command in {"generate", "replace"} and TIME_SERIES.search(arg):
            return self._generate_time_series(command, arg)
        return False

    # ------------------------------------------------------------------
    # Time-series operators in assignments
    # ------------------------------------------------------------------
    def _generate_time_series(self, command, arg):
        """Evaluate an assignment that uses a time-series operator.

        The engine's expression evaluator does not know the ``L``/``F``/
        ``D``/``S`` operators, so each operator term is evaluated here with
        the panel's own time-series machinery.  The terms are then replaced
        by temporary variables and the assignment is handed back to the
        engine, which keeps the storage-type and qualifier rules.
        """
        terms = {}

        def replace(match):
            specification = match.group(0)
            if specification not in terms:
                name = self._hidden_name()
                terms[specification] = name
                self.data[name] = time_series(self, specification)
            return terms[specification]

        rewritten = _rewrite_time_series(arg, replace)
        try:
            self._generate(command, rewritten)
        finally:
            for name in terms.values():
                if name in self.data:
                    del self.data[name]
        return True

    # ------------------------------------------------------------------
    # Panel declarations
    # ------------------------------------------------------------------
    def _panel_identifier(self):
        """Return the panel identifier, whichever name holds it.

        The engine records the panel in ``panel_id``/``panel_time``; the
        older branch used ``_panel_variable``/``_time_variable``.  Both
        spellings are read, so either producer works.
        """
        return getattr(self, "panel_id", None) or getattr(self, "_panel_variable", None)

    def _time_identifier(self):
        """Return the time variable, whichever name holds it."""
        return getattr(self, "panel_time", None) or getattr(self, "_time_variable", None)

    def _tsset(self, arg):
        """Record the time variable of a time series (``tsset``)."""
        parts = split_outside(arg)
        options = self.options(" ".join(parts[1:]),
                               {"clear", "delta", "daily", "weekly", "monthly",
                                "quarterly", "yearly", "generic"})
        if "clear" in options:
            if parts[0]:
                raise StataError("clear does not accept variables", 198)
            self._time_variable = None
            self._time_delta = 1
            self.panel_time = None
            return
        names = parts[0].split()
        if not names:
            raise StataError("tsset requires a time variable", 100)
        if len(names) > 1:
            raise StataError("at most one declaration variable allowed", 198)
        for name in names:
            self.varlist(name)
        if any(not pd.api.types.is_numeric_dtype(self.data[name]) for name in names):
            raise StataError("numeric declaration variables required", 109)
        time = names[0]
        if self.data.loc[self.data[time].notna()].duplicated(time).any():
            raise StataError("repeated time values within panel", 451)
        delta = float(options.get("delta", 1))
        if delta <= 0:
            raise StataError("delta must be positive", 198)
        self._time_variable = time
        self._time_delta = delta
        self.panel_time = time
        self.data = self.data.sort_values(names, kind="stable").reset_index(drop=True)
        self.returns = {"panelvar": "", "timevar": time, "tdelta": delta}
        self.emit(f"Panel: ; time: {time}; delta: {delta:g}")

    def _xtdescribe(self, arg):
        """Describe the panel structure (``xtdescribe``)."""
        panel = self._panel_identifier()
        if not panel:
            raise StataError("panel variable not set; use xtset", 459)
        sizes = self.data.groupby(panel, dropna=True).size()
        self.returns = {"N": len(self.data), "N_g": len(sizes),
                        "Tmin": int(sizes.min()), "Tmax": int(sizes.max())}
        self.emit(str(self.returns))

    # ------------------------------------------------------------------
    # xtset
    # ------------------------------------------------------------------
    def _xtset(self, arg: str) -> None:
        """Record the panel identifier and the time variable."""
        parts = _words(arg)
        if not parts:
            raise StataError("xtset requires an id variable and a time variable", 100)
        if len(parts) < 2:
            raise StataError("xtset requires an id variable and a time variable", 100)
        idvar, timevar = parts[0], parts[1]
        for name in (idvar, timevar):
            if name not in self.data.columns:
                raise StataError(f"variable {name} not found", 111)
        self.panel_id = idvar
        self.panel_time = timevar
        # Stata sorts the data by the panel variables when they are set.
        self.data = self.data.sort_values([idvar, timevar], kind="stable").reset_index(drop=True)
        self._xtset_report(idvar, timevar)

    def _xtset_report(self, idvar: str, timevar: str) -> None:
        """Print the report ``xtset`` gives about the panel structure."""
        times = self.data[timevar]
        if pd.api.types.is_numeric_dtype(times):
            low, high = times.min(), times.max()
            unique = np.sort(times.dropna().unique())
            step = np.diff(unique)
            if len(unique) < 2:
                delta = "."
            elif (step == step[0]).all():
                delta = "1 unit" if step[0] == 1 else f"{step[0]:g} units"
            else:
                delta = "1 unit"
            span = f"{low:g} to {high:g}"
        else:
            delta = "."
            span = f"{times.min()} to {times.max()}"
        # Stata calls a panel strongly balanced when every group covers
        # the same time periods, weakly balanced when the groups are
        # consecutive but not identical, and unbalanced otherwise.
        counts = self.data.groupby(idvar).size()
        sizes = sorted(set(counts.tolist()))
        if len(sizes) == 1:
            balance = "strongly balanced"
        elif self._time_complete(idvar, timevar):
            balance = "weakly balanced"
        else:
            balance = "unbalanced"
        self.emit(f"       panel variable:  {idvar} ({balance})")
        self.emit(f"        time variable:  {timevar}, {span}")
        self.emit(f"                delta:  {delta}")

    def _time_complete(self, idvar: str, timevar: str) -> bool:
        """Report whether every panel covers the same time periods.

        A panel is complete when each group observes every time period
        that the panel as a whole contains.
        """
        frame = self.data[[idvar, timevar]].dropna()
        if frame.empty:
            return False
        periods = set(frame[timevar].unique())
        for _, group in frame.groupby(idvar):
            if set(group[timevar].unique()) != periods:
                return False
        return True

    # ------------------------------------------------------------------
    # xtsum
    # ------------------------------------------------------------------
    def _xtsum(self, arg: str) -> None:
        """Summarize a variable's overall, between and within variation."""
        names = self.varlist(arg)
        if not names:
            raise StataError("no variables specified", 100)
        if self._panel_identifier() is None:
            raise StataError("no panel variables declared", 460)
        self.emit("Variable         |      Mean   Std. Dev.       Min        Max |    Observations")
        self.emit("-" * 17 + "+" + "-" * 44 + "+" + "-" * 16)
        for name in names:
            if not pd.api.types.is_numeric_dtype(self.data[name]):
                raise StataError(f"variable {name} is not numeric", 109)
            self._xtsum_one(self.data, name)

    def _xtsum_one(self, frame: pd.DataFrame, name: str) -> None:
        """Print the three ``xtsum`` rows for one variable."""
        panel_id = self._panel_identifier()
        panel = frame[panel_id]
        values = frame[name]
        valid = values.notna() & panel.notna()
        groups = frame.loc[valid].groupby(panel_id, sort=True)
        overall = values[valid].astype(float)
        means = groups[name].mean()
        counts = groups.size()
        grand_mean = overall.mean()
        # The within deviation is measured from each group's own mean; the
        # between deviation is measured from the grand mean.
        within = overall - groups[name].transform("mean")
        between = means - grand_mean

        overall_sd = overall.std(ddof=1) if len(overall) > 1 else np.nan
        # Stata's between and within standard deviations use the number of
        # groups and the number of observations respectively.
        between_sd = between.std(ddof=1) if len(between) > 1 else np.nan
        within_sd = within.std(ddof=1) if len(within) > 1 else np.nan
        t_bar = len(overall) / len(counts) if len(counts) else np.nan

        self.returns = {
            "N": int(len(overall)),
            "mean": overall.mean(),
            "sd": overall_sd,
            "min": overall.min(),
            "max": overall.max(),
            "n_groups": int(len(counts)),
            "Tbar": t_bar,
        }
        # Stata puts the variable name in a nine column field and the
        # statistic in an eight column field.
        label = name[:9].ljust(9)
        # The within row reports the deviations recentered on the grand
        # mean, which is how Stata presents them.
        recentered = within + grand_mean
        # Stata's numeric block is 44 columns wide: the mean ends in
        # column 10, the standard deviation in 21, the minimum in 32 and
        # the maximum in 43.
        self.emit(f"{label}{'overall':<8}|{_number(overall.mean(), 10)}"
                  f"{_number(overall_sd, 11)}{_number(overall.min(), 11)}"
                  f"{_number(overall.max(), 11)} |     N ={len(overall):>8d}")
        self.emit(f"{'':<9}{'between':<8}|{'':<10}{_number(between_sd, 11)}"
                  f"{_number(means.min(), 11)}{_number(means.max(), 11)}"
                  f" |     n ={len(counts):>8d}")
        # An unbalanced panel reports the average number of observations
        # per group instead of the common number.
        balanced = counts.nunique() == 1
        label = "T" if balanced else "T-bar"
        self.emit(f"{'':<9}{'within':<8}|{'':<10}{_number(within_sd, 11)}"
                  f"{_number(recentered.min(), 11)}{_number(recentered.max(), 11)}"
                  f" | {label} ={t_bar:>8.6g}")

    # ------------------------------------------------------------------
    # Absorbed (fixed-effects) estimators
    # ------------------------------------------------------------------
    def _fixed_effects(self, command, arg):
        """Estimate a model with the fixed effects projected out of the data.

        ``areg`` and ``reghdfe`` absorb the effects named in ``absorb()``;
        ``xtreg`` absorbs the declared panel identifier.  The effects are
        removed with the alternating least-squares algorithm before the
        regression is estimated, so the reported standard errors account
        for the absorbed variation.
        """
        parts = split_outside(arg)
        options = self.options(" ".join(parts[1:]),
                                {"absorb", "fe", "robust", "vce", "cluster",
                                 "noconstant", "keepsingletons", "tolerance",
                                 "iterate", "residuals", "level", "nosample",
                                 "noabsorbtest", "dfabsorb"})
        if "noconstant" in options:
            raise StataError("noconstant for absorbed models is not implemented", 199)
        if command == "xtreg":
            if "fe" not in options:
                raise StataError("xtreg currently requires fe", 199)
            absorb = self._panel_identifier()
            if not absorb:
                raise StataError("panel variable not set; use xtset", 459)
        else:
            absorb = options.get("absorb")
            if not isinstance(absorb, str) or not absorb.strip():
                raise StataError("absorb() is required", 198)
        specification, weight_kind, weight_expression = parse_weight(parts[0])
        base, valid = self.qualifiers(specification)
        words = base.split(maxsplit=1)
        if not words or words[0] not in self.data:
            raise StataError("dependent variable not found", 111)
        dependent = words[0]
        outcome = self.data[dependent]
        if not pd.api.types.is_numeric_dtype(outcome):
            raise StataError("numeric outcome required", 109)
        valid &= outcome.notna() & np.isfinite(outcome)
        weights = pd.Series(1., index=self.data.index)
        if weight_kind:
            if weight_kind not in {"aw", "pw", "fw"}:
                raise StataError("unsupported weight type", 199)
            weights = pd.Series(self.evaluate(weight_expression),
                                index=self.data.index, dtype=float)
            if weights.lt(0).any():
                raise StataError("negative weights not allowed", 402)
            if weight_kind == "fw" and weights.dropna().mod(1).ne(0).any():
                raise StataError("frequency weights must be integers", 401)
            valid &= weights.gt(0) & np.isfinite(weights)
        design = build_design(self, words[1] if len(words) > 1 else "", valid)
        valid &= design.frame.notna().all(axis=1) & np.isfinite(design.frame).all(axis=1)
        effects = []
        for term in model_tokens(absorb):
            groups, slopes = [], []
            if "##" in term:
                raise StataError("factorial absorb syntax is not implemented", 199)
            for component in term.split("#"):
                if component.startswith("c."):
                    slopes.append(component[2:])
                else:
                    groups.append(component[2:] if component.startswith("i.") else component)
            for name in groups + slopes:
                self.varlist(name)
                valid &= self.data[name].notna()
            effects.append({"groups": groups, "slopes": slopes, "name": term})
        if not effects:
            raise StataError("empty absorb specification", 198)
        # ``reghdfe`` removes the singleton groups iteratively unless the
        # user asks for them to be kept.
        if command == "reghdfe" and "keepsingletons" not in options:
            for _ in range(100):
                previous = valid.copy()
                for effect in effects:
                    if effect["slopes"] or not effect["groups"]:
                        continue
                    counts = self.data.loc[valid].groupby(
                        effect["groups"], dropna=False)[dependent].transform("size")
                    valid.loc[counts.index] &= counts.gt(1)
                if previous.equals(valid):
                    break
        cluster_text = options.get("cluster")
        vce = options.get("vce", "robust" if "robust" in options or weight_kind == "pw" else "ols")
        if cluster_text:
            if "vce" in options:
                raise StataError("cluster() and vce() may not be combined", 198)
            vce = "cluster " + str(cluster_text)
        if command == "xtreg" and vce == "robust":
            vce = "cluster " + absorb
        cluster = None
        if str(vce).startswith("cluster "):
            clusters = str(vce).split()[1:]
            if len(clusters) != 1:
                raise StataError("multiway cluster covariance is not yet implemented", 199)
            cluster = clusters[0]
            self.varlist(cluster)
            valid &= self.data[cluster].notna()
        elif vce not in {"ols", "robust"}:
            raise StataError("unsupported covariance type", 199)
        sample = self.data.loc[valid]
        n = len(sample)
        if not n:
            raise StataError("no observations", 2000)
        w = weights.loc[valid].to_numpy()
        y = outcome.loc[valid].to_numpy(dtype=float)
        x = design.frame.loc[valid].to_numpy(dtype=float)
        terms, total_levels = [], 0
        for effect in effects:
            groups, slopes = effect["groups"], effect["slopes"]
            codes = (pd.factorize(pd.MultiIndex.from_frame(sample[groups]), sort=True)[0]
                     if groups else np.zeros(n, dtype=int))
            basis = np.ones(n)
            for name in slopes:
                if not pd.api.types.is_numeric_dtype(sample[name]):
                    raise StataError("numeric absorbed slopes required", 109)
                basis *= sample[name].to_numpy(dtype=float)
            count = int(codes.max()) + 1
            total_levels += count
            terms.append((codes, basis, count, effect))
        has_intercept_effect = any(not effect["slopes"] for effect in effects)
        z = np.column_stack([y, x] if has_intercept_effect else [y, x, np.ones(n)])
        means = np.average(z, axis=0, weights=w)
        transformed = z.copy()
        tolerance = float(options.get("tolerance", 1e-10))
        iterations = int(options.get("iterate", 10000))
        if not (0 < tolerance < 1 and 1 <= iterations <= 100000):
            raise StataError("invalid projection convergence settings", 198)
        for _ in range(iterations):
            self._tick()
            previous = transformed.copy()
            for codes, basis, count, _effect in terms:
                denominators = np.bincount(codes, weights=w * basis * basis, minlength=count)
                for column in range(transformed.shape[1]):
                    numerators = np.bincount(codes, weights=w * basis * transformed[:, column],
                                              minlength=count)
                    coefficients = np.divide(numerators, denominators, out=np.zeros(count),
                                             where=denominators > 0)
                    transformed[:, column] -= basis * coefficients[codes]
            if np.max(np.abs(transformed - previous)) < tolerance:
                break
        else:
            raise StataError("absorbed projections did not converge", 430)
        if has_intercept_effect:
            transformed += means
            yy = transformed[:, 0]
            xx = pd.DataFrame(transformed[:, 1:], columns=design.frame.columns, index=sample.index)
            xx, omitted = independent_columns(xx, constant=True)
            xx["_cons"] = 1.
        else:
            yy = transformed[:, 0]
            xx = pd.DataFrame(transformed[:, 1:],
                              columns=list(design.frame.columns) + ["_cons"], index=sample.index)
            xx, omitted = independent_columns(xx, constant=False)
        # The absorbed rank is exact for manageable problems.  Large one or
        # two categorical effects use group and connected-component identities
        # instead of dense dummy matrices.
        def absorbed_rank_of(subset) -> int:
            """Rank of the dummy blocks of a subset of the absorbed effects."""
            if n * total_levels <= 2_000_000 and total_levels <= 1000:
                blocks = []
                for codes, basis, count, _effect in subset:
                    block = np.zeros((n, count))
                    block[np.arange(n), codes] = basis
                    blocks.append(block)
                return int(np.linalg.matrix_rank(np.column_stack(blocks)))
            if len(subset) == 1:
                return int(subset[0][2])
            raise StataError("exact degrees of freedom for this large absorb specification are not yet implemented", 199)

        if n * total_levels <= 2_000_000 and total_levels <= 1000:
            absorbed_rank = absorbed_rank_of(terms)
        elif len(terms) == 1:
            absorbed_rank = terms[0][2]
        elif len(terms) == 2 and all(not term[3]["slopes"] for term in terms):
            first, second = terms
            offset = first[2]
            parent = np.arange(offset + second[2])

            def find(node):
                while parent[node] != node:
                    parent[node] = parent[parent[node]]
                    node = parent[node]
                return node

            for left, right in zip(first[0], second[0] + offset):
                parent[find(left)] = find(right)
            components = len({find(node) for node in range(len(parent))})
            absorbed_rank = total_levels - components
        else:
            raise StataError("exact degrees of freedom for this large absorb specification are not yet implemented", 199)
        absorbed_df = absorbed_rank - int(has_intercept_effect)
        # ``reghdfe`` reports ``e(df_a)`` as the absorbed rank, without the
        # constant's degree of freedom, and drops the parameters of any
        # absorbed effect nested within the clustering variable (the cluster
        # adjustment counts those already).  Measured on 19.5: two-way absorb
        # gives 25, and clustering on a variable that nests ``unit`` gives 5.
        reported_dfa = absorbed_rank
        if cluster is not None and command == "reghdfe":
            nested_terms = [term for term in terms
                            if term[3]["groups"] and not term[3]["slopes"]
                            and sample.groupby(term[3]["groups"])[cluster].nunique().max() == 1]
            if nested_terms:
                reported_dfa = absorbed_rank - absorbed_rank_of(nested_terms)
        k = xx.shape[1]
        N = int(w.sum()) if weight_kind == "fw" else n
        df_resid = N - k - absorbed_df
        if df_resid <= 0:
            raise StataError("insufficient residual degrees of freedom", 2001)
        matrix = xx.to_numpy()
        bread = np.linalg.inv(matrix.T @ (w[:, None] * matrix))
        beta = bread @ (matrix.T @ (w * yy))
        residual = yy - matrix @ beta
        rss_raw = float(np.sum(w * residual ** 2))
        denominator = df_resid
        clusters = None
        if vce == "ols":
            covariance = bread * rss_raw / df_resid
        elif vce == "robust":
            frequency = w if weight_kind == "fw" else w * w
            covariance = (bread @ (matrix.T @ ((frequency * residual ** 2)[:, None] * matrix))
                          @ bread * N / df_resid)
        else:
            grouped = (pd.DataFrame(matrix * (w * residual)[:, None])
                       .groupby(sample[cluster].to_numpy(), sort=False).sum().to_numpy())
            clusters = len(grouped)
            if clusters < 2:
                raise StataError("at least two clusters required", 459)
            if command == "xtreg":
                denominator = N - k
            elif command == "reghdfe":
                # reghdfe's small-sample factor divides by the residual
                # degrees of freedom that exclude the *reported* absorbed
                # degrees of freedom, so a fully nested specification
                # (df_a = 0) divides by N - k.
                denominator = N - k - reported_dfa
            covariance = (bread @ (grouped.T @ grouped) @ bread
                          * clusters / (clusters - 1) * (N - 1) / denominator)
        scale = n / w.sum() if weight_kind in {"aw", "pw"} else 1.
        rss = rss_raw * scale
        tss = float(np.sum(w * (y - np.average(y, weights=w)) ** 2)) * scale
        centered_within = transformed[:, 0] - np.average(transformed[:, 0], weights=w)
        within_tss = float(np.sum(w * centered_within ** 2)) * scale
        results = {
            "N": N, "df_r": clusters - 1 if clusters else df_resid, "df_a": reported_dfa,
            "df_m": k - 1, "cmd": command, "depvar": dependent, "rank": k, "rss": rss,
            "mss": tss - rss, "r2": 1 - rss / tss if tss else np.nan,
            "r2_a": 1 - (rss / df_resid) / (tss / (N - 1)) if tss and N > 1 else np.nan,
            "rmse": float(np.sqrt(rss / df_resid)),
            "r2_within": 1 - rss / within_tss if within_tss else np.nan,
            "converged": 1, "absvars": absorb, "N_clust": clusters or 0,
        }
        if command == "xtreg":
            results["N_g"] = sample[absorb].nunique()
            results["r2_w"] = results["r2_within"]
        params = pd.Series(beta, index=xx.columns)
        post_linear_result(self, params, covariance, valid, design, results, residual)
        for name in omitted:
            self.coefficients[name] = (0., 0.)
        regressors = (design.frame.loc[valid]
                      .reindex(columns=[c for c in params.index if c != "_cons"], fill_value=0)
                      .to_numpy())
        self._absorbed_effects = (outcome.loc[valid]
                                  - regressors @ params.drop("_cons", errors="ignore").to_numpy()
                                  - params.get("_cons", 0)
                                  - residual)
        if isinstance(options.get("residuals"), str):
            name = options["residuals"]
            self._new_name(name)
            self.data[name] = self._fit_residuals
        return True
