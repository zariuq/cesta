#include "alloc.h"
/* an allocation known null owns nothing: returning on that edge leaks nothing */
int fill(size_t n) {
    unsigned *a = malloc((n ? n : 1) * sizeof *a);
    if (!a) return 0;
    for (size_t i = 0; i < n; i++) a[i] = (unsigned)i;
    free(a);
    return 1;
}
