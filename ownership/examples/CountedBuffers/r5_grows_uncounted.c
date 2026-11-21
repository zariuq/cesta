#include "table.h"
int add(Table *t, unsigned v) { return push_u(&t->cells, &t->used, &t->cap, v); }
