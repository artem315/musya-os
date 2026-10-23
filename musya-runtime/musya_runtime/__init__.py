"""MUSYA Runtime - the language of MURCLOUD OS (ARTYONIX STUDIOS).

Quick use::

    import asyncio
    from musya_runtime import run, SandboxHost

    asyncio.run(run('print "МУРГОТОВО!"', SandboxHost("./project")))
"""

from .errors import (MusyaAssertionError, MusyaError, MusyaLimitError, MusyaPermissionError,
                     MusyaRuntimeError, MusyaSyntaxError, MusyaThrow)
from .interpreter import Interpreter, Limits, RunResult, TestResult, run
from .interpreter.host import RUNTIME_PERMISSIONS, RuntimeHost, SandboxHost, safe_join
from .lexer import tokenize
from .manifest import (ALL_PERMISSIONS, SENSITIVE_PERMISSIONS, Manifest, ManifestError,
                       load_manifest_file, validate_manifest)
from .parser import SERVICE_SIGNATURES, parse

__version__ = "10.0.0b0"

__all__ = [
    "tokenize", "parse", "run", "Interpreter", "Limits", "RunResult", "TestResult",
    "RuntimeHost", "SandboxHost", "safe_join", "RUNTIME_PERMISSIONS", "ALL_PERMISSIONS",
    "SENSITIVE_PERMISSIONS", "Manifest", "ManifestError", "validate_manifest", "load_manifest_file",
    "SERVICE_SIGNATURES", "MusyaError", "MusyaSyntaxError", "MusyaRuntimeError", "MusyaPermissionError",
    "MusyaLimitError", "MusyaThrow", "MusyaAssertionError", "__version__",
]
