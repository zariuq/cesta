CESTA ?= ../build/cesta
CFLAGS ?= -O2 -g0 -std=gnu11 -Wall -Wextra -Werror
BUILD ?= ../build/examples/make
export CESTA_BUILD_DIR = $(BUILD)/generated
PROGRAMS = unsigned-array malloc-list function-pointers bitfields-designated generic-static-assert gnu-statement-expression
TARGETS = $(addprefix $(BUILD)/,$(PROGRAMS))
OBJECTS = $(addsuffix .o,$(TARGETS))

.PHONY: all run
all: $(TARGETS)

include ../cesta.mk

$(BUILD)/%.o: %.csexp $(CESTA)
	mkdir -p $(BUILD)
	$(CESTA) cc --cc $(CESTA_NATIVE_CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c $< -o $@

$(TARGETS): %: %.o
	$(CESTA) cc --cc $(CESTA_NATIVE_CC) $(LDFLAGS) $< $(LDLIBS) -o $@

run: all
	set -e; $(foreach program,$(TARGETS),$(program);)

-include $(addsuffix .d,$(TARGETS))
