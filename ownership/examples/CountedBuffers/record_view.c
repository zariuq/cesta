#include "table.h"
typedef struct { const unsigned *cells; size_t n; } Window;
Window window(const Table *t) { return (Window){t->cells, t->used}; }
unsigned window_sum(const Table *t) {
    Window w = window(t);
    unsigned s = 0;
    for (size_t i = 0; i < w.n; i++) s += w.cells[i];
    return s;
}
