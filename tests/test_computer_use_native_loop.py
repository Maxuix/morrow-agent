"""The native fixture's scripted Provider chooses only a safe observed token."""

import base64
import io
import json
import runpy
from types import SimpleNamespace

import pytest
from PIL import Image

from morrow.core.computer_use import ComputerUseContractError


@pytest.fixture
def module():
    return runpy.run_path("evals/computer_use/native_loop.py")


def observation(elements):
    return [
        SimpleNamespace(
            role="tool",
            content=json.dumps(
                {
                    "result": {
                        "observation_id": "cobs_fixture",
                        "elements": elements,
                    }
                }
            ),
        )
    ]


def image_part():
    stream = io.BytesIO()
    Image.new("RGB", (1040, 1024)).save(stream, format="PNG")
    return SimpleNamespace(data=base64.b64encode(stream.getvalue()).decode())


@pytest.mark.parametrize(
    "elements",
    [
        [{"role": "axtextfield", "sensitive": True, "element_ref": "secure"}],
        [
            {"role": "axtextfield", "sensitive": False, "element_ref": value}
            for value in ("one", "two")
        ],
    ],
)
async def test_secure_or_ambiguous_input_never_proposes_an_action(module, elements):
    provider = module["NativeProvider"]("controlled marker")
    provider.images = [[], []]
    module["NativeProvider"].stream.__globals__["iter_image_parts"] = lambda _: (image_part(),)
    with pytest.raises(ComputerUseContractError, match="fixture_input_ambiguous"):
        async for _ in provider.stream(None, observation(elements)):
            pass
    assert len(provider.responses) == 1


async def test_missing_provider_image_refuses_before_input_proposal(module):
    provider = module["NativeProvider"]("controlled marker")
    provider.images = [[], []]
    module["NativeProvider"].stream.__globals__["iter_image_parts"] = lambda _: ()
    with pytest.raises(ComputerUseContractError, match="fixture_provider_image_missing"):
        async for _ in provider.stream(None, observation([])):
            pass
    assert len(provider.responses) == 1


async def test_provider_uses_only_non_sensitive_current_reference(module):
    provider = module["NativeProvider"]("controlled marker")
    provider.images = [[], []]
    module["NativeProvider"].stream.__globals__["iter_image_parts"] = lambda _: (image_part(),)
    elements = [
        {"role": "axtextfield", "sensitive": True, "element_ref": "secure"},
        {"role": "axtextfield", "sensitive": False, "element_ref": "current"},
    ]
    async for _ in provider.stream(None, observation(elements)):
        pass
    proposed = json.loads(provider.responses[-1].tool_calls[0].arguments)
    assert proposed["observation_id"] == "cobs_fixture"
    assert proposed["action"]["element_ref"] == "current"
