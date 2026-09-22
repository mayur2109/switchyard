def test_candidate_provider_contract_normalizes_read_only_results():
    from switchyard.providers import CandidateProvider, normalize_candidates

    class SearchProvider:
        def search(self, query):
            return [{"id": "note-1", "text": "billing decision", "citation": query}]

    assert isinstance(SearchProvider(), CandidateProvider)
    candidates = normalize_candidates(SearchProvider().search("billing"))
    assert candidates[0].id == "note-1"
    assert candidates[0].citation == "billing"
    assert candidates[0].mandatory is False
