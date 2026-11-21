#include "table.h"
const unsigned *one(const unsigned *p) { return p; }
unsigned bad(const Table *t) { return *one(t->cells); }
