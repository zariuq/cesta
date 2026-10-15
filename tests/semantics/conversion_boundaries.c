enum extended_unsigned { small_unsigned = 8u, large_unsigned = 0x80000000u };
enum extended_signed { small_signed = 3, negative_signed = -1, large_signed = 0x80000000u };
enum initializer_order { earlier = 1, later = earlier + 1, wide = 0x80000000u };
_Static_assert(sizeof(earlier) == sizeof(unsigned), "completed enum type");
_Static_assert(later == 2, "point-of-declaration initializer value");
struct Fields { unsigned narrow:3; unsigned full:32; int signed_narrow:3; };
static _Atomic(unsigned) state;
static _Atomic(struct Fields *) published = ((void *)0);
unsigned conditional_fields(struct Fields *fields, int condition) {
    return (condition ? fields->narrow : 0u) +
           (condition ? fields->full : 0u) +
           (condition ? fields->signed_narrow : 0u);
}
void atomic_store(unsigned value, struct Fields *fields) {
    state = value;
    state = small_signed;
    state += value;
    published = fields;
}
void qualified(char *const *outer, const char *inner, void **other);
void pointer_conversions(char **source) { qualified(source, *source, (void **)source); }
void array_qualifiers(const char (*rows)[4]);
void array_conversion(char rows[2][4]) { array_qualifiers(rows); }
unsigned enum_values(void) { return small_unsigned + earlier + later; }
