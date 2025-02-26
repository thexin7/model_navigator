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
"""TensorRT runners."""
import contextlib
import copy
from collections import OrderedDict
from distutils.version import LooseVersion
from typing import List, Optional, Sequence

import numpy as np

from model_navigator.api.config import Format, TensorType
from model_navigator.core.logger import LOGGER
from model_navigator.core.tensor import get_tensor_type
from model_navigator.exceptions import ModelNavigatorError, ModelNavigatorUserInputError
from model_navigator.frameworks.tensorrt import cuda as cuda_utils
from model_navigator.frameworks.tensorrt import utils as trt_utils
from model_navigator.runners.base import DeviceKind, NavigatorRunner
from model_navigator.runners.registry import register_runner
from model_navigator.utils import common as utils
from model_navigator.utils import module

trt = module.lazy_import("tensorrt")
torch = module.lazy_import("torch")


class FormattedArray:
    """Representes an array whose semantic shape differs from its physical size in memory.

    [EXPERIMENTAL, UNTESTED] This API is experimental and untested and may be significantly
    modified in future releases. Use with caution!

    For example, consider an ``NCHW`` tensor of shape ``(1, 3, 28, 28)``. If we use a vectorized format
    like ``N(C/4)HW4``, then the physical size of the array would be ``(1, 1, 28, 28 * 4)`` since
    the channel dimension would be padded to a multiple of 4. However, we still need a way to keep
    track of the semantic shape for things like shape inference.

    This class provides a mechanism to specify the shape and dtype of an array independently of
    the underlying array.
    """

    def __init__(self, array: np.ndarray, shape: Sequence[int], dtype: np.dtype):
        """Initialization of FormattedArray.

        Args:
            array: The array. In most cases, this will be a raw byte-array.
            shape: The semantic shape of the data.
            dtype: The data type.
        """
        self.array = array
        self.shape = shape
        self.dtype = dtype


def _make_output_allocator():
    if LooseVersion(trt.__version__) <= LooseVersion("8.5.0.9"):
        raise ModelNavigatorUserInputError("This function should only be called in TensorRT 8.5 and newer")

    class OutputAllocator(trt.IOutputAllocator):
        def __init__(self):
            trt.IOutputAllocator.__init__(self)
            self.buffers = {}
            self.shapes = {}

        def reallocate_output(self, tensor_name, memory, size, alignment):
            shape = (size,)
            if tensor_name not in self.buffers:
                self.buffers[tensor_name] = cuda_utils.DeviceArray.raw(shape)
            else:
                self.buffers[tensor_name].resize(shape)
            LOGGER.debug(f"Reallocated output tensor: {tensor_name} to: {self.buffers[tensor_name]}")
            return self.buffers[tensor_name].ptr

        def notify_shape(self, tensor_name, shape):
            self.shapes[tensor_name] = tuple(shape)

    return OutputAllocator()


