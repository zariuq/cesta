#define str(x) #x
#define xstr(x) str(x)
#define F(a) a+1
#define E
xstr(F(2)) xstr( a  +  b ) xstr(E x E) xstr(-F(1)) str("a\n" '\"') xstr(f (y)) str(  leading   and
trailing   ) str(a/**/b)
#define PASTE(a,b) a##b
PASTE(x,) PASTE(,y) PASTE(+,=) PASTE(1,.5e+3) PASTE(<,<=)
