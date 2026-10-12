CC ?= cc
CPPFLAGS ?=
CFLAGS ?= -O2 -g
WARN = -std=c11 -Wall -Wextra -Wpedantic -Werror
REFERENCE_SYNTAX ?= ../hyperon/MeTTaIL/GSLT/src/main/bnfc/metta_venus.cf
REFERENCE_FIXTURES ?= ../hyperon/MeTTaIL/GSLT/src/test/module
CETTA_SOURCE ?= ../hyperon/CeTTa/src
C11PARSER_TESTS ?= ../repos/C11parser/tests
LIBSOURCES = src/base.c src/atom.c src/wire.c src/grammar.c src/dfa.c src/forest.c src/lr.c src/scope.c src/tree.c src/module.c src/compile.c src/metadata.c src/view.c
SOURCES = $(LIBSOURCES) src/main.c

.PHONY: all test test-reference test-c test-examples test-driver test-literal-bytes
all: build/cesta

build/cesta-bootstrap: $(SOURCES) src/cesta.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) $(SOURCES) -o $@

build/embed: tools/embed.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) $< -o $@

build/language-data.c: build/embed build/C11.cf build/CGNU.cf grammar/TheoryModule.cf grammar/C11.profile grammar/CGNU.profile grammar/CGNUC11.profile presentations/CompactC.module presentations/CompactC2.module presentations/CMeraStyle.module
	./build/embed C11Grammar build/C11.cf CGNUGrammar build/CGNU.cf TheoryModuleSyntax grammar/TheoryModule.cf C11Profile grammar/C11.profile CGNUProfile grammar/CGNU.profile CGNUC11Profile grammar/CGNUC11.profile CompactCModule presentations/CompactC.module CompactC2Module presentations/CompactC2.module CMeraStyleModule presentations/CMeraStyle.module > $@

build/cesta: $(SOURCES) src/cesta.h src/driver.c src/view_driver.c build/language-data.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) -Isrc -DCE_BUILTIN_LANGUAGES $(SOURCES) src/driver.c src/view_driver.c build/language-data.c -o $@

build/test-foundation: tests/foundation.c $(LIBSOURCES) src/cesta.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/foundation.c $(LIBSOURCES) -o $@

test: build/cesta build/test-foundation
	./build/test-foundation

build/test-modules: tests/modules.c $(LIBSOURCES) src/cesta.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/modules.c $(LIBSOURCES) -o $@

test-reference: build/test-modules
	./build/test-modules $(REFERENCE_SYNTAX) $(REFERENCE_FIXTURES)

test: test-reference

.PHONY: test-modules test-workbench
test-modules: build/test-modules grammar/TheoryModule.cf
	./build/test-modules grammar/TheoryModule.cf

test-workbench: build/cesta
	python3 tests/workbench.py

test: test-modules test-workbench

build/C11.cf: build/cesta-bootstrap grammar/CLex.module grammar/CExpr.module grammar/CDecl.module grammar/CStmt.module grammar/CExternal.module grammar/TheoryModule.cf
	./build/cesta-bootstrap module-grammar grammar/TheoryModule.cf grammar/CExternal.module C11 > $@

build/test-c-scope: tests/c_scope.c $(LIBSOURCES) src/cesta.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/c_scope.c $(LIBSOURCES) -o $@

test-c: build/C11.cf build/test-c-scope

test: test-c

build/CGNU.cf: build/cesta-bootstrap build/C11.cf grammar/GNU.module grammar/TheoryModule.cf
	./build/cesta-bootstrap module-grammar grammar/TheoryModule.cf grammar/GNU.module CGNU > $@

test-c: build/CGNU.cf
	./build/test-c-scope build/C11.cf grammar/C11.profile
	./build/test-c-scope build/CGNU.cf grammar/CGNU.profile

test-examples: build/cesta build/C11.cf build/CGNU.cf
	python3 tests/examples.py

test: test-examples

test-driver: build/cesta
	python3 tests/driver.py

test: test-driver

test-literal-bytes: build/cesta
	python3 tests/literal_bytes.py

test: test-literal-bytes

.PHONY: test-c-printer
test-c-printer: build/cesta
	python3 -B tests/c_printer.py

test: test-c-printer

build/test-grammar-trees: tests/grammar_trees.c $(LIBSOURCES) src/cesta.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/grammar_trees.c $(LIBSOURCES) -o $@

.PHONY: test-grammar-trees
test-grammar-trees: build/test-grammar-trees build/C11.cf build/CGNU.cf
	./build/test-grammar-trees build/C11.cf grammar/C11.profile 10000
	./build/test-grammar-trees build/CGNU.cf grammar/CGNU.profile 10000

test: test-grammar-trees

.PHONY: test-coverage
test-coverage:
	python3 -B tests/n1570_coverage.py --self-test --report build/audit-coverage/normative_coverage_report.json

test: test-coverage

.PHONY: test-metadata test-petta
test-metadata: build/cesta build/C11.cf build/CGNU.cf
	python3 -B tests/metadata_admission.py $(METADATA_TEST_FLAGS)

