.DEFAULT_GOAL := square
CESTA ?= ../../build/cesta
CFLAGS ?= -std=gnu11 -O2 -Wall -Wextra -Werror
CPPFLAGS += -MMD -MP
include ../../cesta.mk

square: square.o

-include square.d
