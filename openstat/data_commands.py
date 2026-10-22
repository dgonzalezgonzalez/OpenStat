"""Independent dataset I/O and restructuring commands.

Only the explicit syntax below is accepted. Unsupported options are errors;
these commands never invoke Stata or execute an external statistical runtime.
"""
from __future__ import annotations

import copy
import csv
import re

import numpy as np
import pandas as pd

from .parser import split_outside, tokens, unquote


class IOCommands:
    def _data_command(self, command, arg):
        from .engine import StataError

        metadata_names = ("variable_labels", "value_labels", "value_label_names", "formats", "dataset_label", "_storage_types")

        def metadata():
            return {name: copy.deepcopy(getattr(self, name, {} if name=='_storage_types' else None)) for name in metadata_names}

        def restore_metadata(values):
            for name, value in values.items():
                setattr(self, name, value)

        def reset_metadata():
            self.variable_labels = {}
            self.value_labels = {}
            self.value_label_names = {}
            self.formats = {}
            self.dataset_label = ""
            self._storage_types = {}

        def retain_metadata(columns):
            for name in ("variable_labels", "value_label_names", "formats", "_storage_types"):
                setattr(self, name, {key: value for key, value in getattr(self, name).items() if key in columns})

        def merge_metadata(master, using, new_columns):
            restore_metadata(master)
            for key, value in using["value_labels"].items():
                self.value_labels.setdefault(key, value)
            for name in ("variable_labels", "value_label_names", "formats", "_storage_types"):
                target = getattr(self, name)
                for key in new_columns:
                    if key in using[name]:
                        target[key] = using[name][key]

        def parts(allowed):
            pieces = split_outside(arg)
            return pieces[0], self.options(" ".join(pieces[1:]), allowed)

        def filename(text, extension):
            names = tokens(text)
            if len(names) != 1:
                raise StataError("one filename required", 198)
            if unquote(names[0]).startswith(("http://", "https://")):
                raise StataError("remote dataset I/O is not implemented", 199)
            return self.path(names[0], extension)

        def require_file(path):
            if not path.is_file():
                raise StataError(f"file {path.name} not found", 601)

        def require_writable(path, options):
            if path.exists() and "replace" not in options:
                raise StataError(f"file {path.name} already exists", 602)
            if not path.parent.is_dir():
                raise StataError(f"directory for {path.name} not found", 603)

        def labeled(frame, options):
            result = frame.copy()
            if "nolabel" not in options:
                for column in result:
                    table = self.value_labels.get(self.value_label_names.get(column), {})
                    if table:
                        result[column] = result[column].map(lambda value: table.get(value, value) if not pd.isna(value) else value)
            return result

        def using_parts(text):
            values = tokens(text)
            markers = [index for index, value in enumerate(values) if value.lower() == "using"]
            if len(markers) != 1:
                raise StataError("using filename required", 198)
            position = markers[0]
            return " ".join(values[:position]), " ".join(values[position + 1:])

        def variable_names(names, lower=False):
            output = []
            for index, original in enumerate(names, 1):
                value = str(original).lower() if lower else str(original)
                value = re.sub(r"[^A-Za-z0-9_]", "_", value)
                if not value or not re.match(r"[A-Za-z_]", value):
                    value = "v" + value
                value = value[:32]
                base, count = value, 1
                while value in output:
                    suffix = f"_{count}"
                    value = base[:32-len(suffix)] + suffix
                    count += 1
                output.append(value)
            return output

        if command == "use":
            head, options = parts({"clear", "nolabel"})
            if any(value.lower() == "using" for value in tokens(head)):
                variables, source = using_parts(head)
            else:
                variables, source = "", head
            path = filename(source, ".dta")
            require_file(path)
            old_data, old_metadata = self.data, metadata()
            try:
                self.data = self._read(path)
                base, mask = self.qualifiers(variables)
                columns = self.varlist(base)
                self.data = self.data.loc[mask, columns].reset_index(drop=True)
                retain_metadata(columns)
                if "nolabel" in options:
                    self.value_labels = {}
            except Exception:
                self.data = old_data
                restore_metadata(old_metadata)
                raise
            self.emit(f"({len(self.data):,} observations read)")
            return True

        if command in {"save", "saveold"}:
            head, options = parts({"replace", "empty", "nolabel", "version"} if command == "saveold" else {"replace", "empty", "nolabel"})
            path = filename(head, ".dta")
            require_writable(path, options)
            if self.data.shape[1] == 0 and "empty" not in options:
                raise StataError("no variables defined", 111)
            version = 118
            if command == "saveold" and str(options.get("version", "14")) != "14":
                raise StataError("saveold currently supports version(14) only", 199)
            labels = self.value_label_names
            try:
                if "nolabel" in options:
                    self.value_label_names = {}
                self._write(path, version=version)
            except OSError as error:
                raise StataError(f"could not write {path.name}: {type(error).__name__}", 603) from error
            finally:
                self.value_label_names = labels
            self.emit(f"file {path.name} saved")
            return True

        if command == "import":
            kind, _, remainder = arg.partition(" ")
            if kind not in {"delimited", "excel"}:
                raise StataError(f"import {kind} is not implemented", 199)
            pieces = split_outside(remainder)
            source = pieces[0]
            if source.startswith("using "):
                source = source[6:].strip()
            allowed = {"clear", "varnames", "delimiters", "encoding", "stringcols", "case"} if kind == "delimited" else {"clear", "firstrow", "sheet", "allstring"}
            options = self.options(" ".join(pieces[1:]), allowed)
            path = filename(source, ".csv" if kind == "delimited" else ".xlsx")
            require_file(path)
            if kind == "delimited":
                header = str(options.get("varnames", "1"))
                if header not in {"1", "nonames"}:
                    raise StataError("only varnames(1) and varnames(nonames) are implemented", 199)
                separator = unquote(str(options.get("delimiters", ",")))
                if len(separator) != 1:
                    raise StataError("one delimiter character required", 198)
                if "stringcols" in options and options["stringcols"] != "_all":
                    raise StataError("only stringcols(_all) is implemented", 199)
                name_case = str(options.get("case", "lower"))
                if name_case not in {"lower", "upper", "preserve"}:
                    raise StataError("case() must be lower, upper, or preserve", 198)
                frame = pd.read_csv(path, sep=separator, header=0 if header == "1" else None,
                                    encoding=unquote(str(options.get("encoding", "utf-8-sig"))),
                                    dtype=str if "stringcols" in options else None)
                if header == "nonames":
                    frame.columns = [f"v{i}" for i in range(1, len(frame.columns) + 1)]
                frame.columns = variable_names(frame.columns, lower=name_case == "lower")
                if name_case == "upper":
                    frame.columns = [value.upper() for value in frame.columns]
            else:
                frame = pd.read_excel(path, sheet_name=unquote(str(options.get("sheet", "0"))) if "sheet" in options else 0,
                                      header=0 if "firstrow" in options else None,
                                      dtype=str if "allstring" in options else None)
                if "firstrow" not in options:
                    def excel_column(number):
                        name = ""
                        while number:
                            number, remainder = divmod(number - 1, 26)
                            name = chr(65 + remainder) + name
                        return name
                    frame.columns = [excel_column(i) for i in range(1, len(frame.columns) + 1)]
                frame.columns = variable_names(frame.columns)
            for column in frame:
                if pd.api.types.is_object_dtype(frame[column]) or pd.api.types.is_string_dtype(frame[column]):
                    frame[column] = frame[column].fillna("")
            self.data = frame.reset_index(drop=True)
            reset_metadata()
            self.emit(f"({self.data.shape[1]} vars, {len(self.data):,} obs)")
            return True

        if command == "export":
            kind, _, remainder = arg.partition(" ")
            if kind not in {"delimited", "excel"}:
                raise StataError(f"export {kind} is not implemented", 199)
            pieces = split_outside(remainder)
            variables, destination = using_parts(pieces[0])
            allowed = {"replace", "nolabel", "novarnames", "delimiter", "quote", "encoding", "missing"} if kind == "delimited" else {"replace", "nolabel", "firstrow", "sheet"}
            options = self.options(" ".join(pieces[1:]), allowed)
            path = filename(destination, ".csv" if kind == "delimited" else ".xlsx")
            require_writable(path, options)
            base, mask = self.qualifiers(variables)
            frame = labeled(self.data.loc[mask, self.varlist(base)], options)
            if kind == "delimited":
                delimiter = unquote(str(options.get("delimiter", ",")))
                if len(delimiter) != 1:
                    raise StataError("one delimiter character required", 198)
                frame.to_csv(path, index=False, header="novarnames" not in options, sep=delimiter,
                             na_rep="." if "missing" in options else "", quoting=csv.QUOTE_ALL if "quote" in options else csv.QUOTE_MINIMAL,
                             encoding=unquote(str(options.get("encoding", "utf-8"))))
            else:
                firstrow = options.get("firstrow")
                if firstrow is not None and firstrow not in {"variables", "varlabels"}:
                    raise StataError("firstrow() must be variables or varlabels", 198)
                headers = [self.variable_labels.get(column, column) or column for column in frame] if firstrow == "varlabels" else firstrow == "variables"
                frame.to_excel(path, index=False, header=headers, sheet_name=unquote(str(options.get("sheet", "Sheet1"))))
            self.emit(f"file {path.name} saved")
            return True

        if command == "append":
            head, options = parts({"generate", "force", "keep", "nolabel"})
            if not head.startswith("using "):
                raise StataError("append using filename required", 198)
            sources = tokens(head[6:])
            if not sources:
                raise StataError("using filename required", 198)
            generate = options.get("generate")
            if generate:
                self._new_name(generate)
            master_data, master_meta = self.data.copy(), metadata()
            original_meta = copy.deepcopy(master_meta)
            result = self.data.copy()
            if generate:
                result[generate] = 0
            try:
                for source_index, source in enumerate(sources, 1):
                    path = filename(source, ".dta")
                    require_file(path)
                    incoming = self._read(path)
                    incoming_meta = metadata()
                    restore_metadata(master_meta)
                    if "keep" in options:
                        requested = options["keep"].split()
                        missing = [name for name in requested if name not in incoming]
                        if missing:
                            raise StataError(f"variable {missing[0]} not found", 111)
                        incoming = incoming[requested]
                    for column in set(result) & set(incoming):
                        left_numeric = pd.api.types.is_numeric_dtype(result[column])
                        right_numeric = pd.api.types.is_numeric_dtype(incoming[column])
                        if left_numeric != right_numeric:
                            if "force" not in options:
                                raise StataError(f"variable {column} has incompatible types", 106)
                            incoming[column] = np.nan if left_numeric else ""
                    new_columns = [column for column in incoming if column not in result]
                    if generate:
                        if generate in incoming:
                            raise StataError(f"variable {generate} already defined in using data", 110)
                        incoming[generate] = source_index
                    result = pd.concat([result, incoming], ignore_index=True, sort=False)
                    for column in result:
                        source_values = master_data[column] if column in master_data else incoming[column] if column in incoming else result[column]
                        if not pd.api.types.is_numeric_dtype(source_values):
                            result[column] = result[column].fillna("")
                    if "nolabel" in options:
                        incoming_meta["value_labels"] = {}
                    merge_metadata(master_meta, incoming_meta, new_columns)
                    master_meta = metadata()
                self.data = result
            except Exception:
                self.data = master_data
                restore_metadata(original_meta)
                raise
            retain_metadata(self.data.columns)
            self.emit(f"({len(self.data):,} observations)")
            return True

        if command == "merge":
            head, options = parts({"generate", "nogen", "nogenerate", "keep", "assert", "keepusing", "nolabel"})
            if "nogenerate" in options:
                options["nogen"] = True
            specification, source = using_parts(head)
            pieces = specification.split(maxsplit=1)
            if len(pieces) != 2 or pieces[0] not in {"1:1", "1:m", "m:1"}:
                raise StataError("only merge 1:1, 1:m, and m:1 are implemented", 199)
            cardinality, key_text = pieces
            keys = self.varlist(key_text, default=False)
            if not keys:
                raise StataError("merge key required", 198)
            generate = options.get("generate", "_merge")
            if "nogen" not in options:
                self._new_name(generate)
            path = filename(source, ".dta")
            require_file(path)
            master_data, master_meta = self.data.copy(), metadata()
            try:
                incoming = self._read(path)
                incoming_meta = metadata()
                restore_metadata(master_meta)
                for key in keys:
                    if key not in incoming:
                        raise StataError(f"variable {key} not found in using data", 111)
                    if pd.api.types.is_numeric_dtype(master_data[key]) != pd.api.types.is_numeric_dtype(incoming[key]):
                        raise StataError(f"key {key} has incompatible types", 106)
                if cardinality in {"1:1", "1:m"} and master_data.duplicated(keys).any():
                    raise StataError("variables do not uniquely identify observations in master data", 459)
                if cardinality in {"1:1", "m:1"} and incoming.duplicated(keys).any():
                    raise StataError("variables do not uniquely identify observations in using data", 459)
                if "keepusing" in options:
                    requested = options["keepusing"].split()
                    for name in requested:
                        if name not in incoming:
                            raise StataError(f"variable {name} not found", 111)
                    incoming = incoming[list(dict.fromkeys(keys + requested))]
                overlap = [column for column in master_data if column in incoming and column not in keys]
                for column in overlap:
                    if pd.api.types.is_numeric_dtype(master_data[column]) != pd.api.types.is_numeric_dtype(incoming[column]):
                        raise StataError(f"variable {column} has incompatible types", 106)
                new_columns = [column for column in incoming if column not in master_data]
                temporary_suffix = "__openstat_using"
                if any(column.endswith(temporary_suffix) for column in master_data):
                    raise StataError("reserved internal merge suffix in variable name", 198)
                merged = master_data.merge(incoming, how="outer", on=keys, suffixes=("", temporary_suffix), indicator="__openstat_merge", sort=False)
                status = merged.pop("__openstat_merge").astype(object).map({"left_only": 1, "right_only": 2, "both": 3}).astype("int8")
                for column in overlap:
                    merged[column] = merged[column].where(status != 2, merged[column + temporary_suffix])
                    merged.drop(columns=column + temporary_suffix, inplace=True)
                def statuses(value):
                    codes = {"master": 1, "using": 2, "match": 3, "1": 1, "2": 2, "3": 3}
                    requested = value.split()
                    if not requested or any(item not in codes for item in requested):
                        raise StataError("merge status must be master, using, match, or 1 2 3", 198)
                    return {codes[item] for item in requested}
                if "assert" in options and not status.isin(statuses(options["assert"])).all():
                    raise StataError("merge results do not match assert()", 459)
                if "nogen" not in options:
                    merged[generate] = status
                if "keep" in options:
                    merged = merged.loc[status.isin(statuses(options["keep"]))]
                self.data = merged.sort_values(keys, kind="stable", na_position="last").reset_index(drop=True)
                if "nolabel" in options:
                    incoming_meta["value_labels"] = {}
                merge_metadata(master_meta, incoming_meta, new_columns)
                retain_metadata(self.data.columns)
                if "nogen" not in options:
                    self.variable_labels[generate] = "Matching result from merge"
                    self.value_labels[generate] = {1: "Master only (1)", 2: "Using only (2)", 3: "Matched (3)"}
                    self.value_label_names[generate] = generate
            except Exception:
                self.data = master_data
                restore_metadata(master_meta)
                raise
            self.emit(f"({len(self.data):,} observations after merge)")
            return True

        if command == "reshape":
            head, options = parts({"i", "j"})
            values = head.split()
            if len(values) < 2 or values[0] not in {"long", "wide"}:
                raise StataError("reshape long|wide stubnames required", 198)
            direction, stubs = values[0], values[1:]
            if "i" not in options or "j" not in options:
                raise StataError("reshape requires i() and j()", 198)
            keys = self.varlist(options["i"], default=False)
            j = str(options["j"])
            if not keys or not re.fullmatch(r"[A-Za-z_]\w{0,31}", j):
                raise StataError("invalid reshape keys", 198)
            original_meta = metadata()
            if direction == "long":
                self._new_name(j)
                if self.data.duplicated(keys).any():
                    raise StataError("i() does not uniquely identify observations", 9)
                mappings, levels = {}, set()
                for stub in stubs:
                    mapping = {}
                    for column in self.data:
                        match = re.fullmatch(re.escape(stub) + r"(\d+)", column)
                        if match:
                            level = int(match[1])
                            if level in mapping:
                                raise StataError("ambiguous numeric reshape suffix", 198)
                            mapping[level] = column
                    if not mapping:
                        raise StataError(f"no numeric-suffix variables for {stub}", 111)
                    mappings[stub] = mapping
                    levels.update(mapping)
                reshaped = {column for mapping in mappings.values() for column in mapping.values()}
                constant = [column for column in self.data if column not in reshaped and column not in keys]
                for stub in stubs:
                    if stub in self.data:
                        raise StataError(f"variable {stub} already defined", 110)
                rows = []
                for record in self.data.to_dict("records"):
                    for level in sorted(levels):
                        row = {column: record[column] for column in keys + constant}
                        row[j] = level
                        row.update({stub: record.get(mapping.get(level), np.nan) for stub, mapping in mappings.items()})
                        rows.append(row)
                self.data = pd.DataFrame(rows, columns=keys + [j] + constant + stubs).sort_values(keys + [j], kind="stable").reset_index(drop=True)
                retain_metadata(keys + constant)
                for name in ("variable_labels", "value_label_names", "formats"):
                    target = getattr(self, name)
                    for stub, mapping in mappings.items():
                        first = mapping[min(mapping)]
                        if first in original_meta[name]:
                            target[stub] = original_meta[name][first]
            else:
                for column in stubs + [j]:
                    if column not in self.data:
                        raise StataError(f"variable {column} not found", 111)
                if self.data.duplicated(keys + [j]).any():
                    raise StataError("j() values are not unique within i()", 9)
                if not pd.api.types.is_numeric_dtype(self.data[j]) or self.data[j].isna().any() or (self.data[j] != np.floor(self.data[j])).any() or (self.data[j] < 0).any():
                    raise StataError("only nonnegative integer j() values are implemented", 199)
                constant = [column for column in self.data if column not in keys + [j] + stubs]
                levels = sorted(int(value) for value in self.data[j].unique())
                rows = []
                for _, group in self.data.groupby(keys, dropna=False, sort=False):
                    if any(group[column].nunique(dropna=False) > 1 for column in constant):
                        raise StataError("variables are not constant within i()", 9)
                    first = group.iloc[0]
                    row = {column: first[column] for column in keys + constant}
                    for stub in stubs:
                        indexed = group.set_index(j)[stub]
                        for level in levels:
                            name = f"{stub}{level}"
                            if len(name) > 32 or name in keys + constant:
                                raise StataError("invalid or conflicting wide variable name", 198)
                            row[name] = indexed.get(level, np.nan)
                    rows.append(row)
                self.data = pd.DataFrame(rows, columns=keys + constant + [f"{stub}{level}" for stub in stubs for level in levels]).sort_values(keys, kind="stable").reset_index(drop=True)
                retain_metadata(keys + constant)
                for name in ("variable_labels", "value_label_names", "formats"):
                    target = getattr(self, name)
                    for stub in stubs:
                        if stub in original_meta[name]:
                            for level in levels:
                                target[f"{stub}{level}"] = original_meta[name][stub]
            self.emit(f"({len(self.data):,} observations after reshape {direction})")
            return True

        if command == "collapse":
            head, options = parts({"by"})
            base, mask = self.qualifiers(head)
            base = re.sub(r"\s*=\s*", "=", base)
            keys = self.varlist(str(options["by"]), default=False) if "by" in options else []
            pattern = re.compile(r"\(([A-Za-z]+)\)|([^\s()]+)")
            parsed, statistic, cursor = [], "mean", 0
            for match in pattern.finditer(base):
                if base[cursor:match.start()].strip():
                    raise StataError("invalid collapse specification", 198)
                cursor = match.end()
                if match[1]:
                    statistic = match[1].lower()
                    if statistic not in {"mean", "sum", "count", "min", "max", "sd"}:
                        raise StataError(f"collapse ({statistic}) is not implemented", 199)
                else:
                    specification = match[2]
                    if "=" in specification:
                        output, source = specification.split("=", 1)
                        columns = self.varlist(source, default=False)
                        if len(columns) != 1:
                            raise StataError("one source required for collapse assignment", 198)
                        parsed.append((output, columns[0], statistic))
                    else:
                        parsed.extend((column, column, statistic) for column in self.varlist(specification, default=False))
            if base[cursor:].strip() or not parsed:
                raise StataError("invalid collapse specification", 198)
            outputs = [output for output, _, _ in parsed]
            if len(set(outputs)) != len(outputs) or set(outputs) & set(keys):
                raise StataError("duplicate collapse output name", 110)
            for output, source, _ in parsed:
                if not re.fullmatch(r"[A-Za-z_]\w{0,31}", output):
                    raise StataError("invalid collapse output name", 198)
                if not pd.api.types.is_numeric_dtype(self.data[source]):
                    raise StataError(f"variable {source} must be numeric", 109)
            source_data, original_meta = self.data.loc[mask], metadata()
            grouped = source_data.groupby(keys, dropna=False, sort=True) if keys else [((), source_data)]
            rows = []
            for group_key, group in grouped:
                group_key = group_key if isinstance(group_key, tuple) else (group_key,)
                row = dict(zip(keys, group_key))
                for output, source, statistic in parsed:
                    values = group[source]
                    row[output] = values.count() if statistic == "count" else values.std(ddof=1) if statistic == "sd" else getattr(values, statistic)()
                rows.append(row)
            self.data = pd.DataFrame(rows, columns=keys + outputs).reset_index(drop=True)
            retain_metadata(keys)
            for output, source, statistic in parsed:
                self.variable_labels[output] = f"({statistic}) {original_meta['variable_labels'].get(source, source)}"
            self.emit(f"({len(self.data):,} observations after collapse)")
            return True

        return False
