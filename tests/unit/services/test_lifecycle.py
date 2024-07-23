# Copyright 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# For further info, check https://github.com/canonical/charmcraft

from craft_application.models import BuildInfo
from craft_providers.bases import BaseName
import pytest

from charmcraft import services, utils


@pytest.fixture()
def service(service_factory: services.CharmcraftServiceFactory) -> services.LifecycleService:
    return service_factory.lifecycle


@pytest.mark.parametrize(
    ("build_plan", "expected"),
    [
        ([], utils.get_host_architecture()),
        (
            [
                BuildInfo(
                    platform="all",
                    build_on=utils.get_host_architecture(),
                    build_for="all",
                    base=BaseName("ubuntu", "22.04")
                ),
            ],
            utils.get_host_architecture(),
        ),
        (
            [
                BuildInfo(
                    platform="something-else",
                    build_on=utils.get_host_architecture(),
                    build_for=f"{utils.get_host_architecture()}-something",
                    base=BaseName("ubuntu", "22.04")
                ),
            ],
            "something",
        ),
    ]
)
def test_get_build_for(service: services.LifecycleService, build_plan, expected):
    service._build_plan = build_plan

    assert service._get_build_for() == expected
