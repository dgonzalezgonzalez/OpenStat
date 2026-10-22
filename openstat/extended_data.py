"""Independent data-management commands with explicit grammar validation.

Behavioral contracts are documented in dduplicates.pdf, disid.pdf, drecode.pdf,
dexpand.pdf, dcross.pdf, djoinby.pdf and drecast.pdf from the public manuals.
"""
from __future__ import annotations

import copy
import re

import numpy as np
import pandas as pd

from .parser import outside_positions, quoted_end, split_outside, tokens, unquote


def _error(message, rc=198):
    from .engine import StataError
    raise StataError(message, rc)


class ExtendedDataCommands:
    def _extended_data(self, command, arg):
        handlers = {'duplicates': self._duplicates, 'isid': self._isid,
                    'recode': self._recode, 'expand': self._expand_observations,
                    'cross': self._cross_join, 'joinby': self._cross_join,
                    'compress': self._compress, 'recast': self._recast}
        if command in {'cross', 'joinby'}:
            self._cross_join(arg, command=command)
            return True
        if command in handlers:
            handlers[command](arg)
            return True
        if command == 'rename' and (arg.startswith('(') or len(split_outside(arg)) > 1):
            self._rename_group(arg)
            return True
        return False

    def _qualified(self, text):
        return self.qualifiers(' '+text if text.startswith(('if ', 'in ')) else text)

    def _duplicates(self, arg):
        words = arg.split(maxsplit=1)
        if not words:
            _error('duplicates subcommand required')
        action = {'rep': 'report', 'exam': 'examples', 'lis': 'list', 'ta': 'tag'}.get(words[0], words[0])
        parts = split_outside(words[1] if len(words) > 1 else '')
        allowed = {'generate', 'gen'} if action == 'tag' else {'force'} if action == 'drop' else {'noobs', 'clean'} if action in {'examples', 'list'} else set()
        options = self.options(' '.join(parts[1:]), allowed)
        if action not in {'report', 'examples', 'list', 'tag', 'drop'}:
            _error('unsupported duplicates subcommand', 199)
        base, mask = self._qualified(parts[0])
        names = self.varlist(base)
        if not names:
            _error('no variables defined', 111)
        frame = self.data.loc[mask, names]
        groups = frame.groupby(names, dropna=False, sort=False).ngroup()
        counts = groups.map(groups.value_counts()).astype('int64')
        self.returns = {'N': len(frame)}
        if action == 'report':
            sizes = groups.value_counts()
            self.returns['unique_value'] = len(sizes)
            distribution = sizes.value_counts().sort_index()
            self.emit('Copies Observations Surplus')
            for size, number in distribution.items():
                self.emit(f'{size:>6} {size*number:>12} {(size-1)*number:>7}')
        elif action == 'tag':
            if len(options) != 1 or not ({'generate', 'gen'} & options.keys()):
                _error('generate() required')
            name = str(options.get('generate', options.get('gen')))
            self._new_name(name)
            self.data[name] = np.nan
            self.data.loc[mask, name] = counts.to_numpy()-1
            self._storage_types[name] = 'long'
        elif action == 'drop':
            if base and 'force' not in options:
                _error('force option required with a variable list')
            dropped = frame.index[frame.duplicated(keep='first')]
            self.data = self.data.drop(index=dropped).reset_index(drop=True)
            self.returns['N_drop'] = len(dropped)
            self.emit(f'({len(dropped)} observations deleted)')
        else:
            selected = counts > 1
            if action == 'examples':
                selected &= ~groups.duplicated()
            self.emit(frame.loc[selected].to_string(index='noobs' not in options))

    def _isid(self, arg):
        parts = split_outside(arg)
        options = self.options(' '.join(parts[1:]), {'sort', 'missok'})
        if re.search(r'\busing\b', parts[0]):
            _error('isid using is not implemented', 199)
        names = self.varlist(parts[0], default=False)
        if not names:
            _error('identifier variables required', 100)
        frame = self.data[names]
        if 'missok' not in options and (frame.isna() | frame.eq('')).to_numpy().any():
            _error('identifier variables should never be missing', 459)
        if frame.duplicated().any():
            _error('variables do not uniquely identify the observations', 459)
        if 'sort' in options:
            self.data = self.data.sort_values(names, kind='stable', na_position='last').reset_index(drop=True)

    def _expand_observations(self, arg):
        parts = split_outside(arg)
        options = self.options(' '.join(parts[1:]), {'generate', 'gen'})
        if len(options) > 1:
            _error('generate() specified twice')
        expression, mask = self._qualified(parts[0].lstrip('= '))
        if not expression:
            _error('expand expression required')
        values = pd.Series(self.evaluate(expression), index=self.data.index)
        if not pd.api.types.is_numeric_dtype(values):
            _error('numeric expression required', 109)
        # Counts at or below one and missing counts retain their original row.
        counts = np.floor(values.fillna(1).clip(lower=1).to_numpy(dtype=float)+.5)
        counts[~mask.to_numpy()] = 1
        if not np.isfinite(counts).all() or counts.sum() > 10000000:
            _error('expanded dataset exceeds 10000000-observation execution limit', 901)
        counts = counts.astype('int64')
        added_index = np.repeat(np.arange(len(self.data)), counts-1)
        marker = options.get('generate', options.get('gen'))
        if marker is not None:
            self._new_name(str(marker))
        original = self.data.copy()
        additional = original.iloc[added_index].copy()
        if marker is not None:
            original[str(marker)] = 0
            additional[str(marker)] = 1
            self._storage_types[str(marker)] = 'byte'
        self.data = pd.concat([original, additional], ignore_index=True)
        self.emit(f'({len(additional)} observations created)')

    @staticmethod
    def _recode_number(text, series):
        text = text.strip().lower()
        if text in {'min', 'max'}:
            return getattr(series, text)()
        if text == '.':
            return np.nan
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
            _error(f'invalid recode value {text}')
        return float(text)

    def _recode(self, arg):
        parts = split_outside(arg)
        options = self.options(' '.join(parts[1:]), {'generate', 'gen', 'into', 'prefix', 'label', 'copyrest', 'test'})
        output_specs = [key for key in ('generate', 'gen', 'into', 'prefix') if key in options]
        if len(output_specs) > 1:
            _error('specify one of generate() and prefix()')
        head = parts[0]
        first = next((i for i in outside_positions(head, '') if head[i] == '('), None)
        if first is None:
            _error('recode requires parenthesized rules', 199)
        names = self.varlist(head[:first], default=False)
        if not names:
            _error('recode variables required', 100)
        rules, position = [], first
        while position < len(head):
            while position < len(head) and head[position].isspace():
                position += 1
            if position == len(head) or head[position] != '(':
                break
            start, depth, position = position+1, 1, position+1
            while position < len(head) and depth:
                end = quoted_end(head, position)
                if end is not None:
                    position = end
                    continue
                if head[position] == '(':
                    depth += 1
                elif head[position] == ')':
                    depth -= 1
                position += 1
            if depth:
                _error('unclosed recode rule')
            rule = split_outside(head[start:position-1], '=')
            if len(rule) != 2 or not rule[0] or not rule[1]:
                _error('invalid recode rule')
            rhs = tokens(rule[1])
            if not 1 <= len(rhs) <= 2:
                _error('invalid recode replacement')
            rules.append((rule[0].strip(), rhs[0], unquote(rhs[1]) if len(rhs) == 2 else None))
        qualifier, mask = self._qualified(head[position:].strip())
        if qualifier:
            _error('invalid recode qualifier')
        special = []
        for lhs, _, _ in rules:
            keyword = {'miss': 'missing', 'nonmiss': 'nonmissing', '*': 'else'}.get(lhs, lhs)
            if keyword in {'missing', 'nonmissing', 'else'}:
                special.append(keyword)
            elif special:
                _error('keyword recode rules must be last')
        if len(set(special)) != len(special) or 'else' in special and len(special) > 1:
            _error('incompatible keyword recode rules')
        if output_specs:
            spec = output_specs[0]
            targets = [str(options[spec])+name for name in names] if spec == 'prefix' else str(options[spec]).split()
            if len(targets) != len(names):
                _error('generate() requires one name per source variable')
            if len(targets) != len(set(targets)):
                _error('duplicate target variable', 110)
            for name in targets:
                self._new_name(name)
        else:
            targets = names
            if 'label' in options or any(label is not None for _, _, label in rules):
                _error('value labels require generate() or prefix()')
        outputs, labels = {}, {}
        for source, target in zip(names, targets):
            series = self.data[source]
            if not pd.api.types.is_numeric_dtype(series):
                _error('recode requires numeric variables', 109)
            original = series.astype(float)
            selected = original.loc[mask]
            result = original.copy()
            unmatched = mask.copy()
            code_labels = {}
            used_masks = []
            for index, (lhs, rhs, label) in enumerate(rules, 1):
                keyword = {'miss': 'missing', 'nonmiss': 'nonmissing', '*': 'else'}.get(lhs, lhs)
                if keyword in {'missing', 'nonmissing', 'else'}:
                    match = original.isna() if keyword == 'missing' else original.notna() if keyword == 'nonmissing' else pd.Series(True, index=original.index)
                else:
                    match = pd.Series(False, index=original.index)
                    for element in lhs.split():
                        bounds = element.split('/')
                        if len(bounds) > 2:
                            _error('invalid recode range')
                        low = self._recode_number(bounds[0], selected)
                        if len(bounds) == 1:
                            match |= original.isna() if pd.isna(low) else original.eq(low)
                        else:
                            high = self._recode_number(bounds[1], selected)
                            # Numeric missing values sort above all real values.
                            ordered = original.fillna(np.inf)
                            match |= ordered.between(np.inf if pd.isna(low) else low, np.inf if pd.isna(high) else high)
                value = self._recode_number(rhs, selected)
                if label is not None:
                    if pd.isna(value) or value != int(value):
                        _error('value labels require integer nonmissing replacements', 199)
                    code_labels[int(value)] = label
                if 'test' in options:
                    for previous, previous_mask in enumerate(used_masks, 1):
                        overlap = int((match & previous_mask & mask).sum())
                        if overlap and keyword not in {'missing', 'nonmissing', 'else'}:
                            self.emit(f'{source}: rules {previous} and {index} overlap in {overlap} observations')
                    if not (match & unmatched).any():
                        self.emit(f'{source}: rule {index} is never invoked')
                result.loc[match & unmatched] = value
                unmatched &= ~match
                used_masks.append(match)
            if target != source and 'copyrest' not in options:
                result.loc[~mask] = np.nan
            dtype = series.dtype if target == source else np.dtype('float32')
            if pd.api.types.is_integer_dtype(dtype):
                limits = np.iinfo(dtype)
                if result.isna().any() or result.ne(np.trunc(result)).any() or not result.between(limits.min, limits.max).all():
                    dtype = np.dtype('float64')
            with np.errstate(over='ignore', invalid='ignore'):
                outputs[target] = result.astype(dtype)
            if not np.isfinite(outputs[target].dropna()).all():
                outputs[target] = result.astype('float64')
            if code_labels:
                label_name = str(options.get('label', target))
                if not re.fullmatch(r'[A-Za-z_]\w{0,31}', label_name):
                    _error('invalid value label name')
                if label_name in self.value_labels:
                    _error(f'value label {label_name} already defined', 110)
                labels[target] = (label_name, code_labels)
        for source, target in zip(names, targets):
            changed = int((self.data[source].ne(outputs[target]) & ~(self.data[source].isna() & outputs[target].isna()) & mask).sum())
            self.data[target] = outputs[target]
            if target != source:
                self._storage_types[target] = 'float'
                if source in self.variable_labels:
                    self.variable_labels[target] = self.variable_labels[source]
            if target in labels:
                label_name, code_labels = labels[target]
                self.value_labels[label_name] = code_labels
                self.value_label_names[target] = label_name
            self.emit(f'({changed} changes made to {target})')

    def _rename_group(self, arg):
        parts = split_outside(arg)
        options = self.options(' '.join(parts[1:]), {'lower', 'upper', 'proper'})
        if options:
            if len(options) != 1 or parts[0].startswith('('):
                _error('invalid rename case conversion')
            old = self.varlist(parts[0])
            operation = {'lower': str.lower, 'upper': str.upper, 'proper': str.title}[next(iter(options))]
            new = [operation(name) for name in old]
        else:
            match = re.fullmatch(r'\(([^()]*)\)\s*\(([^()]*)\)', parts[0])
            if not match:
                _error('invalid group rename')
            old, new = self.varlist(match[1], default=False), match[2].split()
        if len(old) != len(new):
            _error('old and new variable lists must have equal length')
        if any(not re.fullmatch(r'[A-Za-z_]\w{0,31}', name) for name in new):
            _error('invalid new variable name')
        final = [dict(zip(old, new)).get(name, name) for name in self.data]
        if len(final) != len(set(final)):
            _error('variable already defined', 110)
        mapping = dict(zip(old, new))
        self.data.rename(columns=mapping, inplace=True)
        for attribute in ('variable_labels', 'value_label_names', 'formats', '_storage_types'):
            values = getattr(self, attribute)
            setattr(self, attribute, {mapping.get(name, name): value for name, value in values.items()})
        for attribute in ('_panel_variable', '_time_variable'):
            value = getattr(self, attribute, None)
            if value in mapping:
                setattr(self, attribute, mapping[value])

    def _cross_join(self, arg, *, command):
        parts = split_outside(arg)
        options = self.options(' '.join(parts[1:]), set() if command == 'cross' else {'unmatched', 'merge', '_merge', 'update', 'replace', 'nolabel'})
        words = tokens(parts[0])
        position = len(words)-2
        if position < 0 or words[position] != 'using':
            _error('using filename required')
        if command == 'cross' and position:
            _error('cross does not accept a variable list')
        path = self.path(words[-1], '.dta')
        if not path.is_file():
            _error('file not found', 601)
        metadata_names = ('variable_labels', 'value_labels', 'value_label_names', 'formats', '_storage_types', 'dataset_label')
        metadata = {name: copy.deepcopy(getattr(self, name)) for name in metadata_names}
        try:
            using = self._read(path)
            using_metadata = {name: copy.deepcopy(getattr(self, name)) for name in metadata_names}
        finally:
            for name, value in metadata.items():
                setattr(self, name, value)
        keys = self.varlist(' '.join(words[:position]), default=False) if position else [name for name in self.data if name in using] if command == 'joinby' else []
        if command == 'joinby' and not keys:
            _error('no common variables for joinby', 102)
        for name in keys:
            if name not in using:
                _error(f'variable {name} not found in using data', 111)
            if pd.api.types.is_numeric_dtype(self.data[name]) != pd.api.types.is_numeric_dtype(using[name]):
                _error(f'type mismatch for {name}', 106)
        unmatched = str(options.get('unmatched', 'none'))
        if unmatched not in {'none', 'both', 'master', 'using'}:
            _error('invalid unmatched() option')
        if 'replace' in options and 'update' not in options:
            _error('replace requires update')
        indicator = str(options.get('merge', options.get('_merge', '_merge'))) if 'unmatched' in options else None
        if indicator:
            self._new_name(indicator)
            if indicator in using:
                _error(f'variable {indicator} already defined in using data', 110)
        collisions = [name for name in self.data if name in using and name not in keys]
        for name in collisions:
            if pd.api.types.is_numeric_dtype(self.data[name]) != pd.api.types.is_numeric_dtype(using[name]):
                _error(f'type mismatch for {name}', 106)
        suffix = '__openstat_using'
        if any(name.endswith(suffix) for name in list(self.data)+list(using)):
            _error('temporary merge variable collision', 110)
        if command == 'cross':
            if len(self.data)*len(using) > 10000000:
                _error('cross exceeds 10000000-observation execution limit', 901)
            result = self.data.merge(using.drop(columns=collisions), how='cross', sort=False)
        else:
            # Estimate the product within each key before allocating a join.
            master_counts = self.data.groupby(keys, dropna=False).size()
            using_counts = using.groupby(keys, dropna=False).size()
            if master_counts.mul(using_counts, fill_value=0).sum()+len(self.data)+len(using) > 10000000:
                _error('joinby exceeds 10000000-observation execution limit', 901)
            how = {'none': 'inner', 'both': 'outer', 'master': 'left', 'using': 'right'}[unmatched]
            merge_flag = self._temporary_name()
            result = self.data.merge(using, on=keys, how=how, suffixes=('', suffix), indicator=merge_flag, sort=False)
            for name in collisions:
                source = result[name+suffix]
                missing = source.isna() | source.eq('')
                master_missing = result[name].isna() | result[name].eq('')
                update_mask = result[merge_flag].eq('right_only') | (master_missing if 'update' in options else False)
                if 'replace' in options:
                    update_mask |= ~missing
                result.loc[update_mask, name] = source.loc[update_mask]
                result.drop(columns=[name+suffix], inplace=True)
            if indicator:
                result[indicator] = result[merge_flag].map({'left_only': 1, 'right_only': 2, 'both': 3}).astype('int8')
            result.drop(columns=[merge_flag], inplace=True)
        self.data = result.reset_index(drop=True)
        for name in self.data:
            if not pd.api.types.is_numeric_dtype(self.data[name]):
                self.data[name] = self.data[name].fillna('')
        for attribute in ('variable_labels', 'value_label_names', 'formats', '_storage_types'):
            target = getattr(self, attribute)
            for name, value in using_metadata[attribute].items():
                if name not in metadata[attribute] and name not in collisions:
                    target[name] = value
        if 'nolabel' not in options:
            for name, table in using_metadata['value_labels'].items():
                self.value_labels.setdefault(name, table)

    def _recast(self, arg):
        parts = split_outside(arg)
        options = self.options(' '.join(parts[1:]), {'force'})
        words = parts[0].split(maxsplit=1)
        if len(words) != 2 or not re.fullmatch(r'byte|int|long|float|double|str[1-9]\d*|strL', words[0]):
            _error('recast requires a storage type and variable list')
        storage, names = words[0], self.varlist(words[1], default=False)
        outputs = {}
        for name in names:
            original = self.data[name]
            numeric = pd.api.types.is_numeric_dtype(original)
            if storage.startswith('str'):
                if numeric:
                    _error('numeric to string recast is not allowed', 109)
                value = original.fillna('').astype(str)
                if storage != 'strL':
                    width = int(storage[3:])
                    if width > 2045:
                        _error('string storage width exceeds 2045')
                    truncated = value.str.slice(0, width)
                    if 'force' not in options and not value.equals(truncated):
                        _error('recast would lose information; specify force', 459)
                    value = truncated
            else:
                if not numeric:
                    _error('string to numeric recast is not allowed', 109)
                value = original.astype(float)
                if storage in {'byte', 'int', 'long'}:
                    low, high = {'byte': (-127, 100), 'int': (-32767, 32740), 'long': (-2147483647, 2147483620)}[storage]
                    value = np.trunc(value).where(value.between(low, high))
                    if value.notna().all():
                        value = value.astype({'byte': 'int8', 'int': 'int16', 'long': 'int32'}[storage])
                else:
                    dtype = 'float32' if storage == 'float' else 'float64'
                    with np.errstate(over='ignore', invalid='ignore'):
                        value = value.astype(dtype)
                    value = value.where(value.abs() <= np.finfo(dtype).max/2)
                if 'force' not in options:
                    equal = value.astype(float).eq(original.astype(float)) | (value.isna() & original.isna())
                    if not equal.all():
                        _error('recast would lose information; specify force', 459)
            outputs[name] = value
        for name, value in outputs.items():
            self.data[name] = value
            self._storage_types[name] = storage

    def _compress(self, arg):
        parts = split_outside(arg)
        self.options(' '.join(parts[1:]), set())
        for name in self.varlist(parts[0]):
            series = self.data[name]
            if not pd.api.types.is_numeric_dtype(series):
                maximum = int(series.fillna('').astype(str).str.len().max()) if len(series) else 1
                self._storage_types[name] = 'str'+str(max(1, maximum)) if maximum <= 2045 else 'strL'
                continue
            values = series.dropna()
            integer = values.eq(np.trunc(values)).all()
            if integer:
                for storage, low, high in [('byte', -127, 100), ('int', -32767, 32740), ('long', -2147483647, 2147483620)]:
                    if values.between(low, high).all():
                        self._recast(storage+' '+name)
                        break
                else:
                    self._storage_types[name] = 'double'
            else:
                with np.errstate(over='ignore', invalid='ignore'):
                    smaller = values.astype('float32').astype(float)
                if smaller.eq(values.astype(float)).all():
                    self._recast('float '+name)
