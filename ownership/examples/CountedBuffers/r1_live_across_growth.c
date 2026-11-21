#include "table.h"
int bad(Table *t, unsigned v) { unsigned *p = &t->cells[0]; int ok = push_u(&t->cells, &t->used, &t->cap, v); *p = v; return ok; }
