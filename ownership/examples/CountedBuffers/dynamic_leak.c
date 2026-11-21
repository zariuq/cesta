#include "alloc.h"
unsigned leak(size_t n) { unsigned *a = malloc(n * sizeof *a); if (!a) { free(a); return 0; } a[0] = 1; return a[0]; }
