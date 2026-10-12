"""Measure complete documents and their payloads with one fixed test tokenizer."""

import hashlib
import json
from pathlib import Path
import re
import sys

import tiktoken

if tiktoken.__version__ != "0.12.0":
    raise RuntimeError("qualification uses the pinned tiktoken 0.12.0 oracle")

encoding = tiktoken.get_encoding("o200k_base")
fingerprint = hashlib.sha256(encoding._pat_str.encode())
for spelling, rank in sorted(encoding._mergeable_ranks.items()):
    fingerprint.update(len(spelling).to_bytes(4, "big"))
    fingerprint.update(spelling)
    fingerprint.update(rank.to_bytes(4, "big"))
metadata = {"implementation": "tiktoken", "version": tiktoken.__version__,
            "encoding": encoding.name, "pattern_and_ranks_sha256": fingerprint.hexdigest(),
            "input": "UTF-8 text, ordinary encoding with no special-token recognition"}


def count(data):
    return len(encoding.encode_ordinary(data.decode("utf-8")))


records = []
for filename in sys.argv[1:]:
    data = Path(filename).read_bytes()
    record = {"file": Path(filename).name, "tokens": count(data)}
    match = re.match(rb'^\(cesta [^ ()]+ "[a-f0-9]{16}" "[a-f0-9]{16}" [^ ()]+ ', data)
    if match:
        tail = data[match.end():].rstrip()
        if not tail.endswith(b")"):
            raise RuntimeError("malformed presentation document")
        record["payload_tokens"] = count(tail[:-1])
        # Boundary merges make this a measured difference, rather than the
        # separately tokenized envelope's additive token count.
        record["document_minus_payload_tokens"] = record["tokens"]-record["payload_tokens"]
    records.append(record)
print(json.dumps({"tokenizer": metadata, "records": records}))
