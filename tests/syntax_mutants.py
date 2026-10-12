"""One-token deletion/duplication against independent compiler/oracle checks."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import subprocess

from corpus import run, pycparser_check
from oracle_triage import check_controls, load_pyc_audit, mutant_disposition

ROOT = Path(__file__).resolve().parent.parent


class Probe:
    def __init__(self, grammar, profile):
        self.process = subprocess.Popen([str(ROOT / "build/syntax-probe"),str(grammar),str(profile)],
            cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,bufsize=1)

    def request(self, action, path):
        self.process.stdin.write(f"{action} {path}\n")
        self.process.stdin.flush()
        reply = self.process.stdout.readline().rstrip("\n")
        if not reply:
            raise RuntimeError("native syntax probe stopped before answering")
        return reply

    def spans(self, path):
        reply = self.request("T",path).split()
        if reply[0] != "T" or len(reply) != 2+2*int(reply[1]):
            raise RuntimeError("baseline does not have declared lexical token spans")
        return [(int(reply[i]),int(reply[i+1])) for i in range(2,len(reply),2)]

    def accepts(self, path):
        reply = self.request("P",path).split(" ",2)
        return {"accepted":reply[0] == "A","offset":int(reply[1]),
                "diagnostic":reply[2] if len(reply)>2 else ""}

    def close(self):
        self.process.stdin.close()
        if self.process.wait() != 0:
            raise RuntimeError("native syntax probe failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus",type=Path,default=ROOT.parent / "repos/C11parser/tests")
    parser.add_argument("--count",type=int,default=1500)
    parser.add_argument("--grammar",type=Path,default=ROOT / "build/CGNU.cf")
    parser.add_argument("--profile",type=Path,default=ROOT / "grammar/CGNUC11.profile")
    parser.add_argument("--output",type=Path,default=ROOT / "build/syntax-mutants")
    options = parser.parse_args()
    if options.count < 1000:
        parser.error("the qualification requires at least 1000 distinct mutations")
    output = options.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("qualification artifacts belong under build")
    output.mkdir(parents=True,exist_ok=True)
    current = output / "current.c"
    flags = ["-std=c11","-fsyntax-only"]
    probe = Probe(options.grammar,options.profile)
    baselines, corpus = [], []
    try:
        audit = load_pyc_audit()
        controls = check_controls(probe,output / "controls",audit)
        for path in sorted(options.corpus.glob("*.c")):
            gcc,_ = run(["gcc",*flags,str(path)])
            if not gcc["accepted"]:
                continue
            prep,data = run(["gcc","-std=c11","-E","-P",str(path)])
            if not prep["accepted"]:
                raise RuntimeError(f"accepted baseline does not preprocess: {path}")
            current.write_bytes(data)
            admitted = probe.accepts(current)
            if not admitted["accepted"]:
                raise RuntimeError(f"baseline parser regression: {path.name}: {admitted['diagnostic']}")
            spans = probe.spans(current)
            compilers = {}
            for compiler in ("gcc","clang"):
                compilers[compiler],_ = run([compiler,*flags,str(current)])
            baselines.append({"source":path.name,"sha256":hashlib.sha256(data).hexdigest(),
                              "tokens":len(spans),"compilers":compilers})
            corpus.append((path.name,data,spans))
        if not corpus:
            raise RuntimeError("empty accepted baseline corpus")
        capacity = sum(2*len(spans) for _,_,spans in corpus)
        if options.count > capacity:
            raise RuntimeError("requested mutation count exceeds distinct token edits")
        rng = random.Random(0x43455354414D5554)
        selected, records = set(), []
        while len(records)<options.count:
            index = rng.randrange(len(corpus))
            name,data,spans = corpus[index]
            token = rng.randrange(len(spans)); duplicate = bool(rng.randrange(2))
            key = (index,token,duplicate)
            if key in selected:
                continue
            selected.add(key)
            offset,length = spans[token]
            spelling = data[offset:offset+length]
            replacement = b" "+spelling+b" "+spelling+b" " if duplicate else b" "
            mutant = data[:offset]+replacement+data[offset+length:]
            current.write_bytes(mutant)
            record = {"source":name,"token":token,"offset":offset,
                      "edit":"duplicate" if duplicate else "delete",
                      "spelling_hex":spelling.hex(),"sha256":hashlib.sha256(mutant).hexdigest(),
                      "cesta":probe.accepts(current)}
            for compiler in ("gcc","clang"):
                record[compiler],_ = run([compiler,*flags,str(current)])
            record["pycparser"] = pycparser_check(mutant)
            families,unknown = mutant_disposition(record,current,audit)
            record["triage"] = {"families":families,"unexplained":unknown}
            records.append(record)
            if len({record[name]["accepted"] for name in ("cesta","gcc","clang","pycparser")}) > 1 or unknown:
                failure = output / "disagreements"
                failure.mkdir(exist_ok=True)
                (failure / f"{len(records):04d}.c").write_bytes(mutant)
            if len(records)%100 == 0:
                print(f"syntax mutants: {len(records)}/{options.count}",flush=True)
        disagreements = [i+1 for i,r in enumerate(records)
                         if r["cesta"]["accepted"] != r["gcc"]["accepted"]]
        summary = {"count":len(records),"distinct_original_files":len({r['source'] for r in records}),
                   "baselines":baselines,"mutants":records,"gcc_disagreements":disagreements,
                   "grammar_sha256":hashlib.sha256(options.grammar.read_bytes()).hexdigest(),
                   "profile_sha256":hashlib.sha256(options.profile.read_bytes()).hexdigest(),
                   "flags":flags,"seed":0x43455354414D5554,"controls":controls,
                   "triage_families":dict(Counter(family for record in records for family in record["triage"]["families"])),
                   "unexplained":[{"index":i+1,"reason":reason} for i,record in enumerate(records)
                                  for reason in record["triage"]["unexplained"]]}
        (output / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
        print(json.dumps({"mutants":len(records),"files":summary['distinct_original_files'],
                          "gcc_agreements":len(records)-len(disagreements),
                          "gcc_disagreements":len(disagreements),"paired_control_families":controls,
                          "unexplained":summary["unexplained"]}))
        if summary["unexplained"]:
            raise SystemExit(1)
    finally:
        probe.close()


if __name__ == "__main__":
    main()
