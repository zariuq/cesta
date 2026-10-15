#pragma GCC diagnostic push
#pragma weak foo
#define P _Pragma("GCC dependency \"pragmas.c\"")
int a;
_Pragma("omp parallel")
#define X 1
#pragma push_macro("X")
#undef X
#define X 2
X
#pragma pop_macro("X")
X
#pragma STDC FP_CONTRACT ON
#define EMPTY
#pragma EMPTY foo
#ident "version 1"
int b; P
