#include "table.h"
unsigned pick(const Table *t, size_t i) { return t->cells[i]; }
void put(Table *t, size_t i, unsigned v) { t->cells[i] = v; }
unsigned sum(const Table *t) {
    unsigned s = 0;
    for (size_t i = 0; i < t->used; i++) s += t->cells[i];
    return s;
}
const unsigned *at(const Table *t, size_t i) { return &t->cells[i]; }
unsigned last(const unsigned *a, size_t n) { return n ? a[n - 1] : 0; }
unsigned via(const Table *t) { return last(t->cells, t->used); }
unsigned tail(const Table *t, size_t k) { const unsigned *p = t->cells + k; return p[0]; }
int add(Table *t, unsigned v) { return push_u(&t->cells, &t->used, &t->cap, v); }
int add_twice(Table *t, unsigned v) {
    unsigned before = t->used ? t->cells[0] : v;   /* a value, not a pointer, crosses the growth */
    return push_u(&t->cells, &t->used, &t->cap, before) && push_u(&t->cells, &t->used, &t->cap, v);
}
unsigned lookup(const Map *m, unsigned key) {
    for (size_t i = 0; i < m->n; i++) if (m->pairs[i].key == key) return m->pairs[i].value;
    return 0;
}
unsigned seen_at(const Map *m, size_t i) { return m->seen[i]; }
