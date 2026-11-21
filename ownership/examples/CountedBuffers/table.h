#include <stddef.h>
typedef struct { unsigned *cells; size_t used, cap; } Table;
typedef struct { unsigned key, value; } Pair;
typedef struct { Pair *pairs; size_t n; const unsigned *seen; size_t seen_n; } Map;
int push_u(unsigned **buffer, size_t *length, size_t *capacity, unsigned value);
