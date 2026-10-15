#define X 9
#define E
#define V(a,...) a,##__VA_ARGS__ end
#define NAMED(fmt, args...) printf(fmt, ## args)
#define OPT(a,...) f(a __VA_OPT__(,) __VA_ARGS__)
#define ONLY(...) [__VA_ARGS__]
V(1) V(1,2) V(1,) V(1,X) NAMED("a") NAMED("a", 1, 2) OPT(1) OPT(1,) OPT(1,E) OPT(1,2,3) ONLY() ONLY(1,2)