test-petta: build/cesta
	python3 -B tests/petta_analysis.py $(PETTA_TEST_FLAGS)

test: test-metadata test-petta

build/syntax-probe: tests/syntax_probe.c $(LIBSOURCES) src/cesta.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/syntax_probe.c $(LIBSOURCES) -o $@

.PHONY: test-mutants
test-mutants: build/syntax-probe build/CGNU.cf
	python3 tests/syntax_mutants.py

test: test-mutants

build/cetta-data-reader: tests/cetta_data_reader.c $(addprefix $(CETTA_SOURCE)/,atom.c symbol.c name_key.c parser.c) $(wildcard $(CETTA_SOURCE)/*.h)
	$(CC) -std=c11 -O2 -g0 -pthread -ffunction-sections -fdata-sections -I$(CETTA_SOURCE) tests/cetta_data_reader.c $(addprefix $(CETTA_SOURCE)/,atom.c symbol.c name_key.c parser.c) -Wl,--gc-sections -lgmp -lm -o $@

.PHONY: test-corpora test-c11parser
test-corpora: build/cesta build/cetta-data-reader build/syntax-probe
	python3 -B tests/corpus_suite.py

test-c11parser: build/cesta
	python3 -B tests/c11parser_oracle.py $(C11PARSER_TESTS)

test: test-corpora test-c11parser

.PHONY: test-lean
test-lean:
	python3 -B tests/lean_contracts.py $(LEAN_TEST_FLAGS)

test: test-lean

build/test-presentations: tests/presentations.c $(LIBSOURCES) src/cesta.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/presentations.c $(LIBSOURCES) -o $@

.PHONY: test-presentations
test-presentations: build/test-presentations build/CGNU.cf
	./build/test-presentations

test: test-presentations

.PHONY: test-presentation-driver
test-presentation-driver: build/cesta
	python3 -B tests/presentation_driver.py

test: test-presentation-driver

build/test-wire: tests/wire.c src/base.c src/atom.c src/wire.c src/cesta.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/wire.c src/base.c src/atom.c src/wire.c -o $@

.PHONY: test-wire
test-wire: build/test-wire
	./build/test-wire

test: test-wire

.PHONY: test-portable-readers
test-portable-readers: build/cesta build/test-wire build/cetta-data-reader
	python3 -B tests/portable_readers.py $(PORTABLE_READER_TEST_FLAGS)

test: test-portable-readers

.PHONY: test-presentation-admission
test-presentation-admission: test-presentations test-presentation-coverage test-lean
	python3 -B tests/presentation_admission.py $(PRESENTATION_TEST_FLAGS)
	python3 -B tests/presentation_admission.py --input-prefix presentation-complete --output build/presentation-complete-admission $(COMPLETE_PRESENTATION_TEST_FLAGS)
	python3 -B tests/presentation_admission.py --input-prefix presentation-cmera-complete --output build/presentation-cmera-complete-admission $(COMPLETE_PRESENTATION_TEST_FLAGS)
	python3 -B tests/presentation_admission.py --input-prefix presentation-cmera-controls --output build/presentation-cmera-controls-admission $(COMPLETE_PRESENTATION_TEST_FLAGS)
	python3 -B tests/presentation_admission.py --input-prefix presentation-compact2-complete --output build/presentation-compact2-complete-admission $(COMPLETE_PRESENTATION_TEST_FLAGS)
	python3 -B tests/presentation_admission.py --input-prefix presentation-compact2-controls --output build/presentation-compact2-controls-admission $(COMPLETE_PRESENTATION_TEST_FLAGS)

test: test-presentation-admission

build/test-presentation-coverage: tests/presentation_coverage.c $(LIBSOURCES) src/cesta.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/presentation_coverage.c $(LIBSOURCES) -o $@

.PHONY: test-presentation-coverage
test-presentation-coverage: build/test-presentation-coverage build/CGNU.cf
	./build/test-presentation-coverage
	./build/test-presentation-coverage presentation-cmera-complete cmera-style/1
	./build/test-presentation-coverage presentation-compact2-complete compact-c/2

test: test-presentation-coverage

build/test-whole-program-trees: tests/whole_program_trees.c $(LIBSOURCES) src/cesta.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARN) tests/whole_program_trees.c $(LIBSOURCES) -o $@

.PHONY: test-whole-program-trees
test-whole-program-trees: build/test-whole-program-trees build/CGNU.cf
	./build/test-whole-program-trees 10000

test: test-whole-program-trees

.PHONY: test-build-dependencies
test-build-dependencies: build/cesta
	python3 -B tests/build_dependencies.py

test: test-build-dependencies

.PHONY: test-compact-examples
test-compact-examples: build/cesta
	python3 -B tests/compact_examples.py

test: test-compact-examples

.PHONY: test-presentation-corpora
test-presentation-corpora: build/cesta build/cetta-data-reader
	python3 -B tests/presentation_qualification.py --reuse $(PRESENTATION_CORPUS_TEST_FLAGS)

test: test-presentation-corpora

.PHONY: test-presentation-semantics
test-presentation-semantics: build/cesta
	python3 -B tests/presentation_semantics.py

test: test-presentation-semantics
