"""F-2 t2 guard (plan 5.8, F2-D7): outside the sealed r14 file, identity capture is the real function.

The autouse fixture in tests/probes/conftest.py replaces harness_identity.capture with
uncaptured for test_k_task8_binding.py only. This module is not that file, so it must see the
real capture and the production resolver must reach it. Synthetic and hermetic.
"""
from scieflow.core import harness_identity

_REAL_CAPTURE = harness_identity.capture


def test_capture_is_the_real_function_outside_the_sealed_file():
    assert harness_identity.capture is _REAL_CAPTURE
    assert harness_identity.capture.__name__ == 'capture'
    assert harness_identity.capture is not harness_identity.uncaptured
