"""Exercise ownership over the actual preprocessed C tree and both build paths."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def document(source, contracts=""):
    return '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) ' + contracts + ') (native ' + json.dumps(source) + ')))\n'


POSITIVE = {
    "fields": ("typedef struct { int x, y; } Pair; static void inc(int *p) { ++*p; } int main(void) { Pair p={1,2}; int *x=&p.x; inc(&p.y); *x+=3; return p.y==3 ? 0:1; }", ""),
    "readers": ("int f(void) { int n=4; const int *a=&n; const int *b=a; return *a+*b; }", ""),
    "scoped_reborrow": ("int f(void) { int n=4; int *a=&n; { int *b=a; ++*b; } ++*a; return *a; }", ""),
    "returned_field": ("typedef struct {int x,y;} P; const int *field(const P *p) { return &p->y; } int f(void) { P p={1,2}; const int *v=field(&p); p.x=4; return *v; }", "(function field (returns-borrow 0 y))"),
    "nullable_guard": ("const int *find(const int *p, int flag) { if(flag) return p; return 0; } int f(void) { int n=3; const int *p=find(&n,1); return p ? *p : 0; }", "(function find (returns-borrow 0 nullable))"),
    "static_output": ("void text(const char **out) { *out=\"ok\"; } const char *f(void) { const char *p=0; text(&p); return p; }", "(function text (stores 0 static)) (function f (returns-static nullable))"),
    "loop_loans": ("int f(void) { int n=0; for(int i=0;i<4;++i) { int *p=&n; ++*p; } return n; }", ""),
    "record_copy": ("typedef struct {int a[3]; int n;} P; P f(P p) { P q=p; q.n=3; return q; }", ""),
    "borrowed_output": ("void out(const int *p,const int **q) { *q=p; } int f(void) { int n=3; const int *p=0; out(&n,&p); return *p; }", "(function out (stores 1 0))"),
    "short_circuit": ("const int *find(const int *p) { return p; } int f(void) { int n=3; const int *p=find(&n); return p && *p>0; }", "(function find (returns-borrow 0 nullable))"),
    "nested_records": ("typedef struct {int a,b;} I; typedef struct {I i; int n;} O; int f(void) { O o={{1,2},3}; int *p=&o.i.a; o.i.b=8; o.n=9; return *p; }", ""),
    "owned_global": ("static int n; void inc(int *p) { ++*p; } int f(void) { inc(&n); return n; }", "(global n f)"),
    "constant_global": ("static const int answer=42; int f(void) { return answer; }", ""),
    "native_preserves_pointer": ("extern void touch(int **out); int f(int *p) { touch(&p); return *p; }", '(foreign touch (preserves 0) (obligation "Preserves the stored pointer and its lifetime."))'),
}

NEGATIVE = {
    "overlapping_mutable": ("int f(void) { int n=3; int *p=&n; int *q=&n; return *p+*q; }", "", "overlapping loan"),
    "write_under_shared": ("int f(void) { int n=3; const int *p=&n; n=4; return *p; }", "", "overlapping loan"),
    "read_under_exclusive": ("int f(void) { int n=3; int *p=&n; return n+*p; }", "", "overlapping loan"),
    "aliased_call": ("void use(int *p,int *q) { ++*p; ++*q; } int f(void) { int n=3; use(&n,&n); return n; }", "", "overlapping loan"),
    "return_local": ("const int *f(const int *unused) { int n=4; return &n; }", "(function f (returns-borrow 0))", "escapes"),
    "return_wrong_input": ("const int *f(const int *a,const int *b) { return b; }", "(function f (returns-borrow 0))", "escapes"),
    "escaped_scope": ("int f(void) { const int *p=0; { int n=3; p=&n; } return *p; }", "", "outlives"),
    "output_local": ("void f(const int **p) { int n=3; *p=&n; }", "(function f (stores 0 static))", "non-static"),
    "uninitialized": ("int f(void) { int n; return n; }", "", "uninitialized"),
    "nullable_access": ("const int *find(const int *p) { return p; } int f(void) { int n=3; const int *p=find(&n); return *p; }", "(function find (returns-borrow 0 nullable))", "non-null"),
    "unknown_native": ("extern void use(int *p); void f(int *p) { use(p); }", "", "foreign obligation"),
    "manufactured_pointer": ("int f(unsigned long value) { int *p=(int *)value; return *p; }", "", "manufacture ownership"),
    "global_without_owner": ("static int n; int f(void) { return ++n; }", "", "declared owner"),
    "wrong_return_field": ("typedef struct {int x,y;} P; const int *f(const P *p) { return &p->x; }", "(function f (returns-borrow 0 y))", "escapes"),
    "forgotten_contract": ("int f(void) { return 0; }", "(function absent)", "no C declaration"),
    "partial_output": ("void f(int yes,const char **out) { if(yes) *out=\"yes\"; }", "(function f (stores 1 static))", "every fall-through"),
    "wrong_global_owner": ("static int n; int owner(void) { return n; } int f(void) { return ++n; }", "(global n owner)", "declared owner"),
    "global_reentry_under_borrow": ("static int n; int f(void) { int *p=&n; int x=f(); return *p+x; }", "(global n f)", "overlapping loan"),
    "returned_mutated_input": ("const int *f(const int *p) { int local=3; p=&local; return p; }", "(function f (returns-borrow 0))", "escapes"),
    "dynamic_pointer_index": ("void f(int *p,unsigned index) { p[index]=3; }", "", "bounded view"),
    "volatile_device": ("int f(volatile int *device) { return *device; }", "", "native resource contract"),
    "vla_sizeof_effect": ("extern int native_effect(int *p); unsigned long f(int *p) { return sizeof(int[native_effect(p)]); }", "", "runtime type-layout"),
    "uncontracted_pointer_output": ("extern void touch(int **out); int f(int *p) { touch(&p); return *p; }", '(foreign touch (obligation "A native operation."))', "stores or preserves"),
    "false_preserves": ("void touch(int **out) { *out=0; }", '(function touch (preserves 0))', "lifetime destination"),
    "line_marker_bypass": ('int f(void) { return 0; }\n#line 1 "other.c"\nint hidden(void) { int n=3; int *p=&n; n=4; return *p; }', "", "overlapping loan"),
    "unadmitted_packing": ("#pragma pack(push,1)\ntypedef struct {char x; int y;} P;\n#pragma pack(pop)\nint f(P *p) { return p->y; }", "", "pragma"),
    "conflicting_result": ('const int *f(const int *p) { return p; }', '(function f (returns-borrow 0) (returns-static))', "conflicting"),
    "nonpointer_result": ('int f(void) { return 3; }', '(function f (returns-static))', "non-pointer"),
    "conflicting_store": ('void f(const char **p) { *p="a"; }', '(function f (stores 0 static) (preserves 0))', "conflicting"),
}

HEAP_C = "extern void *reserve(unsigned long n); extern void dispose(void *p); "
HEAP_CONTRACT = '(allocator heap reserve dispose (obligation "Fresh allocation of the requested extent; dispose releases it once and accepts null."))'
POSITIVE.update({
    "heap_move": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; int *q=p; int value=*q; dispose(q); return value; }", HEAP_CONTRACT),
    "heap_borrow": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; int result=0; { const int *r=p; result=*r; } dispose(p); return result; }", HEAP_CONTRACT),
    "owned_argument": (HEAP_C + "void consume(int *p) { dispose(p); } int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } consume(p); return 1; }", HEAP_CONTRACT + " (function consume (owns 0 heap))"),
    "owned_result": (HEAP_C + "int *make(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; return p; } int f(void) { int *p=make(); if(!p) { dispose(p); return 0; } int value=*p; dispose(p); return value; }", HEAP_CONTRACT + " (function make (returns-own heap nullable))"),
    "direct_owned_result": (HEAP_C + "int *make(void) { return (int *)reserve(sizeof(int)); }", HEAP_CONTRACT + " (function make (returns-own heap nullable))"),
    "direct_release": (HEAP_C + "void f(void) { dispose(reserve(sizeof(int))); }", HEAP_CONTRACT),
})
NEGATIVE.update({
    "moved_owner": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } int *q=p; int value=*p; dispose(q); return value; }", HEAP_CONTRACT, "moved value"),
    "double_release": (HEAP_C + "void f(void) { int *p=(int *)reserve(sizeof(int)); dispose(p); dispose(p); }", HEAP_CONTRACT, "moved value"),
    "release_under_borrow": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } const int *q=p; dispose(p); return *q; }", HEAP_CONTRACT, "outlives"),
    "move_under_borrow": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } const int *q=p; int *r=p; int value=*q; dispose(r); return value; }", HEAP_CONTRACT, "while it is borrowed"),
    "lost_owner": (HEAP_C + "void f(void) { int *p=(int *)reserve(sizeof(int)); }", HEAP_CONTRACT, "without release"),
    "stack_is_not_heap": (HEAP_C + "void f(void) { int n=3; dispose(&n); }", HEAP_CONTRACT, "matching allocator family"),
    "wrong_allocator": (HEAP_C + "extern void *other(unsigned long); extern void other_free(void *); void f(void) { int *p=(int *)reserve(sizeof(int)); other_free(p); }", HEAP_CONTRACT + ' (allocator other_heap other other_free (obligation "A different allocation family."))', "matching allocator family"),
    "discarded_owner": (HEAP_C + "void f(void) { reserve(sizeof(int)); }", HEAP_CONTRACT, "discarded owned result"),
    "cast_discarded_owner": (HEAP_C + "void f(void) { (void)reserve(sizeof(int)); }", HEAP_CONTRACT, "discarded owned result"),
})


def run(args):
    return subprocess.run(list(map(str,args)),cwd=ROOT,capture_output=True,text=True)


def main():
    global BINARY
    parser = argparse.ArgumentParser()
    parser.add_argument("--cesta",type=Path,default=BINARY)
    parser.add_argument("--output",type=Path,default=ROOT/"build/ownership-core.json")
    parser.add_argument("--quick",action="store_true")
    parser.add_argument("--case",action="append",default=[])
    args = parser.parse_args()
    BINARY = args.cesta
    assert set(args.case) <= POSITIVE.keys() | NEGATIVE.keys(),args.case
    count = 0
    tested = {True:set(),False:set()}
    with tempfile.TemporaryDirectory(prefix="ownership-",dir=ROOT/"build") as temporary:
        directory = Path(temporary)
        for compiler in (("gcc",) if args.quick else ("gcc","clang")):
            for accepted, cases in ((True,POSITIVE),(False,NEGATIVE)):
                for name, case in cases.items():
                    if args.case and name not in args.case:
                        continue
                    if args.quick and name not in {"fields","returned_field","heap_move","overlapping_mutable","return_local","wrong_allocator"}:
                        continue
                    tested[accepted].add(name)
                    source, contracts, *needle = case
                    raw = directory/(name+".c"); raw.write_text(source)
                    baseline = run([compiler,"-std=c11","-fsyntax-only",raw])
                    assert baseline.returncode == 0,(name,baseline.stderr)
                    authored = directory/(name+".cesta"); authored.write_text(document(source,contracts))
                    exported = directory/(name+"-export.c")
                    commands = [
                        [BINARY,"export-c",authored,"--std=c11","--cc",compiler,"-o",exported],
                        [BINARY,"cc","--cc",compiler,"-std=c11","-fsyntax-only",authored],
                    ]
                    for command in commands:
                        result = run(command)
                        assert (result.returncode == 0) == accepted,(name,command,result.stderr)
                        if not accepted:
                            assert "ownership" in result.stderr and needle[0] in result.stderr,(name,result.stderr)
                        count += 1
                    if accepted:
                        native = run([compiler,"-std=c11","-fsyntax-only",exported])
                        assert native.returncode == 0,(name,native.stderr)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    compilers = ["gcc"] if args.quick else ["gcc","clang"]
    args.output.write_text(json.dumps({"checks":count,"positive":len(tested[True]),"negative":len(tested[False]),
        "compilers":compilers,"paths":["compile","export"]},indent=2)+"\n")
    print(f"ownership: {count} compiler/export checks; {len(tested[True])} accepted and {len(tested[False])} refused programs under {'/'.join(compilers)}")


if __name__ == "__main__":
    main()
