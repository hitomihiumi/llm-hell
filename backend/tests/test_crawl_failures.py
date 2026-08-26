"""Which failures abandon a crawl, and which are just a file.

The crawler gives up on a source after three failures in a row, on the
reasoning that a source refusing three documents is rate-limiting or down and
continuing only deepens the hole. That is right for a source and wrong for a
document: Drive holds video, `manage_docs` answers a request to read an .mp4
with `400 INVALID_ARGUMENT`, and three adjacent videos would have abandoned
every document after them.
"""

from app.services.search.crawl import _looks_unreadable


def test_a_video_that_is_not_a_document_is_a_file_problem():
    assert _looks_unreadable(
        'manage_docs failed: {"error": "Request contains an invalid argument.", '
        '"status": 400, "reason": "INVALID_ARGUMENT"}'
    )


def test_rate_limiting_is_the_source_failing():
    assert not _looks_unreadable("GitLab API error: 429 Too Many Requests")


def test_a_timeout_is_the_source_failing():
    assert not _looks_unreadable("ReadTimeout: timed out after 30s")


def test_a_refused_connection_is_the_source_failing():
    assert not _looks_unreadable("ConnectError: connection refused")


def test_a_server_error_is_the_source_failing():
    assert not _looks_unreadable("upstream returned 503 Service Unavailable")


def test_an_unrecognised_failure_is_treated_as_the_file():
    """The give-up budget exists to stop hammering a source that is refusing.
    Spending it on something unrecognised would abandon a whole source over
    three odd files."""
    assert _looks_unreadable("could not parse: unexpected end of stream")
