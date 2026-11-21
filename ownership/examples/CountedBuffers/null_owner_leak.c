#include "alloc.h"
/* only the null edge owns nothing: returning on the non-null edge leaks */
int leak(size_t n) {
    unsigned *a = malloc((n ? n : 1) * sizeof *a);
    if (a) return 0;
    free(a);
    return 1;
}
