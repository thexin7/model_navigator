#!/usr/bin/env python3
# Copyright (c) 2022, NVIDIA CORPORATION. All rights reserved.
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
"""e2e tests for exporting JAX identity model"""
import argparse
import logging
import pathlib

import yaml

LOGGER = logging.getLogger((__package__ or "main").split(".")[-1])
METADATA = {
    "image_name": "nvcr.io/nvidia/tensorflow:{version}-tf2-py3",
}
EXPECTED_STATUES = [
    "jax.JAX",
    "onnx.OnnxCUDA",
    "onnx.OnnxTensorRT",
    "onnx-jit.OnnxCUDA",
    "onnx-jit.OnnxTensorRT",
    "onnx-jit-xla.OnnxCUDA",
    "onnx-jit-xla.OnnxTensorRT",
    "onnx-xla.OnnxCUDA",
    "onnx-xla.OnnxTensorRT",
    "tf-savedmodel.TensorFlowSavedModel",
    "tf-savedmodel-jit.TensorFlowSavedModel",
    "tf-savedmodel-jit-xla.TensorFlowSavedModel",
    "tf-savedmodel-xla.TensorFlowSavedModel",
    "tf-trt-fp16.TensorFlowTensorRT",
    "tf-trt-fp32.TensorFlowTensorRT",
    "tf-trt-jit-fp16.TensorFlowTensorRT",
    "tf-trt-jit-fp32.TensorFlowTensorRT",
    "tf-trt-jit-xla-fp16.TensorFlowTensorRT",
    "tf-trt-jit-xla-fp32.TensorFlowTensorRT",
    "tf-trt-xla-fp16.TensorFlowTensorRT",
    "tf-trt-xla-fp32.TensorFlowTensorRT",
    "trt-fp16.TensorRT",
    "trt-fp32.TensorRT",
    "trt-jit-fp16.TensorRT",
    "trt-jit-fp32.TensorRT",
    "trt-jit-xla-fp32.TensorRT",
    "trt-jit-xla-fp16.TensorRT",
    "trt-xla-fp32.TensorRT",
    "trt-xla-fp16.TensorRT",
]


def main():
    import jax.numpy as jnp  # pytype: disable=import-error
    import numpy as np

    import model_navigator as nav
    from tests import utils
    from tests.functional.common.utils import collect_status, validate_status

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        type=pathlib.Path,
        required=True,
        help="Status file where per path result is stored.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Timeout for test.",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format=utils.DEFAULT_LOG_FORMAT)
    LOGGER.debug(f"CLI args: {args}")

    def predict(inputs, params):
        outputs = jnp.dot(inputs, params)
        return outputs

    dataloader = [np.random.rand(2, 3, 3) for _ in range(2)]
    params = np.random.rand(2, 3, 3)

    def verify_func(ys_runner, ys_expected):
        for y_runner, y_expected in zip(ys_runner, ys_expected):
            if not all([np.allclose(a, b) for a, b in zip(y_runner.values(), y_expected.values())]):
                return False
        return True

    package = nav.jax.optimize(
        model=predict,
        model_params=params,
        dataloader=dataloader,
        verify_func=verify_func,
        batching=False,
        verbose=True,
        profiler_config=nav.ProfilerConfig(stability_percentage=100),
    )
    nav.package.save(package, "package.nav", override=True)

    status_file = args.status
    status = collect_status(package.status)

    validate_status(status, expected_statuses=EXPECTED_STATUES)

    with status_file.open("w") as fp:
        yaml.safe_dump(status, fp)

    LOGGER.info(f"Status saved to {status_file}")


if __name__ == "__main__":
    main()
