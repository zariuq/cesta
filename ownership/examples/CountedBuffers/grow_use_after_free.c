#include "alloc.h"
/* the old storage is reached after it was released */
int grow(Table *t) {
    unsigned *fresh = calloc(8, sizeof *fresh);
    if (!fresh) return 0;
    free(t->cells);
    fresh[0] = t->cells[0];
    t->cells = fresh;
    return 1;
}
