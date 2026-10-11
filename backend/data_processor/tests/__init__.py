"""Unit tests for data_processor.

Pure-logic tests only: every case in this package runs against importable
functions with no database access, so they use SimpleTestCase.

Business rules are taken from '4RC Documentation.pdf' (sections 3.3-3.5).
Where the implementation departs from that document, the test asserts the
CURRENT behaviour and carries a DOC MISMATCH note naming the difference.
"""
