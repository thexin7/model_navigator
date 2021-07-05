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
from typing import Dict, Tuple

import click
import numpy as np

from model_navigator.constants import ALL_OTHER_INPUTS
from model_navigator.model import Format
from model_navigator.tensor import TensorSpec
from model_navigator.triton import DeviceKind
from model_navigator.utils.cli import CliSpec


class ModelConfigCli:
    model_name = CliSpec(help="Name of the model.", param_decls=["-n", "--model-name"])
    model_path = CliSpec(help="Path to the model file.", param_decls=["-p", "--model-path"])
    model_format = CliSpec(
        help="Format of the model. Should be provided in case it is not possible to obtain format from model filename."
    )
    model_version = CliSpec(help="Version of model used by the Triton Inference Server.")


def _parse_io(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config file
            value = {
                name: TensorSpec(name=spec["name"], shape=tuple(spec["shape"]), dtype=np.dtype(spec["dtype"]))
                for name, spec in value.items()
            }
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for item in value:
                # in case of rename provide <new_io_name>=<old_io_name>:<shape>:<dtype>
                if "=" in item:
                    io_name, item = item.split("=")
                else:
                    io_name = None

                tensor_name, shape, dtype = item.rsplit(":", 2)
                shape = tuple(map(int, shape.split(",")))
                dtype = np.dtype(dtype)

                if io_name is None:
                    io_name = tensor_name

                parsed_value[io_name] = TensorSpec(name=tensor_name, shape=shape, dtype=dtype)
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as model signature input/output")
    return value


class ModelSignatureConfigCli:
    inputs = CliSpec(help="Signature of the model inputs.", parse_and_verify_callback=_parse_io)
    outputs = CliSpec(help="Signature of the model outputs.", parse_and_verify_callback=_parse_io)


def parse_shapes(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config file
            value = {name: tuple(shape) for name, shape in value.items()}
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for item in value:
                input_name, shape = item.split("=")

                shape = tuple(map(int, shape.split(",")))
                parsed_value[input_name] = shape
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as shape spec")
    return value


def serialize_shapes(param, value: Dict[str, Tuple]):
    return [f"{name}={','.join(map(str, shape))}" for name, shape in value.items()]


def parse_value_ranges(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config
            value = {name: tuple(value_range) for name, value_range in value.items()}
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for entry in value:
                *input_name, value_range = entry.rsplit("=", 1)
                input_name = (input_name or [ALL_OTHER_INPUTS])[0]
                lower_bound, upper_bound = value_range.split(",")
                has_dot = "." in lower_bound or "." in upper_bound
                lower_bound, upper_bound = float(lower_bound), float(upper_bound)
                if lower_bound.is_integer() and upper_bound.is_integer() and not has_dot:
                    lower_bound, upper_bound = int(lower_bound), int(upper_bound)

                parsed_value[input_name] = (lower_bound, upper_bound)
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as value range")
    return value


def serialize_value_ranges(param, value: Dict[str, Tuple]):
    return [
        f"{name}={value_range[0]},{value_range[1]}"
        if name == ALL_OTHER_INPUTS
        else f"{value_range[0]},{value_range[1]}"
        for name, value_range in value.items()
    ]


def serialize_dtypes(param, value: Dict[str, np.dtype]):
    return [f"{name}={str(dtype)}" if name == ALL_OTHER_INPUTS else str(dtype) for name, dtype in value.items()]


def parse_dtypes(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config
            value = {name: np.dtype(dtype) for name, dtype in value.items()}
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for entry in value:
                *input_name, dtype = entry.rsplit("=", 1)
                input_name = (input_name or [ALL_OTHER_INPUTS])[0]
                parsed_value[input_name] = np.dtype(dtype)
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as value range")
    return value


class DatasetProfileConfigCli:
    min_shapes = CliSpec(
        help=(
            "Map of features names and minimum shapes visible in the dataset. "
            "Format: --min-shapes <input0>=D0,D1,..,DN .. <inputN>=D0,D1,..,DN"
        ),
        parse_and_verify_callback=parse_shapes,
    )
    opt_shapes = CliSpec(
        help=(
            "Map of features names and optimal shapes visible in the dataset. "
            "Used during the definition of the TensorRT optimization profile. "
            "Format: --opt-shapes <input0>=D0,D1,..,DN .. <inputN>=D0,D1,..,DN"
        ),
        parse_and_verify_callback=parse_shapes,
    )
    max_shapes = CliSpec(
        help=(
            "Map of features names and maximal shapes visible in the dataset. "
            "Format: --max-shapes <input0>=D0,D1,..,DN .. <inputN>=D0,D1,..,DN"
        ),
        parse_and_verify_callback=parse_shapes,
    )
    value_ranges = CliSpec(
        help=(
            "Map of features names and range of values visible in the dataset. "
            "Format: --value-ranges <input0>=<lower_bound>,<upper_bound> .. "
            "<inputN>=<lower_bound>,<upper_bound> <default_lower_bound>,<default_upper_bound>"
        ),
        parse_and_verify_callback=parse_value_ranges,
        serialize_default_callback=serialize_value_ranges,
    )
    dtypes = CliSpec(
        help=(
            "Map of features names and numpy dtypes visible in the dataset. "
            "Format: --dtypes <input0>=<dtype> <input1>=<dtype> <default_dtype>"
        ),
        parse_and_verify_callback=parse_dtypes,
        serialize_default_callback=serialize_dtypes,
    )


def _parse_target_formats(ctx, param, value):
    # parser was added for easier integration with existing projects
    # can be removed when project switch to this format values
    if value:
        EXTERNAL_PROJECTS_INTEGRATION_MAPPING = {
            "ts-trace": Format.TORCHSCRIPT,
            "ts-script": Format.TORCHSCRIPT,
        }

        def _str2format(format_str):
            try:
                return Format(format_str)
            except ValueError:
                pass
            try:
                return EXTERNAL_PROJECTS_INTEGRATION_MAPPING[format_str]
            except KeyError:
                raise click.BadParameter(f"{format_str} is not valid Format")

        if isinstance(value, list):  # from cli or config
            value = [_str2format(entry) for entry in value]
        elif isinstance(value, str):  # from cli or config
            value = _str2format(value)
        elif isinstance(value, Format):  # from cli or config
            value = value.value
        else:
            raise click.BadParameter(f"Could not parse {value} as comparator config")

    return value


def _serialize_target_formats(param, value):
    if isinstance(value, list):
        value = [f"{f.value}" for f in value]
    elif isinstance(value, Format):
        return value.value
    else:
        raise click.BadParameter(f"Could not parse {value} as comparator config")

    return value


class ConversionSetConfigCli:
    # parser was added for easier integration with existing projects
    target_formats = CliSpec(
        help="Target format to generate.",
        parse_and_verify_callback=_parse_target_formats,
        serialize_default_callback=_serialize_target_formats,
    )

    # ONNX specific
    onnx_opsets = CliSpec(help="Generate an ONNX graph that uses only ops available in a given opset.")

    # TRT specific
    target_precisions = CliSpec(help="Configure TensorRT builder for precision layer selection.")
    max_workspace_size = CliSpec(help="The amount of workspace the ICudaEngine uses.")


class ConversionSetHelmChartConfigCli:
    """
    CLI spec for Helm Chart generator which override default values
    """

    # parser was added for easier integration with existing projects
    target_formats = CliSpec(
        help="Target format to generate.",
        parse_and_verify_callback=_parse_target_formats,
        default_factory=lambda: [],
    )

    # ONNX specific
    onnx_opsets = CliSpec(
        help="Generate an ONNX graph that uses only ops available in a given opset.", default_factory=lambda: []
    )

    # TRT specific
    target_precisions = CliSpec(
        help="Configure TensorRT builder for precision layer selection.", default_factory=lambda: []
    )
    max_workspace_size = CliSpec(help="The amount of workspace the ICudaEngine uses.")


class TritonClientConfigCli:
    server_url = CliSpec(help="Inference server URL in format protocol://host[:port]")


class TritonModelOptimizationConfigCli:
    backend_accelerator = CliSpec(help="Select Backend Accelerator used to serve the model.")
    # TODO: ensure that it works for also for ONNX backend
    tensorrt_precision = CliSpec(help="Target model precision for TensorRT acceleration.")
    tensorrt_capture_cuda_graph = CliSpec(help="Enable CUDA capture graph feature on the TensorRT backend.")


class TritonModelSchedulerConfigCli:
    max_batch_size = CliSpec(
        help="Maximum batch size allowed for inference. "
        "A max_batch_size value of 0 indicates that batching is not allowed for the model"
    )
    preferred_batch_sizes = CliSpec(
        help="Batch sizes that the dynamic batcher should attempt to create. "
        "In case --max-queue-delay-us is set and this parameter is not, default value will be --max-batch-size."
    )
    max_queue_delay_us = CliSpec(help="Max delay time that the dynamic batcher will wait to form a batch.")


def _serialize_engine_count(param, value: Dict[DeviceKind, int]):
    return [f"{kind.value}={count}" for kind, count in value.items()]


def _parse_engine_count(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config
            value = {DeviceKind(kind): int(count) for kind, count in value.items()}
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for entry in value:
                kind, count = entry.split("=")
                parsed_value[DeviceKind(kind)] = int(count)
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as value range")
    return value


class TritonModelInstancesConfigCli:
    engine_count_per_device = CliSpec(
        help="Mapping of device kind to model instances count on a single device. Available devices: [cpu|gpu]. "
        "Format: --engine-count-per-device <kind>=<count>",
        parse_and_verify_callback=_parse_engine_count,
        serialize_default_callback=_serialize_engine_count,
    )


def _serialize_tolerance_parameters(param, value):
    if isinstance(value, float):
        value = {ALL_OTHER_INPUTS: value}
    return [f"{k}={v}" if k == ALL_OTHER_INPUTS else str(v) for k, v in value.items()]


def _parse_tolerance_parameters(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config
            pass
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for entry in value:
                *name_, value_ = entry.rsplit("=", 1)
                name_ = (name_ or [ALL_OTHER_INPUTS])[0]
                try:
                    value_ = float(value_)
                    parsed_value[name_] = value_
                except ValueError:
                    raise click.BadParameter(f"Could not parse {param}: {name_}={value_} as float.")
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as comparator config")
    return value


class ComparatorConfigCli:
    atol = CliSpec(
        help=(
            "Absolute tolerance parameter for output comparison. "
            "To specify per-output tolerances, use the format: --atol [<out_name>:]<atol>. "
            "Example: --atol 1e-5 out0:1e-4 out1:1e-3"
        ),
        parse_and_verify_callback=_parse_tolerance_parameters,
        serialize_default_callback=_serialize_tolerance_parameters,
    )
    rtol = CliSpec(
        help=(
            "Relative tolerance parameter for output comparison. "
            "To specify per-output tolerances, use the format: --rtol [<out_name>:]<rtol>. "
            "Example: --rtol 1e-5 out0:1e-4 out1:1e-3"
        ),
        parse_and_verify_callback=_parse_tolerance_parameters,
        serialize_default_callback=_serialize_tolerance_parameters,
    )
    max_batch_size = CliSpec(help=TritonModelSchedulerConfigCli.max_batch_size.help)


def _serialize_objectives(param, value: Dict[str, int]):
    if list(set(value.values())) == 1:
        return [name for name, weight in value.items()]
    else:
        return [f"{name}={weight}" for name, weight in value.items()]


def _parse_objectives(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config
            value = {name: int(weight) for name, weight in value.items()}
        elif isinstance(value, list):  # from cli or config
            parsed_value = {}
            at_least_one_have_weight = False
            for entry in value:
                name, *weight = entry.split("=")
                have_weight = bool(len(weight))
                if have_weight:
                    at_least_one_have_weight = bool(have_weight)
                    weight = int(weight[0])
                elif not at_least_one_have_weight:
                    weight = 1
                else:
                    raise click.BadParameter(
                        "Provide weight for other objectives, if one of objectives have weight assigned"
                    )
                parsed_value[name] = weight

            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as value range")
    return value


def parse_instance_counts(ctx, param, value):
    if value:
        if isinstance(value, dict):  # from config file
            value = {name: list(count) for name, count in value.items()}
        elif isinstance(value, list):  # from cli
            parsed_value = {}
            for item in value:
                input_name, count = item.split("=")

                count = list(map(int, count.split(",")))
                parsed_value[input_name] = count
            value = parsed_value
        else:
            raise click.BadParameter(f"Could not parse {value} as instance count")
    return value


class ModelAnalyzerTritonConfigCli:
    triton_launch_mode = CliSpec(
        help="The method used  to launch the Triton Server. "
        "'local' assume tritonserver binary is available locally. "
        "'docker' pulls and launches a triton docker container with the specified version."
    )
    model_repository = CliSpec(help="Path to the Triton Model Repository.")
    triton_server_path = CliSpec(help="Path to the Triton Server binary when the local mode is enabled.")
    perf_measurement_window = CliSpec(
        help="Time interval in milliseconds between perf_analyzer measurements. perf_analyzer will take "
        "measurements over all the requests completed within this time interval."
    )


class ModelAnalyzerProfileConfigCli:
    max_concurrency = CliSpec(help="Max concurrency used for config search in analysis.")
    max_instance_count = CliSpec(help="Max number of model instances used for config search in analysis.")
    max_batch_size = CliSpec(help=TritonModelSchedulerConfigCli.max_batch_size.help)
    concurrency = CliSpec(
        help="""List of concurrency values used for config search in analysis. """
        """Disable search over max_concurrency. """
        """Format: --concurrency 1 2 4 ... N""",
    )
    instance_counts = CliSpec(
        help="""List of model instance count values used for config search in analysis. """
        """Disable search over max_instance_count in profiling. """
        """Format: --instance-counts <DeviceKind>=<count> <DeviceKind>=<count> ...""",
        parse_and_verify_callback=parse_instance_counts,
    )
    preferred_batch_sizes = CliSpec(help=TritonModelSchedulerConfigCli.preferred_batch_sizes.help)


class ModelAnalyzerAnalysisConfigCli:
    max_latency_ms = CliSpec(help="Maximum latency in ms that the analyzed models should match.")
    min_throughput = CliSpec(help="Minimal throughput that the analyzed models should match.")
    max_gpu_usage_mb = CliSpec(help="Maximal GPU memory usage in MB that analyzed model should match.")
    top_n_configs = CliSpec(help="Number of top final configurations selected from the analysis.")
    objectives = CliSpec(
        help="The Model Navigator uses the objectives described here to find the best configuration for the model.",
        parse_and_verify_callback=_parse_objectives,
        serialize_default_callback=_serialize_objectives,
    )