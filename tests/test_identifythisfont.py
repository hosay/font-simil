"""dev/identifythisfont.py: pure parsing for the r/identifythisfont eval set (no network)."""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"))

import identifythisfont as itf  # noqa: E402


def post(**kw):
    d = {
        "id": "abc123",
        "title": "What font is this?",
        "author": "op_user",
        "link_flair_text": "Identified",
        "post_hint": "image",
        "url": "https://i.redd.it/xyz.jpeg",
        "domain": "i.redd.it",
        "over_18": False,
        "is_video": False,
        "permalink": "/r/identifythisfont/comments/abc123/what_font/",
    }
    d.update(kw)
    return d


def comment(cid, author, body, score=1, replies=(), parent="t3_abc123", distinguished=None):
    return {
        "kind": "t1",
        "data": {
            "id": cid,
            "name": f"t1_{cid}",
            "parent_id": parent,
            "author": author,
            "body": body,
            "score": score,
            "distinguished": distinguished,
            "replies": {"kind": "Listing", "data": {"children": list(replies)}} if replies else "",
        },
    }


# --- listing filter and image choice ---------------------------------------------------------


def test_identified_image_post_is_kept():
    assert itf.image_urls(post()) == ["https://i.redd.it/xyz.jpeg"]
    assert itf.keep_post(post())


@pytest.mark.parametrize(
    "kw",
    [
        {"link_flair_text": "Open Question"},
        {"link_flair_text": None},
        {"over_18": True},
        {"is_video": True},
        {"post_hint": "link", "url": "https://imgur.com/a/x", "domain": "imgur.com"},
        {
            "post_hint": None,
            "url": "https://www.reddit.com/r/x/",
            "domain": "self.identifythisfont",
        },
    ],
)
def test_other_posts_are_skipped(kw):
    assert not itf.keep_post(post(**kw))


def test_gallery_uses_gallery_order_and_full_size_source():
    p = post(
        post_hint=None,
        url="https://www.reddit.com/gallery/abc123",
        domain="reddit.com",
        is_gallery=True,
        gallery_data={"items": [{"media_id": "m2"}, {"media_id": "m1"}]},
        media_metadata={
            "m1": {
                "status": "valid",
                "e": "Image",
                "s": {"u": "https://preview.redd.it/m1.jpg?a=1&amp;b=2"},
            },
            "m2": {
                "status": "valid",
                "e": "Image",
                "s": {"u": "https://preview.redd.it/m2.jpg?a=1"},
            },
        },
    )
    assert itf.image_urls(p) == [
        "https://preview.redd.it/m2.jpg?a=1",
        "https://preview.redd.it/m1.jpg?a=1&b=2",
    ]
    assert itf.keep_post(p)


def test_gallery_skips_invalid_and_non_image_items():
    p = post(
        post_hint=None,
        is_gallery=True,
        domain="reddit.com",
        url="https://www.reddit.com/gallery/abc123",
        gallery_data={"items": [{"media_id": "a"}, {"media_id": "b"}]},
        media_metadata={
            "a": {"status": "failed"},
            "b": {
                "status": "valid",
                "e": "AnimatedImage",
                "s": {"gif": "https://i.redd.it/b.gif"},
            },
        },
    )
    assert itf.image_urls(p) == []
    assert not itf.keep_post(p)


# --- responses: JSON vs challenge / block page -----------------------------------------------


def test_parse_json_body_accepts_listing():
    assert (
        itf.parse_json_body('{"kind": "Listing", "data": {"children": []}}')["kind"] == "Listing"
    )


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Skip to main content Welcome to Reddit",
        "<html><title>Reddit</title></html>",
        '{"message": "Too Many Requests", "error": 429}',
        '{"reason": "private", "message": "Forbidden", "error": 403}',
    ],
)
def test_parse_json_body_rejects_challenge_and_errors(body):
    with pytest.raises(itf.Blocked):
        itf.parse_json_body(body)


# --- pacing ----------------------------------------------------------------------------------


def test_pacing_is_human_and_has_long_breaks():
    pace = itf.Pacer(random.Random(0))
    delays = [pace.next_delay() for _ in range(100)]
    assert all(8 <= d <= 200 for d in delays)
    assert min(delays) >= 8
    assert sum(d >= 60 for d in delays) >= 2  # a long break every ~25 requests
    assert sum(delays) / len(delays) >= 12


# --- answer selection -----------------------------------------------------------------------

GAZ = itf.Gazetteer(
    {
        "cooper black": "Cooper Black",
        "gill sans": "Gill Sans",
        "p22 underground": "P22 Underground",
        "im fell english": "IM Fell English",
        "futura": "Futura",
        "futura pt": "Futura PT",
        "johnston": "Johnston",
    }
)


def tree(*top):
    return list(top)


def test_answer_is_comment_op_thanked():
    t = tree(
        comment(
            "a",
            "u1",
            "[Johnston.](https://en.wikipedia.org/wiki/Johnston_(typeface))",
            77,
            replies=[comment("b", "op_user", "Awesome. Thank you.", 9, parent="t1_a")],
        ),
        comment("c", "u2", "Possibly an updated Gill Sans", -2),
    )
    a = itf.find_answer(post(), t, GAZ)
    assert (a.name, a.confidence, a.method) == ("Johnston", "high", "op_thanks")


