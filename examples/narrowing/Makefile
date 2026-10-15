.DEFAULT_GOAL := narrow
CC ?= cc
CESTA ?= ../../build/cesta
CESTA_BUILD_DIR ?= ../../build/narrowing-example-generated
export CESTA_BUILD_DIR
CFLAGS ?= -std=gnu11 -O2 -g0 -Wall -Wextra -Werror
CPPFLAGS += -MMD -MP
include ../../cesta.mk

narrow: narrow.o
	$(CC) $(CFLAGS) narrow.o -o $@

exported.c: narrow.cesta ../../macros/Narrowing.module
	$(CESTA) expand --cc $(CC) $< -o $@ -- $(filter-out -MMD -MP,$(CPPFLAGS)) $(CFLAGS)

exported: exported.c
	$(CC) $(CFLAGS) $< -o $@

-include narrow.d
