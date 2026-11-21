#include "alloc.h"
void twice(size_t n) { unsigned *a = malloc(n * sizeof *a); free(a); free(a); }
