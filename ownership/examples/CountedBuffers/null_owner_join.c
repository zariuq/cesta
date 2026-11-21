#include "alloc.h"
/* one branch knows the allocation null, the other uses it: after the join it
   is one nullable owner, released once */
int probe(size_t n) {
    unsigned *a = malloc((n ? n : 1) * sizeof *a);
    int ok;
    if (!a) ok = 0;
    else { a[0] = 1; ok = 1; }
    free(a);
    return ok;
}
