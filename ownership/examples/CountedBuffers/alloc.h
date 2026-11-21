#include <stddef.h>
#include <stdlib.h>
#include <string.h>
typedef struct { unsigned *cells; size_t used, cap; } Table;
