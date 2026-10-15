#define E
#define F(...) [a , ## __VA_ARGS__ b]
#define G(x,...) [a , ## __VA_ARGS__ b]
1: F() F( ) F(x) F(E)
2: G(1) G(1,) G(1,x) G(1,E)
#define LC(...) _LC(__VA_ARGS__)
#define _LC(...) , ## __VA_ARGS__
3: [LC()] [LC(E)] [LC(x)]
