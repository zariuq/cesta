/* Data-model sensitive expressions, layouts and constants. */
enum small { S0, S1 = 7 };
enum negative { N0 = -1, N1 = 3 };
struct mixed { char c; long l; long long ll; double d; short s; void *p; long double ld; };
struct nested { struct mixed m[2]; int tail[3]; char after; };
struct bit_mixed { unsigned char a:1; unsigned int b:1; unsigned char c:1; char tail; };
struct bit_shared { unsigned int a:3; unsigned int b:5; char tail; };
struct bit_zero { unsigned char a:1; unsigned int :0; unsigned char b:1; char tail; };
struct bit_zero_first { unsigned int :0; char tail; };
struct bit_full { unsigned int a:31; unsigned int b:2; char tail; };
union bit_union { unsigned int a:1; unsigned char b:1; };
union u { long l; char bytes[10]; double d; };
typedef long (*handler)(unsigned long, int);
typedef unsigned int native_word __attribute__((mode(word)));
typedef unsigned int native_pointer __attribute__((mode(pointer)));
static long target(unsigned long a, int b) { return (long)a + b; }
unsigned long f(int i, unsigned u, long l, unsigned long ul, long long ll, unsigned long long ull,
                short s, unsigned char uc, float fl, double d, long double ld, char *p, char *q,
                struct mixed *m, enum small e, enum negative n) {
    unsigned long r = 0;
    r += i + u;            /* unsigned int */
    r += l + u;            /* long on LP64, unsigned long on ILP32 and LLP64 */
    r += ul + ll;          /* unsigned long on LP64, long long elsewhere: size-dependent */
    r += ll + u;
    r += ull - l;
    r += s * uc;
    r += (unsigned long)(fl + d);
    r += (unsigned long)(d * ld);
    r += (unsigned long)(p - q);   /* ptrdiff_t */
    r += sizeof(struct mixed) + _Alignof(struct mixed) + sizeof(union u) + sizeof(struct nested);
    r += sizeof(__builtin_va_list);
    r += sizeof(native_word) + sizeof(native_pointer);
    r += (native_word)1 + (native_pointer)2;
    r += sizeof m->ld + sizeof(handler) + sizeof(L'x') + sizeof(L"wide");
    r += e + n + (e < n) + (u < l) + (l < ul);
    r += (i ? l : u) + (i ? ul : ll);
    handler h = target;
    r += (unsigned long)h(ul, i);
    r += 'a' + L'b' + u'c' + U'd';
    r += 0x7fffffff + 0xffffffff + 4294967296 + 0x100000000;
    return r;
}
