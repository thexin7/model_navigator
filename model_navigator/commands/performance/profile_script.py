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
"""Script for running profiling on a runner."""

import pathlib
from typing import Dict, List, Optional

import fire

from model_navigator.api.config import OptimizationProfile
from model_navigator.commands.performance.profiler import Profiler
from model_navigator.core.tensor import TensorMetadata
from model_navigator.runners.registry import get_runner
from model_navigator.utils.dataloader import load_samples


def get_model() -> object:
    """Get model instance.

    Returns:
        Model to be profiled.
    """
    raise NotImplementedError(
        "Please implement the get_model() function if model cannot be read by the runner from the path."
    )


def profile(
    batch_dim: int,
    results_path: str,
    runner_name: str,
    optimization_profile: Dict,
    input_metadata: List,
    output_metadata: List,
    sample_id: int = 0,
    navigator_workspace: Optional[str] = None,
    model_path: Optional[str] = None,
) -> None:
    """Run profiling.

    Args:
        batch_dim: Batch dimension.
        results_path: Path to store the profiling results in.
        runner_name: Name of the runner to profile.
        optimization_profile: Optimization profile used for configuration.
        input_metadata: Input metadata.
        output_metadata: Output metadata.
        sample_id: Identifier of profiled sample.
        navigator_workspace: Path of the Model Navigator workspace.
            When None use current workdir. Defaults to None.
        model_path: Path to the model.
            When None use `get_model()` to load the model. Defaults to None.
    """
    if not navigator_workspace:
        navigator_workspace = pathlib.Path.cwd()
    navigator_workspace = pathlib.Path(navigator_workspace)

    profiling_sample = load_samples("profiler_sample", navigator_workspace, batch_dim)[0]

    if model_path:
        model = navigator_workspace / model_path
    else:
        model = get_model()

    runner = get_runner(runner_name)(
        model=model,
        input_metadata=TensorMetadata.from_json(input_metadata),
        output_metadata=TensorMetadata.from_json(output_metadata),
    )  # pytype: disable=not-instantiable

    Profiler(
        profile=OptimizationProfile.from_dict(optimization_profile),
        batch_dim=batch_dim,
        results_path=pathlib.Path(results_path),
    ).run(
        runner=runner,
        profiling_sample=profiling_sample,
        sample_id=sample_id,
    )


if __name__ == "__main__":
    fire.Fire(profile)
