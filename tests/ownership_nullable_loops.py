"""Non-null facts must be loop invariants; break exits retain their changes."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ('for_null_backedge', False, 'int f(unsigned n){const char *p="x";int sum=0;for(unsigned i=0;i<n;++i){sum+=*p;p=0;}return sum;}', ''),
    ('continue_null_backedge', False, 'int f(unsigned n){const char *p="x";int sum=0;for(unsigned i=0;i<n;++i){sum+=*p;p=0;continue;}return sum;}', ''),
    ('continue_checks_for_step', False, 'int f(unsigned n){const char *p="x";int sum=0;for(unsigned i=0;i<n;p=0){sum+=*p;++i;continue;}return sum;}', ''),
    ('field_null_backedge', False, 'int f(unsigned n){Label l={"x"};int sum=0;for(unsigned i=0;i<n;++i){sum+=*l.text;l.text=0;}return sum;}', ''),
    ('null_after_break', False, 'int f(unsigned n){const char *p="x";for(unsigned i=0;i<n;++i){p=0;break;}return *p;}', ''),
    ('break_cannot_weaken_invariant', False, 'int f(unsigned n){const char *p="x";int sum=0;for(unsigned i=0;i<2;++i){if(n==0){p=0;break;}sum+=*p;p=0;continue;}return sum;}', ''),
    ('nested_break_exit', False, 'int f(unsigned n){const char *p="x";for(unsigned i=0;i<n;++i){for(unsigned j=0;j<n;++j){p=0;break;}return *p;}return 0;}', ''),
    ('condition_false_edge', False, 'int clear(const char **out){*out=0;return 0;}int f(unsigned n){(void)n;const char *p="x";while(clear(&p)){p="x";}return *p;}', ''),
    ('guarded_while', True, 'int f(unsigned n){(void)n;const char *p="x";int sum=0;while(p){sum+=*p;p=0;}return sum;}', 'return f(0)!=120;'),
    ('guarded_for', True, 'int f(unsigned n){const char *p="x";int sum=0;for(unsigned i=0;i<n && p;++i){sum+=*p;p=0;}return sum;}', 'return f(2)!=120 || f(0)!=0;'),
    ('nullable_entry_field', True, 'int f(unsigned n){Label l={0};int sum=0;for(unsigned i=0;i<n;++i){if(l.text)sum+=*l.text;l.text="x";}return sum;}', 'return f(2)!=120 || f(0)!=0;'),
    ('guard_after_break', True, 'int f(unsigned n){const char *p="x";for(unsigned i=0;i<n;++i){p=0;break;}if(p)return *p;return 0;}', 'return f(1)!=0 || f(0)!=120;'),
    ('separate_break_path', True, 'int f(unsigned n){const char *p="x";int sum=0;for(unsigned i=0;i<2;++i){if(n==0){p=0;break;}sum+=*p;}return sum;}', 'return f(0)!=0 || f(1)!=240;'),
    ('break_skips_for_step', True, 'int f(unsigned n){const char *p="x";for(unsigned i=0;i<n;p=0){break;}return *p;}', 'return f(2)!=120 || f(0)!=120;'),
    ('guard_false_edge', True, 'int clear(const char **out){*out=0;return 0;}int f(unsigned n){(void)n;const char *p="x";while(clear(&p)){p="x";}if(p)return *p;return 0;}', 'return f(2)!=0;'),
]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--cesta', type=Path, default=ROOT / 'build/cesta')
    ap.add_argument('--output', type=Path, default=ROOT / 'build/ownership-nullable-loops')
    ap.add_argument('--lean-root', type=Path); ap.add_argument('--admit', action='store_true'); args = ap.parse_args()
    if args.admit and args.lean_root is None: args.lean_root = ROOT.parent / 'Mettapedia/lean/mettapedia'
    w = args.output.resolve(); w.mkdir(parents=True, exist_ok=True); records = []; plans = {}
    prefix = 'typedef struct Label {const char *text;} Label;\n'
    for name, accepted, body, harness in CASES:
        contract = '(function clear (stores 0 static))' if 'int clear(' in body else ''
        authored = '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) (record Label (field text (static nullable))) ' + contract + ') (native "' + (prefix + body).replace('"', '\\"') + '")))\n'
        source = w / (name + '.cesta'); source.write_text(authored)
        for cc in ('gcc', 'clang'):
            c = w / (name + '-' + cc + '.c')
            plan = c.with_suffix('.plan')
            result = subprocess.run([str(args.cesta.resolve()), 'export-c', str(source), '--std=c11', '--cc', cc, '-o', str(c), '--plan', str(plan)], capture_output=True)
            assert (result.returncode == 0) == accepted, (name, cc, result.stderr.decode()[-4000:])
            if not accepted:
                assert b'ownership' in result.stderr and (b'non-null' in result.stderr or b'nullable' in result.stderr), (name, result.stderr)
                continue
            plans[hashlib.sha256(plan.read_bytes()).hexdigest()] = plan
            unit = w / (name + '-' + cc + '-run.c'); unit.write_text(c.read_text() + '\nint main(void){' + harness + '}\n')
            for mode, flags in [('O0', ['-O0']), ('O2', ['-O2']), ('SAN', ['-O1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie'])]:
                binary = w / (name + '-' + cc + '-' + mode)
                subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Wpedantic', '-Werror', *flags, str(unit), '-o', str(binary)], check=True)
                observed = subprocess.run([str(binary)], capture_output=True, env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:abort_on_error=1'})
                assert observed.returncode == 0, (name, cc, mode, observed.stderr.decode()[-3000:])
                records.append(dict(case=name, compiler=cc, mode=mode))
    admission = []
    if args.lean_root:
        native = importlib.import_module('ownership_admission')
        for i, plan in enumerate(plans.values()):
            record, = native.parse(plan.read_bytes())
            admission.append(native.admit_record(record, w / f'Loop{i}.lean', args.lean_root.resolve()))
    (w / 'summary.json').write_text(json.dumps(dict(accepted=sum(c[1] for c in CASES), refused=sum(not c[1] for c in CASES), runtime=records, admission=admission), indent=2) + '\n')
    print('Loop nullability:', sum(c[1] for c in CASES), 'positive;', sum(not c[1] for c in CASES), 'refusals; GCC/Clang/SAN')


if __name__ == '__main__': main()
