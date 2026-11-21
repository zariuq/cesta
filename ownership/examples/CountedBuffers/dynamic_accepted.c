#include "alloc.h"
unsigned sum_squares(size_t n) {
    unsigned *a = malloc(n * sizeof *a);
    if (!a) { free(a); return 0; }
    memset(a, 0, n * sizeof *a);
    for (size_t i = 0; i < n; i++) a[i] = (unsigned)(i * i);
    unsigned s = 0;
    for (size_t i = 0; i < n; i++) s += a[i];
    free(a);
    return s;
}
int same_prefix(const unsigned *x, const unsigned *y, size_t n) {
    unsigned *b = calloc(n, sizeof *b);
    if (!b) { free(b); return 0; }
    for (size_t i = 0; i < n; i++) b[i] = (unsigned)i;
    int same = memcmp(b, b, n * sizeof *b) == 0;
    free(b);
    (void)x; (void)y;
    return same;
}
Table *empty_table(void) {
    Table *t = calloc(1, sizeof *t);
    return t;
}