def test_op_thanks_deep_in_chain_picks_the_correction():
    t = tree(
        comment(
            "a",
            "u1",
            "Gill Sans",
            5,
            replies=[
                comment(
                    "b",
                    "u2",
                    "Actually it's Futura PT",
                    4,
                    parent="t1_a",
                    replies=[comment("c", "op_user", "that's it, thanks!", 2, parent="t1_b")],
                )
            ],
        ),
    )
    a = itf.find_answer(post(), t, GAZ)
    assert (a.name, a.confidence) == ("Futura PT", "high")


def test_negative_reply_is_not_thanks():
    t = tree(
        comment(
            "a",
            "u1",
            "Gill Sans",
            5,
            replies=[comment("b", "op_user", "Thanks but no, not quite", 1, parent="t1_a")],
        ),
    )
    assert itf.find_answer(post(), t, GAZ) is None


def test_thanked_similar_suggestion_is_marked_similar():
    t = tree(
        comment(
            "a",
            "u1",
            "You might also like IM Fell English, it is similar",
            5,
            replies=[comment("b", "op_user", "Thank you!", 1, parent="t1_a")],
        ),
    )
    a = itf.find_answer(post(), t, GAZ)
    assert (a.name, a.confidence) == ("IM Fell English", "similar")


def test_deleted_parent_is_unlabelled():
    t = tree(
        comment(
            "a",
            "[deleted]",
            "[removed]",
            5,
            replies=[comment("b", "op_user", "thank you!!", 1, parent="t1_a")],
        ),
    )
    assert itf.find_answer(post(), t, GAZ) is None


def test_agreement_of_distinct_authors():
    t = tree(
        comment("a", "u1", "Cooper black", 98),
        comment("b", "u2", "I have a feeling it's Cooper Black", 18),
        comment("c", "u1", "cooper black!", 3),  # same author twice counts once
        comment("d", "AutoModerator", "Cooper Black", 1, distinguished="moderator"),
        comment("e", "u3", "Gill Sans", 2),
    )
    a = itf.find_answer(post(), t, GAZ)
    assert (a.name, a.confidence, a.method) == ("Cooper Black", "medium", "agreement")


def test_agreement_ignores_op_and_non_positive_scores():
    t = tree(
        comment("a", "u1", "Cooper black", 0),
        comment("b", "op_user", "is it Cooper Black?", 5),
        comment("c", "u2", "Cooper Black", 3),
    )
    assert itf.find_answer(post(), t, GAZ) is None


def test_more_nodes_and_empty_replies_are_tolerated():
    t = tree({"kind": "more", "data": {"children": ["x"]}}, comment("a", "u1", "Futura", 3))
    assert itf.find_answer(post(), t, GAZ) is None


def test_lettering_flair_is_skipped():
    assert not itf.keep_post(post(link_flair_text="Lettering"))


# --- name extraction --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("Cooper black", "cooper black"),
        ("[Johnston.](https://en.wikipedia.org/wiki/Johnston_(typeface))", "johnston"),
        (
            "You're in luck, it's actually a font named [Anaktoria](https://localfonts.eu/x/anaktoria/)",
            "anaktoria",
        ),
        (
            "Usable via Adobe Fonts as [here](https://fonts.adobe.com/fonts/p22-underground)",
            "p22 underground",
        ),
        ("https://fonts.google.com/specimen/IM+Fell+English", "im fell english"),
        (
            "[https://fontsinuse.com/typefaces/3913/delphin](https://fontsinuse.com/typefaces/3913/delphin)",
            "delphin",
        ),
        ("Looks like **Futura PT Bold** to me", "futura pt"),
        ("This is Futura.", "futura"),
    ],
)
def test_first_name_candidate(body, expected):
    assert itf.name_candidates(body, GAZ)[0] == expected


def test_lowercase_common_word_in_long_comment_is_not_a_name():
    gaz = itf.Gazetteer({"average": "Average", "futura": "Futura"})
    body = "honestly this is pretty average work for a logo, could be anything really"
    assert itf.name_candidates(body, gaz) == []
    assert itf.name_candidates(
        "Thanks! I think it is Average Sans, or close", itf.Gazetteer({"average": "Average"})
    ) == ["average"]


def test_gazetteer_resolves_weights_and_suffixes():
    assert GAZ.resolve("Futura PT Bold Italic") == "Futura PT"
    assert GAZ.resolve("cooper black font") == "Cooper Black"
    assert GAZ.resolve("Anaktoria") is None


# --- review cases ----------------------------------------------------------------------------

WORDS = frozenset({"play", "share", "average", "basic", "times"})
GAZ2 = itf.Gazetteer(
    {"play": "Play", "share": "Share", "average": "Average", "basic": "Basic", "arial": "Arial",
     "helvetica": "Helvetica", "sans serif": "Inter", "old english": "UnifrakturMaguntia",
     "open sans": "Open Sans", "johnston": "Johnston", "futura": "Futura",
     "futura pt": "Futura PT"},
    common_words=WORDS,
)  # fmt: skip


