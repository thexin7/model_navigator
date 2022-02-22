# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
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

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import torch  # pytype: disable=import-error

from model_navigator.converter.config import TensorRTPrecision
from model_navigator.framework_api.config import Config
from model_navigator.framework_api.package_descriptor import PackageDescriptor
from model_navigator.framework_api.pipelines import TorchPipelineManager
from model_navigator.framework_api.utils import (
    Framework,
    JitType,
    get_default_max_workspace_size,
    get_default_model_name,
    get_default_workdir,
)
from model_navigator.model import Format


def export(
    model: torch.nn.Module,
    dataloader: Callable,
    model_name: Optional[str] = None,
    opset: Optional[int] = None,
    target_formats: Optional[Tuple[Format]] = None,
    jit_options: Optional[Tuple[JitType]] = None,
    workdir: Optional[Path] = None,
    override_workdir: bool = False,
    keep_workdir: bool = True,
    sample_count: Optional[int] = None,
    atol: Optional[float] = None,
    rtol: Optional[float] = None,
    input_names: Optional[Tuple[str]] = None,
    output_names: Optional[Tuple[str]] = None,
    dynamic_axes: Optional[Dict[str, Union[Dict[int, str], List[int]]]] = None,
    trt_dynamic_axes: Optional[Dict[str, Dict[int, Tuple[int, int, int]]]] = None,
    target_precisions: Optional[Tuple[TensorRTPrecision]] = None,
    save_data: bool = True,
    max_workspace_size: Optional[int] = None,
    target_device: Optional[str] = None,
) -> PackageDescriptor:
    """Function exports PyTorch model to all supported formats."""

    if model_name is None:
        model_name = get_default_model_name()
    if max_workspace_size is None:
        max_workspace_size = get_default_max_workspace_size()
    if workdir is None:
        workdir = get_default_workdir()
    if target_formats is None:
        target_formats = (
            Format.TORCHSCRIPT,
            Format.ONNX,
            Format.TORCH_TRT,
            Format.TENSORRT,
        )
    if jit_options is None:
        jit_options = (
            JitType.SCRIPT,
            JitType.TRACE,
        )
    if opset is None:
        opset = torch.onnx.constant_folding_opset_versions[-1]

    if sample_count is None:
        sample_count = 100

    if target_precisions is None:
        target_precisions = (TensorRTPrecision.FP32, TensorRTPrecision.FP16)

    sample = next(dataloader())
    if isinstance(sample, dict):
        forward_kw_names = tuple(sample.keys())
    else:
        forward_kw_names = None

    if target_device is None:
        target_device = "cuda" if torch.cuda.is_available() else "cpu"

    config = Config(
        framework=Framework.PYT,
        model=model,
        model_name=model_name,
        dataloader=dataloader,
        target_formats=target_formats,
        target_jit_type=jit_options,
        opset=opset,
        workdir=workdir,
        override_workdir=override_workdir,
        keep_workdir=keep_workdir,
        sample_count=sample_count,
        atol=atol,
        rtol=rtol,
        dynamic_axes=dynamic_axes,
        target_precisions=target_precisions,
        save_data=save_data,
        _input_names=input_names,
        _output_names=output_names,
        forward_kw_names=forward_kw_names,
        max_workspace_size=max_workspace_size,
        trt_dynamic_axes=trt_dynamic_axes,
        target_device=target_device,
    )

    pipeline_manager = TorchPipelineManager()
    return pipeline_manager.build(config)
