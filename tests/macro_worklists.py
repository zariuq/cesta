"""Growable worklists with checked sizes and explicit allocation failure."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/macro-worklist-tests"
MODULE = ROOT / "macros/Worklists.module"


def run(arguments, cwd=WORK, success=True):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True)
    if (result.returncode == 0) != success:
        raise AssertionError(f"{arguments}\nstatus {result.returncode}\n" +
                             result.stderr.decode(errors="replace")[-8000:])
    return result


def document(*forms):
    return ('(cesta native-c/1 compact-c/2 (unit (include <stddef.h>) (include <stdint.h>) '
    + '(include <stdlib.h>) (use-macros ' + json.dumps(str(MODULE)) + ' Growable) '
    + ' '.join(forms) + '))\n')


DECLARATIONS = r'''
void *resize_checked(void *,size_t);
void *(*choose_allocator(void))(void *,size_t);
int **buffer_slot(void); size_t *length_slot(void); size_t *capacity_slot(void);
int next_value(void);
'''

RUNTIME = r'''
static int allocation_calls, fail_allocation, trace;
static int *ordered_buffer; static size_t ordered_length, ordered_capacity;
void *resize_checked(void *old,size_t bytes) {
    ++allocation_calls;
    return fail_allocation ? NULL : realloc(old,bytes);
}
void *(*choose_allocator(void))(void *,size_t) {trace=trace*10+1;return resize_checked;}
int **buffer_slot(void) {trace=trace*10+2;return &ordered_buffer;}
size_t *length_slot(void) {trace=trace*10+3;return &ordered_length;}
size_t *capacity_slot(void) {trace=trace*10+4;return &ordered_capacity;}
int next_value(void) {trace=trace*10+5;return 33;}
int main(void) {
    int *buffer=NULL;size_t length=0,capacity=0;
    for (int n=0;n<100;++n) if (!push_int(&buffer,&length,&capacity,n*3)) return 1;
    if (length!=100 || capacity<length) return 2;
    for (size_t n=0;n<length;++n) if (buffer[n]!=(int)n*3) return 3;
    int sum=0;while(length) sum+=buffer[--length];
    if(sum!=14850) return 4;
    /* An existing full allocation must survive a failed grow. */
    length=capacity;int *saved=buffer;size_t saved_capacity=capacity;
    int calls=allocation_calls;fail_allocation=1;int value=44;
    if(push_effect(&buffer,&length,&capacity,&value) || value!=45 || buffer!=saved || length!=saved_capacity ||
       capacity!=saved_capacity || allocation_calls!=calls+1) return 5;
    if(buffer[0]!=0 || buffer[99]!=297) return 6;
    fail_allocation=0;calls=allocation_calls;
    length=SIZE_MAX;capacity=SIZE_MAX;
    if(push_int(&buffer,&length,&capacity,44) || buffer!=saved ||
       length!=SIZE_MAX || capacity!=SIZE_MAX || allocation_calls!=calls) return 7;
    capacity=SIZE_MAX/(2*sizeof(int))+1;length=capacity;
    if(push_int(&buffer,&length,&capacity,44) || buffer!=saved ||
       length!=capacity || allocation_calls!=calls) return 8;
    length=3;capacity=2;
    if(push_int(&buffer,&length,&capacity,44) || length!=3 || capacity!=2 ||
       allocation_calls!=calls) return 9;
    length=2;
    if(push_int(&buffer,&length,&length,44) || length!=2 || allocation_calls!=calls) return 10;
    free(buffer);buffer=NULL;length=0;capacity=8;
    if(push_int(&buffer,&length,&capacity,44) || buffer!=NULL || length!=0 ||
       capacity!=8 || allocation_calls!=calls) return 11;
    if(!ordered_push() || trace!=12345 || ordered_length!=1 || ordered_buffer[0]!=33) return 12;
    free(ordered_buffer);
    return 0;
}
'''

REFERENCE = r'''
#define PUSH(E,C,allocator,buffer,length,capacity,value) __extension__ ({ \
    void *(*saved_allocator)(void *,C)=(allocator); E **saved_buffer=&(buffer); \
    C *saved_length=&(length); C *saved_capacity=&(capacity); E saved_value=(value); \
    __extension__ ({ C next_capacity; C bytes; E *replacement; int accepted=1; \
      if(saved_length==saved_capacity || *saved_length>*saved_capacity || \
         (*saved_capacity && !*saved_buffer)) accepted=0; \
      if(accepted && *saved_length==*saved_capacity) { \
        if(*saved_capacity==0) next_capacity=8; \
        else if(__builtin_add_overflow(*saved_capacity,*saved_capacity,&next_capacity)) accepted=0; \
        if(accepted && __builtin_mul_overflow(next_capacity,sizeof(E),&bytes)) accepted=0; \
        if(accepted) { replacement=saved_allocator(*saved_buffer,bytes); \
          if(!replacement) accepted=0; \
          else {*saved_buffer=replacement;*saved_capacity=next_capacity;} } } \
      if(accepted) {(*saved_buffer)[*saved_length]=saved_value;++*saved_length;} accepted; }); })
int push_int(int **buffer,size_t *length,size_t *capacity,int value) {
    return PUSH(int,size_t,resize_checked,*buffer,*length,*capacity,value);
}
int push_effect(int **buffer,size_t *length,size_t *capacity,int *value) {
    return PUSH(int,size_t,resize_checked,*buffer,*length,*capacity,(*value)++);
}
int ordered_push(void) {
    return PUSH(int,size_t,choose_allocator(),*buffer_slot(),*length_slot(),*capacity_slot(),next_value());
}
'''


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source=WORK/"program.cesta"
    source.write_text(document('(native '+json.dumps(DECLARATIONS)+')',
        '(fn int push_int ((int (** buffer)) (size_t (* length)) (size_t (* capacity)) (int value)) '
        '(return (worklist-push int size_t resize_checked (* buffer) (* length) (* capacity) value)))',
        '(fn int push_effect ((int (** buffer)) (size_t (* length)) (size_t (* capacity)) (int (* value))) '
        '(return (worklist-push int size_t resize_checked (* buffer) (* length) (* capacity) (post++ (* value)))))',
        '(fn int ordered_push (void) (return (worklist-push int size_t (call choose_allocator) '
        '(* (call buffer_slot)) (* (call length_slot)) (* (call capacity_slot)) (call next_value))))',
        '(native '+json.dumps(RUNTIME)+')'))
    handwritten=WORK/"handwritten";handwritten.mkdir(exist_ok=True)
    (handwritten/"program.c").write_text('#include <stddef.h>\n#include <stdint.h>\n#include <stdlib.h>\n'
        +DECLARATIONS+REFERENCE+RUNTIME)
    records=[]
    for compiler in ("gcc","clang"):
        work=WORK/compiler;work.mkdir(exist_ok=True);objects=[]
        for optimization in ("-O0","-O2"):
            flags=["-std=gnu11",optimization,"-g0","-Wall","-Wextra","-Werror"]
            actual=work/(optimization+".o");reference=handwritten/(compiler+optimization+".o")
            run([BINARY,"cc","--cc",compiler,*flags,"-c",source,"-o",actual],work)
            run([compiler,*flags,"-c","program.c","-o",reference],handwritten)
            assert actual.read_bytes()==reference.read_bytes(),compiler+optimization+": worklist object differs"
            executable=work/(optimization+"-program")
            run([compiler,actual,"-o",executable],work);run([executable],work)
            objects.append({"optimization":optimization,"sha256":hashlib.sha256(actual.read_bytes()).hexdigest()})
        negatives=[
            ("wrong-value",'(fn int main (void) (var int (* buffer) 0) (var size_t length 0) (var size_t capacity 0) '
             '(return (worklist-push int size_t resize_checked buffer length capacity 1.0)))'),
            ("wrong-buffer",'(fn int main (void) (var double (* buffer) 0) (var size_t length 0) (var size_t capacity 0) '
             '(return (worklist-push int size_t resize_checked buffer length capacity 1)))'),
            ("signed-count",'(native "void *resize_signed(void *p,int n) {return realloc(p,(size_t)n);}") '
             '(fn int main (void) (var int (* buffer) 0) (var int length 0) (var int capacity 0) '
             '(return (worklist-push int int resize_signed buffer length capacity 1)))'),
        ]
        for name,body in negatives:
            path=work/(name+".cesta");path.write_text(document('(native '+json.dumps(DECLARATIONS)+')',body))
            result=run([BINARY,"cc","--cc",compiler,"-std=gnu11","-c",path,"-o",work/(name+".o")],work,success=False)
            expected=b"worklist count must be unsigned" if name=="signed-count" else b"Cesta macro argument type"
            assert expected in result.stderr,result.stderr
        records.append({"compiler":compiler,"objects":objects,"growth_and_lifo":True,
                        "allocation_failure":True,"size_overflow":True,"state_refusals":True,"ordered_once":True})
        print(compiler+": worklist growth, failure preservation, overflow, order and identical objects passed",flush=True)
    (WORK/"summary.json").write_text(json.dumps({"records":records,
        "binary_sha256":hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        "module_sha256":hashlib.sha256(MODULE.read_bytes()).hexdigest()},indent=2)+"\n")


if __name__=="__main__":
    main()
