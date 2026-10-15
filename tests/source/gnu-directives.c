#ident "v1"
#sccs "v2"
#warning careful
#if 0
#elifdef X
#elifndef Y
#endif
# 7 "x.c"
#
#if 0
#! not a directive
#assert machine(x)
#import "none.h"
#include_next <none.h>
#embed "none.bin"
#endif
