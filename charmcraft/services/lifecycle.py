# Copyright 2023-2024 Canonical Ltd.
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
"""Service class for running craft lifecycle commands."""
from __future__ import annotations

from craft_cli import emit
import craft_parts
from craft_application import errors, services, util
from overrides import override

from charmcraft import utils


class LifecycleService(services.LifecycleService):
    """Business logic for lifecycle builds."""

    def setup(self) -> None:
        """Do Charmcraft-specific setup work."""
        self._manager_kwargs.setdefault("project_name", self._project.name)
        super().setup()

    def _get_build_for(self) -> str:
        """Get the build_for value to pass to craft-parts."""
        if not self._build_plan:
            return utils.get_host_architecture()
        if self._build_plan[0].build_for == "all":
            return utils.get_host_architecture()
        return utils.select_architecture(
                self._build_plan[0].build_for.split("-")
            )

    @override
    def _init_lifecycle_manager(self) -> craft_parts.LifecycleManager:
        """Create and return the Lifecycle manager.

        An application may override this method if needed if the lifecycle
        manager needs to be called differently.
        """
        # TODO: Remove this once https://github.com/canonical/craft-application/issues/394
        # is fixed.
        emit.debug(f"Initialising lifecycle manager in {self._work_dir}")
        emit.trace(f"Lifecycle: {repr(self)}")

        build_for = self._get_build_for()

        if self._project.package_repositories:
            self._manager_kwargs["package_repositories"] = (
                self._project.package_repositories
            )

        pvars: dict[str, str] = {}
        for var in self._app.project_variables:
            pvars[var] = getattr(self._project, var) or ""
        self._project_vars = pvars

        emit.debug(f"Project vars: {self._project_vars}")
        emit.debug(f"Adopting part: {self._project.adopt_info}")

        try:
            return craft_parts.LifecycleManager(
                {"parts": self._project.parts},
                application_name=self._app.name,
                arch=util.convert_architecture_deb_to_platform(build_for),
                cache_dir=self._cache_dir,
                work_dir=self._work_dir,
                ignore_local_sources=self._app.source_ignore_patterns,
                parallel_build_count=self._get_parallel_build_count(),
                project_vars_part_name=self._project.adopt_info,
                project_vars=self._project_vars,
                track_stage_packages=True,
                partitions=self._partitions,
                **self._manager_kwargs,
            )
        except craft_parts.errors.PartsError as err:
            raise errors.PartsLifecycleError.from_parts_error(err) from err
