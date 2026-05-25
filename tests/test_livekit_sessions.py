from voicebot_platform.livekit_sessions import _is_livekit_room_not_found


def test_livekit_room_not_found_detection_accepts_twirp_not_found():
    exc = RuntimeError(
        "TwirpError(code=not_found, message=twirp error unknown: "
        "requested room does not exist, status=404)"
    )

    assert _is_livekit_room_not_found(exc)


def test_livekit_room_not_found_detection_rejects_other_errors():
    exc = RuntimeError("TwirpError(code=internal, message=permission denied)")

    assert not _is_livekit_room_not_found(exc)
