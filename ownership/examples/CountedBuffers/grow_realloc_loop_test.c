#include "alloc.h"
/* realloc's result tested by a loop condition: the edge that finds it null
   leaves the loop with no place to restore the buffer; test it with if */
int grow(Table *t) {
    size_t cap = t->cap ? t->cap * 2 : 8;
    unsigned *moved = realloc(t->cells, cap * sizeof *moved);
    while (moved) {
        t->cells = moved;
        t->cap = cap;
        return 1;
    }
    return 0;
}
