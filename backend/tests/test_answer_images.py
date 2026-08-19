"""Pages of a PDF going into the answer prompt itself.

One model, one pass: the page is attached to the same turn as the text
results, rather than being described first by a second model and answered
from the description. Whatever a transcriber failed to mention cannot be lost
if there is no transcriber.

Verified against a live Gemma 4 31B on a real datasheet: asked how many motors
the wiring diagram shows, the answer with pages attached was "4 motors, the
ESC below the flight controller"; with `ANSWER_IMAGE_HITS=0` and nothing else
changed, the same model said the text does not specify either.
"""

import base64

from app.core.config import Settings
from app.models.endpoint import ModelEndpoint
from app.schemas.search import SearchHit
from app.services.search.answer import build_prompt

ENDPOINT = ModelEndpoint(name="m", base_url="http://x/v1", model_id="m", ctx_window=131072)
SETTINGS = Settings()

HIT = SearchHit(id="google_drive:abc", source="google_drive", kind="document", title="datasheet.pdf", snippet="specs")
OTHER = SearchHit(id="gitlab:code:1:a.py:1", source="gitlab", kind="code", title="a.py", snippet="code")

PAGE = b"\xff\xd8\xfffake-jpeg"


def user_message(messages):
    return next(m for m in messages if m["role"] == "user")


def test_without_images_the_turn_stays_a_plain_string():
    """A text-only server should not be handed the multimodal list form for a
    question that never needed it."""
    messages, _ = build_prompt("q", [HIT], ENDPOINT, settings=SETTINGS)

    assert isinstance(user_message(messages)["content"], str)


def test_pages_are_attached_to_the_same_turn_as_the_results():
    messages, included = build_prompt(
        "q", [HIT], ENDPOINT, settings=SETTINGS, images={HIT.id: [PAGE]}
    )
    content = user_message(messages)["content"]

    assert isinstance(content, list)
    # The text block first, then the label and the picture.
    assert content[0]["type"] == "text"
    assert "Search results" in content[0]["text"]
    assert content[-1]["type"] == "image_url"
    assert content[-1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(content[-1]["image_url"]["url"].split(",", 1)[1]) == PAGE
    assert included == [HIT]


def test_each_picture_is_announced_by_the_number_that_cites_it():
    """`[2]` has to mean the same thing whether the model took it from a
    snippet or from a page, or a citation stops being checkable."""
    messages, _ = build_prompt(
        "q", [OTHER, HIT], ENDPOINT, settings=SETTINGS, images={HIT.id: [PAGE, PAGE]}
    )
    labels = [
        part["text"]
        for part in user_message(messages)["content"]
        if part["type"] == "text" and part["text"].startswith("[")
    ]

    # The PDF is the second hit, so its pages are announced as [2].
    assert labels == ["[2] page image 1:", "[2] page image 2:"]


def test_images_for_a_hit_that_did_not_fit_are_not_attached():
    """The picture must not outlive the hit it belongs to - an image labelled
    with a number that is not in the prompt is worse than no image."""
    messages, included = build_prompt(
        "q", [HIT], ENDPOINT, settings=SETTINGS, images={"google_drive:not-in-results": [PAGE]}
    )

    assert isinstance(user_message(messages)["content"], str)
    assert included == [HIT]
