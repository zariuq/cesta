#include "alloc.h"
/* the reallocation took the slot's buffer, and the moved storage is released
   instead of stored back */
static int push(void *(*resize)(void *, size_t), unsigned **buffer, size_t *length,
                size_t *capacity, unsigned value) {
    (void)length; (void)value;
    unsigned *moved = resize(*buffer, 2 * *capacity * sizeof *moved);
    if (!moved) return 0;
    free(moved);
    return 1;
}
int fill(Table *t) { return push(realloc, &t->cells, &t->used, &t->cap, 1); }
