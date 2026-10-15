#define G(a) a
#define F(a,b) __LINE__ G(__LINE__) a b
#define H __LINE__
F(1,
2)
G(
__LINE__)
G(H
)
F(
G(__LINE__),
H)
#line 100 "renamed.c"
__LINE__ __FILE__
