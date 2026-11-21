#include "table.h"
typedef struct { const unsigned *first; } Peek;
Peek peek(const Table *t) { return (Peek){t->cells}; }
