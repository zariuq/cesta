"""Qualify actual link records against their emitted ownership body plans.

Only Cesta's permission additions are interpreted. Canonical C declaration
keys, source projection, native obligations and actual linker selection remain
explicit boundaries. Allocation-family names are interned across units; local
numeric family identifiers are renamed throughout the admitted plans.
"""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

from oracles.pro_compact_cesta import Sym, Text, parse
from ownership_admission import admit_record, signature


def dump(value):
    if isinstance(value, list):
        return '(' + ' '.join(map(dump, value)) + ')'
    if isinstance(value, Text):
        return json.dumps(value.data.decode())
    return str(value)


def section(value, name):
    return next(item for item in value if isinstance(item, list) and item and item[0] == name)


def family_position(node):
    if not node or not isinstance(node[0], str):
        return None
    if node[0] == 'owned':
        if len(node) != 3:
            return None
        return 1 if node[-1] in ('nullable', 'nonnull') else 2
    if node[0] == 'optional-owned':
        return 2
    if node[0] == 'released':
        return 1
    if node[0] in ('result', 'returns') and node[1] in ('owned', 'own'):
        return 2
    if node[0] == 'root':
        return 5
    if node[0] in ('release', 'give', 'handoff'):
        return 2
    if node[0] == 'record-give':
        return 3
    return None


def portable(value, families):
    if not isinstance(value, list):
        return value
    if value and value[0] == 'callback':
        assert len(value) == 3 and isinstance(value[1], Text)
        return [Sym('static'), value[2]]
    result = [portable(child, families) for child in value]
    position = family_position(value)
    if position is not None:
        assert isinstance(value[position], Text), ('expected a named family', value)
        name = value[position].data.decode()
        key = 'named/' + name
        families.setdefault(key, len(families) + 1)
        result[position] = Sym(str(families[key]))
    return result


def bind_families(named, numeric, local):
    """Require identical structure, except for declared family names/IDs.

    Hidden private-state parameters are deliberately not erased: those require
    their own framing rule and cannot be certified by this explicit interface.
    """
    if not isinstance(named, list):
        assert named == numeric, ('plan/link signature mismatch', named, numeric)
        return
    if named and named[0] == 'callback':
        named = [Sym('static'), named[2]]
    assert isinstance(numeric, list) and len(named) == len(numeric), (named, numeric)
    position = family_position(named)
    for index, (a, b) in enumerate(zip(named, numeric)):
        if index == position:
            assert isinstance(a, Text) and isinstance(b, str) and int(b) > 0
            number, name = int(b), a.data.decode()
            assert local.setdefault(number, name) == name, 'one local family acquired two names'
            assert len(set(local.values())) == len(local), 'distinct local families share a name'
        else:
            bind_families(a, b, local)


def rename_plan(value, local, families, unit):
    if not isinstance(value, list):
        return value
    # Invocation arguments carry holder IDs, not allocation-family IDs.
    if value and value[0] == 'arguments':
        return copy.deepcopy(value)
    def number(value):
        old = int(value)
        if old == 0:
            return Sym('0')
        # Named and private families inhabit disjoint namespaces, including
        # when a user chooses a name resembling an internal diagnostic.
        key = 'named/' + local[old] if old in local else f'private/{unit}/{old}'
        families.setdefault(key, len(families) + 1)
        return Sym(str(families[key]))
    result = [rename_plan(child, local, families, unit) for child in value]
    position = family_position(value)
    if position is not None:
        result[position] = number(value[position])
    if value and value[0] == 'ambient':
        result = [value[0]] + [[pair[0], number(pair[1])] for pair in value[1:]]
    return result


def contract(value, families, unit):
    layouts = section(value, 'records')[1:]
    records = [(' '.join(map(dump, record[:2])), dump(record)) for record in layouts]
    callbacks = section(value, 'callbacks')
    for group in callbacks[1:]:
        assert group[0] == 'callback' and len(group) == 5, 'callback provenance missing; regenerate link records'
        assert group[2][0] in ('open', 'targets'), 'unknown callback provenance'
    private = any(isinstance(part, list) and part and part[0] == 'targets' and
                  any(target[0] == 'internal' for target in part[1:])
                  for callback in callbacks[1:] for part in callback[1:])
    # Distinct unit records denote distinct internal-linkage scopes, even
    # when the same source was compiled twice with different definitions.
    key = ('unit/' + str(unit) + ':' if private else '') + dump(callbacks)
    return ('⟨' + signature(portable(section(value, 'signature'), families)) + ', ' +
            json.dumps(dump(section(value, 'type'))) + ', ' +
            json.dumps(key) + ', [' +
            ', '.join('(' + json.dumps(name) + ', ' + json.dumps(layout) + ')'
                      for name, layout in records) + ']⟩')