class TensorRTRunner(NavigatorRunner):
    """TensorRT runner class."""

    def __init__(self, optimization_profile: Optional[int] = None, *args, **kwargs):
        """Initialization of TensorRTRunner.

        Args:
            model: Path to the model.plan
            optimization_profile: The index of the optimization profile to set each time this runner is activated.
                When this is not provided, the profile is not set explicitly and will default to the 0th profile.
                You can also change the profile after the runner is active using the ``set_profile()`` method.
        """
        super().__init__(*args, **kwargs)
        self._engine_or_context = trt_utils.EngineFromBytes(utils.BytesFromPath(self.model.as_posix()))
        self.optimization_profile = optimization_profile
        self.context = None
        self.engine = None
        self.stream = None
        self.output_allocator = None
        self.device_buffers = None
        self.host_output_buffers = None
        self.use_cuda_graphs = False

    @classmethod
    def format(cls) -> Format:
        """Returns runner format."""
        return Format.TENSORRT

    @classmethod
    def name(cls) -> str:
        """Returns runner name."""
        return "TensorRT"

    @classmethod
    def devices_kind(cls) -> List[DeviceKind]:
        """Returns supported devices for runner."""
        return [DeviceKind.CUDA]

    def set_profile(self, index: int):
        """Sets the active optimization profile for this runner.

        The runner must already be active (see ``__enter__()`` or ``activate()``).

        This only applies if your engine was built with multiple
        optimization profiles.

        In TensorRT 8.0 and newer, the profile will be set asynchronously
        using this runner's CUDA stream (``runner.stream``).

        By default, the runner uses the first profile (profile 0).

        Args:
            index: The index of the optimization profile to use.
        """
        if not hasattr(self, "context") or self.context is None:
            raise ModelNavigatorError(f"{self.name:35} | Must be activated prior to calling set_profile()")

        try:
            self.context.set_optimization_profile_async
        except AttributeError:
            self.context.active_optimization_profile = index
        else:
            if not self.context.set_optimization_profile_async(index, self.stream.ptr):
                raise ModelNavigatorError(f"Failed to set optimization profile to: {index}")

    def get_available_return_types_impl(self) -> List[TensorType]:
        """Implementation of get_available_return_types method."""
        if trt_utils._should_use_v3_api():
            return [TensorType.NUMPY, TensorType.TORCH]
        else:
            return [TensorType.NUMPY]

    def get_available_input_types(self) -> List[TensorType]:
        """Implementation of get_available_return_types method."""
        if trt_utils._should_use_v3_api():
            return [TensorType.NUMPY, TensorType.TORCH]
        else:
            return [TensorType.NUMPY]

    def get_input_metadata_impl(self):
        """Implementation of get_input_metadata method.

        Returns:
            TensorMetadata: Input metadata.
        """
        if trt_utils._should_use_v3_api():
            return trt_utils.get_metadata_from_engine(self.engine, mode=trt.TensorIOMode.INPUT)
        else:
            start_binding, end_binding = trt_utils.get_active_profile_bindings(self.context)
            # This function always uses binding names of the 0th profile.
            return trt_utils.get_input_metadata_from_engine(self.engine, start_binding, end_binding)

    def activate_impl(self):
        """Implementation of activate method.

        Activate runner and prepare it for inference.
        """
        engine_or_context, owning = utils.invoke_if_callable(self._engine_or_context)

        if isinstance(engine_or_context, trt.ICudaEngine):
            self.engine = engine_or_context
            self.owns_engine = owning
            self.context = self.engine.create_execution_context()
            self.owns_context = True
            if not self.context:
                raise RuntimeError("Failed to create execution context")
        elif isinstance(engine_or_context, trt.IExecutionContext):
            self.context = engine_or_context
            self.owns_context = owning
            self.engine = self.context.engine
            self.owns_engine = False
        else:
            raise RuntimeError(
                "Invalid Engine or Context. Please ensure the engine was built correctly. See error log for details."
            )

        def make_buffers_legacy():
            """Creates empty host and device buffers for the specified engine.

            Always uses binding names from Profile 0.
            """
            device_buffers = OrderedDict()
            host_output_buffers = OrderedDict()

            for idx in range(trt_utils.get_bindings_per_profile(self.engine)):
                binding = self.engine[idx]
                dtype = trt_utils.np_dtype_from_trt(self.engine.get_binding_dtype(binding))
                device_buffers[binding] = cuda_utils.DeviceArray(dtype=dtype)
                if not self.engine.binding_is_input(binding):
                    host_output_buffers[binding] = np.empty(shape=(), dtype=dtype)

            LOGGER.debug(f"Initialized device buffers: {device_buffers}")
            return device_buffers, host_output_buffers, None

        def make_buffers():
            """Creates empty host buffers for outputs and empty device buffers for inputs."""
            device_buffers = OrderedDict()
            host_output_buffers = OrderedDict()
            output_allocator = _make_output_allocator()

            for idx in range(self.engine.num_io_tensors):
                name = self.engine.get_tensor_name(idx)

                # NOTE: We use raw arrays to enable vectorized formats.
                if self.engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
                    device_buffers[name] = cuda_utils.DeviceArray.raw(shape=())
                elif self.engine.get_tensor_mode(name) == trt.TensorIOMode.OUTPUT:
                    host_output_buffers[name] = np.empty(shape=(), dtype=np.byte)
                    if not self.context.set_output_allocator(name, output_allocator):
                        raise ModelNavigatorError(f"For output: {name}, failed to set output allocator")
                else:
                    # TODO: warning, error or exception here?
                    LOGGER.warning(
                        f"Unexpected tensor I/O mode encountered during inference: {self.engine.get_tensor_mode(name)}.\n"
                        "Please update this implementation!"
                    )

            LOGGER.debug(f"Initialized device buffers: {device_buffers}")
            return device_buffers, host_output_buffers, output_allocator

        self.device_buffers, self.host_output_buffers, self.output_allocator = (
            make_buffers() if trt_utils._should_use_v3_api() else make_buffers_legacy()
        )
        self.stream = cuda_utils.Stream()

        if self.optimization_profile is not None:
            self.set_profile(self.optimization_profile)
        self.cuda_graph = None

    def _set_shapes_from_feed_dict_legacy(self, feed_dict):
        """Sets context shapes according to the provided feed_dict.

        Note that ``infer()`` will call this function automatically, and hence
        you should only use it if you plan to use this runner's context manually.

        Args:
            feed_dict (OrderedDict[str, numpy.ndarray]):
                    A mapping of input tensor names to corresponding input NumPy arrays.

        Returns:
            Tuple[int, int]: The start and end binding indices of the modified bindings.
        """

        def is_dynamic_shape_input(binding):
            return self.engine.is_shape_binding(binding) and self.engine.binding_is_input(binding)

        start_binding, end_binding = trt_utils.get_active_profile_bindings(self.context)
        for name, inp in feed_dict.items():
            binding = start_binding + self.engine[name]
            # Only set shapes if required.
            # get_shape/get_binding_shape will return what a shape input/data input is currently set to.
            if is_dynamic_shape_input(binding):  # For input shape tensors
                if isinstance(inp, cuda_utils.DeviceView):
                    raise ModelNavigatorUserInputError(
                        f"A DeviceView was provided for input: {name}, but since this is a shape tensor, "
                        "it must reside in host memory. Please use a NumPy array instead. "
                    )

                if tuple(self.context.get_shape(binding)) != tuple(inp):
                    LOGGER.debug(f"Setting shape binding: {name} (index: {binding}) to: {inp}")
                    if not self.context.set_shape_input(binding, inp):
                        raise ModelNavigatorError(
                            f"Failed to set shape binding: {name} (index: {binding}) to: {inp}. "
                            "Are these values valid for the binding?"
                        )

            elif trt_utils.is_shape_dynamic(self.engine.get_binding_shape(binding)):
                shape = inp.shape
                if tuple(self.context.get_binding_shape(binding)) != tuple(shape):
                    LOGGER.debug(f"Setting binding: {name} (index: {binding}) to shape: {shape}")
                    if not self.context.set_binding_shape(binding, shape):
                        raise ModelNavigatorError(
                            f"Failed to set binding: {name} (index: {binding}) to shape: {shape}. "
                            "Is this shape valid for the binding?"
                        )

        if not self.context.all_binding_shapes_specified:
            raise ModelNavigatorUserInputError(
                f"Some input shapes were not specified.\nNote: Network inputs are: {self.get_input_metadata()}"
            )
        if not self.context.all_shape_inputs_specified:
            raise ModelNavigatorUserInputError(
                f"Some shape inputs were not specified.\nNote: Network inputs are: {self.get_input_metadata()}"
            )

        return start_binding, end_binding

    def _infer_impl_legacy(self, feed_dict, copy_outputs_to_host):
        if self.use_cuda_graphs:
            LOGGER.warning("CUDA graphs are not supported in legacy mode. Ignoring use_cuda_graphs=True.")
        start_binding, end_binding = self._set_shapes_from_feed_dict_legacy(feed_dict)

        # Resize output device buffers - host buffers will be automatically resized by copy_to
        for binding in range(start_binding, end_binding):
            if not self.engine.binding_is_input(binding):
                name = self.engine[binding - start_binding]  # Use profile 0 binding names for all buffers.
                shape = tuple(self.context.get_binding_shape(binding))
                self.device_buffers[name].resize(shape)

        # Use a shallow copy in case we need to replace our allocated buffers with provided DeviceViews.
        dev_bufs = copy.copy(self.device_buffers)
        for name, buffer in feed_dict.items():
            if isinstance(buffer, cuda_utils.DeviceView):
                dev_bufs[name] = buffer
            elif isinstance(buffer, np.ndarray):
                dev_bufs[name].resize(buffer.shape)
                buffer = utils.make_contiguous(buffer)
                dev_bufs[name].copy_from(buffer, self.stream)
            else:
                raise ModelNavigatorUserInputError(
                    f"For input: {name}, unrecognized type in feed_dict: {type(buffer).__name__}.\n"
                    "Please provide either a NumPy array or DeviceView. "
                )

        # Need to offset bindings in case the active profile is not 0.
        bindings = [0] * start_binding + [buf.ptr for buf in dev_bufs.values()]
        success = self.context.execute_async_v2(bindings=bindings, stream_handle=self.stream.ptr)
        if not success:
            raise ModelNavigatorError("Model execution failed. Please see the log messages above for details")

        output_buffers = OrderedDict()
        for name, buffer in self.host_output_buffers.items():
            if copy_outputs_to_host:
                self.host_output_buffers[name] = utils.resize_buffer(buffer, dev_bufs[name].shape)
                dev_bufs[name].copy_to(self.host_output_buffers[name], self.stream)
                output_buffers[name] = self.host_output_buffers[name]
            else:
                output_buffers[name] = dev_bufs[name].view()

        self.stream.synchronize()
        return output_buffers

    def _infer_impl_v3(self, feed_dict):  # noqa C901
        reshape = False
        for idx in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(idx)

            if self.engine.get_tensor_mode(name) != trt.TensorIOMode.INPUT:
                continue

            # Set up input tensor shapes and copy from host memory if needed
            array = self._input_tensor_view(feed_dict, name)

            underlying_array = array.array

            ptr = None
            if self.engine.is_shape_inference_io(name):
                if not isinstance(underlying_array, np.ndarray):
                    raise ModelNavigatorUserInputError(
                        f"A {type(underlying_array).__name__} was provided for input: {name}, but since this is a shape tensor, "
                        "it must reside in host memory. Please use a NumPy array instead. "
                    )

                ptr = underlying_array.ctypes.data
            else:
                if isinstance(underlying_array, cuda_utils.DeviceView):
                    ptr = underlying_array.ptr
                elif isinstance(underlying_array, np.ndarray):
                    underlying_array = utils.make_contiguous(underlying_array)
                    dev_array = self.device_buffers[name]
                    dev_array.resize(shape=(underlying_array.nbytes,))

                    # For scalars, we need to reshape the array to 1D before we can use `view()` or NumPy complains.
                    if not underlying_array.shape:
                        view = underlying_array.reshape(-1).view(np.byte)
                    else:
                        view = underlying_array.view(np.byte)

                    dev_array.copy_from(view, stream=self.stream)
                    ptr = dev_array.ptr
                else:
                    raise ModelNavigatorUserInputError(
                        f"For input: {name}, unrecognized type in feed_dict: {type(underlying_array).__name__}.\n"
                        "Please provide either a NumPy array or DeviceView. "
                    )

            # Only update the input shape/address if something has changed. Otherwise, we'd be
            # doing extra work unnecessarily.
            # We retrieve the semantic shape from the FormattedArray, *not* the underlying array.
            tensor_shape = self.context.get_tensor_shape(name)
            if tensor_shape != array.shape:
                LOGGER.debug(f"Setting {name} input shape to: {array.shape}")
                reshape = True
                if not self.context.set_input_shape(name, array.shape):
                    raise ModelNavigatorError(
                        f"""For input: {name}, failed to set shape from {tensor_shape} to: {array.shape}."""
                        f"""Please, review if input data shape match the maximal input size which is {tensor_shape}."""
                    )

            if self.context.get_tensor_address(name) != ptr:
                if not self.context.set_tensor_address(name, ptr):
                    raise ModelNavigatorError(f"For input: {name}, failed to set tensor address to: {ptr}")
        # Any change in shape requires recapture of CUDA graph
        if reshape:
            self.cuda_graph = None
            self.graph_exe = None

        if self.use_cuda_graphs:
            if self.cuda_graph is None:
                # do inference before CUDA graph capture
                if not self.context.execute_async_v3(self.stream.ptr):
                    raise ModelNavigatorError(
                        "`execute_async_v3()` failed. Please see the logging output above for details."
                    )
                self.stream.synchronize()  # Added just in case
                # CUDA graph capture
                self.stream.begin_capture()

                if not self.context.execute_async_v3(self.stream.ptr):
                    raise ModelNavigatorError(
                        "`execute_async_v3()` failed. Please see the logging output above for details."
                    )
                self.cuda_graph = self.stream.end_capture()
                self.stream.synchronize()  # Added to prevent crash
                self.graph_exe = self.cuda_graph.instantiate()
                self.stream.synchronize()  # Added just in case

            self.graph_exe.launch(self.stream)
            self.stream.synchronize()  # Added base on examples
        else:
            if not self.context.execute_async_v3(self.stream.ptr):
                raise ModelNavigatorError(
                    "`execute_async_v3()` failed. Please see the logging output above for details."
                )

        output_buffers = OrderedDict()
        for name in self.host_output_buffers.keys():
            output_buffers[name] = self._output_tensor_view(name, self.return_type)

        self.stream.synchronize()
        return output_buffers

    def _input_tensor_view(self, feed_dict, name) -> FormattedArray:
        # Set up input tensor shapes and copy from host memory if needed
        array = feed_dict[name]
        tensor_type = get_tensor_type(array)
        if tensor_type == TensorType.TORCH:
            if not array.is_cuda:
                array = array.cuda()
            array = cuda_utils.DeviceView(
                ptr=array.data_ptr(), shape=array.shape, dtype=utils.torch_to_numpy_dtype(array.dtype)
            )
        array = FormattedArray(array, shape=array.shape, dtype=array.dtype)  # pytype: disable=wrong-arg-types
        return array

    def _output_tensor_view(self, name, return_type):
        # If we're dealing with vectorized formats, we need to return a FormattedArray.
        # Otherwise, we create a view instead with the correct shape/dtype.
        raw_array = self.output_allocator.buffers[name]
        shape = self.output_allocator.shapes[name]
        dtype = np.dtype(trt.nptype(self.engine.get_tensor_dtype(name)))

        using_nonlinear_format = self.engine.get_tensor_format(name) != trt.TensorFormat.LINEAR
        if using_nonlinear_format:
            raise NotImplementedError("Nonlinear formats are not yet supported.")
        # The memory allocated by the output allocator may be larger than actually required.
        # If we're using a vectorized format, then we need to copy the whole thing.
        # Otherwise, we can determine how much we actually need.
        nbytes = raw_array.nbytes if using_nonlinear_format else (utils.volume(shape) * dtype.itemsize)

        if return_type == TensorType.NUMPY:
            self.host_output_buffers[name] = utils.resize_buffer(self.host_output_buffers[name], (nbytes,))
            raw_array.view(shape=(nbytes,)).copy_to(self.host_output_buffers[name], stream=self.stream)
            raw_array = self.host_output_buffers[name]

            array = FormattedArray(raw_array, shape=shape, dtype=dtype)
            array = raw_array.view(dtype).reshape(shape)
        else:
            assert return_type == TensorType.TORCH
            array = cuda_utils.DeviceView(raw_array.ptr, shape, dtype).torch(stream=self.stream)

        return array

    def infer_impl(
        self,
        feed_dict,
    ):
        """Implementation for running inference with TensorRT.

        Do not call this method directly - use ``infer()`` instead,
        which will forward unrecognized arguments to this method.

        In addition to accepting NumPy arrays in the feed_dict, this runner can also
        accept DeviceViews. In that case, no host-to-device copy is necessary for the inputs.

        Args:
            feed_dict (OrderedDict[str, Union[numpy.ndarray, DeviceView]]):
                    A mapping of input tensor names to corresponding input NumPy arrays
                    or DeviceViews.

            copy_outputs_to_host (bool):
                    Whether to copy inference outputs back to host memory.
                    If this is False, DeviceViews are returned
                    instead of NumPy arrays.
                    Defaults to True.

        Returns:
            OrderedDict[str, Union[numpy.ndarray, DeviceView]]:
                    A mapping of output tensor names to corresponding output NumPy arrays
                    or DeviceViews.
        """
        input_metadata = self.get_input_metadata_impl()
        feed_dict = {
            name: trt_utils.cast_tensor(tensor, dtype=input_metadata[name].dtype)
            for name, tensor in feed_dict.items()
            if name in input_metadata
        }

        if trt_utils._should_use_v3_api():
            output_buffers = self._infer_impl_v3(feed_dict)
        else:
            output_buffers = self._infer_impl_legacy(feed_dict, True)
        out_dict = {k: v for k, v in output_buffers.items() if k in self.output_metadata}

        return out_dict

    def deactivate_impl(self):
        """Implementation for deactivating the runner.

        Do not call this method directly - use ``deactivate()`` instead,

        Deactive the runner by freeing all resources.
        """
        with contextlib.ExitStack() as stack:
            if self.owns_engine:
                stack.enter_context(self.engine)
            if self.owns_context:
                stack.enter_context(self.context)

            [buf.free() for buf in self.device_buffers.values()]
            self.stream.free()

        del (
            self.engine,
            self.owns_engine,
            self.context,
            self.owns_context,
            self.device_buffers,
            self.host_output_buffers,
            self.output_allocator,
            self.stream,
        )


class TensorRTCUDAGraphRunner(TensorRTRunner):
    """TensorRT runner that uses CUDA graphs for faster inference."""

    def __init__(self, *args, **kwargs):
        """Initialization of TensorRT runner that uses CUDA graphs for faster inference."""
        super().__init__(*args, **kwargs)
        self.use_cuda_graphs = True

    @classmethod
    def name(cls) -> str:
        """Returns the name of the runner."""
        return "TensorRTCUDAGraph"


def register_tensorrt_runners():
    """Register TensorRT runners."""
    register_runner(TensorRTRunner)
    register_runner(TensorRTCUDAGraphRunner)
