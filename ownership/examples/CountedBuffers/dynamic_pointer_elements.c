#include "alloc.h"
void names(size_t n, char *s) { char **v = malloc(n * sizeof *v); if (!v) { free(v); return; } v[0] = s; free(v); }
