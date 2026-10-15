.DEFAULT_GOAL := hello
CESTA ?= ../../build/cesta
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror
CPPFLAGS += -MMD -MP
include ../../cesta.mk

hello: hello.o

-include hello.d
