#include "alloc.h"
/* a push in the shape a generated worklist push has: failure kept in a flag */
int push(Table *t, unsigned v) {
    int accepted = 1;
    if (t->used == t->cap) {
        size_t cap = t->cap ? t->cap * 2 : 8;
        unsigned *moved = realloc(t->cells, cap * sizeof *moved);
        if (!moved) accepted = 0;
        else { t->cells = moved; t->cap = cap; }
    }
    if (accepted) t->cells[t->used++] = v;
    return accepted;
}
