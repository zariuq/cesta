#include "alloc.h"
/* growth by reallocation: the buffer is suspended from realloc until its
   result is stored back; a null result moved nothing */
int grow(Table *t) {
    size_t cap = t->cap ? t->cap * 2 : 8;
    unsigned *moved = realloc(t->cells, cap * sizeof *moved);
    if (!moved) return 0;
    t->cells = moved;
    t->cap = cap;
    return 1;
}
