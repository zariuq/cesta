#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

enum Phase { FRESH, RUNNING, READY, FAILED };
typedef struct {
    uint64_t id, value;
    enum Phase phase;
    unsigned effects, mode;
} Call;
typedef struct {
    uint64_t id, call, reader, deadline;
    bool pending, notified;
} Wake;
typedef struct {
    Call call;
    Wake wakes[4];
    uint64_t clock, next;
    unsigned notifications;
    bool deny_park, dishonest_park;
} Machine;
typedef struct {
    Machine *machine;
    uint64_t id, reader, answer;
} CallOnceContext;

static bool call_once_identity(CallOnceContext *c)
{
    return c->id != 0 && c->machine->call.id == c->id;
}
static bool call_once_result_ready(CallOnceContext *c)
{
    return call_once_identity(c) && c->machine->call.phase == READY;
}
static bool call_once_ready(CallOnceContext *c)
{
    if (!call_once_result_ready(c)) return false;
    c->answer = c->machine->call.value;
    return true;
}
static bool call_once_has_permit(CallOnceContext *c)
{
    return call_once_identity(c) && c->machine->call.phase == FRESH;
}
static bool call_once_permit(CallOnceContext *c) { return call_once_has_permit(c); }
static bool call_once_perform(CallOnceContext *c)
{
    if (!call_once_has_permit(c)) return false;
    Call *call = &c->machine->call;
    call->phase = RUNNING;
    ++call->effects;
    if (call->mode == 0) call->phase = READY;
    if (call->mode == 1) call->phase = FAILED;
    return call_once_ready(c);
}
static bool call_once_has_failed(CallOnceContext *c)
{
    return call_once_identity(c) && c->machine->call.phase == FAILED;
}
static bool call_once_failed(CallOnceContext *c) { return call_once_has_failed(c); }
static bool call_once_wake_registered(CallOnceContext *c)
{
    if (!call_once_identity(c)) return false;
    Machine *m = c->machine;
    for (unsigned i = 0; i < 4; ++i) {
        Wake *w = &m->wakes[i];
        if (w->pending && w->call == c->id && w->reader == c->reader &&
            w->deadline > m->clock) return true;
    }
    return false;
}
static bool call_once_park(CallOnceContext *c)
{
    Machine *m = c->machine;
    if (!call_once_identity(c) || m->call.phase != RUNNING || m->deny_park) return false;
    if (m->dishonest_park) return true;
    if (call_once_wake_registered(c)) return true;
    if (m->clock > UINT64_MAX - 3 || m->next == UINT64_MAX) return false;
    for (unsigned i = 0; i < 4; ++i) {
        Wake *w = &m->wakes[i];
        if (!w->pending && !w->notified) {
            *w = (Wake){.id = ++m->next, .call = c->id, .reader = c->reader,
                .deadline = m->clock + 3, .pending = true};
            return true;
        }
    }
    return false;
}

#define call_once_run_CHECKED CHECKED
#define call_once_run_CHECK_FAILURE(ctx, kind, id, expected) _Exit(83)
#include "once.h"

static void check(bool condition) { if (!condition) abort(); }
static void notify(Machine *m, Wake *w)
{
    if (!w->pending) return;
    w->pending = false;
    w->notified = true;
    ++m->notifications;
}
static void tick(Machine *m)
{
    check(m->clock != UINT64_MAX);
    ++m->clock;
    for (unsigned i = 0; i < 4; ++i)
        if (m->wakes[i].deadline <= m->clock) notify(m, &m->wakes[i]);
}
static bool complete(Machine *m, uint64_t id, uint64_t value)
{
    if (m->call.id != id || m->call.phase != RUNNING) return false;
    m->call.value = value;
    m->call.phase = READY;
    for (unsigned i = 0; i < 4; ++i)
        if (m->wakes[i].call == id) notify(m, &m->wakes[i]);
    return true;
}
static bool acknowledge(Machine *m, uint64_t id)
{
    for (unsigned i = 0; i < 4; ++i)
        if (m->wakes[i].id == id && m->wakes[i].notified) {
            m->wakes[i].notified = false;
            return true;
        }
    return false;
}

int main(int argc, char **argv)
{
    (void)argv;
    if (argc > 1) {
        Machine m = {.call = {.id = 1, .phase = RUNNING}, .dishonest_park = true};
        CallOnceContext c = {.machine = &m, .id = 1, .reader = 1};
        return call_once_run(&c) == call_once_run_suspended ? 0 : 2;
    }
    for (unsigned seed = 1; seed <= 128; ++seed) {
        for (unsigned mode = 0; mode < 4; ++mode) {
            Machine m = {.call = {.id = seed, .value = 71u * seed, .mode = mode}};
            for (unsigned reader = 0; reader < 4; ++reader) {
                CallOnceContext c = {.machine = &m, .id = seed, .reader = reader};
                call_once_run_outcome result = call_once_run(&c);
                check(m.call.effects == 1);
                check(result == (mode == 0 ? call_once_run_answered :
                    mode == 1 ? call_once_run_raised : call_once_run_suspended));
                if (mode == 0) check(c.answer == 71u * seed);
                if (mode >= 2) check(call_once_wake_registered(&c));
            }
            if (mode < 2) continue;
            CallOnceContext excess = {.machine = &m, .id = seed, .reader = 9};
            check(call_once_run(&excess) == call_once_run_resource_failure);
            check(m.call.effects == 1);
            if (mode == 2) {
                tick(&m); tick(&m);
                check(m.notifications == 0);
                tick(&m);
                check(m.notifications == 4);
            }
            check(!complete(&m, seed + 1, 999));
            check(complete(&m, seed, 97u * seed));
            check(!complete(&m, seed, 999));
            check(m.notifications == 4 && m.call.effects == 1);
            tick(&m); tick(&m); tick(&m); tick(&m);
            check(m.notifications == 4);
            for (unsigned i = 0; i < 4; ++i) {
                uint64_t wake = m.wakes[i].id;
                check(acknowledge(&m, wake) && !acknowledge(&m, wake));
                CallOnceContext c = {.machine = &m, .id = seed, .reader = i};
                check(call_once_run(&c) == call_once_run_answered && c.answer == 97u * seed);
                check(m.call.effects == 1);
            }
        }
    }
    Machine exhausted = {.call = {.id = 1, .phase = RUNNING}, .clock = UINT64_MAX - 2};
    CallOnceContext c = {.machine = &exhausted, .id = 1, .reader = 1};
    check(call_once_run(&c) == call_once_run_resource_failure);
    exhausted.clock = 0; exhausted.next = UINT64_MAX;
    check(call_once_run(&c) == call_once_run_resource_failure);
    return 0;
}
