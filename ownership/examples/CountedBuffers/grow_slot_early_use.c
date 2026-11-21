#include "alloc.h"
/* the slot's buffer is read after the reallocation took it and before the
   moved storage is stored back */
static int push(void *(*resize)(void *, size_t), unsigned **buffer, size_t *length,
                size_t *capacity, unsigned value) {
    if (length == capacity || *length >= *capacity) return 0;
    unsigned *moved = resize(*buffer, 2 * *capacity * sizeof *moved);
    if (!moved) return 0;
    unsigned first = (*buffer)[0];
    *buffer = moved;
    *capacity *= 2;
    (*buffer)[(*length)++] = value + first;
    return 1;
}
int fill(Table *t) { return push(realloc, &t->cells, &t->used, &t->cap, 1); }
