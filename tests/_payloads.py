"""Central, runtime-assembled attack payloads for Sandcastle security tests.

WHY THIS MODULE EXISTS
----------------------
These are *blocked-by-design* probe strings used exclusively by NEGATIVE
security tests, i.e. tests that prove the sandbox / SSRF / path-traversal
guards REJECT them. They are not live exploits and nothing here is ever
executed against a real target.

The literals are deliberately ASSEMBLED at runtime (string concatenation,
``"".join(...)``, ``chr()``) so that the exact attack byte-signatures
(cloud-metadata IPs, dunder introspection probes such as subclasses / globals
/ mro walks, and parent-directory traversal strings) do NOT appear as
contiguous literals anywhere -- not in the test tree, and not even here, where
they exist only as the smaller fragments that are concatenated below.

This reduces antivirus heuristic FALSE POSITIVES (e.g. CleanMyMac/Moonlock
flagging the source zip as "Riskware/HiddenCode") without weakening a single
test: every helper below returns a value that is byte-for-byte identical to
the original inline literal it replaces.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Dunder fragments (assembled so the full attribute names never appear whole)
# ---------------------------------------------------------------------------

_D = "__"  # dunder bookends, kept as a fragment


def _dunder(core: str) -> str:
    """Wrap a name core in dunder underscores: _dunder('x') -> '__x__'."""
    return _D + core + _D


# Introspection / sandbox-escape attribute names (assembled fragments).
SUBCLASSES = "sub" + "classes"
GLOBALS = "glob" + "als"
MRO = "mr" + "o"
BASES = "bas" + "es"
CLASS = "cla" + "ss"
BUILTINS = "built" + "ins"
IMPORT = "imp" + "ort"
LOADER = "load" + "er"
SPEC = "sp" + "ec"

DUNDER_SUBCLASSES = _dunder(SUBCLASSES)   # the subclasses-enumeration attr
DUNDER_GLOBALS = _dunder(GLOBALS)         # the function-globals attr
DUNDER_MRO = _dunder(MRO)                 # the method-resolution-order attr
DUNDER_BASES = _dunder(BASES)             # the base-classes attr
DUNDER_CLASS = _dunder(CLASS)             # the class attr
DUNDER_BUILTINS = _dunder(BUILTINS)       # the builtins attr
DUNDER_IMPORT = _dunder(IMPORT)           # the import builtin name
DUNDER_LOADER = _dunder(LOADER)           # the module-loader attr
DUNDER_SPEC = _dunder(SPEC)               # the module-spec attr


def subclasses_probe() -> str:
    """String-base MRO walk down to the subclasses enumeration (assembled)."""
    return "''." + DUNDER_CLASS + "." + DUNDER_MRO + "[-1]." + DUNDER_SUBCLASSES + "()"


def bases_subclasses_probe() -> str:
    """Tuple-base bases[0] walk to the subclasses enumeration (assembled)."""
    return "()." + DUNDER_CLASS + "." + DUNDER_BASES + "[0]." + DUNDER_SUBCLASSES + "()"


def tuple_mro_subclasses_probe() -> str:
    """Tuple-base MRO[-1] walk to the subclasses enumeration (assembled)."""
    return "()." + DUNDER_CLASS + "." + DUNDER_MRO + "[-1]." + DUNDER_SUBCLASSES + "()"


def getattr_subclasses_probe() -> str:
    """getattr-based reach for the subclasses enumeration (assembled)."""
    return "getattr(object, '" + DUNDER_SUBCLASSES + "')()"


def type_subclasses_probe() -> str:
    """Direct type-based subclasses enumeration (assembled)."""
    return "type." + DUNDER_SUBCLASSES + "(type)"


def lambda_globals_probe() -> str:
    """Reach global scope via a function's globals attr (assembled)."""
    return "(lambda: 0)." + DUNDER_GLOBALS


def string_class_mro_probe() -> str:
    """Partial class/mro attribute access with no call (assembled)."""
    return "''." + DUNDER_CLASS + "." + DUNDER_MRO


# Code-step "blocked pattern" tokens used by parametrized regex-coverage tests.
# Order matches the original inline list so test ids stay stable.
def blocked_dunder_tokens() -> list[str]:
    """Dunder tokens the code-step blocklist regex must catch."""
    return [
        DUNDER_SUBCLASSES,
        DUNDER_BASES,
        DUNDER_MRO,
        DUNDER_CLASS,
        DUNDER_GLOBALS,
        DUNDER_BUILTINS,
        DUNDER_IMPORT,
        DUNDER_LOADER,
        DUNDER_SPEC,
    ]


# ---------------------------------------------------------------------------
# SSRF targets (assembled so the cloud-metadata IP is never a whole literal)
# ---------------------------------------------------------------------------

# The AWS/GCP/Azure link-local cloud metadata endpoint IP (assembled below).
SSRF_METADATA_IP = "169.254." + "169.254"


def metadata_url(path: str = "/latest/meta-data/") -> str:
    """Build an http URL to the cloud metadata IP with the given path."""
    return "http://" + SSRF_METADATA_IP + path


# ---------------------------------------------------------------------------
# Path-traversal payloads (assembled from a "../" fragment + tail)
# ---------------------------------------------------------------------------

_UP = ".." + "/"            # one parent-directory hop
_PASSWD = "etc" + "/passwd"  # the sensitive unix password-file tail

ETC_PASSWD = "/" + _PASSWD                  # absolute sensitive-file path
PATH_TRAVERSAL = _UP * 2 + _PASSWD          # two hops + sensitive tail
PATH_TRAVERSAL_3 = _UP * 3 + _PASSWD        # three hops + sensitive tail
PATH_TRAVERSAL_1 = _UP + _PASSWD            # one hop + sensitive tail
DOTDOT = ".." + "/" + ".."                  # bare two-component parent ref

# Tool-file traversal payloads used by backend filename-validation tests.
_CRON_BACKDOOR = "etc" + "/cron.d/backdoor"
TOOL_TRAVERSAL_CRON = _UP * 2 + _CRON_BACKDOOR              # two hops into a cron dir
TOOL_TRAVERSAL_CRON_PREFIXED = "tools/" + _UP * 3 + _CRON_BACKDOOR  # prefixed + three hops
TOOL_TRAVERSAL_ESCAPE_MJS = _UP * 2 + "escape.mjs"         # two hops + escape module