def qualify(records, plans, work, lean_root, accepted=True):
    """Admit body modules, then resolve the same external link assumptions."""
    assert len(records) == len(plans) and records
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    families, units = {}, []
    for index, (record_path, plan_path) in enumerate(zip(records, plans)):
        record, = parse(Path(record_path).read_bytes())
        plan, = parse(Path(plan_path).read_bytes())
        assert record[:2] == [Sym('cesta'), Sym('link-contracts/1')] and len(record) == 3
        unit = record[2]
        functions = {str(item[1]): item for item in plan[2:] if item[0] == 'function'}
        callables = {str(item[1]): item[2] for item in plan[2:] if item[0] in ('function', 'foreign')}
        local = {}
        for entry in unit[4:]:
            if entry[0] == 'define' and entry[3] != 'external':
                continue
            value = section(entry, 'contract')
            if str(entry[1]) in callables:
                bind_families(section(value, 'signature'), callables[str(entry[1])], local)
            portable(section(value, 'signature'), families)
        renamed = rename_plan(copy.deepcopy(plan), local, families, index)
        target = work / f'body{index}.lean'
        target.with_suffix('.plan').write_text(dump(renamed) + '\n')
        stats = admit_record(renamed, target, lean_root)
        prefix = 'Ownership' + hashlib.sha256(target.name.encode()).hexdigest()[:20] + 'Parts'
        units.append((unit, renamed, prefix, stats))

    imports = ['import Mettapedia.GSLT.LanguageDef.OwnershipComposition']
    imports += [f'import body{index}' for index in range(len(units))]
    declarations = imports + ['open Mettapedia.GSLT.LanguageDef.Ownership',
        'open Mettapedia.GSLT.LanguageDef.Ownership.Composition',
        'set_option maxRecDepth 200000', 'set_option maxHeartbeats 0', 'namespace ActualComposition']
    definitions, assumptions, targets = [], [], []
    for unit_index, (unit, plan, prefix, _) in enumerate(units):
        targets.append('(' + json.dumps(unit[2][1].data.decode()) + ', ' +
                       ('true' if unit[3][1] == '1' else 'false') + ')')
        functions = [item for item in plan[2:] if item[0] == 'function']
        callables = {str(item[1]): item[2] for item in plan[2:] if item[0] in ('function', 'foreign')}
        for entry in unit[4:]:
            if entry[0] == 'define' and entry[3] != 'external':
                continue
            name = str(entry[1])
            value = section(entry, 'contract')
            compiled_signature = portable(section(value, 'signature'), families)
            if name in callables:
                assert compiled_signature == callables[name], ('canonical signature mismatch', name)
            if entry[0] == 'define':
                checked = entry[2] == 'checked'
                body = 'none'
                if checked:
                    fn_index = next(i for i, fn in enumerate(functions) if fn[1] == name)
                    body = (f'(some ⟨{prefix}.wholeModule, {prefix}.wholeModule_admitted, '
                            f'{prefix}.fn{fn_index}, by simp [{prefix}.wholeModule]⟩)')
                obligation = '' if checked else section(entry, 'obligation')[1].data.decode()
                identifier = f'definition{len(definitions)}'
                definitions.append(identifier)
                declarations.append(f'def {identifier} : Definition := ⟨{json.dumps(name)}, '
                    f'{contract(value, families, unit_index)}, {str(checked).lower()}, {body}, {json.dumps(obligation)}⟩')
            else:
                assert name in callables, ('link assumption absent from body plan', name)
                identifier = f'assumption{len(assumptions)}'
                assumptions.append(identifier)
                obligation = section(entry, 'obligation')[1].data.decode()
                declarations.append(f'def {identifier} : Assumption := ⟨{json.dumps(name)}, '
                                    f'{contract(value, families, unit_index)}, {json.dumps(obligation)}⟩')
    declarations += [
        'def program : Program := ⟨[' + ', '.join(targets) + '], [' + ', '.join(definitions) +
        '], [' + ', '.join(assumptions) + ']⟩',
        'theorem verdict : (Composition.check program).isSome = ' + str(accepted).lower() + ' := by decide +kernel',
        'def isClosed : Resolution → Bool | .closed _ => true | _ => false',
        'def closedCount (p : Program) : Option Nat := '
        '(Composition.check p).map fun results => (results.filter isClosed).length',
    ]
    external = [entry for unit, _, _, _ in units for entry in unit[4:]
                if entry[0] == 'define' and entry[3] == 'external']
    expected_closed = 0
    for unit, _, _, _ in units:
        for assume in unit[4:]:
            if assume[0] == 'assume':
                selected = next((entry for entry in external if entry[1] == assume[1]), None)
                expected_closed += selected is not None and selected[2] == 'checked'
    outcome = f'some {expected_closed}' if accepted else 'none'
    declarations.append(f'theorem count : closedCount program = {outcome} := by decide +kernel')
    # A checked label is insufficient. Use the real contracts but withhold the
    # admitted body, rather than merely checking a separate toy predicate.
    for i, definition in enumerate(definitions):
        declarations.append(f'example : certified {{ {definition} with body := none }} = false := by rfl')
    if accepted and expected_closed:
        declarations.append('example : (Composition.check { program with definitions := '
            'program.definitions.map (fun d => { d with body := none }) }).isNone = true := by decide +kernel')
    declarations += ['#print axioms verdict', '#print axioms count', 'end ActualComposition']
    source = work / 'Composition.lean'
    source.write_text('\n'.join(declarations) + '\n')
    environment = dict(os.environ)
    environment['LEAN_PATH'] = str(work) + os.pathsep + environment.get('LEAN_PATH', '')
    command = ['lake', 'env', 'lean', '-DwarningAsError=true', '--root=' + str(work), source]
    result = subprocess.run(list(map(str, command)), cwd=lean_root, text=True, capture_output=True, env=environment)
    source.with_suffix('.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, (command, result.returncode, result.stdout, result.stderr)
    summary = dict(units=len(units), accepted=accepted, closed=expected_closed if accepted else None, assumptions=len(assumptions),
        functions=sum(stats['functions'] for _, _, _, stats in units),
        altered_plans_refused=sum(stats['mutants'] for _, _, _, stats in units), families=families,
        records=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in records],
        plans=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in plans])
    (work / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
