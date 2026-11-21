#include "alloc.h"
unsigned last(const unsigned *a, size_t n);
unsigned bad(void) { return last(0, 0); }
