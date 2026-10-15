/* Name identity crosses UTF-8, short UCN and long UCN spellings. Stringified
   arguments keep their written spelling, including through a second macro. */
#define \u03b1 7
int a = α;
#if defined(α) && defined(\U000003b1)
int b = 1;
#else
#error Unicode macro lookup failed
#endif
#undef α
#ifdef \u03b1
#error Unicode macro undefinition failed
#endif
#define β 9
int c = \u03b2;
#define F(\u03b1, β) (α + \U000003b2)
int d = F(1, 2);
#define CAT(a,b) a ## b
#define preα 11
int e = CAT(pre, \u03b1);
int f = CAT(pre, α);
#define STR(x) #x
#define STR2(x) STR(x)
const char *g = STR(\u03b1);
const char *h = STR(α);
#define SPELL \u03b3
const char *i = STR2(SPELL);
#define SPELL γ
const char *j = STR2(SPELL);
#define \U00010400 13
int k = 𐐀;
int \u03b4 = 2;
int *p = &δ;
#define SELF(ε) ε
int l = SELF(SELF(3));
#define λ λ
int λ = 4;
#undef λ
#define μ 1
#pragma push_macro("μ")
#undef μ
#define \u03bc 2
int m = μ;
#pragma pop_macro("μ")
int n = \u03bc;
