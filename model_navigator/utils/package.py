# Copyright (c) 2021-2023, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Load package utils."""

from pathlib import Path
from typing import TYPE_CHECKING

from packaging import version

from model_navigator.api.config import Format
from model_navigator.configuration.model.model_config import ModelConfig
from model_navigator.exceptions import ModelNavigatorBackwardCompatibilityError
from model_navigator.logger import LOGGER
from model_navigator.utils.framework import Framework
from model_navigator.utils.tensor import TensorMetadata

if TYPE_CHECKING:
    from model_navigator.core.package import Package


class PackageUpdater:
    """Class for updating the package to the current version.

    Raises:
        ModelNavigatorBackwardCompatibilityError: When the package is no longer supported.
    """

    def __init__(self):
        """Construct PackageUpdater."""
        self._updates = {version.parse("0.3.3"): self._update_from_v0_3_3}

    @staticmethod
    def _update_from_v0_3_3(package):
        if package.framework == Framework.TENSORFLOW:
            if len(package.status.input_metadata) > 1:
                raise ModelNavigatorBackwardCompatibilityError(
                    "Cannot load TensorFlow2 .nav packages generated by Model Navigator "
                    "version < 0.3.4 and with multiple inputs."
                )
            model_config = package.status.models_status[Format.TF_SAVEDMODEL.value].model_config
            _update_savedmodel_signature(
                model_config=model_config,
                input_metadata=package.status.input_metadata,
                output_metadata=package.status.output_metadata,
                workspace=package.workspace,
            )

    def update(self, package: "Package", pkg_version: version.Version):
        """Update the package to the current version.

        Args:
            package (Package): Package to be updated.
            pkg_version (version.Version): Version of the package to be updated.
        """
        for update_from_version, update_func in self._updates.items():
            if pkg_version <= update_from_version:
                update_func(package)


def _update_savedmodel_signature(
    model_config: ModelConfig,
    input_metadata: TensorMetadata,
    output_metadata: TensorMetadata,
    workspace: Path,
    verbose: bool = False,
):
    LOGGER.info("Updating SavedModel signature...")
    from model_navigator.commands.export.tf import UpdateSavedModelSignature

    UpdateSavedModelSignature().run(
        path=model_config.path,
        input_metadata=input_metadata,
        output_metadata=output_metadata,
        workspace=workspace,
        verbose=verbose,
    )