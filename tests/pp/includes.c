#include "local/q.h"
#include <a/h.h>
#include "local/once.h"
#include "local/once.h"
#if __has_include(<a/h.h>) && !__has_include("missing.h") && __has_include("local/q.h")
has_include_works
#endif
#define HEADER <a/h.h>
#include HEADER
#define QUOTED "local/q.h"
#include QUOTED
__INCLUDE_LEVEL__ __COUNTER__ __COUNTER__
