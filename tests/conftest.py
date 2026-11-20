import os

import pytest

os.environ["LLM_DISABLED"] = "1"  # tests never call a real model; LLM loops are tested with scripted fakes

from pmadvisor import whatif  # noqa: E402
from pmadvisor.scenario import build, demo  # noqa: E402


@pytest.fixture(scope="session")
def demo_scenario():
    return demo()


@pytest.fixture(scope="session")
def demo_an(demo_scenario):
    return whatif.analyse(demo_scenario)


@pytest.fixture(scope="session")
def healthy_an():
    return whatif.analyse(build(3, []))
