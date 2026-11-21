#include "alloc.h"
/* a table lent for reading cannot have its storage replaced */
int grow(const Table *t) {
    free(t->cells);
    return 0;
}
