#define foo foo
#define a a b
#define x y
#define y x
#define f(x) f(x) g
#define g f
foo a x y f(1) f(f)(2)
#define AA BB
#define BB AA
AA BB
