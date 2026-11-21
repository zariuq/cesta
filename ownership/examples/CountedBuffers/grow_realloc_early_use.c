#include "alloc.h"
/* the buffer is reached through its field before realloc's result is stored back */
int grow(Table *t) {
    unsigned *moved = realloc(t->cells, 16 * sizeof *moved);
    if (!moved) return 0;
    unsigned first = t->cells[0];
    t->cells = moved;
    return (int)first;
}
