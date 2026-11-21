#include "alloc.h"
/* growth through a pointer to the buffer's slot: the callee sees the slot as
   a record of one counted field, and calls the family's reallocation through
   its parameter; the caller lends the buffer whole and passes realloc */
static int push(void *(*resize)(void *, size_t), unsigned **buffer, size_t *length,
                size_t *capacity, unsigned value) {
    if (length == capacity || *length > *capacity || (*capacity && !*buffer)) return 0;
    if (*length == *capacity) {
        size_t next = *capacity ? *capacity * 2 : 8;
        unsigned *moved = resize(*buffer, next * sizeof *moved);
        if (!moved) return 0;
        *buffer = moved;
        *capacity = next;
    }
    (*buffer)[(*length)++] = value;
    return 1;
}

int fill(Table *t) {
    for (unsigned v = 0; v < 100; ++v)
        if (!push(realloc, &t->cells, &t->used, &t->cap, v)) return 0;
    return 1;
}
