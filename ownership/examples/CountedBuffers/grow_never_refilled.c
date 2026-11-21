#include "alloc.h"
/* the storage is released and nothing is stored back */
int drop(Table *t) {
    free(t->cells);
    return 0;
}
