#define OBJ (1)
#define PAREN (x)
#define EMPTY_PARAMS() 0
#define ONLY_VARIADIC(...) __VA_ARGS__
#define LIST_VARIADIC(a, b, ...) a b __VA_ARGS__
#define NAMED(args...) args
#define NAMED_AFTER(fmt, args...) fmt args
#define EMPTY
#define S(x) #x
#define P(a,b) a##b
#define DIGRAPH(a,b) a%:%:b
#undef EMPTY
