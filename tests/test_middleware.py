import asyncio


def candidates():
    return [
        {"id": "one", "text": "first", "citation": "note://one"},
        {"id": "two", "text": "second", "mandatory": True},
    ]


def test_select_context_builds_typed_request_for_custom_harnesses():
    from switchyard.middleware import select_context

    class Client:
        def select_items(self, request):
            assert request.task == "billing"
            assert request.budget_bytes == 1000
            assert request.items[1].mandatory is True
            return {"selected_ids": ["two"], "selected_items": [request.items[1].model_dump()]}

    result = select_context(Client(), "billing", candidates(), budget_bytes=1000)
    assert result["selected_ids"] == ["two"]


def test_select_context_supports_async_harnesses():
    from switchyard.middleware import aselect_context

    class Client:
        async def select_items(self, request):
            return {"task": request.task, "count": len(request.items)}

    result = asyncio.run(aselect_context(Client(), "incident", candidates(), budget_bytes=500))
    assert result == {"task": "incident", "count": 2}
