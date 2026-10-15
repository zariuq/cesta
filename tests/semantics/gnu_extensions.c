#ident "Cesta extension controls"
typedef unsigned long Count;
static void release_count(Count *value) { (void)value; }
int attributed_declaration(void) {
    __attribute__((cleanup(release_count))) Count value = 3;
    return (int)value;
}
int labels(int choice) {
    goto named;
named: __attribute__((unused)) __attribute__((cold)) int value = 2;
    switch (choice) {
    case 0: int first = 1; value += first; break;
    case 1 ... 3: int second = 4; value += second; break;
    case 4: __attribute__((fallthrough));
    default: int last = 8; value += last; break;
    }
    return value;
}
struct AlignedTail { int count; _Alignas(16) unsigned char state[]; };
_Static_assert(_Alignof(struct AlignedTail) == 16, "member alignment");
void *frame(void) { return __builtin_frame_address(0); }
void *caller(void) { return __builtin_return_address(0); }
void prefetch(const void *p) {
    __builtin_prefetch(p);
    __builtin_prefetch(p, 0);
    __builtin_prefetch(p, 1, 3);
}
