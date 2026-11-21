"""Exercise ownership over the actual preprocessed C tree and both build paths."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"


def document(source, contracts=""):
    return '(cesta native-c/1 compact-c/2 (unit (ownership (check-all) ' + contracts + ') (native ' + json.dumps(source) + ')))\n'


POSITIVE = {
    "switch_total_returns": ("int f(int n){switch(n){case 0:return 7;default:return 9;}}", ""),
    "switch_total_grouped_default": ("int f(int n){switch(n){case 0:return 7;default:case 1:case 2:return 9;}}", ""),
    "switch_total_nested": ("int f(int n,int m){switch(n){case 0:switch(m){case 1:return 3;default:return 4;}default:return 9;}}", ""),
    "switch_total_default_only": ("int f(int n){switch(n){default:return 9;}}", ""),
    "switch_total_break_continues": ("int f(int n){switch(n){case 0:break;default:return 9;}return 7;}", ""),

    "const_array_parameter": ("typedef int Row[3]; int f(const Row *p) {return (*p)[0]+(*p)[2];}", ""),
    "const_matrix_parameter": ("typedef int Matrix[2][3]; int f(const Matrix *p) {return (*p)[0][1]+(*p)[1][2];}", ""),
    "const_array_record_call": ("typedef int Row[3]; typedef struct {Row row; int other;} Box; int read_row(const Row *p) {return (*p)[1];} int f(void) {Box box={{1,2,3},4}; int result; {const Box *view=&box;result=read_row(&view->row);}box.other=9;return result+box.other;}", ""),
    "const_array_static_reads": ("typedef int Row[3]; static const Row data={1,2,3}; int read_row(const Row *p) {return (*p)[1];} int f(void) {return read_row(&data)+read_row(&data);}", ""),
    "switch_grouped_returns": ("int f(int n) {switch(n){case 0:case 1:return 7;default:return 9;}return 0;}", ""),
    "switch_grouped_breaks": ("int f(int n) {int x=0;switch(n){case 0:case 1:x=7;break;default:x=9;}return x;}", ""),
    "switch_default_group": ("int f(int n) {int x=0;switch(n){default:case 1:case 2:x=7;break;}return x;}", ""),
    "switch_grouped_loop": ("int f(int n) {int x=0;switch(n){case 0:case 1:for(int i=0;i<3;++i){if(i==2)break;++x;}break;default:x=9;}return x;}", ""),
    "switch_disjoint_loan": ("typedef struct {int x,y;} P; int f(int n) {P p={1,2};const int *held=&p.x;switch(n){case 0:case 1:++p.y;break;default:p.y=9;}return *held+p.y;}", ""),
    "value_record_projection": ("typedef struct {int value;} R; R make(int *count) { ++*count; return (R){7}; } int f(void) { int count=0; int result=make(&count).value; return count==1?result:99; }", ""),
    "nested_value_projection": ("typedef struct {int value;} I; typedef struct {I inner;} O; O make(int *count) { ++*count; return (O){{7}}; } int f(void) { int count=0; int result=make(&count).inner.value; return count==1?result:99; }", ""),
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
    "typed_foreign_opaque_record": ("typedef struct NativeStream {int *private_state;} NativeStream; extern int native_step(NativeStream *); int f(NativeStream *stream) { return native_step(stream); }", '(foreign native_step (obligation "Advances the typed native stream without exposing or retaining its private representation."))'),
    "checked_opaque_forwarding": ("typedef struct NativeStream {int *private_state;} NativeStream; extern int native_step(NativeStream *); int relay(NativeStream *stream,int depth) { return depth ? relay(stream,depth-1) : native_step(stream); }", '(foreign native_step (opaque 0) (obligation "Advances the typed native stream without exposing or retaining its private representation.")) (function relay (opaque 0))'),
    "shallow_record_forwarding": (
        "typedef struct { int *state; } Handle; extern int native_step(Handle *); "
        "static int relay(Handle *handle) { return native_step(handle); }",
        "(record Handle (field state (borrow write))) "
        "(foreign native_step (shallow 0) (obligation \"Uses only the handle storage and retains no pointer.\")) "
        "(function relay (shallow 0))"),
    "owning_field_update_then_scalar": (
        "#include <stdlib.h>\ntypedef struct { int *data; int flag; } Box; "
        "static void update(Box *b) { if (b->data) { b->flag=1; return; } "
        "b->data=malloc(sizeof *b->data); b->flag=1; } "
        "static int use(void) { Box b={0}; update(&b); int flag=b.flag; free(b.data); return flag; }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record Box (field data (owns heap nullable))) (function update (updates-field 0 data))"),
    "updates_nested_pointee": (
        "#include <stdlib.h>\ntypedef struct { int *data; } Inner; "
        "typedef struct { Inner *inner; } Outer; "
        "static void update(void *context) { Outer *o=context; "
        "if (o->inner->data) return; o->inner->data=malloc(sizeof *o->inner->data); } "
        "static int use(void) { Inner i={0}; Outer o={&i}; update(&o); free(o.inner->data); return 0; }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record Inner (field data (owns heap nullable))) "
        "(record Outer (field inner (borrow write) (pointee))) "
        "(function update (context 0 Outer) (updates-pointee 0 inner))"),
    "preserves_nested_pointee": (
        "typedef struct { const int *view; } Inner; typedef struct { Inner *inner; } Outer; "
        "static int inspect(void *context) { Outer *o=context; return o->inner->view ? *o->inner->view : 0; } "
        "static int use(void) { int n=7; Inner i={&n}; Outer o={&i}; return inspect(&o); }",
        "(record Inner (field view (borrow read nullable))) "
        "(record Outer (field inner (borrow write) (pointee))) "
        "(function inspect (context 0 Outer) (pointee 0 inner) "
        "(preserves-field 0 (inner view)))"),
    "stores_null_field": (
        "typedef struct { const int *view; } Holder; "
        "static void clear(Holder *h) { h->view=0; } "
        "static int use(void) { int n=3; Holder h={&n}; clear(&h); return h.view != 0; }",
        "(record Holder (field view (borrow read nullable))) "
        "(function clear (stores-field 0 view null))"),
    "framed_parameter_before_field_post": (
        "typedef struct { const int *view; } Holder; typedef struct { int a,b,c; } Extra; "
        "static int publish(Holder *h, const int *source, const Extra *extra) { "
        "h->view=source; return extra->a+extra->c; } "
        "static int use(void) { int n=7; Holder h={0}; Extra x={1,2,3}; "
        "int sum=publish(&h,&n,&x); return sum+(h.view ? *h.view : 0); }",
        "(record Holder (field view (borrow read nullable))) "
        "(function publish (stores-field 0 view 1))"),
    "updates_whole_record": (
        "#include <stdlib.h>\ntypedef struct { int *a; int *b; } PairOwners; "
        "static void renew(PairOwners *p) { free(p->a); p->a=malloc(sizeof *p->a); "
        "free(p->b); p->b=malloc(sizeof *p->b); } "
        "static int use(void) { PairOwners p={0}; renew(&p); free(p.a); free(p.b); return 0; }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record PairOwners (field a (owns heap nullable)) (field b (owns heap nullable))) "
        "(function renew (updates-record 0))"),
    "opaque_callback_context": ("typedef struct {void *context;int (*step)(void *);} NativeOps; int run(const NativeOps *ops) {return ops->step(ops->context);}", '(callback NativeStep (field NativeOps step) (opaque 0)) (record NativeOps (field context (opaque)) (field step (callback NativeStep)))'),
    "opaque_cursor_callbacks": (
        "typedef struct { void *store; int (*open)(void *, void **); "
        "int (*next)(void *); void (*close)(void *); } CursorOps;\n"
        "static int run(const CursorOps *ops) {\n"
        "  void *cursor=0;\n"
        "  if (!ops->open(ops->store,&cursor)) return 0;\n"
        "  int value=ops->next(cursor);\n"
        "  ops->close(cursor);\n"
        "  return value;\n}",
        "(callback CursorOpen (field CursorOps open) (opaque 0) (stores 1 0)) "
        "(callback CursorNext (field CursorOps next) (opaque 0)) "
        "(callback CursorClose (field CursorOps close) (opaque 0)) "
        "(record CursorOps (field store (opaque)) (field open (callback CursorOpen)) "
        "(field next (callback CursorNext)) (field close (callback CursorClose)))"),
    "variadic_whole_buffer": ("typedef struct {const int *data;unsigned n;} Ints; extern void inspect(int tag,...); void f(const Ints *v) {if(!v->data)return;inspect(0,v->data);}", '(record Ints (field data (borrow read nullable) (count n))) (foreign inspect (variadic-read) (obligation "Reads each typed pointer argument without retaining it."))'),
    "nullable_whole_buffer": (
        "#include <stddef.h>\nstatic int first(const int *p,size_t n) { "
        "if (!p) return n ? 99 : 0; return p[0]; } "
        "int f(void) { int a[1]={7}; return first(0,0)+first(a,1); }",
        "(function first (buffer 0) (nullable 0))"),
    "nullable_counted_field_call": (
        "#include <stddef.h>\ntypedef struct { const int *data; size_t n; } Window; "
        "static int total(const Window *w) { int sum=0; "
        "for (size_t i=0;i<w->n;++i) sum+=w->data[i]; return sum; } "
        "int f(const Window *w) { return total(w); }",
        "(record Window (field data (borrow read nullable) (count n)))"),
}

NEGATIVE = {
    "switch_missing_default": ("int f(int n){switch(n){case 0:return 7;case 1:return 9;}}", "", "end without a result"),
    "switch_break_without_return": ("int f(int n){switch(n){case 0:break;default:return 9;}}", "", "end without a result"),
    "switch_nested_break_without_return": ("int f(int n,int m){switch(n){case 0:switch(m){case 1:break;default:return 4;}break;default:return 9;}}", "", "end without a result"),

    "const_array_store": ("typedef int Row[3]; void f(const Row *p) {Row *w=(Row *)p;(*w)[0]=7;}", "", "ownership"),
    "const_matrix_store": ("typedef int Matrix[2][3]; void f(const Matrix *p) {Matrix *w=(Matrix *)p;(*w)[1][2]=7;}", "", "ownership"),
    "const_array_live_reader": ("typedef int Row[3]; typedef struct {Row row;} Box; int read_row(const Row *p){return (*p)[0];}void write_row(Row *p){(*p)[0]=7;}int f(Box *box){const Box *view=box;int result=read_row(&view->row);write_row(&box->row);return result+view->row[0];}", "", "loan"),
    "switch_fallthrough": ("int f(int n) {int x=0;switch(n){case 0:x=1;case 1:x=2;break;default:x=3;}return x;}", "", "fall-through"),
    "switch_partial_fallthrough": ("int f(int n) {int x=0;switch(n){case 0:case 1:if(n==0)break;x=1;case 2:x=2;break;default:x=3;}return x;}", "", "fall-through"),
    "switch_grouped_live_loan": ("typedef struct {int x,y;} P; int f(int n) {P p={1,2};const int *held=&p.x;switch(n){case 0:case 1:++p.x;break;default:p.y=9;}return *held+p.y;}", "", "loan"),
    "temporary_reference_projection": ("typedef struct {const int *data; int value;} R; extern R make(void); int f(void) { return make().value; }", '(foreign make (obligation "Returns a native record."))', "plain value"),
    "temporary_opaque_projection": ("typedef struct {int value;} Ticket; extern Ticket make(void); int f(void) { return make().value; }", '(capability Ticket permits) (foreign make (obligation "Returns a native record."))', "opaque capability"),
    "temporary_uncontracted_producer": ("typedef struct {int value;} R; extern R make(int *p); int f(int *p) { return make(p).value; }", '', "foreign obligation"),
    "temporary_producer_loan": ("typedef struct {int value;} R; R make(int *p) { *p=7; return (R){1}; } int f(int *p) { const int *hold=p; int result=make(p).value; return *hold+result; }", '', "loan"),
    "temporary_volatile_projection": ("typedef struct {volatile int value;} R; extern R make(void); int f(void) { return make().value; }", '(foreign make (obligation "Returns a native record."))', "native resource"),
    "temporary_array_escape": ("typedef struct {int data[2];} R; extern R make(void); const int *f(void) { return make().data; }", '(function f (returns-static)) (foreign make (obligation "Returns a native value record."))', "temporary array"),
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
    "source_defined_opaque_record": ("typedef struct NativeStream {int *private_state;} NativeStream; int native_step(NativeStream *stream) { return stream->private_state != 0; }", '(foreign native_step (obligation "A native stream operation."))', "defined function must be checked"),
    "opaque_representation_access": ("typedef struct NativeStream {int *private_state;} NativeStream; int inspect(NativeStream *stream) { return stream->private_state != 0; }", '(function inspect (opaque 0))', "representation is private"),
    "opaque_origin_laundered": ("typedef struct NativeStream {int *private_state;} NativeStream; static int ordinary(NativeStream *stream) {(void)stream;return 0;} int inspect(NativeStream *stream) { return ordinary(stream); }", '(function inspect (opaque 0))', "retain its typed boundary"),
    "opaque_callback_context_exposed": ("typedef struct {void *context;int (*step)(void *);} NativeOps; int run(const NativeOps *ops) {return *(int *)ops->context;}", '(callback NativeStep (field NativeOps step) (opaque 0)) (record NativeOps (field context (opaque)) (field step (callback NativeStep)))', "opaque native handle"),
    "shallow_record_follows_field": (
        "typedef struct { int *state; } Handle; static int inspect(Handle *handle) { return *handle->state; }",
        "(record Handle (field state (borrow write))) (function inspect (shallow 0))",
        "uninitialized"),
    "owning_field_update_leaks": (
        "#include <stdlib.h>\ntypedef struct { int *data; } Box; "
        "static void update(Box *b) { if (b->data) return; b->data=malloc(sizeof *b->data); } "
        "static int use(void) { Box b={0}; update(&b); return b.data != 0; }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record Box (field data (owns heap nullable))) (function update (updates-field 0 data))",
        "unreleased owning field"),
    "updates_nested_pointee_leaks": (
        "#include <stdlib.h>\ntypedef struct { int *data; } Inner; "
        "typedef struct { Inner *inner; } Outer; "
        "static void update(void *context) { Outer *o=context; "
        "if (o->inner->data) return; o->inner->data=malloc(sizeof *o->inner->data); } "
        "static int use(void) { Inner i={0}; Outer o={&i}; update(&o); return o.inner->data != 0; }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record Inner (field data (owns heap nullable))) "
        "(record Outer (field inner (borrow write) (pointee))) "
        "(function update (context 0 Outer) (updates-pointee 0 inner))",
        "unreleased owning field"),
    "stores_null_nonnullable_field": (
        "typedef struct { const int *view; } Holder; static void clear(Holder *h) { h->view=0; }",
        "(record Holder (field view (borrow read))) (function clear (stores-field 0 view null))",
        "nullable borrowed field"),
    "wrong_typed_context": (
        "typedef struct { int value; } A; typedef struct { int value; } B; "
        "static int inspect(void *context) { A *a=context; return a->value; } "
        "static int use(void) { B b={7}; return inspect(&b); }",
        "(record A) (record B) (function inspect (context 0 A))",
        "exact object"),
    "variadic_whole_buffer_unstated": ("typedef struct {const int *data;unsigned n;} Ints; extern void inspect(int tag,...); void f(const Ints *v) {if(!v->data)return;inspect(0,v->data);}", '(record Ints (field data (borrow read nullable) (count n))) (foreign inspect (obligation "A native variadic call."))', "whole dynamic spans"),
    "nullable_whole_buffer_unguarded": (
        "#include <stddef.h>\nstatic int first(const int *p,size_t n) { (void)n; return p[0]; }",
        "(function first (buffer 0) (nullable 0))", "non-null branch"),
    "nullable_counted_field_known_null": (
        "#include <stddef.h>\ntypedef struct { const int *data; size_t n; } Window; "
        "static int total(const Window *w) { int sum=0; "
        "for (size_t i=0;i<w->n;++i) sum+=w->data[i]; return sum; } "
        "int f(void) { Window w={0,0}; return total(&w); }",
        "(record Window (field data (borrow read nullable) (count n)))", "must exist"),
}

HEAP_C = "extern void *reserve(unsigned long n); extern void dispose(void *p); "
HEAP_CONTRACT = '(allocator heap reserve dispose (obligation "Fresh allocation of the requested extent; dispose releases it once and accepts null."))'
READER_RECORD = 'typedef struct {int *item;} Reader; '
READER_CONTRACT = '(record Reader (field item (borrow read)))'
OWNER_RECORD = 'typedef struct {int *item;} Owner; '
OWNER_CONTRACT = HEAP_CONTRACT + ' (record Owner (field item (owns heap)))'
OWNER_START = 'int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; '
TICKET_C = 'typedef struct {unsigned long long identity;} Ticket; extern Ticket *issue(void); extern void perform(Ticket *); extern int inspect(const Ticket *); '
TICKET_CONTRACT = '(capability Ticket effects) (foreign issue (returns-own effects) (obligation "Issues one fresh live effect permit for this logical call.")) (foreign perform (owns 0 effects) (obligation "Consumes the supplied permit and performs its call effect at most once.")) (foreign inspect (obligation "Reads the valid handle without retaining or consuming it."))'
POSITIVE.update({
    "function_name_shadowed_by_consuming_pointer": (HEAP_C + 'void f(int *p) {(void)p;} void h(int *p) {dispose(p);} int g(void) {int *p=(int *)reserve(sizeof(int));if(!p){dispose(p);return 0;}void (*f)(int *)=h;f(p);return 0;}', HEAP_CONTRACT + ' (function h (owns 0 heap))'),
    "transitive_global_call": ('static int n; int owner(void) {return ++n;} int relay(void) {return owner();} int f(void) {return relay();}', '(global n owner)'),
    "recursive_global_call": ('static int n; int owner(int); int relay(int depth) {return depth ? owner(depth-1) : 0;} int owner(int depth) {++n;return depth ? relay(depth-1) : n;}', '(global n owner)'),
    "variadic_global_call": ('static int n; int owner(int value,...) {n+=value;return n;} int f(void) {return owner(1,2);}', '(global n owner)'),
    "arguments_before_call_loan": ('void add(int *p,int value) {*p+=value;}int f(void) {int n=3;add(&n,n);return n;}', ''),
    "effect_after_argument_read": (TICKET_C + 'extern void perform_value(Ticket *,int);int f(void) {Ticket *t=issue();perform_value(t,inspect(t));return 7;}', TICKET_CONTRACT + ' (foreign perform_value (owns 0 effects) (obligation "Consumes the permit after its value argument has been evaluated."))'),
    "shared_record_copy_lifetime": (READER_RECORD + 'int f(void) {int n=7;Reader out={&n};{Reader inner={&n};out=inner;}return *out.item;}', READER_CONTRACT),
    "shared_pointer_copy_lifetime": ('int f(void) {int n=7;const int *out=&n;{const int *inner=&n;out=inner;}return *out;}', ''),
    "capability_once": (TICKET_C + 'int f(void) {Ticket *t=issue();int n=inspect(t)+inspect(t);perform(t);return n;}', TICKET_CONTRACT),
    "capability_move": (TICKET_C + 'int f(void) {Ticket *t=issue();Ticket *m=t;perform(m);return 7;}', TICKET_CONTRACT),
    "capability_record": (TICKET_C + 'typedef struct {Ticket *effect;} Call; int f(void) {Call c={issue()};Call m=c;perform(m.effect);return 7;}', TICKET_CONTRACT + ' (record Call (field effect (owns effects)))'),
})
POSITIVE["switch_total_heap_release"] = (HEAP_C + "void f(int n){int *p=(int *)reserve(4);if(!p){dispose(p);return;}switch(n){case 0:dispose(p);return;default:dispose(p);return;}}", HEAP_CONTRACT)

NEGATIVE.update({
    "indirect_global_reentry": ('static int n; int owner(void); void relay(void) {(void)owner();} int owner(void) {int *p=&n;relay();return *p;}', '(global n owner)', 'overlapping loan'),
    "global_alias_through_helper": ('static int n; void owner(int step); void relay(const int *p) {owner(0);(void)*p;} void owner(int step) {if(step)relay(&n);else ++n;}', '(global n owner)', 'overlapping loan'),
    "global_alias_in_record": ('static int n; typedef struct {const int *view;} Reader; void owner(int step); void relay(const Reader *p) {owner(0);(void)*p->view;} void owner(int step) {if(step){Reader r={&n};relay(&r);}else ++n;}', '(global n owner) (record Reader (field view (borrow read)))', 'overlapping loan'),
    # A local pointer hides the function of the same name: the call goes where the pointer does.
    "function_name_shadowed_by_pointer": ('int f(int value) {return value;} int g(void) {int (*f)(int);return f(1);}', '', 'uninitialized or moved value'),
    "function_name_shadowed_by_parameter": ('int f(int value) {return value;} int g(int (*f)(int)) {return f(1);}', '', 'checked body'),
    "function_name_shadowed_by_consuming_pointer": (HEAP_C + 'void f(int *p) {(void)p;} void h(int *p) {dispose(p);} int g(void) {int *p=(int *)reserve(sizeof(int));if(!p){dispose(p);return 0;}void (*f)(int *)=h;f(p);dispose(p);return 0;}', HEAP_CONTRACT + ' (function h (owns 0 heap))', 'moved value'),
    "argument_consumed_before_call": (TICKET_C + 'extern void perform_value(Ticket *,int);int f(void) {Ticket *t=issue();perform_value(t,(perform(t),7));return 0;}', TICKET_CONTRACT + ' (foreign perform_value (owns 0 effects) (obligation "Consumes the supplied permit once."))', 'moved'),
    "capability_forged_ticket": (TICKET_C + 'int f(void) {Ticket t={7};perform(&t);return 0;}', TICKET_CONTRACT, 'declared issuer'),
    "capability_field_bypass": (TICKET_C + 'int f(void) {Ticket *t=issue();t->identity=7;perform(t);return 0;}', TICKET_CONTRACT, 'representation is private'),
    "capability_double_effect": (TICKET_C + 'int f(void) {Ticket *t=issue();perform(t);perform(t);return 0;}', TICKET_CONTRACT, 'moved value'),
    "capability_per_reader_effect": (TICKET_C + 'typedef struct {Ticket *effect;} Call; int f(void) {Call c={issue()};Call a=c;Call b=c;perform(a.effect);perform(b.effect);return 0;}', TICKET_CONTRACT + ' (record Call (field effect (owns effects)))', 'moved'),
    "capability_integer_forge": (TICKET_C + 'int f(unsigned long n) {Ticket *t=(Ticket *)n;perform(t);return 0;}', TICKET_CONTRACT, 'opaque capability cannot be forged'),
    "capability_heap_forge": (HEAP_C + TICKET_C + 'int f(void) {Ticket *t=(Ticket *)reserve(sizeof(Ticket));if(!t)return 0;perform(t);return 0;}', HEAP_CONTRACT + TICKET_CONTRACT, 'opaque capability cannot be forged'),
    "capability_erased_interface": (TICKET_C + 'extern void raw(void *);int f(void) {Ticket *t=issue();raw(t);perform(t);return 0;}', TICKET_CONTRACT + ' (foreign raw (obligation "Writes an ordinary byte buffer."))', 'typed interface'),
    "capability_static_forge": (TICKET_C + 'static const Ticket t={7};int f(void) {return inspect(&t);}', TICKET_CONTRACT, 'declared issuer'),
    "reference_slot_erased": ('extern void raw(void *);int f(void) {int n=7;const int *p=&n;raw(&p);return *p;}', '(foreign raw (obligation "Writes an ordinary byte buffer."))', 'reference-bearing storage'),
    "reference_slot_erased_local": ('extern void raw(void *);int f(void) {int n=7;const int *p=&n;{void *v=&p;raw(v);}return *p;}', '(foreign raw (obligation "Writes an ordinary byte buffer."))', 'typed lifetime interface'),
    "capability_void_owner": (TICKET_C + 'int f(void) {Ticket *t=issue();void *v=t;Ticket *u=(Ticket *)v;perform(u);return 0;}', TICKET_CONTRACT, 'typed provenance'),
    "capability_void_borrow": (TICKET_C + 'int f(void) {Ticket *t=issue();{const Ticket *b=t;const void *v=b;(void)v;}perform(t);return 0;}', TICKET_CONTRACT, 'typed provenance'),
    "capability_void_reborrow": (TICKET_C + 'int f(void) {Ticket *t=issue();{void *v=&*t;(void)v;}perform(t);return 0;}', TICKET_CONTRACT, 'typed provenance'),
    "capability_raw_cast": (TICKET_C + 'int f(void) {Ticket *t=issue();unsigned long long *p=(unsigned long long *)t;*p=77;perform(t);return 0;}', TICKET_CONTRACT, 'representation cannot be exposed'),
    "capability_allocator_laundering": (HEAP_C + TICKET_C + 'int f(void) {return 0;}', TICKET_CONTRACT + ' (allocator effects reserve dispose (obligation "Fresh ordinary storage, not an authority issuer."))', 'typed issuer contracts'),
    "capability_family_alias": (TICKET_C + 'typedef struct {unsigned long long identity;} Other;int f(void) {return 0;}', TICKET_CONTRACT + ' (capability Other effects)', 'distinct authority families'),
    "capability_untyped_issuer": (TICKET_C + 'extern int *fake(void);int f(void) {return 0;}', TICKET_CONTRACT + ' (foreign fake (returns-own effects) (obligation "Fresh integer storage."))', 'matching typed result'),
})
POSITIVE.update({
    "record_field_post": (READER_RECORD + 'void replace(Reader *r,int *p) {r->item=p;} int f(void) {int a=3,b=7;Reader r={&a};replace(&r,&b);return *r.item;}', READER_CONTRACT + ' (function replace (stores-field 0 item 1))'),
    "record_field_post_branches": (READER_RECORD + 'void replace(Reader *r,int *p,int yes) {if(yes)r->item=p;else r->item=p;} int f(void) {int a=3,b=7;Reader r={&a};replace(&r,&b,1);return *r.item;}', READER_CONTRACT + ' (function replace (stores-field 0 item 1))'),
    "record_field_post_transient": (READER_RECORD + 'void replace(Reader *r,int *p) {int temporary=3;r->item=&temporary;*r->item+=1;r->item=p;} int f(void) {int a=3,b=7;Reader r={&a};replace(&r,&b);return *r.item;}', '(record Reader (field item (borrow write))) (function replace (stores-field 0 item 1))'),
    "record_field_post_source_path": (READER_RECORD + 'typedef struct {int *item;} Holder; void replace(Reader *r,Holder *h) {r->item=h->item;} int f(void) {int a=3,b=7;Reader r={&a};Holder h={&b};replace(&r,&h);return *r.item;}', READER_CONTRACT + ' (record Holder (field item (borrow write))) (function replace (stores-field 0 item (borrow 1 item)))'),
    "record_field_post_preserved": (READER_RECORD + 'void preserve(Reader *r) {(void)r;} int f(void) {int a=3;Reader r={&a};preserve(&r);return *r.item;}', READER_CONTRACT + ' (function preserve (preserves-field 0 item))'),
    "record_field_post_restore_branch": (READER_RECORD + 'void temporary(Reader *r,int yes) {int *entry=r->item;if(yes){int local=3;r->item=&local;*r->item+=1;r->item=entry;}} int f(void) {int a=3;Reader r={&a};temporary(&r,1);return *r.item;}', '(record Reader (field item (borrow write))) (function temporary (preserves-field 0 item))'),
    "record_take_field": (HEAP_C + OWNER_RECORD + 'void consume(Owner *r) {dispose(r->item);} int f(void) {' + OWNER_START + 'Owner r={p};consume(&r);return 7;}', OWNER_CONTRACT + ' (function consume (takes-field 0 item))'),
    "record_heap_destroy": (HEAP_C + OWNER_RECORD + 'void destroy(Owner *r) {dispose(r->item);dispose(r);} int f(void) {Owner *r=(Owner *)reserve(sizeof(Owner));if(!r){dispose(r);return 0;} int *p=(int *)reserve(sizeof(int));if(!p){dispose(p);dispose(r);return 0;} *p=7;r->item=p;destroy(r);return 7;}', OWNER_CONTRACT + ' (function destroy (owns 0 heap))'),
    "record_call_read": (READER_RECORD + 'int read_item(const Reader *r) { return *r->item; } int f(void) {int n=7;Reader r={&n};return read_item(&r);}', READER_CONTRACT),
    "record_call_write": (READER_RECORD + 'void write_item(Reader *r) { *r->item=9; } int f(void) {int n=7;Reader r={&n};write_item(&r);return *r.item;}', '(record Reader (field item (borrow write)))'),
    "record_call_get": (READER_RECORD + 'const int *get_item(const Reader *r) { return r->item; } int f(void) {int n=7;Reader r={&n};const int *p=get_item(&r);return *p;}', READER_CONTRACT + ' (function get_item (returns-borrow 0 item))'),
    "record_call_nullable": (READER_RECORD + 'int read_item(const Reader *r) {if(!r->item)return 0;return *r->item;} int f(void) {Reader r={0};return read_item(&r);}', '(record Reader (field item (borrow read nullable)))'),
    "record_value_consume": (HEAP_C + OWNER_RECORD + 'int consume(Owner value) {int n=*value.item;dispose(value.item);return n;} int f(void) {' + OWNER_START + 'Owner value={p};return consume(value);}', OWNER_CONTRACT),
    "nullable_call": ('int read_item(const int *p) {if(!p)return 0;return *p;} int f(void) {return read_item(0);}', '(function read_item (nullable 0))'),
    "record_static_call": ('typedef struct {const char *name;} Label; const char *get_name(const Label *p) {return p->name;} int f(void) {Label p={"ok"};return get_name(&p)[0];}', '(record Label (field name (static))) (function get_name (returns-static))'),
    "borrowed_heap_field": (HEAP_C + 'typedef struct {int a,b;} Pair; int *field(Pair *p) { return &p->b; } int f(void) { Pair *p=(Pair *)reserve(sizeof(Pair)); if(!p) { dispose(p); return 0; } p->a=1; p->b=2; int result=0; { int *q=field(p); *q=7; result=*q; } dispose(p); return result; }', HEAP_CONTRACT + ' (function field (returns-borrow 0 b))'),
    "typed_call_rights": ("void write_view(int *p) { *p=9; } int read_view(const int *p) { return *p; } int f(void) { int n=3; const int *p=&n; return read_view(p); }", ""),
    "typed_call_extent": ("typedef struct { int a,b; } Pair; int read_pair(const Pair *p) { return p->b; } int read_item(const int *p) { return *p; } int f(void) { Pair p={3,4}; return read_item(&p.a); }", ""),
    "heap_move": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; int *q=p; int value=*q; dispose(q); return value; }", HEAP_CONTRACT),
    "heap_borrow": (HEAP_C + "int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; int result=0; { const int *r=p; result=*r; } dispose(p); return result; }", HEAP_CONTRACT),
    "owned_argument": (HEAP_C + "void consume(int *p) { dispose(p); } int f(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } consume(p); return 1; }", HEAP_CONTRACT + " (function consume (owns 0 heap))"),
    "owned_result": (HEAP_C + "int *make(void) { int *p=(int *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } *p=7; return p; } int f(void) { int *p=make(); if(!p) { dispose(p); return 0; } int value=*p; dispose(p); return value; }", HEAP_CONTRACT + " (function make (returns-own heap nullable))"),
    "direct_owned_result": (HEAP_C + "int *make(void) { return (int *)reserve(sizeof(int)); }", HEAP_CONTRACT + " (function make (returns-own heap nullable))"),
    "owned_record_pointer_result": (HEAP_C + 'typedef struct {int *item;} Box; Box *make(void) {Box *b=(Box *)reserve(sizeof *b);if(!b){dispose(b);return 0;}*b=(Box){0};int *p=(int *)reserve(sizeof *p);if(!p){dispose(p);dispose(b);return 0;}*p=7;b->item=p;return b;} void destroy(Box *b){dispose(b->item);dispose(b);} int f(void){Box *b=make();if(!b){dispose(b);return 0;}if(!b->item){destroy(b);return 0;}int n=*b->item;destroy(b);return n;}', HEAP_CONTRACT + ' (record Box (field item (owns heap nullable))) (function make (returns-own heap nullable) (returns-pointee ((item) (owns heap)))) (function destroy (owns 0 heap))'),
    "borrowed_record_pointer_result": (HEAP_C + 'typedef struct {int *item;} Reader; Reader *make(int *p) {Reader *r=(Reader *)reserve(sizeof *r);if(!r){dispose(r);return 0;}*r=(Reader){p};return r;} int f(void){int n=7;Reader *r=make(&n);if(!r){dispose(r);return 0;}int out=*r->item;dispose(r);return out;}', HEAP_CONTRACT + ' (record Reader (field item (borrow read))) (function make (returns-own heap nullable) (returns-pointee ((item) (borrow 0))))'),
    "direct_release": (HEAP_C + "void f(void) { dispose(reserve(sizeof(int))); }", HEAP_CONTRACT),
})

NEGATIVE["undersized_call"] = (HEAP_C + "typedef struct {int a,b;} Pair; int read_pair(const Pair *p) { return p->a; } int f(void) { Pair *p=(Pair *)reserve(sizeof(int)); if(!p) { dispose(p); return 0; } int n=read_pair(p); dispose(p); return n; }", HEAP_CONTRACT, "smaller")
NEGATIVE.update({
    "record_field_post_partial": (READER_RECORD + 'void replace(Reader *r,int *p,int yes) {if(yes)r->item=p;}', READER_CONTRACT + ' (function replace (stores-field 0 item 1))', "stored field's lifetime"),
    "record_field_preserve_changed": (READER_RECORD + 'void change(Reader *r,int *p) {r->item=p;}', READER_CONTRACT + ' (function change (preserves-field 0 item))', "does not retain its original lifetime"),
    "owned_record_pointer_missing_fields": (HEAP_C + 'typedef struct {int *item;} Box; Box *make(void) {Box *b=(Box *)reserve(sizeof *b);if(!b){dispose(b);return 0;}*b=(Box){0};int *p=(int *)reserve(sizeof *p);if(!p){dispose(p);dispose(b);return 0;}b->item=p;return b;}', HEAP_CONTRACT + ' (record Box (field item (owns heap nullable))) (function make (returns-own heap nullable))', "owned allocation leaves scope"),
    "record_field_post_local": (READER_RECORD + 'void replace(Reader *r,int *p) {int n=7;r->item=&n;}', READER_CONTRACT + ' (function replace (stores-field 0 item 1))', 'outlives'),
    "record_take_twice": (HEAP_C + OWNER_RECORD + 'void consume(Owner *r) {dispose(r->item);} int f(void) {' + OWNER_START + 'Owner r={p};consume(&r);consume(&r);return 7;}', OWNER_CONTRACT + ' (function consume (takes-field 0 item))', 'moved record field'),
    "record_post_dangling": (READER_RECORD + 'void replace(Reader *r,int *p) {r->item=p;} int f(void) {int a=3;Reader r={&a};{int b=7;replace(&r,&b);}return *r.item;}', READER_CONTRACT + ' (function replace (stores-field 0 item 1))', 'outlives'),
    "record_uncontracted_return": (READER_RECORD + 'Reader make(int *p) {Reader r={p};return r;}', READER_CONTRACT, 'field result contracts'),
    "record_const_call_rights": (READER_RECORD + 'void write_item(const Reader *r) { *r->item=9; }', '(record Reader (field item (borrow write)))', 'shared loan'),
    "record_field_postcondition": (READER_RECORD + 'void replace(Reader *r,const int *p) { r->item=(int *)p; }', READER_CONTRACT, 'relational field postcondition'),
    "nullable_call_unguarded": ('int read_item(const int *p) {return *p;}', '(function read_item (nullable 0))', 'non-null branch'),
    "record_nullable_unguarded": (READER_RECORD + 'int read_item(const Reader *r) {return *r->item;}', '(record Reader (field item (borrow read nullable)))', 'non-null branch'),
    "borrowed_field_is_not_owner": (HEAP_C + 'typedef struct {int a,b;} Pair; int *field(Pair *p) { return &p->b; } int f(void) { Pair *p=(Pair *)reserve(sizeof(Pair)); if(!p) { dispose(p); return 0; } p->a=1; p->b=2; int *q=field(p); dispose(q); return 0; }', HEAP_CONTRACT + ' (function field (returns-borrow 0 b))', 'matching allocator family'),
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

READ_VIEW = '\n#include <stddef.h>\ntypedef struct {const unsigned char *data; size_t len;} ReadView; '
WRITE_VIEW = '\n#include <stddef.h>\ntypedef struct {unsigned char *data; size_t len;} WriteView; '
READ_CONTRACT = '(view ReadView data len read)'
WRITE_CONTRACT = '(view WriteView data len write)'
POSITIVE.update({
    "read_view": (READ_VIEW + 'int f(void) { unsigned char data[8]={1,2,3}; ReadView v={data,8}; return v.data[2]; }', READ_CONTRACT),
    "shared_view_copy": (READ_VIEW + 'int f(void) { unsigned char data[8]={1,2,3}; ReadView a={data,8}; ReadView b=a; return a.data[0]+b.data[1]; }', READ_CONTRACT),
    "shared_view_narrow_copy": (READ_VIEW + 'int f(void) { unsigned char data[8]={0,0,0,0,0,0,0,9}; ReadView v={data,8}; ReadView r=v; v=(ReadView){v.data,4}; return r.data[7]; }', READ_CONTRACT),
    "write_view": (WRITE_VIEW + 'int f(void) { unsigned char data[8]={0}; WriteView v={.len=8,.data=data}; v.data[7]=9; return v.data[7]; }', WRITE_CONTRACT),
    "returned_view": (READ_VIEW + 'typedef struct {unsigned char bytes[8];} Frame; ReadView frame_view(const Frame *p) { return (ReadView){.data=p->bytes,.len=8}; } int f(void) { Frame p={{3,4}}; ReadView v=frame_view(&p); return v.data[1]; }', READ_CONTRACT + ' (function frame_view (returns-borrow 0 bytes))'),
    "view_call": (READ_VIEW + 'extern unsigned first(ReadView); int f(void) { unsigned char data[8]={9}; ReadView v={data,8}; return (int)first(v); }', READ_CONTRACT + ' (foreign first (obligation "Reads only within the supplied byte view during this call."))'),
    "empty_read_view": (READ_VIEW + 'int f(void) { ReadView v={0,0}; return (int)v.len; }', READ_CONTRACT),
    "narrow_view": (READ_VIEW + 'int f(void) { unsigned char data[8]={1,2,3}; ReadView v={data,8}; v=(ReadView){v.data,4}; return v.data[3]; }', READ_CONTRACT),
    "static_view": (READ_VIEW + 'static const unsigned char data[8]={1,2,3}; int f(void) { ReadView v={data,8}; v=(ReadView){v.data,4}; return v.data[2]; }', READ_CONTRACT),
})
NEGATIVE.update({
    "view_too_long": (READ_VIEW + 'int f(void) { unsigned char data[8]={0}; ReadView v={data,9}; return (int)v.len; }', READ_CONTRACT, 'exceeds'),
    "view_length_update": (READ_VIEW + 'void f(void) { unsigned char data[8]={0}; ReadView v={data,8}; v.len=9; }', READ_CONTRACT, 'independently'),
    "view_pointer_update": (READ_VIEW + 'void f(void) { unsigned char data[8]={0}; ReadView v={data,8}; v.data=data; }', READ_CONTRACT, 'independently'),
    "view_length_address": (READ_VIEW + 'unsigned long f(void) { unsigned char data[8]={0}; ReadView v={data,8}; const size_t *p=&v.len; return *p; }', READ_CONTRACT, 'cannot escape'),
    "view_outlives_array": (READ_VIEW + 'unsigned f(void) { ReadView v; { unsigned char data[8]={0}; v=(ReadView){data,8}; } return v.data[0]; }', READ_CONTRACT, 'outlives'),
    "view_out_of_bounds": (READ_VIEW + 'int f(void) { unsigned char data[8]={0}; ReadView v={data,8}; return v.data[8]; }', READ_CONTRACT, 'exceeds'),
    "view_write_alias": (WRITE_VIEW + 'void f(void) { unsigned char data[8]={0}; WriteView a={data,8}; WriteView b=a; a.data[0]=1; b.data[0]=2; }', WRITE_CONTRACT, 'overlapping'),
    "view_wrong_return_extent": (READ_VIEW + 'typedef struct {unsigned char bytes[8];} Frame; ReadView f(const Frame *p) { return (ReadView){p->bytes,7}; }', READ_CONTRACT + ' (function f (returns-borrow 0 bytes))', 'extent differs'),
    "view_native_field_write": (READ_VIEW + 'extern void change(ReadView *); void f(void) { unsigned char data[8]={0}; ReadView v={data,8}; change(&v); }', READ_CONTRACT + ' (foreign change (obligation "Writes the record."))', 'field effects'),
    "interior_result_index": ('const int *find(const int *p) { return p; } int f(void) { int a[2]={1,2}; const int *p=find(a); return p[1]; }', '(function find (returns-borrow 0))', 'remaining extent'),
    "narrow_view_bounds": (READ_VIEW + 'int f(void) { unsigned char data[8]={0}; ReadView v={data,8}; v=(ReadView){v.data,4}; return v.data[4]; }', READ_CONTRACT, 'exceeds'),
    "narrow_view_under_borrow": (WRITE_VIEW + 'int f(void) { unsigned char data[8]={0}; WriteView v={data,8}; WriteView r=v; v=(WriteView){v.data,4}; return r.data[0]; }', WRITE_CONTRACT, 'overlapping loan'),
    "view_length_under_exclusive_borrow": (READ_VIEW + 'int f(void) { unsigned char data[8]={0}, other[8]={0}; ReadView v={data,8}; ReadView *p=&v; ReadView r={other,v.len}; (void)p; return r.data[0]; }', READ_CONTRACT, 'overlapping'),
})

OWNED_VIEW = HEAP_C + WRITE_VIEW
OWNED_CONTRACT = HEAP_CONTRACT + ' (view WriteView data len write (owns heap))'
OWNED_START = 'unsigned char *p=(unsigned char *)reserve(8); if(!p) { dispose(p); return 0; } WriteView a={p,8}; '
POSITIVE.update({
    "record_borrow": (READER_RECORD + 'int f(void) { int n=7; Reader r={&n}; return *r.item; }', READER_CONTRACT),
    "record_exclusive": (READER_RECORD + 'int f(void) { int n=7; Reader r={&n}; *r.item=9; return *r.item; }', '(record Reader (field item (borrow write)))'),
    "record_move": (HEAP_C + OWNER_RECORD + 'int f(void) {' + OWNER_START + 'Owner a={p}; Owner b=a; int n=*b.item; dispose(b.item); return n; }', OWNER_CONTRACT),
    "record_reader_scope": (HEAP_C + READER_RECORD + 'int f(void) {' + OWNER_START + 'int n=0; { Reader r={p}; n=*r.item; } dispose(p); return n; }', HEAP_CONTRACT + ' ' + READER_CONTRACT),
    "record_static_field": ('typedef struct {const char *name;} Label; int f(void) { Label a={"ok"}; Label b=a; return b.name[0]; }', '(record Label (field name (static)))'),
    "record_nullable_field": ('typedef struct {const char *name;} Label; int f(void) { Label a={0}; return a.name==0; }', '(record Label (field name (static nullable)))'),
    "record_rebind": (READER_RECORD + 'int f(void) { int a=3,b=4; Reader r={&a}; r.item=&b; return *r.item; }', READER_CONTRACT),
    "record_two_owners": (HEAP_C + 'typedef struct {int *a,*b;} Pair; int f(void) { int *a=(int *)reserve(sizeof(int)); if(!a) {dispose(a);return 0;} int *b=(int *)reserve(sizeof(int)); if(!b) {dispose(b);dispose(a);return 0;} *a=3;*b=4;Pair p={a,b};int n=*p.a+*p.b;dispose(p.b);dispose(p.a);return n;}', HEAP_CONTRACT + ' (record Pair (field a (owns heap)) (field b (owns heap)))'),
})
NEGATIVE.update({
    "record_reader_freed": (HEAP_C + READER_RECORD + 'int f(void) {' + OWNER_START + 'Reader r={p}; dispose(p); return *r.item; }', HEAP_CONTRACT + ' ' + READER_CONTRACT, 'outlives'),
    "record_rights_bypass": (READER_RECORD + 'int f(void) { int n=7; Reader r={&n}; int *p=r.item; *p=9; return n; }', READER_CONTRACT, 'shared loan'),
    "record_dangling_field": (READER_RECORD + 'int f(void) { Reader r; {int n=7; r.item=&n;} return *r.item; }', READER_CONTRACT, 'outlives'),
    "record_double_move": (HEAP_C + OWNER_RECORD + 'int f(void) {' + OWNER_START + 'Owner a={p}; Owner b=a; Owner c=a; dispose(b.item);dispose(c.item);return 0;}', OWNER_CONTRACT, 'moved reference field'),
    "record_owner_overwrite": (HEAP_C + OWNER_RECORD + 'int f(void) {' + OWNER_START + 'Owner a={p}; a.item=0; return 0;}', HEAP_CONTRACT + ' (record Owner (field item (owns heap nullable)))', 'release authority'),
    "record_field_leak": (HEAP_C + OWNER_RECORD + 'int f(void) {' + OWNER_START + 'Owner a={p}; return *a.item;}', OWNER_CONTRACT, 'unreleased owning field'),
    "record_nonstatic_field": ('typedef struct {const char *name;} Label; int f(void) {char name[2]={0};Label a={name};return a.name[0];}', '(record Label (field name (static)))', 'non-static'),
    "record_missing_field": ('typedef struct {int *a,*b;} Pair; int f(void) {return 0;}', '(record Pair (field a (borrow read)))', 'every pointer field'),
})
POSITIVE.update({
    "owning_view_move": (OWNED_VIEW + 'int f(void) {' + OWNED_START + 'WriteView b=a; b.data[0]=7; int n=b.data[0]; dispose(b.data); return n; }', OWNED_CONTRACT),
    "owning_view_borrow": (OWNED_VIEW + READ_VIEW + 'int f(void) {' + OWNED_START + 'a.data[0]=7; int n=0; { ReadView r={a.data,a.len}; n=r.data[0]; } dispose(a.data); return n; }', OWNED_CONTRACT + ' ' + READ_CONTRACT),
    "owning_view_transfer": (OWNED_VIEW + 'extern void consume_buffer(WriteView); int f(void) {' + OWNED_START + 'consume_buffer(a); return 1; }', OWNED_CONTRACT + ' (foreign consume_buffer (owns 0 heap) (obligation "Consumes and releases the entire supplied buffer once."))'),
})
NEGATIVE.update({
    "owning_view_double_move": (OWNED_VIEW + 'int f(void) {' + OWNED_START + 'WriteView b=a; WriteView c=a; dispose(b.data); dispose(c.data); return 0; }', OWNED_CONTRACT, 'moved value'),
    "owning_view_dangling_borrow": (OWNED_VIEW + READ_VIEW + 'int f(void) {' + OWNED_START + 'ReadView r={a.data,a.len}; dispose(a.data); return r.data[0]; }', OWNED_CONTRACT + ' ' + READ_CONTRACT, 'outlives'),
    "owning_view_short_record": (OWNED_VIEW + 'int f(void) { unsigned char *p=(unsigned char *)reserve(8); if(!p) { dispose(p); return 0; } WriteView a={p,7}; dispose(a.data); return 0; }', OWNED_CONTRACT, 'whole allocation'),
    "owning_view_move_under_borrow": (OWNED_VIEW + READ_VIEW + 'int f(void) {' + OWNED_START + 'ReadView r={a.data,a.len}; WriteView b=a; int n=r.data[0]; dispose(b.data); return n; }', OWNED_CONTRACT + ' ' + READ_CONTRACT, 'while it is borrowed'),
    "owning_view_leak": (OWNED_VIEW + 'int f(void) {' + OWNED_START + 'a.data[0]=3; return 0; }', OWNED_CONTRACT, 'without release'),
})
# Definite initialization at joins, and the overflow builtins' writes.
POSITIVE.update({
    "join_written_one_side_unread": ("int f(int c) { int n; if (c) n = 1; return 7; }", ""),
    "overflow_builtin_initializes": ("int f(unsigned a, unsigned b) { unsigned r; if (__builtin_mul_overflow(a, b, &r)) return 0; return (int)r; }", ""),
})
NEGATIVE.update({
    "join_written_one_side_read": ("int f(int c) { int n; if (c) n = 1; return n; }", "", "uninitialized"),
    "flag_guarded_first_write": ("int f(unsigned a, unsigned b) { unsigned r; int ok = 1; if (ok && __builtin_mul_overflow(a, b, &r)) ok = 0; if (ok) return (int)r; return 0; }", "", "uninitialized"),
})
# Outputs: (initializes I) lends uninitialized storage that the callee writes before every return;
# (initializes I when-true) before every return of a value other than 0, so the caller may read it on
# the edge where the call's value is not 0.
OUTPUTS_C = "#include <stdbool.h>\nstatic bool find(int k, int *out) { if (k > 0) { *out = k; return true; } return false; }\nstatic void get(int *out) { *out = 3; }\n"
OUTPUTS_CONTRACT = "(function find (initializes 1 when-true)) (function get (initializes 0))"
POSITIVE.update({
    "output_when_true": (OUTPUTS_C + "int f(int k) { int v; if (find(k, &v)) return v; return 0; }", OUTPUTS_CONTRACT),
    "output_always": (OUTPUTS_C + "int f(void) { int v; get(&v); return v; }", OUTPUTS_CONTRACT),
    "output_negated_under_or": (OUTPUTS_C + "int f(int k) { int v; if (!find(k, &v) || k > 100) return 0; return v; }", OUTPUTS_CONTRACT),
    # the call runs only on the edge where the earlier operand is false: there its output is written
    "output_always_after_short_circuit": ("#include <stdbool.h>\nstatic bool fill(int *out) { *out = 4; return true; } int f(int a) { int v; if (a > 5 || !fill(&v)) return 0; return v; }", "(function fill (initializes 0))"),
})
NEGATIVE.update({
    # on the true edge of the ||, fill may not have run
    "output_read_where_call_skipped": ("#include <stdbool.h>\nstatic bool fill(int *out) { *out = 4; return true; } int f(int a) { int v; if (a > 5 || !fill(&v)) return v; return 0; }", "(function fill (initializes 0))", "uninitialized"),
})
NEGATIVE.update({
    "output_read_on_false_edge": (OUTPUTS_C + "int f(int k) { int v; if (!find(k, &v)) return v; return 0; }", OUTPUTS_CONTRACT, "uninitialized"),
    "output_read_before_written": ("static void bump(int *out) { *out = *out + 1; } int f(void) { int v; bump(&v); return v; }", "(function bump (initializes 0))", "read before it is written"),
    "output_unwritten_on_true_return": ("#include <stdbool.h>\nstatic bool find(int k, int *out) { if (k > 0) return true; *out = 1; return false; } int f(int k) { int v; if (find(k, &v)) return v; return 0; }", "(function find (initializes 1 when-true))", "every return of a value other than 0"),
    "output_address_kept": (OUTPUTS_C + "int f(void) { int v; int *p = &v; get(p); return v; }", OUTPUTS_CONTRACT, "output-initialization"),
    "output_read_in_same_call": ("static void put(int *out, int x) { *out = x; } int f(void) { int v; put(&v, v); return v; }", "(function put (initializes 0))", "uninitialized"),
})
# A read-only conditional whose one side is static storage keeps the other side's borrowed origin;
# a writable result, or an owned side, still needs one origin.
STATIC_OR_BORROW = "typedef struct { const int *items; unsigned n; } Box; static const int no_items[1] = {0}; "
STATIC_OR_BORROW_W = "typedef struct { char *items; unsigned n; } Box; "
POSITIVE.update({
    "static_or_borrow_read": (STATIC_OR_BORROW + "const int *items_of(const Box *b) { return b->items ? b->items : no_items; }",
        "(record Box (field items (borrow read nullable))) (function items_of (returns-borrow 0 items))"),
})
NEGATIVE.update({
    "static_or_borrow_write": (STATIC_OR_BORROW_W + "char *items_of(Box *b) { return b->items ? b->items : \"\"; }",
        "(record Box (field items (borrow write nullable))) (function items_of (returns-borrow 0 items))", "common lifetime origin"),
    "static_or_owned_read": (HEAP_C + "static const int no_cells[1] = {0}; int cells(int c) { int *p = (int *)reserve(4); if (!p) { dispose(p); return 0; } *p = 3; const int *r = c ? p : no_cells; int v = r[0]; dispose(p); return v; }",
        HEAP_CONTRACT, "common lifetime origin"),
})
# A borrow ends at its last use: after an item of a block, the borrow of a reference variable declared in
# that block ends when no later item names the variable, nothing else holds the loan and no live loan
# reaches it, through a copy or through a pointer to the variable.
LAST_USE = "typedef struct {int x,y;} P; const int *field(const P *p) { return &p->y; } "
LAST_USE_CONTRACT = "(function field (returns-borrow 0 y))"
LAST_USE_HEAP = "#include <stdlib.h>\nint f(void) { int *owner=malloc(sizeof *owner); if(!owner) return 0; *owner=3; const int *v=owner; int r=*v; free(owner); "
LAST_USE_HEAP_CONTRACT = '(allocator heap malloc free (obligation "malloc/free"))'
POSITIVE.update({
    "borrow_ends_at_last_use": (LAST_USE + "int f(void) { P p={1,2}; const int *v=field(&p); int r=*v; p.y=4; return r+p.y; }", LAST_USE_CONTRACT),
    "exclusive_borrow_ends_at_last_use": ("typedef struct {int x,y;} P; int f(void) { P p={1,2}; int *v=&p.y; *v=3; p.y+=4; return p.y; }", ""),
    "borrow_ends_in_inner_block": (LAST_USE + "int f(int n) { P p={1,2}; int r=0; if(n) { const int *v=field(&p); r=*v; p.y=4; } return r+p.y; }", LAST_USE_CONTRACT),
    "borrow_ends_each_iteration": (LAST_USE + "int f(int n) { P p={1,2}; int r=0; while(n-->0) { const int *v=field(&p); r+=*v; p.y=4; } return r; }", LAST_USE_CONTRACT),
    "borrow_ends_after_loop": (LAST_USE + "int f(int n) { P p={1,2}; const int *v=field(&p); int r=0; while(n-->0) r+=*v; p.y=4; return r+p.y; }", LAST_USE_CONTRACT),
    "release_after_last_use": (LAST_USE_HEAP + "return r; }", LAST_USE_HEAP_CONTRACT),
})
NEGATIVE.update({
    "borrow_named_later": (LAST_USE + "int f(void) { P p={1,2}; const int *v=field(&p); int r=*v; p.y=4; return r+*v; }", LAST_USE_CONTRACT, "overlapping loan"),
    "exclusive_borrow_named_later": ("typedef struct {int x,y;} P; int f(void) { P p={1,2}; int *v=&p.y; *v=3; p.y+=4; return *v; }", "", "overlapping loan"),
    "borrow_used_next_iteration": (LAST_USE + "int f(int n) { P p={1,2}; const int *v=field(&p); int r=0; while(n-->0) { r+=*v; p.y=4; } return r; }", LAST_USE_CONTRACT, "overlapping loan"),
    "borrow_named_after_jump": (LAST_USE + "int f(int n) { P p={1,2}; const int *v=field(&p); int r=*v; if(n) goto out; p.y=4; out: return r+*v; }", LAST_USE_CONTRACT, "overlapping loan"),
    "borrow_copied_then_used": (LAST_USE + "int f(void) { P p={1,2}; const int *v=field(&p); const int *w=v; int r=*v; p.y=4; return r+*w; }", LAST_USE_CONTRACT, "overlapping loan"),
    "borrow_reached_through_pointer": (LAST_USE + "int f(void) { P p={1,2}; const int *v=field(&p); const int *const *pp=&v; int r=*v; p.y=4; return r+**pp; }", LAST_USE_CONTRACT, "overlapping loan"),
    "use_after_release": (LAST_USE_HEAP + "return r+*v; }", LAST_USE_HEAP_CONTRACT, "outlives"),
})
# The frame rule on a record's own storage: an internal callee is lent only the bytes it reaches, in its
# body or through its callees, each read or written; an external callee keeps its whole pointee.
FRAME = "typedef struct { int key; int fired[4]; } X; "
POSITIVE.update({
    "frame_disjoint_field_write": (FRAME + "static void bump_key(X *x) { x->key += 1; } int f(X *x) { const int *held = &x->fired[1]; bump_key(x); return *held + x->key; }", ""),
    "frame_read_beside_exclusive": (FRAME + "static int read_fired(const X *x) { return x->fired[1]; } int f(X *x) { int *w = &x->key; *w = read_fired(x); return *w; }", ""),
    "frame_through_callee": (FRAME + "static void inner(X *x) { x->key = 1; } static void outer(X *x) { inner(x); } int f(X *x) { const int *held = &x->fired[0]; outer(x); return *held; }", ""),
    "frame_recursive": (FRAME + "static void down(X *x, int n) { if (n > 0) { x->key += n; down(x, n - 1); } } int f(X *x) { const int *held = &x->fired[2]; down(x, 3); return *held + x->key; }", ""),
})
NEGATIVE.update({
    "frame_overlapping_field_write": (FRAME + "static void bump_fired(X *x) { x->fired[1] += 1; } int f(X *x) { const int *held = &x->fired[1]; bump_fired(x); return *held + x->key; }", "", "overlapping loan"),
    "frame_overlap_through_callee": (FRAME + "static void inner(X *x) { x->fired[0] = 1; } static void outer(X *x) { inner(x); } int f(X *x) { const int *held = &x->fired[0]; outer(x); return *held; }", "", "overlapping loan"),
    "frame_whole_copy": (FRAME + "static X copy_of(const X *x) { return *x; } int f(X *x) { int *w = &x->key; X c = copy_of(x); *w = 2; return c.key; }", "", "overlapping loan"),
    "frame_external_callee_whole": (FRAME + "void bump_key_ext(X *x) { x->key += 1; } int f(X *x) { const int *held = &x->fired[1]; bump_key_ext(x); return *held; }", "", "overlapping loan"),
})
# A null record owns no fields: an owned record parameter that admits null discharges the record's owning
# fields on its null path, a null record lends null for each field, and a record that may be null hands on
# the fields it would hold to a parameter that admits null.
TABLE = "#include <stdlib.h>\ntypedef struct { int *cells; unsigned n; } Table; static void table_release(Table *t) { free(t->cells); free(t); } "
TABLE_CONTRACT = '(allocator heap malloc free (obligation "malloc/free")) (record Table (field cells (owns heap nullable) (count n))) (function table_release (owns 0 heap)) (function table_free (owns 0 heap) (nullable 0))'
POSITIVE.update({
    "null_record_release_join": (TABLE + "void table_free(Table *t) { if (t) table_release(t); }", TABLE_CONTRACT),
    "null_record_release_return": (TABLE + "void table_free(Table *t) { if (!t) return; table_release(t); }", TABLE_CONTRACT),
    "null_record_argument": (TABLE + "static void table_free(Table *t) { if (t) table_release(t); } int use(void) { Table *t = NULL; table_free(t); return 0; }", TABLE_CONTRACT),
    "maybe_null_record_argument": (TABLE + "static void table_free(Table *t) { if (t) table_release(t); } void table_dispose(Table *t) { table_free(t); }", TABLE_CONTRACT + " (function table_dispose (owns 0 heap) (nullable 0))"),
})
NEGATIVE.update({
    "null_record_release_forgets_field": (TABLE + "void table_free(Table *t) { if (t) free(t); }", TABLE_CONTRACT, "unreleased owning field"),
    "maybe_null_record_to_nonnull": (TABLE + "void table_dispose(Table *t) { table_release(t); }", TABLE_CONTRACT.replace(" (function table_free (owns 0 heap) (nullable 0))", "") + " (function table_dispose (owns 0 heap) (nullable 0))", "precise non-null record"),
})

# One element selected from a counted buffer retains that buffer as its
# lifetime origin.  The callee receives exactly one complete plain-data object;
# bounds remain the caller's explicit C guard.
ELEMENT_VEC = "#include <stddef.h>\ntypedef struct { int *data; size_t len; } ElementVec; "
ELEMENT_RECORD = "(record ElementVec (field data (borrow write nullable) (count len)))"
ELEMENT_FUNCTIONS = " (function read_element (element 0)) (function write_element (element 0)) (function keep_element (element 0) (returns-borrow 0))"
POSITIVE.update({
    "element_exact_object": (
        "static int read_element(const int *p) { return *p; } int f(void) { int n=7; return read_element(&n); }",
        "(function read_element (element 0))"),
    "element_counted_selection": (
        ELEMENT_VEC +
        "static int read_element(const int *p) { return *p; } "
        "static void write_element(int *p, int value) { *p=value; } "
        "static const int *keep_element(const int *p) { return p; } "
        "int f(ElementVec *v, size_t i) { if (!v->data || i >= v->len) return 0; "
        "write_element(&v->data[i], 7); const int *held=keep_element(&v->data[i]); "
        "return read_element(held); }",
        ELEMENT_RECORD + ELEMENT_FUNCTIONS),
    "element_return_exact_object": (
        "static const int *keep_element(const int *p) { return p; } "
        "int f(void) { int n=9; return *keep_element(&n); }",
        "(function keep_element (element 0) (returns-borrow 0))"),
    "takes_nested_record": (
        "#include <stdlib.h>\n"
        "typedef struct { int *data; } Leaf; "
        "typedef struct { Leaf leaf; int *top; } Nest; "
        "static void nested_free(Nest *n) { free(n->leaf.data); free(n->top); }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record Leaf (field data (owns heap nullable))) "
        "(record Nest (field top (owns heap nullable))) "
        "(function nested_free (takes-record 0))"),
})
NEGATIVE.update({
    "element_uncounted_interior": (
        "#include <stddef.h>\nstatic int read_element(const int *p) { return *p; } "
        "int f(size_t i) { int a[2]={1,2}; return read_element(&a[i]); }",
        "(function read_element (element 0))", "exact object or a counted-buffer origin"),
    "element_nullable_argument": (
        "static int read_element(const int *p) { return *p; } int f(void) { return read_element(0); }",
        "(function read_element (element 0))", "non-null selected object"),
    "element_reaches_second_object": (
        "static int read_second(const int *p) { return p[1]; }",
        "(function read_second (element 0))", "constant subscript exceeds"),
    "element_reference_bearing": (
        "typedef struct { int *value; } Cell; static int read_cell(const Cell *p) { return *p->value; }",
        "(function read_cell (element 0))", "complete plain-data object"),
    "element_incomplete": (
        "struct Cell; static int present(const struct Cell *p) { return p != 0; }",
        "(function present (element 0))", "complete plain-data object"),
    "element_nullable_contract": (
        "static int maybe(const int *p) { return p ? *p : 0; }",
        "(function maybe (element 0) (nullable 0))", "element is a distinct nonnull borrow"),
    "element_origin_laundered": (
        ELEMENT_VEC +
        "static int ordinary(const int *p) { return *p; } "
        "static int selected(const int *p) { return ordinary(p); } "
        "int f(ElementVec *v, size_t i) { if (!v->data || i >= v->len) return 0; "
        "return selected(&v->data[i]); }",
        ELEMENT_RECORD + " (function selected (element 0))", "must retain that origin"),
    "takes_nested_record_partial": (
        "#include <stdlib.h>\n"
        "typedef struct { int *data; } Leaf; "
        "typedef struct { Leaf leaf; int *top; } Nest; "
        "static void nested_free(Nest *n) { free(n->top); }",
        "(allocator heap malloc free (obligation \"malloc/free\")) "
        "(record Leaf (field data (owns heap nullable))) "
        "(record Nest (field top (owns heap nullable))) "
        "(function nested_free (takes-record 0))",
        "unreleased owning field"),
    "takes_record_read_only": (
        "typedef struct { int *data; } Leaf; static void inspect(const Leaf *p) { (void)p; }",
        "(record Leaf (field data (borrow read))) (function inspect (takes-record 0))",
        "writable pointer"),
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
    parser.add_argument("--checkpoints",type=Path)
    args = parser.parse_args()
    BINARY = args.cesta
    assert set(args.case) <= POSITIVE.keys() | NEGATIVE.keys(),args.case
    count = 0
    reused = 0
    if args.checkpoints: args.checkpoints.mkdir(parents=True,exist_ok=True)
    identity=hashlib.sha256(BINARY.read_bytes()+Path(__file__).read_bytes()).hexdigest()
    tested = {True:set(),False:set()}
    with tempfile.TemporaryDirectory(prefix="ownership-",dir=ROOT/"build") as temporary:
        directory = Path(temporary)
        for compiler in (("gcc",) if args.quick else ("gcc","clang")):
            version=run([compiler,"--version"])
            assert version.returncode==0,version.stderr
            for accepted, cases in ((True,POSITIVE),(False,NEGATIVE)):
                for name, case in cases.items():
                    if args.case and name not in args.case:
                        continue
                    if args.quick and not args.case and name not in {"fields","returned_field","heap_move","overlapping_mutable","return_local","wrong_allocator","returned_view","owning_view_move","view_too_long","view_length_update"}:
                        continue
                    tested[accepted].add(name)
                    source, contracts, *needle = case
                    fingerprint=hashlib.sha256((identity+compiler+version.stdout+version.stderr+
                        json.dumps([name,source,contracts,needle,accepted])).encode()).hexdigest()
                    checkpoint=args.checkpoints/(compiler+'-'+name+'.json') if args.checkpoints else None
                    previous={}
                    if checkpoint and checkpoint.is_file():
                        try:previous=json.loads(checkpoint.read_text())
                        except (OSError,ValueError):pass
                    if previous.get('fingerprint')==fingerprint and previous.get('checks')==2:
                        count+=2;reused+=1
                        continue
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
                    if checkpoint:
                        checkpoint.write_text(json.dumps(dict(fingerprint=fingerprint,checks=2,
                            case=name,compiler=compiler,accepted=accepted),indent=2)+'\n')
    assert count > 0, "no requested ownership controls ran"
    args.output.parent.mkdir(parents=True,exist_ok=True)
    compilers = ["gcc"] if args.quick else ["gcc","clang"]
    args.output.write_text(json.dumps({"checks":count,"positive":len(tested[True]),"negative":len(tested[False]),
        "compilers":compilers,"paths":["compile","export"],"reused_cases":reused},indent=2)+"\n")
    print(f"ownership: {count} compiler/export checks; {len(tested[True])} accepted and {len(tested[False])} refused programs under {'/'.join(compilers)}")


if __name__ == "__main__":
    main()
