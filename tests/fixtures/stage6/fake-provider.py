"""Shape-only second Provider adapter fixture; it performs no I/O."""


class FakeSecondProvider:
    async def complete(self, model, messages):
        del model, messages
        return "offline fixture response"

    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        yield "offline fixture response"
