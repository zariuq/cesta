#include "alloc.h"
void *arena_alloc(size_t);
void *arena_resize(void *, size_t);
void arena_free(void *);
/* the caller passes another family's reallocation for heap's */
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
int fill(Table *t) { return push(arena_resize, &t->cells, &t->used, &t->cap, 1); }
