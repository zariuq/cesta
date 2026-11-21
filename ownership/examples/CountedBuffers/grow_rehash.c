#include "alloc.h"
/* growth by rehash: fresh storage, the elements copied, the old storage
   released, the fresh storage stored back (the plan's replace) */
int grow(Table *t) {
    size_t cap = t->cap ? t->cap * 2 : 8;
    unsigned *fresh = calloc(cap, sizeof *fresh);
    if (!fresh) return 0;
    for (size_t i = 0; i < t->used; i++) fresh[i] = t->cells[i];
    free(t->cells);
    t->cells = fresh;
    t->cap = cap;
    return 1;
}
