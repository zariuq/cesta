#include "table.h"
#include <stdlib.h>
static int fill(Table *t) { return push_u(&t->cells, &t->used, &t->cap, 1); }
int owner(void) {
    Table t = {0};
    int ok = fill(&t);   /* fill may allocate t.cells; this model would still say null */
    return ok;
}
