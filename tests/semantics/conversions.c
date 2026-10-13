/* Conversions whose wrong elaboration would change values. */
long widen(int i, unsigned u, short s, unsigned char c) { return i + u + s + c; }
unsigned long sign(long l, int i) { unsigned long r = l; r += i; return r; }
int narrow(long l, unsigned long ul) { int r = l; r += ul; return r; }
short shorten(int i) { short s = i; return s; }
double floating(long l, unsigned long ul, float f) { return l + ul + f; }
long divide(int a, unsigned b, long c) { return a / b + c % a; }
unsigned long mixed(unsigned u, long l, long long ll) { return (u < l) + (unsigned long)(ll >> u) + (l ? u : ll); }
int compare(int a, unsigned b) { return a < b; }
long arguments(long (*f)(long, unsigned), int i, short s) { return f(i, s); }
char store(char *p, int v) { *p = v; return *p + 1; }
