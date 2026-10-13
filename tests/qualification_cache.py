"""Content-bound receipts for completed test evidence, not semantic proofs."""

import hashlib
import json
import os

ENVIRONMENT = ("PATH", "LD_LIBRARY_PATH", "LD_PRELOAD", "LIBRARY_PATH", "CPATH",
               "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "COMPILER_PATH", "GCC_EXEC_PREFIX",
               "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_COLLATE", "TZ", "SOURCE_DATE_EPOCH")
CONTEXT = ("corpus", "unit", "source", "configuration", "compiler", "flags", "cwd")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def context(unit):
    return {key: unit[key] for key in CONTEXT}


def environment():
    return {name: os.environ.get(name, "") for name in ENVIRONMENT}


def receipt(record, inputs, unit):
    result = {key: value for key, value in record.items() if key != "certificate"}
    result["certificate"] = {"format": "cesta-test-evidence/1", "inputs_sha256": digest(inputs),
                             "configuration_sha256": digest(context(unit)), "result_sha256": digest(result)}
    return result


def current(record, inputs, unit):
    certificate = record.get("certificate", {})
    return certificate == receipt(record, inputs, unit)["certificate"]
