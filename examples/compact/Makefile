CESTA ?= ../../build/cesta
CFLAGS ?= -std=gnu11 -O3 -g0 -Wall -Wextra -Werror
BUILD ?= ../../build/compact-examples/make
export CESTA_BUILD_DIR = $(BUILD)/generated
PROGRAMS = unsigned-array malloc-list function-pointers bitfields-designated generic-static-assert gnu-statement-expression
TARGETS = $(addprefix $(BUILD)/,$(PROGRAMS))

.PHONY: all
all: $(TARGETS)

include ../../cesta.mk

$(BUILD)/%.o: %.cesta $(CESTA)
	mkdir -p $(BUILD)
	$(CESTA) cc --cc $(CESTA_NATIVE_CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c $< -o $@

$(TARGETS): %: %.o
	$(CC) $(LDFLAGS) $< $(LDLIBS) -o $@

-include $(addsuffix .d,$(TARGETS))
