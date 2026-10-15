#define OBJ_LIKE      (1-1)
#define FUNC_LIKE(a)   ( a )
#define OBJ_LIKE    (0)     // different token sequence
#define OBJ_LIKE    (1 - 1) // different white space
#define FUNC_LIKE(b) ( a ) // different parameter usage
#define FUNC_LIKE(b) ( b ) // different parameter spelling
