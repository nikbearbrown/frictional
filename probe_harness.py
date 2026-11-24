"""Frictional probe harness. It runs INSIDE the container only, never on the host.

probes.py copies this file into a private copy of the project under
.frictional-probe/ and runs it in Frictional's container after setup, with the
network off:

    PYTHONHASHSEED=0 python .frictional-probe/probe_harness.py .frictional-probe/calls-0.json

The calls file (written by probes.py, deleted by this harness once read):

    {"nonce": HEX, "calls": [{"id": "c1", "module": "counter.py", "function": "most_common",
                              "args": "('a b b',)", "kwargs": "{}",
                              "file": optional path of a variant or reference module}]}

"args" and "kwargs" are Python literal source, read with ast.literal_eval.
For each call the harness prints one line to the original standard output:

    FRICTIONAL-PROBE <nonce> {"id": ..., "status": "value", "repr": ..., "sha256": ...}
    FRICTIONAL-PROBE <nonce> {"id": ..., "status": "raises", "type": "ValueError"}
    FRICTIONAL-PROBE <nonce> {"id": ..., "status": "error", "reason": "import" | "timeout" | ...}

and a last line {"done": N}. Project output during imports and calls goes to
/dev/null. Standard library only; works on Python 3.8 and newer.
"""

import ast
import hashlib
import importlib
import importlib.util
import json
import os
import signal
import sys

MARK = "FRICTIONAL-PROBE"
CALL_SECONDS = 2
IMPORT_SECONDS = 10
MAX_REPR = 200
MAX_DEPTH = 20
SCALARS = (type(None), bool, int, float, str)
CONTAINERS = (list, tuple, set, frozenset)


class Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise Timeout()


def plain(value, depth=0):
    """True for None, bool, int, float, str and exact list/tuple/set/frozenset/dict of those."""
    if depth > MAX_DEPTH:
        return False
    kind = type(value)
    if kind in SCALARS:
        return True
    if kind in CONTAINERS:
        return all(plain(item, depth + 1) for item in value)
    if kind is dict:
        return all(plain(k, depth + 1) and plain(v, depth + 1) for k, v in value.items())
    return False


def outcome(value):
    if not plain(value):
        return {"status": "error", "reason": "not-plain"}
    text = repr(value)
    if len(text) > MAX_REPR:
        return {"status": "error", "reason": "too-long"}
    return {"status": "value", "repr": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def package_name(root, module_path):
    """Dotted name for module_path, counting parent folders that have an __init__.py."""
    parts = module_path[:-3].split("/")
    start = len(parts) - 1
    while start > 0 and os.path.isfile(os.path.join(root, *parts[:start], "__init__.py")):
        start -= 1
    base = os.path.join(root, *parts[:start]) if start else root
    return base, ".".join(parts[start:])


class Loader:
    def __init__(self, root):
        self.root = root
        self.cache = {}
        self.count = 0

    def load(self, module_path, file_path=None):
        key = (module_path, file_path)
        if key in self.cache:
            return self.cache[key]
        base, dotted = package_name(self.root, module_path)
        for entry in (self.root, base):
            if entry not in sys.path:
                sys.path.insert(0, entry)
        signal.alarm(IMPORT_SECONDS)
        try:
            if file_path is None:
                module = importlib.import_module(dotted)
            else:
                self.count += 1
                spec = importlib.util.spec_from_file_location("_frictional_probe_%d" % self.count,
                                                              os.path.join(self.root, file_path))
                module = importlib.util.module_from_spec(spec)
                module.__package__ = dotted.rpartition(".")[0]
                spec.loader.exec_module(module)
        except Timeout:
            module = ("error", "import-timeout")
        except BaseException:
            module = ("error", "import")
        finally:
            signal.alarm(0)
        self.cache[key] = module
        return module


def run_call(loader, call, timed_out):
    where = (call["module"], call.get("file"), call["function"])
    if where in timed_out:
        return {"status": "error", "reason": "skipped"}
    module = loader.load(call["module"], call.get("file"))
    if isinstance(module, tuple):
        return {"status": "error", "reason": module[1]}
    function = getattr(module, call["function"], None)
    if not callable(function):
        return {"status": "error", "reason": "missing"}
    try:
        args = ast.literal_eval(call["args"])
        kwargs = ast.literal_eval(call["kwargs"])
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return {"status": "error", "reason": "arguments"}
    if type(args) is not tuple or type(kwargs) is not dict:
        return {"status": "error", "reason": "arguments"}
    signal.alarm(CALL_SECONDS)
    try:
        value = function(*args, **kwargs)
    except Timeout:
        timed_out.add(where)
        return {"status": "error", "reason": "timeout"}
    except Exception as exc:
        return {"status": "raises", "type": type(exc).__name__[:80]}
    except BaseException:
        return {"status": "error", "reason": "aborted"}
    finally:
        signal.alarm(0)
    try:
        return outcome(value)
    except Exception:
        return {"status": "error", "reason": "not-plain"}


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: probe_harness.py CALLS.json\n")
        return 2
    calls_path = argv[1]
    with open(calls_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    os.remove(calls_path)
    nonce = data["nonce"]
    calls = data["calls"]
    root = os.getcwd()
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    sink = os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink, 1)
    os.dup2(sink, 2)
    sys.stdout = open(os.devnull, "w")
    sys.stderr = sys.stdout
    signal.signal(signal.SIGALRM, _alarm)
    loader = Loader(root)
    timed_out = set()
    done = 0
    for call in calls:
        result = run_call(loader, call, timed_out)
        result["id"] = call["id"]
        out.write("%s %s %s\n" % (MARK, nonce, json.dumps(result, sort_keys=True, ensure_ascii=True)))
        out.flush()
        done += 1
    out.write("%s %s %s\n" % (MARK, nonce, json.dumps({"done": done})))
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
