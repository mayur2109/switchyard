def test_mainframe_provider_parses_curated_search_output_into_cited_candidates(tmp_path):
    from switchyard.mainframe import MainFrameProvider

    root = tmp_path / "MainFrame"
    root.mkdir()

    def runner(query, search_root):
        assert query == "billing"
        assert search_root == root
        return """# MainFrame vault search
# root: /tmp/MainFrame
Projects/billing.md:12:Use the approved billing workflow.
Notes/decision.md:4:Keep the local Vault authoritative.
not a search result
"""

    provider = MainFrameProvider(root, runner=runner)
    candidates = provider.search("billing")

    assert len(candidates) == 2
    assert candidates[0]["id"].startswith("mainframe:")
    assert candidates[0]["text"] == "Use the approved billing workflow."
    assert candidates[0]["citation"] == "Projects/billing.md:12"
    assert candidates[0]["trust"] == "mainframe-curated"
    assert candidates[0]["mandatory"] is False


def test_mainframe_provider_rejects_missing_vault_root(tmp_path):
    from switchyard.mainframe import MainFrameProvider

    provider = MainFrameProvider(tmp_path / "missing", runner=lambda query, root: "")
    try:
        provider.search("query")
    except ValueError as error:
        assert "Vault root" in str(error)
    else:
        raise AssertionError("missing Vault root should be rejected")
