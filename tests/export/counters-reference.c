/* Written independently of the exporter: the table as a project writes it. */
typedef enum {
    PROBE_COUNTER_LOOKUP = 0,
    PROBE_COUNTER_CLONE = 1,
    PROBE_COUNTER_REGISTRY_RESOLVE_HIT = 2,
    PROBE_COUNTER_MATCH_INDEX_FREE_BYTES = 3,
    PROBE_COUNTER_COUNT = 4
} ProbeCounter;

static const char *const PROBE_COUNTER_NAMES[PROBE_COUNTER_COUNT] = {
    "lookup",
    "clone",
    "resolve-registry-hit",
    "match-index-free-bytes",
};