@pytest.mark.parametrize(
    "body,expected",
    [
        ("Thanks anyway, it's a generic sans serif", []),
        ("just a basic sans serif", []),
        ("Share the original image please. Could be Helvetica", ["helvetica"]),
        ("Not sure. Play with Arial", ["arial"]),
        ("[here](https://en.wikipedia.org/wiki/Johnston_(typeface))", ["johnston"]),
        (r"It's Open\_Sans", ["open sans"]),
        ("Play", ["play"]),
        ("I'd say it's Play, the Google font", ["play"]),
    ],
)
def test_review_name_cases(body, expected):
    assert itf.name_candidates(body, GAZ2)[: len(expected) or None] == expected


def test_deleted_op_does_not_count_deleted_users_thanks():
    t = tree(
        comment("a", "u1", "Helvetica", 5,
                replies=[comment("b", "[deleted]", "thank you", 1, parent="t1_a")]),
    )
    assert itf.find_answer(post(author="[deleted]"), t, GAZ2) is None


@pytest.mark.parametrize(
    "reply", ["thanks anyway", "Thanks for trying!", "thanks though, still looking"]
)
def test_polite_non_answers_are_not_thanks(reply):
    t = tree(comment("a", "u1", "Helvetica", 5,
                     replies=[comment("b", "op_user", reply, 1, parent="t1_a")]))
    assert itf.find_answer(post(), t, GAZ2) is None


def test_close_enough_reply_marks_similar():
    t = tree(comment("a", "u1", "Helvetica", 5,
                     replies=[comment("b", "op_user", "close enough, thanks!", 1, parent="t1_a")]))
    assert itf.find_answer(post(), t, GAZ2).confidence == "similar"


def test_agreement_merges_style_variants():
    t = tree(
        comment("a", "u1", "Futura PT Bold", 5),
        comment("b", "u2", "futura pt", 3),
        comment("c", "u3", "Helvetica", 2),
    )
    a = itf.find_answer(post(), t, GAZ2)
    assert (a.name, a.method) == ("Futura PT", "agreement")


# --- resume and split -------------------------------------------------------------------------


def test_load_index_last_record_wins_and_failed_posts_are_retried(tmp_path):
    p = tmp_path / "posts.jsonl"
    rows = [
        {"id": "a", "images": [], "n_images": 1},  # old run: failed download, retry
        {"id": "b", "images": [{"sha1": "x"}], "n_images": 1},
        {"id": "c", "images": [], "n_images": 1, "dupes": True},  # every image a duplicate
        {"id": "d", "images": [], "n_images": 1, "failed": True},
        {"id": "d", "images": [{"sha1": "y"}], "n_images": 1},  # retried later: done
    ]
    p.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows))
    recs = itf.load_index(p)
    assert [r["id"] for r in recs] == ["a", "b", "c", "d"]
    assert itf.done_ids(recs) == {"b", "c", "d"}


def test_split_puts_row_in_test_when_any_family_is_test():
    split_of = {"arimo": "test"}.get
    assert itf.split_for("helvetica", "helvetica", ["arimo"], split_of) == "test"
    assert itf.split_for("helvetica", "helvetica", ["inter"], split_of) == "dev"
    assert itf.split_for("arimo", "arimo", [], split_of) == "test"


@pytest.mark.parametrize(
    "body,expected",
    [("Baller shirt btw", []), ("lol no idea", []), ("Friz Quadrata", ["friz quadrata"]),
     ("Papyrus.", ["papyrus"]), ("papyrus", ["papyrus"]), ("Papyrus for sure", ["papyrus"])],
)  # fmt: skip
def test_unknown_short_answer_must_look_like_a_name(body, expected):
    assert itf.name_candidates(body, GAZ) == expected


def test_box_fractions_to_pixels():
    assert itf.box_pixels([0.1, 0.25, 0.9, 0.75], (1000, 400)) == (100, 100, 900, 300)
    assert itf.box_pixels(None, (1000, 400)) is None


def test_agreement_is_weighted_by_score():
    """The 478-point answer beats three 1-point jokes (r/identifythisfont 1pnbncy)."""
    t = tree(
        comment("a", "u1", "Papyrus.", 478),
        comment("b", "u2", "papyrus", 2),
        comment("c", "u3", "Papyrus for sure", 2),
        comment("d", "u4", "Hippy Comic Sans", 3),
        comment("e", "u5", "Comic Sans", 1),
        comment("f", "u6", "Only thing we have here is comic sans", 1),
    )
    gaz = itf.Gazetteer({"comic sans": "Comic Sans"})
    a = itf.find_answer(post(), t, gaz)
    assert (a.name, a.method) == ("Papyrus", "agreement")


def test_agreement_needs_a_clear_score_margin():
    t = tree(
        comment("a", "u1", "Futura", 10),
        comment("b", "u2", "Futura", 2),
        comment("c", "u3", "Helvetica", 9),
    )
    assert itf.find_answer(post(), t, GAZ2) is None
