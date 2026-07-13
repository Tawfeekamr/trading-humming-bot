"""Project-wide pytest configuration.

The RL test suite (tests/test_rl_*.py) needs the heavy RL stack — torch,
stable-baselines3, gymnasium, pyarrow — declared in requirements-rl.txt.
CI's test job installs only requirements.txt (deliberately, to keep the test
env light), so the RL stack is absent there. Left unguarded, those tests
either abort collection or fail at runtime, which blocks every deploy.

So: when torch (the canonical RL dep) isn't importable, skip collecting the
RL test files entirely. They run in full under the local conda env where the
RL stack is installed. (A dedicated RL CI job installing requirements-rl.txt
would be the proper home for these — tracked separately.)
"""
from __future__ import annotations

try:
    import torch  # noqa: F401
    _RL_STACK_AVAILABLE = True
except ImportError:
    _RL_STACK_AVAILABLE = False

if not _RL_STACK_AVAILABLE:
    collect_ignore_glob = ["test_rl_*"]
