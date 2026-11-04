from jobcopilot.apply.overlap import repeats


def test_shared_passage_of_eight_words_is_flagged():
    a = "At Acme I built the MCP server that lets agents query the database."
    b = "I built the MCP server that lets agents query the database and send alerts."
    assert repeats("cover letter", a, [("Why us?", b)]) == ["cover letter repeats your answer to \"Why us?\": \"I built the MCP server that lets agents query the database\""]


def test_short_overlaps_case_and_punctuation():
    a = "I built the MCP server. It was fun."
    b = "i built the MCP server, and many other things."
    assert repeats("x", a, [("q", b)]) == []
    long_a = "I BUILT the MCP server, which lets agents query the database!"
    long_b = "i built the mcp server which lets agents query the database"
    assert repeats("x", long_a, [("q", long_b)])


def test_empty_and_self():
    assert repeats("x", "", [("q", "anything at all here")]) == []
    assert repeats("x", "same text " * 10, []) == []
