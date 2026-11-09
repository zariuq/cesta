"""Optional span boundary evidence and independent permission-plan admission."""

import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile

import checked_spans as spans
import checked_spans_admission as admission
import checked_admission as legacy
from oracles.pro_compact_cesta import parse

MODEL = 'Mettapedia.GSLT.LanguageDef.CheckedSpanPlacement'


def contract(source):
    return json.loads(spans.run([spans.BINARY, 'checked', 'contract', source]).stdout)


def lean_contract(row):
    parameters = []
    for i, parameter in enumerate(row['params']):
        slot = parameter['type']
        if 'element' in slot:
            parameters.append(f"({i}, {admission.element(slot['element'])}, {legacy.kind(slot['mode'])}, {slot['element_bytes']})")
    separate = [f'({i}, {j})' for i, j in row['separate']]
    origin = 'none' if row['borrowed_from'] == -1 else f"some {row['borrowed_from']}"
    return f'⟨{legacy.list_of(parameters)}, {legacy.list_of(separate)}, {origin}⟩'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lean', action='store_true')
    parser.add_argument('--lean-memory-mb', type=int)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix='span-contract-', dir=spans.ROOT/'build'))
    source = work/'spans.cesta'
    source.write_text(spans.document(spans.functions()))
    lines = ['import '+MODEL, 'set_option autoImplicit false', 'set_option Elab.async false',
             'set_option maxRecDepth 32768', 'set_option maxHeartbeats 16000000',
             'open Mettapedia.GSLT.LanguageDef.CheckedScalarSpans', 'open '+MODEL]
    sources = [source, spans.ROOT/'checked/examples/CopySpan.cesta',
               spans.ROOT/'checked/examples/ByteViews.cesta', spans.ROOT/'checked/examples/Owners.cesta']
    pair = work/'pairs.cesta'
    pair.write_text(spans.document('''
      (fn pairs (params (a (span u32 read)) (b (span u32 read)) (c usize)
        (d (span u32 write)) (e (span u32 own))) (returns unit) (effects heap)
        (body (require (le (len a) (len d))) (var local usize c)
          (require (le local (len d))) (return)))'''))
    sources.append(pair)
    projected = work/'projected.cesta'
    projected.write_text(spans.document('''
      (foreign external_call external_copy
        (params (a (span u32 read) data) (b (span u32 write) data_len))
        (returns unit) (effects) (obligation "Reads a and writes b within their extents; mémoire λ; retains no pointer."))
      (fn relay (params (a (span u32 read)) (b (span u32 write))) (returns unit) (effects)
        (body (call external_call a b) (return)))'''))
    sources.append(projected)
    assert contract(projected)['foreign'][0]['obligation'].encode('latin1') == \
        'Reads a and writes b within their extents; mémoire λ; retains no pointer.'.encode('utf8')
    positive = negative = 0
    for module_index, source in enumerate(sources):
        data = contract(source)
        record, = parse(spans.run([spans.BINARY,'checked','plan',source]).stdout)
        lines += [f'def actual{module_index} : Module := {admission.module(record)}',
                  f'example : admit actual{module_index} = true := by decide']
        if source.name == 'CopySpan.cesta':
            lines += [f'''def copyEntry : Function := actual{module_index}.functions[0]'(by decide)
def copyEnvironment : List Signature := actual{module_index}.foreign ++
  actual{module_index}.functions.map Function.signature
theorem actual_copy_live_prefix : Mettapedia.Machines.CMemory.PermissionFlow.Prefix
    (transition copyEnvironment copyEntry.signature) copyEntry.body
    (initial copyEntry.signature) (initial copyEntry.signature) := by
  exact .step (after := initial copyEntry.signature) (by decide) (.start _ _)
example : Apart 8192 12 4100 12 := by
  exact admitted_prefix_writer_apart (f := copyEntry) (environment := copyEnvironment) (reading := false)
    (writer := {{Controls.view with binding := Controls.destination, offset := 0}})
    (reader := Controls.view) (limit := 65536)
    (table := [Controls.allocation, {{Controls.allocation with root := 1, base := 8192}}])
    (wordBytes := 8) (countByteLimit := 1024) (x := ⟨8192, 3, 4⟩) (y := ⟨4100, 3, 4⟩)
    (by decide) actual_copy_live_prefix (by decide) (by decide) (by decide)
    (by decide) (by decide) (by decide)''']
        assert data['format'] == 'cesta-span-contract/1'
        assert data['module'] == str(record[2][1])
        declarations = [d for d in record[4:] if d[0] == 'function']
        assert len(declarations) == len(data['functions'])
        for declaration, row in zip(declarations,data['functions']):
            assert row['name'] == str(declaration[1])
            assert row['symbol'] == data['module']+'_'+row['name']
            expected_pairs = []
            for i, typ in enumerate(declaration[2][1:]):
                element, mode = admission.element_type(typ)
                actual = row['params'][i]['type']
                if element:
                    width = {'bool':1,'u8':1,'u16':2,'u32':4,'u64':8,'usize':data['word_bits']//8}[element]
                    assert actual == dict(element=element,mode=mode,element_bytes=width,
                        alignment='c-alignof',count_limit='PTRDIFF_MAX/element_bytes')
                    for j, other in enumerate(declaration[2][1:]):
                        e, m = admission.element_type(other)
                        if i < j and e and (mode != 'read' or m != 'read'): expected_pairs.append([i,j])
                else: assert actual == {'scalar': mode}
            assert row['separate'] == expected_pairs
            assert row['borrowed_from'] == int(declaration[3][2])
            sig = admission.signature(declaration)
            expression = f'entryContract {data["word_bits"]//8} ({sig})'
            lines.append(f'example : {expression} = {lean_contract(row)} := by decide')
            positive += 1
            for change in ('width','mode','pair','lifetime'):
                mutant = copy.deepcopy(row)
                typed = [p['type'] for p in mutant['params'] if 'element' in p['type']]
                if change in ('width','mode') and not typed: continue
                if change == 'width': typed[0]['element_bytes'] += 1
                if change == 'mode': typed[0]['mode'] = 'write' if typed[0]['mode'] == 'read' else 'read'
                if change == 'pair': mutant['separate'].append([0,0])
                if change == 'lifetime': mutant['borrowed_from'] = 0 if row['borrowed_from'] != 0 else -1
                lines.append(f'example : {expression} ≠ {lean_contract(mutant)} := by decide')
                negative += 1
        # Moving the source does not change interface evidence.
        relocated = work/('relocated-'+source.name)
        relocated.write_bytes(source.read_bytes())
        assert contract(relocated) == data
    assert contract(pair)['functions'][0]['entry_requires'] == [['le',['len','a'],['len','d']]]
    thirtytwo = work/'word32.cesta'
    thirtytwo.write_text(spans.document('(fn f (params (a (span usize read))) (returns unit) (effects) (body (return)))').replace('(word-bits 64)','(word-bits 32)'))
    assert contract(thirtytwo)['functions'][0]['params'][0]['type']['element_bytes'] == 4
    library = work/'copy_library.cesta'
    library.write_bytes((spans.ROOT/'checked/examples/CopySpan.cesta').read_bytes())
    importer = work/'importer.cesta'
    importer.write_text(spans.document('''
      (fn relay (params (a (span u32 read)) (b (span u32 write))) (returns unit) (effects)
        (body (call Copy_copy_u32 a b) (return)))''',
        imports='(import "copy_library.cesta" as Copy)'))
    imported = contract(importer)
    assert [f['name'] for f in imported['functions']] == ['relay']
    assert imported['functions'][0]['separate'] == [[0,1]]
    assert imported['functions'][0]['entry_requires'] == []
    bad = work/'dangling.cesta'
    bad.write_text(spans.document('''(fn escape (params (a read)) (returns read from a) (effects)
      (body (array local 4) (borrow b read local) (return b)))'''))
    output = work/'refused.json'
    spans.run([spans.BINARY,'checked','contract',bad,'-o',output],accepted=False)
    assert not output.exists()
    if args.lean:
        for name in ('resolved_machine_bounds','usable_writer_apart','admitted_prefix_writer_apart',
                     'stale_incarnation_refused','element_access_within'):
            lines.append('#print axioms '+name)
        proof=work/'Contract.lean';proof.write_text('\n\n'.join(lines)+'\n')
        command=['lake','env','lean','-DwarningAsError=true']
        if args.lean_memory_mb is not None: command += ['-M',str(args.lean_memory_mb)]
        result=subprocess.run([*command,proof],cwd=spans.ROOT.parent/'Mettapedia/lean/mettapedia',capture_output=True)
        (work/'lean.log').write_bytes(result.stdout+result.stderr)
        assert result.returncode == 0,(work,result.stdout.decode(),result.stderr.decode())
        assert b'sorryAx' not in result.stdout
    print(f'span contracts: {positive} emitted interfaces; '+
          (f'{negative} mutations independently refused by Lean' if args.lean else 'Lean admission not requested'))


if __name__ == '__main__':main()
